"""Unit tests for approval gate functions."""
from lightagent.agents.subgraphs.gates import failure_gate, score_gate
from lightagent.agents.state import create_initial_state


def _state_with_meta(meta: dict) -> dict:
    """Create a state dict with custom metadata."""
    s = create_initial_state("sess-test")
    s["metadata"] = meta
    return s


def test_score_gate_passes_above_threshold() -> None:
    """Gate returns on_pass when score >= threshold."""
    gate = score_gate(
        field="dev_pipeline.review_result.score",
        threshold=0.8,
        on_pass="__end__",
        on_fail="developer",
    )
    state = _state_with_meta({"dev_pipeline": {"review_result": {"score": 0.9}}})
    assert gate(state) == "__end__"


def test_score_gate_fails_below_threshold() -> None:
    """Gate returns on_fail when score < threshold."""
    gate = score_gate(
        field="dev_pipeline.review_result.score",
        threshold=0.8,
        on_pass="__end__",
        on_fail="developer",
    )
    state = _state_with_meta({"dev_pipeline": {"review_result": {"score": 0.5}}})
    assert gate(state) == "developer"


def test_score_gate_max_iterations_forces_pass() -> None:
    """Gate forces on_pass when iteration_count >= max_iterations."""
    gate = score_gate(
        field="dev_pipeline.review_result.score",
        threshold=0.8,
        on_pass="__end__",
        on_fail="developer",
        max_iterations=3,
    )
    state = _state_with_meta({"dev_pipeline": {"review_result": {"score": 0.0}}})
    state["iteration_count"] = 3
    assert gate(state) == "__end__"


def test_score_gate_missing_field_goes_to_fail() -> None:
    """Gate returns on_fail when metadata field is missing."""
    gate = score_gate(
        field="dev_pipeline.review_result.score",
        threshold=0.8,
        on_pass="__end__",
        on_fail="developer",
    )
    state = _state_with_meta({})
    assert gate(state) == "developer"


def test_test_failure_gate_routes_to_developer_on_failures() -> None:
    """Gate returns on_fail when failing_tests list is non-empty."""
    gate = failure_gate(
        field="dev_pipeline.test_report.failing_tests",
        on_pass="qa_agent",
        on_fail="developer",
    )
    state = _state_with_meta({"dev_pipeline": {"test_report": {"failing_tests": ["test_login"]}}})
    assert gate(state) == "developer"


def test_test_failure_gate_passes_when_no_failures() -> None:
    """Gate returns on_pass when failing_tests list is empty."""
    gate = failure_gate(
        field="dev_pipeline.test_report.failing_tests",
        on_pass="qa_agent",
        on_fail="developer",
    )
    state = _state_with_meta({"dev_pipeline": {"test_report": {"failing_tests": []}}})
    assert gate(state) == "qa_agent"
