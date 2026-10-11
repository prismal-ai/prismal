#!/usr/bin/env python3
"""Benchmark Ollama typed decision models (Nimble, Tev1) on /v1/systemone.

Standard library only. Measures cold and warm latency, accuracy against labeled
cases and a simple calibration gap (mean top probability minus accuracy).

Two request modes:
  single  one question per request (worst case: one HTTP call per decision)
  batch   every question that shares the same `state` goes in one request,
          as the supervisor would ask them (up to 64 questions per request)
  both    run single and batch and report them side by side (default)

Usage:
    python ollama_decision_bench.py cases.jsonl --models tev1 nimble --runs 3
    python ollama_decision_bench.py cases.batch.sample.jsonl --mode batch

Each line of the cases file is one labeled decision:
    {"state": "El pago no se procesó...", "type": "choice",
     "instructions": "¿Qué equipo atiende el ticket?",
     "criteria": {"pagos": "cobros y reembolsos", "soporte": "uso del producto",
                  "ninguno": "no aplica"},
     "label": "pagos"}
For "noul" cases omit criteria and use "label": true|false.
Cases with an identical "state" string are grouped together in batch mode.
"""

from __future__ import annotations

import argparse
import json
import random
import statistics
import time
import urllib.error
import urllib.request

DEFAULT_URL = "http://localhost:11434"
MAX_QUESTIONS_PER_REQUEST = 64


def post(url: str, body: dict, timeout: float) -> tuple[dict | None, float, str | None]:
    """POST JSON and return (response, elapsed_ms, error)."""
    data = json.dumps(body).encode("utf-8")
    req = urllib.request.Request(url, data=data, headers={"Content-Type": "application/json"})
    start = time.perf_counter()
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            payload = json.loads(resp.read().decode("utf-8"))
        return payload, (time.perf_counter() - start) * 1000, None
    except urllib.error.HTTPError as exc:
        return None, (time.perf_counter() - start) * 1000, f"http_{exc.code}"
    except (urllib.error.URLError, TimeoutError) as exc:
        return None, (time.perf_counter() - start) * 1000, f"error:{exc}"


def build_question(case: dict) -> dict:
    """Translate a labeled case into one /v1/systemone question."""
    question = {"type": case["type"], "instructions": case["instructions"]}
    if case["type"] in ("choice", "score"):
        question["criteria"] = case["criteria"]
    return question


def build_body(model: str, state: str, cases: list[dict], keep_alive: str) -> dict:
    """Build one request asking every case's question about the same state."""
    questions = {f"q{i}": build_question(case) for i, case in enumerate(cases)}
    return {"model": model, "state": state, "questions": questions, "keep_alive": keep_alive}


def score_case(case: dict, answer: dict, threshold: float) -> tuple[bool, float]:
    """Return (correct, top_probability) for one answer."""
    if case["type"] == "noul":
        p_true = float(answer["noul"])
        predicted = p_true >= threshold
        return predicted == bool(case["label"]), max(p_true, 1 - p_true)
    probs = answer.get("probabilities", {})
    top = max(probs.values()) if probs else 0.0
    return answer.get("choice") == case["label"], float(top)


def predicted_value(case: dict, answer: dict) -> object:
    """Compact view of what the model answered, for the misses report."""
    if case["type"] == "noul":
        return round(float(answer.get("noul", 0.0)), 3)
    probs = answer.get("probabilities", {})
    choice = answer.get("choice")
    return f"{choice} ({round(float(probs.get(choice, 0.0)), 3)})"


def noul_as_choice(case: dict) -> dict:
    """Rewrite a yes/no case as a two-option Choice with one-line criteria."""
    if case["type"] != "noul":
        return case
    converted = dict(case)
    converted["type"] = "choice"
    converted["criteria"] = {"si": "la respuesta a la pregunta es sí",
                             "no": "la respuesta a la pregunta es no"}
    converted["label"] = "si" if case["label"] else "no"
    return converted


def percentile(values: list[float], pct: float) -> float:
    """Nearest-rank percentile; 0.0 for an empty list."""
    if not values:
        return 0.0
    ordered = sorted(values)
    index = max(0, min(len(ordered) - 1, round(pct / 100 * len(ordered)) - 1))
    return ordered[index]


