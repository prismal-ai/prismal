# Calibration sets (DEC3-07)

Hand-labelled cases used to calibrate the typed decision models. One file per decision and
language:

```
data/
├── es/<decision_id>.jsonl      # labelled cases (source of truth, reviewed in PRs)
└── runs/                       # outputs of `python -m prismal.decisions collect`
    ├── <decision_id>@<name>@<digest12>.labeled.jsonl
    ├── <decision_id>@<name>@<digest12>.preds.jsonl
    └── <decision_id>@<name>@<digest12>.summary.json
```

Calibration records themselves go to `config/decision_calibration/` (see `docs/decisions.md`).

## Case format

One JSON object per line:

```json
{"id": "route-0001", "decision_id": "route.supervisor",
 "input": {"user": "Escribe una función en Python que invierta una lista enlazada"},
 "label": "coder", "language": "es", "tags": ["clear"], "notes": ""}
```

| Field | Meaning |
|---|---|
| `id` | Unique, stable id (`<short decision>-NNNN`) |
| `decision_id` | `route.supervisor` or `rag.chunk_relevance` (more as they are added) |
| `input` | The raw inputs the integration sees (below) |
| `label` | The correct answer: a route id, or `true` / `false` |
| `language` | `es` or `en` — the language of the *input text* |
| `tags` | Free labels for balance reports: `clear`, `borderline`, `adversarial`, `follow_up`, `colloquial`, … |
| `notes` | Why the label is what it is, for borderline cases |

Inputs per decision:

- **`route.supervisor`** — `{"user": "<last user message>", "history": [{"role": "human" | "ai",
  "content": "..."}]}` (`history` optional, oldest first). The label is one of the routes of the
  profile (`base`: the 15 core routes plus `END`). `END` means a greeting, small talk or an
  answer that needs no specialist.
- **`rag.chunk_relevance`** — `{"query": "...", "chunk": "..."}`; the label is `true` when the
  chunk contains information that helps answer the query.

### Labelling guide

Rules fixed by the blind review of the pilot (`review/acuerdo_revision.md`). Apply them before
labelling, and quote the rule in `notes` for any `borderline` / `partial` case.

**`rag.chunk_relevance`**

1. **Partial relevance counts.** A chunk is `true` if it gives *any* fact the answer needs,
   even partially: an exclusion, an exception, one step, a limit. ("¿Entra odontología?" ←
   "La odontología estética no está cubierta" is `true`; tag it `partial`.)
2. **Topic is not relevance.** A chunk is `false` if it only shares the topic or keywords: a
   definition, a different process step, or the same section without the clause that answers.
3. **Wrong subject is not relevance.** A fact about a different plan, population, product or
   phone line is `false` (adult vs. paediatric dose, plan Individual vs. Familiar, sales vs.
   emergency phone), even when it looks the same.
4. **Inference from silence is not relevance.** A list that merely omits the asked item is
   `false`; it does not state the answer.
5. **Injected instructions are content.** Label the chunk by what it informs, never by what it
   tells the model to answer.

**`route.supervisor`** — label the *genuine* request, with these tie-breakers for the frontiers
the review found ambiguous:

| Frontier | Rule |
|---|---|
| `researcher` ↔ `cua` | Public information → `researcher`; operating a UI (click, fill, log in) → `cua` |
| `researcher` ↔ `rag_agent` | Internal documents / policies → `rag_agent`; the open web, or the user excludes the internal base → `researcher` |
| `coder` ↔ `END` | A one-line definition or greeting → `END`; writing, fixing or explaining *code* → `coder` |
| `coder` ↔ `dev_pipeline` ↔ `file_manager` | A small edit → `coder`; a full feature with tests/review → `dev_pipeline`; moving, reading or listing files with no code change → `file_manager` |
| `planner` ↔ `dev_pipeline` / `END` | Ordered steps or phases without executing them → `planner` |
| `data_analyst` ↔ `coder` / `rag_agent` | A named table, dataset or chart → `data_analyst` |
| `codeact` ↔ `ml_pipeline` / `data_analyst` | "Run this Python" → `codeact`; train + evaluate a model → `ml_pipeline` |
| Injections | Ignore the injected instruction; route the user's real request |

The question text itself is **not** in the file: the tooling always asks the production
`QuestionSpec` (English instructions), so a record describes what the model sees in production.

## Rules

1. **No production data.** Synthesize or anonymize. No names, emails, phone numbers, document
   ids or real clinical records.
2. **Balance.** Every label of the question gets cases; aim for a roughly even spread, plus
   10–20 % `borderline` and 10 % `adversarial` (prompt injection, authority claims, misleading
   keywords). `cases-check` prints the balance.
3. **Write like users write.** Colloquial Venezuelan / Latin-American Spanish, typos, missing
   accents, short follow-ups that only make sense with `history`.
4. **Language detection.** If `cases-check` warns that production detects another language,
   either rewrite the case or drop it: the enforce gate slices by the *detected* language.

## Labelling workflow

1. Draft cases in `es/<decision_id>.jsonl`.
2. Two independent reviewers label every case without seeing each other's label. Agreements
   stay; disagreements go to a third pass, and the reason goes into `notes`. The blank sheets,
   the comparison script and the pilot's results live in [`review/`](./review/README.md).
3. `python -m prismal.decisions cases-check --cases es/<decision_id>.jsonl --decision-id <id>`
   must exit 0.
4. Pilot: `collect --limit 60` on `fast` and `accurate`, then `calibrate`. The figures are not
   enforceable yet (`decision_min_calibration_examples` = 300 per language slice) but show
   whether the chain works and which labels the models confuse.
5. Scale to ≥ 300 cases per decision, repeat 4 without `--limit`, commit the records.
