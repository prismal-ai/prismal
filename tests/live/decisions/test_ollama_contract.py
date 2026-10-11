"""Live contract against a local Ollama ``/v1/systemone`` (DEC Ollama delta — DEC0-10).

Opt-in only: requires ``PRISMAL_OLLAMA_CONTRACT=1`` **and** a reachable Ollama on
``localhost:11434`` serving ``tev1:4b`` and ``nimble:9b-q4_K_M``; otherwise skipped,
so the default suite never calls Ollama.

    PRISMAL_OLLAMA_CONTRACT=1 uv run pytest tests/live/decisions/test_ollama_contract.py -m ollama
"""

from __future__ import annotations

import os
import socket

import pytest

from prismal.decisions.adapters.ollama import OllamaDecisionAdapter, clear_preflight_cache
from prismal.decisions.types import DecisionKind, GatePolicy, OptionSpec, QuestionSpec

BASE = "http://localhost:11434"


def _reachable(host: str = "127.0.0.1", port: int = 11434) -> bool:
    try:
        with socket.create_connection((host, port), timeout=0.5):
            return True
    except OSError:
        return False


pytestmark = [
    pytest.mark.ollama,
    pytest.mark.skipif(
        os.environ.get("PRISMAL_OLLAMA_CONTRACT") != "1" or not _reachable(),
        reason="set PRISMAL_OLLAMA_CONTRACT=1 with a local Ollama on :11434",
    ),
]

CHOICE = QuestionSpec(
    id="route.supervisor",
    kind=DecisionKind.CHOICE,
    instructions="¿Qué agente debe atender la petición del usuario?",
    options=(
        OptionSpec("programador", "Escribe o corrige código fuente."),
        OptionSpec("investigador", "Busca información en la web."),
        OptionSpec("calendario", "Gestiona citas y recordatorios."),
    ),
)
NOUL = QuestionSpec(
    id="hitl.destructive",
    kind=DecisionKind.NOUL,
    instructions="¿La petición solicita una acción destructiva o irreversible?",
)
STATE = {"usuario": "Escribe una función en Python que ordene una lista de números."}
STATE_DESTRUCTIVE = {"usuario": "Borra todos los registros de la base de datos de producción."}
# Cold loads take seconds; the contract checks the wire, not the latency budget.
POLICY = GatePolicy(timeout_ms=60_000)


@pytest.fixture(autouse=True)
def _fresh() -> None:
    clear_preflight_cache()


@pytest.mark.parametrize(
    ("tier", "model", "digest12"),
    [("fast", "tev1:4b", "9b5bb969e46c"), ("accurate", "nimble:9b-q4_K_M", "572f1f4c801d")],
)
async def test_spanish_choice_and_noul(tier: str, model: str, digest12: str) -> None:
    adapter = OllamaDecisionAdapter(base_url=BASE, model_name=model, tier=tier)  # type: ignore[arg-type]
    try:
        choice = (await adapter.decide(STATE, {CHOICE.id: CHOICE}, policy=POLICY))[CHOICE.id]
        noul = (await adapter.decide(STATE_DESTRUCTIVE, {NOUL.id: NOUL}, policy=POLICY))[NOUL.id]
    finally:
        await adapter.aclose()
    assert adapter.model.startswith(f"{model}@")
    if adapter.model != f"{model}@{digest12}":
        pytest.fail(f"digest drifted: {adapter.model} (re-pulled model ⇒ recalibrate)")
    for d in (choice, noul):
        assert not d.abstained, d.reason
        assert d.model == adapter.model
        assert d.backend == f"ollama:{tier}"
    assert choice.value == "programador"
    assert set(choice.probabilities) == {"programador", "investigador", "calendario"}
    assert noul.value is True
