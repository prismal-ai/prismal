"""Build decision backends from settings (Phase DEC — SPEC-DEC-CAS-001 ``from_settings``).

Keys resolve through an injected ``CredentialVaultPort`` (``<ref>@<org_id>`` per
tenant, like GW) or the injected settings; they are never mirrored to
``os.environ``. A backend whose key is missing is skipped (the cascade escalates).
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from prismal.core.logging import get_logger

if TYPE_CHECKING:
    from pydantic import SecretStr

    from prismal.agents.extension.ports import CredentialVaultPort
    from prismal.budget.meter import CostMeter
    from prismal.core.config import Settings
    from prismal.decisions.ports import DecisionPort

logger = get_logger(__name__)

_ENGLISH_ONLY = frozenset({"laya", "laya-typed-decisions"})


def _secret(
    settings: Settings,
    field: str,
    *,
    vault: CredentialVaultPort | None,
    org_id: str | None,
) -> SecretStr | None:
    if vault is not None:
        ref = f"{field}@{org_id}" if org_id else field
        try:
            return vault.resolve(ref).value
        except Exception:
            if org_id:
                return None  # no silent fallback to the operator's global key
    value: SecretStr | None = getattr(settings, field, None)
    if value is None or not value.get_secret_value():
        return None
    return value


def build_backend(
    name: str,
    settings: Settings,
    *,
    vault: CredentialVaultPort | None = None,
    org_id: str | None = None,
    cost_meter: CostMeter | None = None,
) -> DecisionPort | None:
    """One backend for tier ``name``, or ``None`` (rule tier, unknown, or no credential)."""
    if name == "jev":
        key = _secret(settings, "jev_api_key", vault=vault, org_id=org_id)
        if key is None:
            logger.info("decision_backend_skipped", backend="jev", reason="no_credential")
            return None
        if settings.jev_transport == "sdk":
            from prismal.decisions.adapters.jev_sdk import JevSdkDecisionAdapter

            return JevSdkDecisionAdapter(
                api_key=key,
                model=settings.jev_model,
                base_url=settings.jev_base_url,
                max_choice_options=settings.jev_max_choice_options,
                settings=settings,
            )
        from prismal.decisions.adapters.systemone_http import SystemOneHttpDecisionAdapter

        return SystemOneHttpDecisionAdapter(
            base_url=settings.jev_base_url,
            api_key=key,
            model=settings.jev_model,
            max_choice_options=settings.jev_max_choice_options,
            max_request_tokens=settings.jev_max_request_tokens,
            settings=settings,
            cost_meter=cost_meter,
        )
    if name == "laya":
        if settings.laya_mode == "http":
            from prismal.decisions.adapters.systemone_http import SystemOneHttpDecisionAdapter

            languages = {"en"} if settings.laya_checkpoint in _ENGLISH_ONLY else None
            return SystemOneHttpDecisionAdapter(
                base_url=settings.laya_base_url or "",
                api_key=_secret(settings, "laya_api_key", vault=vault, org_id=org_id),
                model=settings.laya_checkpoint,
                name="laya",
                max_choice_options=settings.laya_max_choice_options,
                languages=languages,
                settings=settings,
            )
        from prismal.decisions.adapters.laya_local import LayaLocalDecisionAdapter

        return LayaLocalDecisionAdapter(
            checkpoint=settings.laya_checkpoint,
            preload=settings.laya_preload,
            max_loaded=settings.laya_max_loaded,
            max_len=settings.laya_max_len,
            max_choice_options=settings.laya_max_choice_options,
            cuda_amp=settings.laya_cuda_amp,
            settings=settings,
        )
    if name == "llm":
        from prismal.decisions.adapters.llm_fallback import LlmFallbackDecisionAdapter

        return LlmFallbackDecisionAdapter(
            model=settings.decision_llm_fallback_model, settings=settings, cost_meter=cost_meter
        )
    return None
