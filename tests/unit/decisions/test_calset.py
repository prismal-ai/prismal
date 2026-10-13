"""Calibration-set tooling (Phase DEC — DEC3-07): cases → production questions → preds."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from langchain_core.messages import AIMessage, HumanMessage

# Imported at collection: litellm (pulled in by the meter) loads ``.env`` into
# os.environ on first import; the autouse conftest fixture then scrubs it per test.
import prismal.budget.meter  # noqa: F401
from prismal.agents.supervisor import effective_valid_routes
from prismal.decisions.adapters.fake import FakeDecisionAdapter
from prismal.decisions.calibration import build_calibration_record, load_labeled_set
from prismal.decisions.calset import (
    SUPPORTED_DECISIONS,
    CalCase,
    build_case_state,
    check_cases,
    collect,
    load_cases,
    resolve_question,
    write_collect,
)
from prismal.decisions.cli import _load_predictions
from prismal.decisions.integrations.rag import RAG_QUESTION, build_rag_state
from prismal.decisions.integrations.route import build_route_question, build_route_state
from prismal.decisions.types import Decision, DecisionKind

# ── question resolution ─────────────────────────────────────────────────────


def test_supported_decisions_are_the_pilot_pair() -> None:
    assert {"route.supervisor", "rag.chunk_relevance"} <= set(SUPPORTED_DECISIONS)


def test_route_question_is_the_production_question_for_the_base_profile() -> None:
    q = resolve_question("route.supervisor")
    assert q == build_route_question(effective_valid_routes(False))
    assert "END" in q.labels()
    assert len(q.options) == 16  # 15 core MEMBERS + END


def test_route_question_advanced_profile_adds_the_opt_in_routes() -> None:
    q = resolve_question("route.supervisor", routes_profile="advanced")
    assert q == build_route_question(effective_valid_routes(True))
    assert "tot_agent" in q.labels()


def test_rag_question_is_the_production_question() -> None:
    assert resolve_question("rag.chunk_relevance") is RAG_QUESTION


def test_unknown_decision_is_rejected() -> None:
    with pytest.raises(ValueError, match="not supported"):
        resolve_question("tool.proposal")


def test_unknown_routes_profile_is_rejected() -> None:
    with pytest.raises(ValueError, match="routes profile"):
        resolve_question("route.supervisor", routes_profile="everything")


# ── loading ──────────────────────────────────────────────────────────────────


def _jsonl(path: Path, rows: list[dict[str, object]]) -> Path:
    path.write_text("".join(json.dumps(r, ensure_ascii=False) + "\n" for r in rows), "utf-8")
    return path


ROUTE_ROWS: list[dict[str, object]] = [
    {
        "id": "route-0001",
        "decision_id": "route.supervisor",
        "input": {"user": "Escribe una función en Python que invierta una lista enlazada"},
        "label": "coder",
        "language": "es",
        "tags": ["clear"],
    },
    {
        "id": "route-0002",
        "decision_id": "route.supervisor",
        "input": {
            "user": "¿y eso cuánto cuesta en la versión empresarial?",
            "history": [
                {"role": "human", "content": "Busca el precio actual de LangSmith"},
                {"role": "ai", "content": "LangSmith tiene planes Developer y Plus."},
            ],
        },
        "label": "researcher",
        "language": "es",
        "tags": ["follow_up"],
    },
    {
        "id": "route-0003",
        "decision_id": "route.supervisor",
        "input": {"user": "hola, buenas noches"},
        "label": "END",
        "language": "es",
        "tags": ["small_talk"],
    },
]


def test_load_cases_reads_rows(tmp_path: Path) -> None:
    cases = load_cases(_jsonl(tmp_path / "c.jsonl", ROUTE_ROWS))
    assert [c.id for c in cases] == ["route-0001", "route-0002", "route-0003"]
    assert cases[0].label == "coder"
    assert cases[1].tags == ("follow_up",)


def test_load_cases_normalizes_boolean_labels(tmp_path: Path) -> None:
    row = {
        "id": "rag-1",
        "decision_id": "rag.chunk_relevance",
        "input": {"query": "q", "chunk": "c"},
        "label": True,
        "language": "es",
    }
    (case,) = load_cases(_jsonl(tmp_path / "c.jsonl", [row]))
    assert case.label == "true"


def test_load_cases_reports_the_bad_line(tmp_path: Path) -> None:
    path = tmp_path / "c.jsonl"
    path.write_text(json.dumps(ROUTE_ROWS[0]) + "\n{not json}\n", "utf-8")
    with pytest.raises(ValueError, match="line 2"):
        load_cases(path)


# ── state parity with production ────────────────────────────────────────────


def test_route_state_is_built_by_the_production_builder(tmp_path: Path) -> None:
    case = load_cases(_jsonl(tmp_path / "c.jsonl", ROUTE_ROWS))[1]
    expected = build_route_state(
        [
            HumanMessage(content="Busca el precio actual de LangSmith"),
            AIMessage(content="LangSmith tiene planes Developer y Plus."),
            HumanMessage(content="¿y eso cuánto cuesta en la versión empresarial?"),
        ]
    )
    state = build_case_state(case)
    assert state.sha256 == expected.sha256
    assert dict(state.fields) == dict(expected.fields)


def test_rag_state_is_built_by_the_production_builder() -> None:
    case = CalCase(
        id="rag-1",
        decision_id="rag.chunk_relevance",
        input={"query": "¿Cuál es la dosis de ibuprofeno?", "chunk": "El ibuprofeno se toma…"},
        label="true",
        language="es",
    )
    expected = build_rag_state("¿Cuál es la dosis de ibuprofeno?", "El ibuprofeno se toma…")
    assert build_case_state(case).sha256 == expected.sha256


# ── checking ─────────────────────────────────────────────────────────────────


def test_check_cases_counts_labels_languages_and_tags(tmp_path: Path) -> None:
    cases = load_cases(_jsonl(tmp_path / "c.jsonl", ROUTE_ROWS))
    report = check_cases(cases, resolve_question("route.supervisor"))
    assert report.errors == []
    assert report.n == 3
    assert report.by_label["coder"] == 1
    assert report.by_label["END"] == 1
    assert report.by_language == {"es": 3}
    assert report.by_tag["follow_up"] == 1


def test_check_cases_flags_bad_labels_duplicates_and_inputs(tmp_path: Path) -> None:
    rows = [
        {**ROUTE_ROWS[0], "label": "chef"},  # not an option
        ROUTE_ROWS[2],
        {**ROUTE_ROWS[2]},  # duplicate id
        {**ROUTE_ROWS[0], "id": "route-0009", "input": {}},  # no user text
        {**ROUTE_ROWS[0], "id": "route-0010", "language": "fr"},
        {**ROUTE_ROWS[0], "id": "route-0011", "decision_id": "rag.chunk_relevance"},
    ]
    cases = load_cases(_jsonl(tmp_path / "c.jsonl", rows))
    errors = "\n".join(check_cases(cases, resolve_question("route.supervisor")).errors)
    assert "route-0001: label 'chef'" in errors
    assert "duplicate id 'route-0003'" in errors
    assert "route-0009: input" in errors
    assert "route-0010: language 'fr'" in errors
    assert "route-0011: decision_id" in errors


def test_check_cases_warns_when_detected_language_differs(tmp_path: Path) -> None:
    row = {**ROUTE_ROWS[0], "input": {"user": "Write a function that reverses a list"}}
    cases = load_cases(_jsonl(tmp_path / "c.jsonl", [row]))
    report = check_cases(cases, resolve_question("route.supervisor"))
    assert report.errors == []
    assert any("detected 'en'" in w for w in report.warnings)


def test_check_cases_warns_about_options_without_cases(tmp_path: Path) -> None:
    cases = load_cases(_jsonl(tmp_path / "c.jsonl", ROUTE_ROWS))
    report = check_cases(cases, resolve_question("route.supervisor"))
    assert any("no cases for label 'planner'" in w for w in report.warnings)


# ── collecting predictions ──────────────────────────────────────────────────


def _probs_for(label: str, labels: tuple[str, ...], p: float = 0.8) -> dict[str, float]:
    rest = (1.0 - p) / (len(labels) - 1)
    return {lab: (p if lab == label else rest) for lab in labels}


def _fake_route_backend(answers: dict[str, str | None]) -> FakeDecisionAdapter:
    """Answers by user text; ``None`` ⇒ abstain."""
    q = resolve_question("route.supervisor")

    def script(state: dict[str, str]) -> Decision:
        user = state["user"]
        hit = next((v for k, v in answers.items() if k in user), "END")
        if hit is None:
            return Decision.abstain(q, backend="fake", model="m", reason="timeout")
        return Decision.from_probabilities(
            q, _probs_for(hit, q.labels()), backend="fake", model="m", latency_ms=12.0
        )

    return FakeDecisionAdapter({q.id: script}, name="ollama:fast", model="tev1:4b")


async def test_collect_asks_one_question_per_request_in_order(tmp_path: Path) -> None:
    cases = load_cases(_jsonl(tmp_path / "c.jsonl", ROUTE_ROWS))
    backend = _fake_route_backend({"Python": "coder", "cuánto cuesta": "researcher"})
    result = await collect(cases, resolve_question("route.supervisor"), backend)
    assert [len(qs) for _, qs in backend.calls] == [1, 1, 1]
    assert [r["case_id"] for r in result.predictions] == ["route-0001", "route-0002", "route-0003"]
    assert result.accuracy == pytest.approx(1.0)
    assert result.abstained == []


async def test_collect_drops_abstentions_from_both_files(tmp_path: Path) -> None:
    cases = load_cases(_jsonl(tmp_path / "c.jsonl", ROUTE_ROWS))
    backend = _fake_route_backend({"Python": None, "cuánto cuesta": "planner"})
    result = await collect(cases, resolve_question("route.supervisor"), backend)
    assert result.abstained == [("route-0001", "timeout")]
    assert [r["case_id"] for r in result.labeled] == ["route-0002", "route-0003"]
    assert len(result.labeled) == len(result.predictions) == 2
    assert result.accuracy == pytest.approx(0.5)  # planner wrong, END right
    assert result.confusion[("researcher", "planner")] == 1


async def test_written_files_feed_the_calibrate_pipeline(tmp_path: Path) -> None:
    rows = []
    for i in range(40):
        label = "coder" if i % 2 else "END"
        user = f"Corrige este error de Python número {i}" if label == "coder" else f"hola {i}"
        rows.append(
            {
                "id": f"r-{i:03d}",
                "decision_id": "route.supervisor",
                "input": {"user": user},
                "label": label,
                "language": "es",
            }
        )
    cases = load_cases(_jsonl(tmp_path / "c.jsonl", rows))
    q = resolve_question("route.supervisor")
    result = await collect(cases, q, _fake_route_backend({"Python": "coder"}))
    paths = write_collect(result, tmp_path / "out", model_key="tev1:4b@9b5bb969e46c")
    assert paths.labeled.name == "route.supervisor@tev1:4b@9b5bb969e46c.labeled.jsonl"
    assert paths.predictions.name == "route.supervisor@tev1:4b@9b5bb969e46c.preds.jsonl"

    examples = load_labeled_set(paths.labeled)
    preds = _load_predictions(paths.predictions, "tev1:4b@9b5bb969e46c")
    assert len(examples) == len(preds) == 40
    assert {e.language for e in examples} == {"es"}
    assert preds[0].kind is DecisionKind.CHOICE
    record = build_calibration_record(
        preds,
        examples,
        decision_id=q.id,
        model="tev1:4b@9b5bb969e46c",
        method="temperature",
        target_precision=0.9,
        floor=0.5,
        version="t",
    )
    assert record.n_by_language == {"es": 40}

    summary = json.loads(paths.summary.read_text("utf-8"))
    assert summary["n_cases"] == 40
    assert summary["n_predictions"] == 40
    assert summary["accuracy"] == pytest.approx(1.0)
    assert summary["question_sha256"]
    assert summary["model"] == "tev1:4b@9b5bb969e46c"


async def test_collect_handles_noul_questions(tmp_path: Path) -> None:
    rows = [
        {
            "id": f"rag-{i}",
            "decision_id": "rag.chunk_relevance",
            "input": {"query": "dosis de ibuprofeno", "chunk": chunk},
            "label": label,
            "language": "es",
        }
        for i, (chunk, label) in enumerate(
            [("El ibuprofeno se dosifica cada 8 horas", True), ("El cielo es azul", False)]
        )
    ]
    cases = load_cases(_jsonl(tmp_path / "c.jsonl", rows))

    def script(state: dict[str, str]) -> Decision:
        p_true = 0.9 if "ibuprofeno se" in state["chunk"] else 0.2
        return Decision.from_probabilities(
            RAG_QUESTION, {"true": p_true, "false": 1 - p_true}, backend="f", model="m"
        )

    backend = FakeDecisionAdapter({RAG_QUESTION.id: script})
    result = await collect(cases, RAG_QUESTION, backend)
    assert result.accuracy == pytest.approx(1.0)
    assert result.predictions[0]["kind"] == "noul"
    assert set(result.predictions[0]["probabilities"]) == {"true", "false"}


async def test_collect_uses_a_generous_timeout_not_the_800ms_gate_default(tmp_path: Path) -> None:
    """A slow-but-valid answer is a data point, not an abstention."""
    cases = load_cases(_jsonl(tmp_path / "c.jsonl", ROUTE_ROWS[:1]))
    q = resolve_question("route.supervisor")
    slow = FakeDecisionAdapter(
        {
            q.id: lambda _s: Decision.from_probabilities(
                q, _probs_for("coder", q.labels()), backend="fake", model="m"
            )
        },
        delay_s=0.9,
    )
    result = await collect(cases, q, slow)
    assert result.abstained == []
    assert len(result.predictions) == 1


async def test_collect_timeout_is_configurable(tmp_path: Path) -> None:
    cases = load_cases(_jsonl(tmp_path / "c.jsonl", ROUTE_ROWS[:1]))
    q = resolve_question("route.supervisor")
    slow = FakeDecisionAdapter({q.id: lambda _s: None}, delay_s=0.2)  # type: ignore[arg-type,return-value]
    result = await collect(cases, q, slow, timeout_ms=50)
    assert result.abstained == [("route-0001", "timeout")]
