"""Compare two annotators on a DEC3-07 case set (agreement, Cohen's kappa, disagreements).

Annotator A is the case file (``data/es/<decision_id>.jsonl``); annotator B is a blind review
sheet (``review/revision_ciega_<short>_revisorB.csv``) filled without seeing A's labels.

Usage::

    python specs/typed-decision-models/data/review/compare_reviews.py \\
        --cases specs/typed-decision-models/data/es/rag.chunk_relevance.jsonl \\
        --review specs/typed-decision-models/data/review/revision_ciega_rag_revisorB.csv \\
        --column "relevante_revisor (si/no)"

Prints a JSON summary; ``--disagreements out.csv`` also writes the adjudication sheet.
Standard library only.
"""

from __future__ import annotations

import argparse
import csv
import json
from collections import Counter
from pathlib import Path

_YES_NO = {"si": "true", "sí": "true", "yes": "true", "no": "false"}


def _norm(value: object) -> str:
    if isinstance(value, bool):
        return "true" if value else "false"
    text = str(value).strip()
    return _YES_NO.get(text.lower(), text)


def load_cases(path: Path) -> dict[str, dict[str, object]]:
    """Case rows keyed by id, labels normalized to strings (``true`` / ``false`` for Noul)."""
    out: dict[str, dict[str, object]] = {}
    for line in path.read_text("utf-8").splitlines():
        if line.strip():
            row = json.loads(line)
            row["label"] = _norm(row["label"])
            out[str(row["id"])] = row
    return out


def load_review(path: Path, column: str) -> dict[str, dict[str, str]]:
    """Review rows keyed by id; the annotator's answer is stored under ``_label``."""
    with path.open(encoding="utf-8-sig", newline="") as handle:
        return {row["id"]: {**row, "_label": _norm(row[column])} for row in csv.DictReader(handle)}


def cohen_kappa(a: list[str], b: list[str]) -> float:
    """Cohen's kappa for two label lists of equal length."""
    n = len(a)
    observed = sum(x == y for x, y in zip(a, b, strict=True)) / n
    ca, cb = Counter(a), Counter(b)
    expected = sum(ca[k] * cb[k] for k in set(ca) | set(cb)) / (n * n)
    return (observed - expected) / (1 - expected) if expected < 1 else 1.0


def compare(cases: dict[str, dict[str, object]], review: dict[str, dict[str, str]]) -> dict:
    """Agreement statistics plus the disagreeing ids and B's doubtful-but-agreeing ids."""
    if set(cases) != set(review):
        raise ValueError("case file and review sheet have different ids")
    ids = list(cases)
    a = [str(cases[i]["label"]) for i in ids]
    b = [review[i]["_label"] for i in ids]
    disagreements = [i for i, x, y in zip(ids, a, b, strict=True) if x != y]
    commented = [i for i in ids if review[i].get("comentario", "").strip()]
    by_tag: dict[str, list[int]] = {}
    for i, x, y in zip(ids, a, b, strict=True):
        for tag in cases[i].get("tags") or ["(none)"]:  # type: ignore[union-attr]
            hits = by_tag.setdefault(str(tag), [0, 0])
            hits[0] += x == y
            hits[1] += 1
    return {
        "n": len(ids),
        "agree": len(ids) - len(disagreements),
        "kappa": round(cohen_kappa(a, b), 3),
        "disagreements": disagreements,
        "doubtful_agreements": [i for i in commented if i not in disagreements],
        "pairs": {
            f"{x}->{y}": n
            for (x, y), n in Counter(
                (str(cases[i]["label"]), review[i]["_label"]) for i in disagreements
            ).most_common()
        },
        "by_tag": {t: f"{ok}/{n}" for t, (ok, n) in sorted(by_tag.items())},
    }


_INPUT_COLUMNS = {"user": "mensaje_usuario", "query": "consulta", "chunk": "fragmento"}


def write_disagreements(path: Path, cases: dict, review: dict, ids: list[str]) -> None:
    """Adjudication sheet in the layout of ``desacuerdos_rag.csv``.

    One column per input field (``history`` is JSON-encoded), both labels, A's note, B's
    comment, and empty ``decision_propuesta`` / ``motivo_propuesta`` / ``decision_final`` /
    ``decidido_por`` columns for the third pass.
    """
    keys: list[str] = []
    for i in ids:
        keys += [k for k in cases[i]["input"] if k not in keys]
    header = [
        "id",
        *(_INPUT_COLUMNS.get(k, k) for k in keys),
        "etiqueta_A",
        "etiqueta_B",
        "tags",
        "nota_A",
        "comentario_B",
        "decision_propuesta",
        "motivo_propuesta",
        "decision_final",
        "decidido_por",
    ]
    with path.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.writer(handle, lineterminator="\n")
        writer.writerow(header)
        for i in ids:
            case = cases[i]
            inputs = [case["input"].get(k, "") for k in keys]
            writer.writerow(
                [
                    i,
                    *(
                        v if isinstance(v, str) else json.dumps(v, ensure_ascii=False)
                        for v in inputs
                    ),
                    case["label"],
                    review[i]["_label"],
                    ",".join(case.get("tags") or []),
                    case.get("notes", ""),
                    review[i].get("comentario", ""),
                    "",
                    "",
                    "",
                    "",
                ]
            )


def main() -> int:
    """CLI entry point."""
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--cases", type=Path, required=True)
    parser.add_argument("--review", type=Path, required=True)
    parser.add_argument("--column", required=True, help="Reviewer label column of the sheet")
    parser.add_argument("--disagreements", type=Path, help="Write the adjudication sheet here")
    args = parser.parse_args()
    cases = load_cases(args.cases)
    review = load_review(args.review, args.column)
    result = compare(cases, review)
    if args.disagreements:
        write_disagreements(args.disagreements, cases, review, result["disagreements"])
    print(json.dumps(result, indent=2, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
