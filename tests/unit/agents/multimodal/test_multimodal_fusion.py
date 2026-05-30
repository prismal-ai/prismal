"""Tests for MultimodalFusion (Fase F, SPEC-MM-AGT-005)."""

from __future__ import annotations

from unittest.mock import AsyncMock

import pytest

from prismal.agents.multimodal.modality_router import Modality
from prismal.agents.multimodal.multimodal_fusion import (
    FusionResult,
    ModalContribution,
    MultimodalFusion,
)

CONTRIBS = [
    ModalContribution(Modality.IMAGE, "A dog on a beach.", "vision_agent", 0.9),
    ModalContribution(Modality.AUDIO, "User asks the dog's name.", "audio_agent", 0.95),
]


class TestConcatStrategy:
    async def test_concat_includes_each_contribution(self) -> None:
        fusion = MultimodalFusion(strategy="concat")
        result = await fusion.combine(CONTRIBS)
        assert isinstance(result, FusionResult)
        assert result.strategy_used == "concat"
        assert "A dog on a beach." in result.answer
        assert "User asks the dog's name." in result.answer
        assert result.contributions == CONTRIBS

    async def test_concat_empty_contributions(self) -> None:
        fusion = MultimodalFusion(strategy="concat")
        result = await fusion.combine([])
        assert result.answer == ""


class TestModeratorStrategy:
    async def test_moderator_fn_receives_combined_text(self) -> None:
        moderator = AsyncMock(return_value="A dog on a beach; user wants its name.")
        fusion = MultimodalFusion(strategy="moderator", moderator_fn=moderator)
        result = await fusion.combine(CONTRIBS, context="be concise")
        assert result.strategy_used == "moderator"
        assert result.answer == "A dog on a beach; user wants its name."
        prompt = moderator.call_args.args[0]
        assert "A dog on a beach." in prompt
        assert "be concise" in prompt


class TestMoaStrategy:
    async def test_moa_delegates_to_mixture(self) -> None:
        fake_moa = AsyncMock()
        fake_moa.generate = AsyncMock(
            return_value=type("R", (), {"final_answer": "fused via moa"})()
        )
        fusion = MultimodalFusion(strategy="moa", moa=fake_moa)
        result = await fusion.combine(CONTRIBS)
        assert result.strategy_used == "moa"
        assert result.answer == "fused via moa"
        fake_moa.generate.assert_awaited_once()


class TestDefaultModerator:
    async def test_default_moderator_uses_multimodal_llm(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        from unittest.mock import MagicMock

        llm = MagicMock()
        llm.ainvoke = AsyncMock(return_value=type("M", (), {"content": "fused answer"})())
        monkeypatch.setattr(
            "prismal.providers.multimodal.get_multimodal_llm", lambda **_k: llm
        )
        fusion = MultimodalFusion(strategy="moderator")
        result = await fusion.combine(CONTRIBS)
        assert result.answer == "fused answer"

    async def test_default_moderator_wraps_error(self, monkeypatch: pytest.MonkeyPatch) -> None:
        from unittest.mock import MagicMock

        from prismal.core.exceptions import MultimodalFusionError

        llm = MagicMock()
        llm.ainvoke = AsyncMock(side_effect=RuntimeError("llm down"))
        monkeypatch.setattr(
            "prismal.providers.multimodal.get_multimodal_llm", lambda **_k: llm
        )
        fusion = MultimodalFusion(strategy="moderator")
        with pytest.raises(MultimodalFusionError):
            await fusion.combine(CONTRIBS)


class TestValidation:
    def test_unknown_strategy_rejected(self) -> None:
        with pytest.raises(ValueError, match="strategy"):
            MultimodalFusion(strategy="bogus")  # type: ignore[arg-type]