def make_requests(cases: list[dict], mode: str) -> list[tuple[str, list[dict]]]:
    """Group cases into (state, cases) requests for the given mode."""
    if mode == "single":
        return [(case["state"], [case]) for case in cases]
    groups: dict[str, list[dict]] = {}
    for case in cases:
        groups.setdefault(case["state"], []).append(case)
    requests: list[tuple[str, list[dict]]] = []
    for state, group in groups.items():
        for start in range(0, len(group), MAX_QUESTIONS_PER_REQUEST):
            requests.append((state, group[start:start + MAX_QUESTIONS_PER_REQUEST]))
    return requests


def run_mode(endpoint: str, model: str, cases: list[dict], mode: str, runs: int,
             timeout: float, keep_alive: str, threshold: float, seed: int | None = None,
             group_by_question: bool = False) -> dict:
    """Run every request `runs` times in one mode and aggregate the results."""
    requests = make_requests(cases, mode)
    if group_by_question and mode == "single":
        # Same question back to back: maximizes prompt-cache reuse of the shared prefix.
        requests.sort(key=lambda r: r[1][0]["instructions"])
    rng = random.Random(seed) if seed is not None else None
    request_ms: list[float] = []
    per_decision_ms: list[float] = []
    input_tokens: list[int] = []
    correct = 0
    answered = 0
    errors: dict[str, int] = {}
    top_probs: list[float] = []
    misses: dict[tuple[str, str], dict] = {}
    by_question: dict[str, dict] = {}
    # Derived decisions: OR of the concrete noul answers that feed them, per run and state.
    derived: dict[tuple[int, str, str], dict] = {}
    for run_index in range(runs):
        order = list(requests)
        if rng is not None:
            rng.shuffle(order)
        for state, group in order:
            resp, ms, err = post(endpoint, build_body(model, state, group, keep_alive), timeout)
            if err or not resp:
                errors[err or "empty"] = errors.get(err or "empty", 0) + 1
                continue
            request_ms.append(ms)
            per_decision_ms.append(ms / len(group))
            usage = resp.get("usage", {})
            if "input_tokens" in usage:
                input_tokens.append(int(usage["input_tokens"]))
            if mode == "single":
                case = group[0]
                stats = by_question.setdefault(case["instructions"], {
                    "type": case["type"], "options": len(case.get("criteria", {})),
                    "ms": [], "tokens": [], "usage": {}})
                stats["ms"].append(ms)
                for key, value in usage.items():
                    if isinstance(value, (int, float)):
                        stats["usage"].setdefault(key, []).append(float(value))
                if "input_tokens" in usage:
                    stats["tokens"].append(int(usage["input_tokens"]))
            answers = resp.get("answers", {})
            for i, case in enumerate(group):
                answer = answers.get(f"q{i}")
                if answer is None:
                    errors["missing_answer"] = errors.get("missing_answer", 0) + 1
                    continue
                ok, top = score_case(case, answer, threshold)
                if case.get("derive") and case["type"] == "noul":
                    slot = derived.setdefault((run_index, case["state"], case["derive"]),
                                              {"pred": False, "label": bool(case["derive_label"])})
                    slot["pred"] = slot["pred"] or float(answer["noul"]) >= threshold
                if not ok:
                    key = (case["state"], case["instructions"])
                    miss = misses.setdefault(key, {
                        "state": case["state"][:80], "question": case["instructions"],
                        "label": case["label"], "predicted": [], "count": 0})
                    miss["count"] += 1
                    miss["predicted"].append(predicted_value(case, answer))
                answered += 1
                correct += int(ok)
                top_probs.append(top)

    derived_report: dict[str, dict] = {}
    for (_, state, name), slot in derived.items():
        entry = derived_report.setdefault(name, {"total": 0, "correct": 0, "misses": {}})
        entry["total"] += 1
        if slot["pred"] == slot["label"]:
            entry["correct"] += 1
        else:
            miss = entry["misses"].setdefault(state, {"state": state[:80], "label": slot["label"], "count": 0})
            miss["count"] += 1
    for entry in derived_report.values():
        entry["accuracy"] = round(entry["correct"] / entry["total"], 4) if entry["total"] else 0.0
        entry["misses"] = sorted(entry["misses"].values(), key=lambda m: -m["count"])

    accuracy = correct / answered if answered else 0.0
    mean_top = statistics.fmean(top_probs) if top_probs else 0.0
    return {
        "mode": mode,
        "requests_per_run": len(requests),
        "questions_per_request": round(len(cases) / len(requests), 2) if requests else 0,
        "request_p50_ms": round(percentile(request_ms, 50), 1),
        "request_p95_ms": round(percentile(request_ms, 95), 1),
        "per_decision_p50_ms": round(percentile(per_decision_ms, 50), 1),
        "per_decision_p95_ms": round(percentile(per_decision_ms, 95), 1),
        "total_ms_per_run": round(sum(request_ms) / runs, 1) if runs else 0.0,
        "mean_input_tokens_per_request": round(statistics.fmean(input_tokens), 1) if input_tokens else None,
        "answered": answered,
        "accuracy": round(accuracy, 4),
        "mean_top_probability": round(mean_top, 4),
        "calibration_gap": round(mean_top - accuracy, 4),
        "errors": errors,
        "misses": sorted(misses.values(), key=lambda m: -m["count"]),
        "derived": derived_report,
        "per_question": [
            {"question": q, "type": st["type"], "options": st["options"],
             "min_ms": round(min(st["ms"]), 1),
             "p50_ms": round(percentile(st["ms"], 50), 1),
             "p95_ms": round(percentile(st["ms"], 95), 1),
             "max_ms": round(max(st["ms"]), 1),
             "mean_input_tokens": round(statistics.fmean(st["tokens"]), 1) if st["tokens"] else None,
             "mean_usage": {k: round(statistics.fmean(v), 1) for k, v in sorted(st["usage"].items())}}
            for q, st in sorted(by_question.items(), key=lambda kv: -percentile(kv[1]["ms"], 50))
        ],
    }


