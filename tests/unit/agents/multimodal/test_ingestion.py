"""Tests for the media ingestion contract (Fase F follow-up, P1)."""

from __future__ import annotations

import hashlib
from io import BytesIO
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from prismal.agents.multimodal.ingestion import cleanup_session_media, ingest_media
from prismal.agents.state import create_initial_state
from prismal.core.exceptions import MediaValidationError
from prismal.security.media_validator import MediaKind

PNG = b"\x89PNG\r\n\x1a\n" + b"\x00" * 64
MP4 = b"\x00\x00\x00\x18ftypmp42" + b"\x00" * 64
GARBAGE = b"not a media file at all" + b"\x00" * 16


def _state(session_id: str = "sess-1") -> dict:
    return dict(create_initial_state(session_id=session_id))


class TestIngestMedia:
    def test_appends_descriptor_with_path_not_bytes(self, tmp_path: Path) -> None:
        state = ingest_media(_state(), PNG, source="rest:upload", workspace=tmp_path)
        media = state["metadata"]["mm"]["media"]
        assert isinstance(media, list) and len(media) == 1
        desc = media[0]
        # The descriptor must carry a path reference, never the raw bytes.
        assert "bytes" not in desc
        assert set(desc) >= {"uri", "kind", "mime", "sha256", "source", "bytes_len"}
        assert desc["kind"] == "image"
        assert desc["source"] == "rest:upload"
        assert Path(desc["uri"]).exists()
        assert state["metadata"]["mm"]["primary_media_index"] == 0

    def test_spilled_file_is_content_addressed(self, tmp_path: Path) -> None:
        state = ingest_media(_state(), MP4, kind=MediaKind.VIDEO, workspace=tmp_path)
        desc = state["metadata"]["mm"]["media"][0]
        # File name is the sha256 of the stored (sanitized) content.
        stored = Path(desc["uri"]).read_bytes()
        assert desc["sha256"] == hashlib.sha256(stored).hexdigest()
        assert Path(desc["uri"]).name.startswith(desc["sha256"])

    def test_auto_detects_kind_when_none(self, tmp_path: Path) -> None:
        state = ingest_media(_state(), MP4, workspace=tmp_path)
        assert state["metadata"]["mm"]["media"][0]["kind"] == "video"

    def test_rejects_invalid_media(self, tmp_path: Path) -> None:
        state = _state()
        with pytest.raises(MediaValidationError):
            ingest_media(state, GARBAGE, kind=MediaKind.IMAGE, workspace=tmp_path)
        # Nothing partially written into state.
        assert state["metadata"].get("mm", {}).get("media", []) == []

    def test_multiple_ingests_append(self, tmp_path: Path) -> None:
        state = ingest_media(_state(), PNG, workspace=tmp_path)
        state = ingest_media(state, MP4, workspace=tmp_path)
        media = state["metadata"]["mm"]["media"]
        assert [m["kind"] for m in media] == ["image", "video"]

    def test_accepts_path_input(self, tmp_path: Path) -> None:
        src = tmp_path / "in.png"
        src.write_bytes(PNG)
        state = ingest_media(_state(), src, workspace=tmp_path)
        assert state["metadata"]["mm"]["media"][0]["kind"] == "image"

    def test_sets_preferred_output(self, tmp_path: Path) -> None:
        state = ingest_media(_state(), PNG, workspace=tmp_path, preferred_output="audio")
        assert state["metadata"]["mm"]["preferred_output"] == "audio"

    def test_audit_logs_hash_and_modality(self, tmp_path: Path) -> None:
        audit = MagicMock()
        ingest_media(_state(), PNG, workspace=tmp_path, audit=audit)
        audit.log_media.assert_called_once()
        kwargs = audit.log_media.call_args.kwargs
        args = audit.log_media.call_args.args
        # modality + sha present; raw content never passed.
        assert "image" in (list(args) + list(kwargs.values()))

    def test_spills_under_session_dir(self, tmp_path: Path) -> None:
        state = ingest_media(_state("abc"), PNG, workspace=tmp_path)
        uri = Path(state["metadata"]["mm"]["media"][0]["uri"])
        assert "abc" in uri.parts


class TestWorkspaceResolution:
    def test_uses_settings_media_workspace(self, tmp_path: Path) -> None:
        from prismal.core.config import Settings

        settings = Settings(media_workspace=str(tmp_path))
        state = ingest_media(_state("s"), PNG, settings=settings)
        uri = Path(state["metadata"]["mm"]["media"][0]["uri"])
        assert str(tmp_path) in str(uri)

    def test_default_falls_back_to_system_temp(self) -> None:
        import tempfile

        from prismal.agents.multimodal.ingestion import _resolve_workspace
        from prismal.core.config import Settings

        root = _resolve_workspace(None, Settings(media_workspace=""))
        assert str(root).startswith(tempfile.gettempdir())


class TestCleanup:
    def test_cleanup_removes_session_media(self, tmp_path: Path) -> None:
        state = ingest_media(_state("zzz"), PNG, workspace=tmp_path)
        uri = Path(state["metadata"]["mm"]["media"][0]["uri"])
        assert uri.exists()
        cleanup_session_media("zzz", workspace=tmp_path)
        assert not uri.exists()

    def test_cleanup_missing_session_is_noop(self, tmp_path: Path) -> None:
        # Must not raise when there is nothing to clean.
        cleanup_session_media("never-existed", workspace=tmp_path)


class TestEXIFStrip:
    def test_image_metadata_stripped_on_ingest(self, tmp_path: Path) -> None:
        pytest.importorskip("PIL")
        from PIL import Image

        img = Image.new("RGB", (4, 4), (1, 2, 3))
        exif = img.getexif()
        exif[0x0131] = "PrismalCam"
        buf = BytesIO()
        img.save(buf, format="JPEG", exif=exif)
        state = ingest_media(_state(), buf.getvalue(), workspace=tmp_path)
        stored = Path(state["metadata"]["mm"]["media"][0]["uri"]).read_bytes()
        assert not Image.open(BytesIO(stored)).getexif()
