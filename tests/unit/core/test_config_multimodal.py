"""Tests for the multimodal settings (Fase F)."""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from prismal.core.config import Settings


class TestMultimodalToggleDefaults:
    def test_layer_disabled_by_default(self) -> None:
        s = Settings()
        assert s.multimodal_enabled is False
        assert s.vision_enabled is False
        assert s.audio_enabled is False
        assert s.video_enabled is False

    def test_model_defaults(self) -> None:
        s = Settings()
        # Empty vision_model means "reuse cua_vision_model".
        assert s.vision_model == ""
        assert s.multimodal_model == "gemini/gemini-2.0-flash"
        assert s.cross_modal_embedding_model == "open_clip:ViT-B-32"

    def test_media_limit_defaults(self) -> None:
        s = Settings()
        assert s.max_image_bytes == 10_485_760
        assert s.max_audio_bytes == 52_428_800
        assert s.max_video_bytes == 209_715_200
        assert s.max_audio_duration_s == 600.0
        assert s.max_video_duration_s == 300.0
        assert s.max_frames_per_video == 60
        assert s.video_sample_fps == 1.0

    def test_tts_and_ocr_defaults(self) -> None:
        s = Settings()
        assert s.tts_max_chars == 2000
        assert s.vision_ocr_enabled is False


class TestMultimodalSettingsOverrides:
    def test_env_prefix(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("PRISMAL_MULTIMODAL_ENABLED", "true")
        monkeypatch.setenv("PRISMAL_VISION_OCR_ENABLED", "true")
        monkeypatch.setenv("PRISMAL_MAX_FRAMES_PER_VIDEO", "30")
        s = Settings()
        assert s.multimodal_enabled is True
        assert s.vision_ocr_enabled is True
        assert s.max_frames_per_video == 30


class TestMultimodalSettingsValidation:
    def test_max_frames_upper_bound_enforced(self) -> None:
        with pytest.raises(ValidationError):
            Settings(max_frames_per_video=10_000)

    def test_video_sample_fps_bounds(self) -> None:
        with pytest.raises(ValidationError):
            Settings(video_sample_fps=0.0)
        with pytest.raises(ValidationError):
            Settings(video_sample_fps=50.0)

    def test_tts_max_chars_bounds(self) -> None:
        with pytest.raises(ValidationError):
            Settings(tts_max_chars=0)
        with pytest.raises(ValidationError):
            Settings(tts_max_chars=20_000)

    def test_min_byte_limit_enforced(self) -> None:
        with pytest.raises(ValidationError):
            Settings(max_image_bytes=10)
