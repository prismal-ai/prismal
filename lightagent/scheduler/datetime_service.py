"""
DateTimeService — single source of truth for all datetime operations in LightAgent.

Provides timezone resolution, NTP-corrected timestamps, cron next-run computation,
and UTC conversion utilities. All components MUST use this service instead of
calling ``datetime.now()``, ``croniter``, or APScheduler triggers directly.

Timezone resolution order (highest → lowest priority):
  1. Explicit ``tz_str`` argument passed to :meth:`resolve_timezone`
  2. ``LIGHTAGENT_CRON_TIMEZONE`` environment variable
  3. ``LIGHTAGENT_TIMEZONE`` environment variable
  4. OS local timezone via ``tzlocal.get_localzone()``
  5. UTC (final fallback)
"""

from __future__ import annotations

import logging
from datetime import UTC, datetime, timedelta
from threading import Lock
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

import structlog
import tzlocal
from croniter import croniter

from lightagent.core.config import get_settings

logger: structlog.BoundLogger = structlog.get_logger(__name__)

_UTC = ZoneInfo("UTC")


class DateTimeService:
    """Single source of truth for all datetime operations in LightAgent.

    Timezone resolution order:
      1. Explicit tz_str argument
      2. LIGHTAGENT_CRON_TIMEZONE (for cron operations)
      3. LIGHTAGENT_TIMEZONE
      4. tzlocal.get_localzone()
      5. UTC (fallback)

    Usage::

        dts = DateTimeService.get()
        now = dts.now()  # aware datetime in resolved TZ
        next_fire = dts.next_run("0 9 * * *")

    Tests can reset the singleton via ``DateTimeService._instance = None``.
    """

    _instance: DateTimeService | None = None
    _lock: Lock = Lock()

    # NTP state (per-instance)
    _ntp_offset: timedelta | None
    _ntp_last_sync: datetime | None

    def __init__(self) -> None:
        """Initialise the service and perform the first NTP sync if enabled."""
        self._ntp_offset = None
        self._ntp_last_sync = None
        settings = get_settings()
        if settings.ntp_enabled:
            self._sync_ntp()

    # ── Singleton ──────────────────────────────────────────────────────────────

    @classmethod
    def get(cls) -> DateTimeService:
        """Return the process-wide singleton instance (thread-safe).

        Returns:
            The shared :class:`DateTimeService` instance.
        """
        if cls._instance is None:
            with cls._lock:
                if cls._instance is None:
                    cls._instance = cls()
        return cls._instance

    # ── Timezone resolution ────────────────────────────────────────────────────

    def resolve_timezone(self, tz_str: str | None = None) -> ZoneInfo:
        """Resolve a timezone string to a :class:`~zoneinfo.ZoneInfo` object.

        If *tz_str* is a non-empty string, it is parsed as an IANA name.
        Invalid names log a WARNING and fall back to UTC.

        If *tz_str* is ``None`` or empty the resolution chain is walked:
        ``LIGHTAGENT_CRON_TIMEZONE`` → ``LIGHTAGENT_TIMEZONE`` →
        OS localzone → UTC.

        Args:
            tz_str: IANA timezone name or ``None`` to use the resolution chain.

        Returns:
            A :class:`~zoneinfo.ZoneInfo` instance. Never raises.
        """
        if tz_str:
            return self._parse_iana(tz_str)

        settings = get_settings()

        # Step 2: LIGHTAGENT_CRON_TIMEZONE
        if settings.cron_timezone:
            return self._parse_iana(settings.cron_timezone)

        # Step 3: LIGHTAGENT_TIMEZONE
        if settings.timezone:
            return self._parse_iana(settings.timezone)

        # Step 4: OS local timezone
        try:
            local_tz = tzlocal.get_localzone()
            # tzlocal may return a tzinfo that isn't ZoneInfo; normalise it.
            tz_name = getattr(local_tz, "key", None) or str(local_tz)
            return ZoneInfo(tz_name)
        except Exception as exc:  # tzlocal failure is non-fatal
            logger.debug(
                "tzlocal.get_localzone() failed — falling back to UTC",
                exc_info=exc,
            )

        # Step 5: UTC fallback
        return _UTC

    def get_scheduler_tz(self) -> ZoneInfo:
        """Return the effective timezone for APScheduler initialisation.

        Walks the resolution chain starting from ``LIGHTAGENT_CRON_TIMEZONE``.

        Returns:
            A :class:`~zoneinfo.ZoneInfo` representing the scheduler timezone.
        """
        return self.resolve_timezone(None)

    # ── Current time ──────────────────────────────────────────────────────────

    def now(self, tz: ZoneInfo | None = None) -> datetime:
        """Return the current aware datetime, optionally NTP-corrected.

        Args:
            tz: Target timezone. ``None`` uses :meth:`resolve_timezone`.

        Returns:
            An aware :class:`datetime` in *tz* (or the resolved default TZ).
        """
        target_tz = tz or self.resolve_timezone()
        base = datetime.now(UTC)
        offset = self._get_ntp_offset()
        if offset is not None:
            base = base + offset
        return base.astimezone(target_tz)

    def now_utc_naive(self) -> datetime:
        """Return the current UTC time as a *naive* datetime for SQLite storage.

        SQLite does not support timezone-aware datetimes; use this helper
        instead of ``datetime.now()`` (which is local-time naive) or
        ``datetime.utcnow()`` (deprecated in Python 3.12+).

        Returns:
            A naive :class:`datetime` representing the current UTC instant.
        """
        return self.to_utc_naive(self.now(_UTC))

    # ── Cron helpers ──────────────────────────────────────────────────────────

    def next_run(self, cron_expr: str, tz: ZoneInfo | None = None) -> datetime:
        """Compute the next fire time for a cron expression.

        Uses an aware *now* as the baseline so the result respects DST
        transitions and per-job timezone overrides.

        Args:
            cron_expr: A standard 5-field cron expression (e.g. ``"0 9 * * *"``).
            tz: Timezone for the computation. ``None`` uses the resolved default.

        Returns:
            An aware :class:`datetime` of the next scheduled fire time.

        Raises:
            ValueError: If *cron_expr* is not a valid cron expression.
        """
        effective_tz = tz or self.resolve_timezone()
        now_aware = self.now(effective_tz)
        cron = croniter(cron_expr, now_aware)
        result: datetime = cron.get_next(datetime)
        # croniter may return a naive datetime even with an aware start; ensure aware.
        if result.tzinfo is None:
            result = result.replace(tzinfo=effective_tz)
        return result

    # ── Conversion helpers ─────────────────────────────────────────────────────

    def to_utc_naive(self, aware_dt: datetime) -> datetime:
        """Convert an aware datetime to a naive UTC datetime for SQLite.

        Args:
            aware_dt: A timezone-aware :class:`datetime`.

        Returns:
            A naive :class:`datetime` in UTC (``tzinfo=None``).

        Raises:
            ValueError: If *aware_dt* has no timezone info.
        """
        if aware_dt.tzinfo is None:
            raise ValueError("to_utc_naive() requires an aware datetime; got naive.")
        return aware_dt.astimezone(UTC).replace(tzinfo=None)

    def parse_local(self, dt_str: str, tz: ZoneInfo | None = None) -> datetime:
        """Parse a local datetime string and attach timezone info.

        Supported formats: ``"YYYY-MM-DD HH:MM:SS"`` and ``"YYYY-MM-DDTHH:MM:SS"``.

        Args:
            dt_str: Datetime string without timezone suffix.
            tz: Timezone to attach. ``None`` uses :meth:`resolve_timezone`.

        Returns:
            An aware :class:`datetime` in *tz*.

        Raises:
            ValueError: If *dt_str* cannot be parsed.
        """
        effective_tz = tz or self.resolve_timezone()
        for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%dT%H:%M:%S"):
            try:
                naive = datetime.strptime(dt_str, fmt)  # noqa: DTZ007
                return naive.replace(tzinfo=effective_tz)
            except ValueError:
                continue
        raise ValueError(
            f"Cannot parse datetime string {dt_str!r}. "
            "Expected format: 'YYYY-MM-DD HH:MM:SS' or 'YYYY-MM-DDTHH:MM:SS'."
        )

    # ── NTP ───────────────────────────────────────────────────────────────────

    def ntp_offset(self) -> timedelta | None:
        """Return the cached NTP clock offset, or ``None`` if NTP is disabled.

        Refreshes the cache when ``ntp_sync_interval_seconds`` has elapsed.

        Returns:
            A :class:`timedelta` representing the signed clock offset, or
            ``None`` when NTP is disabled or the server was unreachable.
        """
        settings = get_settings()
        if not settings.ntp_enabled:
            return None
        self._maybe_resync_ntp()
        return self._ntp_offset

    # ── Private helpers ────────────────────────────────────────────────────────

    @staticmethod
    def _parse_iana(tz_str: str) -> ZoneInfo:
        """Parse an IANA timezone string, falling back to UTC on error.

        Logs a WARNING for invalid names rather than raising, so schedule
        operations remain fail-open.

        Args:
            tz_str: IANA timezone name to parse.

        Returns:
            A :class:`~zoneinfo.ZoneInfo` instance (UTC on error).
        """
        try:
            return ZoneInfo(tz_str)
        except (ZoneInfoNotFoundError, KeyError):
            logger.warning(
                "Invalid IANA timezone — falling back to UTC",
                tz_str=tz_str,
            )
            return _UTC

    def _get_ntp_offset(self) -> timedelta | None:
        """Return the current NTP offset without triggering a re-sync."""
        settings = get_settings()
        if not settings.ntp_enabled:
            return None
        return self._ntp_offset

    def _maybe_resync_ntp(self) -> None:
        """Re-sync NTP if the sync interval has elapsed."""
        settings = get_settings()
        if self._ntp_last_sync is None:
            self._sync_ntp()
            return
        elapsed = (datetime.now(UTC) - self._ntp_last_sync).total_seconds()
        if elapsed >= settings.ntp_sync_interval_seconds:
            self._sync_ntp()

    def _sync_ntp(self) -> None:
        """Perform an NTP query and cache the offset.

        Fail-open: logs a WARNING on any error and leaves ``_ntp_offset``
        as ``None`` so the system clock is used instead.
        """
        settings = get_settings()
        try:
            import ntplib  # type: ignore[import-not-found]  # optional dep, no stubs

            client = ntplib.NTPClient()
            response = client.request(settings.ntp_server, version=3)
            offset_seconds: float = response.offset
            self._ntp_offset = timedelta(seconds=offset_seconds)
            self._ntp_last_sync = datetime.now(UTC)

            threshold = settings.ntp_warn_threshold_seconds
            if abs(offset_seconds) > threshold:
                logger.warning(
                    "NTP clock offset exceeds warning threshold",
                    offset_seconds=offset_seconds,
                    threshold_seconds=threshold,
                    server=settings.ntp_server,
                )
            else:
                logger.debug(
                    "NTP sync successful",
                    offset_seconds=offset_seconds,
                    server=settings.ntp_server,
                )
        except Exception as exc:
            log = logging.getLogger(__name__)
            log.warning("NTP server unreachable: %s — %s", settings.ntp_server, exc)
            self._ntp_offset = None
            self._ntp_last_sync = datetime.now(UTC)