def bench_model(url: str, model: str, cases: list[dict], modes: list[str], runs: int,
                timeout: float, keep_alive: str, threshold: float, seed: int | None = None,
                group_by_question: bool = False) -> dict:
    """Measure the cold load once, then every requested mode warm."""
    endpoint = f"{url}/v1/systemone"
    # Cold call: unload first so the first request includes model load.
    post(f"{url}/api/generate", {"model": model, "keep_alive": 0}, timeout)
    first = cases[0]
    _, cold_ms, cold_err = post(endpoint, build_body(model, first["state"], [first], keep_alive), timeout)
    results = [run_mode(endpoint, model, cases, mode, runs, timeout, keep_alive, threshold, seed,
                        group_by_question) for mode in modes]
    sample, _, _ = post(endpoint, build_body(model, first["state"], [first], keep_alive), timeout)
    report = {"model": model, "cold_ms": round(cold_ms, 1), "cold_error": cold_err,
              "usage_sample": (sample or {}).get("usage"), "modes": results}
    by_mode = {r["mode"]: r for r in results}
    if "single" in by_mode and "batch" in by_mode and by_mode["batch"]["total_ms_per_run"]:
        report["batch_speedup"] = round(
            by_mode["single"]["total_ms_per_run"] / by_mode["batch"]["total_ms_per_run"], 2)
    return report


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("cases", help="JSONL file with labeled decision cases")
    parser.add_argument("--models", nargs="+", default=["tev1", "nimble"])
    parser.add_argument("--mode", choices=["single", "batch", "both"], default="both")
    parser.add_argument("--url", default=DEFAULT_URL)
    parser.add_argument("--runs", type=int, default=3)
    parser.add_argument("--timeout", type=float, default=60.0)
    parser.add_argument("--keep-alive", default="30m")
    parser.add_argument("--noul-threshold", type=float, default=0.5)
    parser.add_argument("--noul-as-choice", action="store_true",
                        help="Ask every noul case as a two-option Choice (si/no) instead")
    parser.add_argument("--shuffle", type=int, metavar="SEED",
                        help="Shuffle request order every run with this seed (tests cache/order effects)")
    parser.add_argument("--group-by-question", action="store_true",
                        help="Single mode: send all cases of the same question back to back")
    parser.add_argument("--out", help="Optional path for the JSON report")
    args = parser.parse_args()

    with open(args.cases, encoding="utf-8") as handle:
        cases = [json.loads(line) for line in handle if line.strip()]
    if not cases:
        raise SystemExit("no cases found")
    if args.noul_as_choice:
        cases = [noul_as_choice(case) for case in cases]

    modes = ["single", "batch"] if args.mode == "both" else [args.mode]
    report = [bench_model(args.url, m, cases, modes, args.runs, args.timeout,
                          args.keep_alive, args.noul_threshold, args.shuffle,
                          args.group_by_question) for m in args.models]
    text = json.dumps(report, ensure_ascii=False, indent=2)
    print(text)
    if args.out:
        with open(args.out, "w", encoding="utf-8") as handle:
            handle.write(text)


if __name__ == "__main__":
    main()
