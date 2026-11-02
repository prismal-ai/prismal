# Blind review of the DEC3-07 pilot sets

Second-annotator pass over `../es/route.supervisor.jsonl` (71 cases) and
`../es/rag.chunk_relevance.jsonl` (60 cases), done blind: the reviewer saw only the review
sheets, never the case files or their labels. Full analysis (Spanish):
[`acuerdo_revision.md`](./acuerdo_revision.md).

| File | Content |
|---|---|
| `revision_ciega_route.csv`, `revision_ciega_rag.csv` | Blank blind-review sheets (inputs only, no labels). Reuse them for a human pass. |
| `revision_ciega_route_revisorB.csv`, `revision_ciega_rag_revisorB.csv` | Annotator B's labels and doubt comments |
| `acuerdo_revision.md` | Agreement, Cohen's kappa, confusions, per-tag agreement, disagreements, B's doubtful agreements, findings and next steps |
| `desacuerdos_rag.csv` | Adjudication sheet for the 3 `rag` disagreements, with a proposed decision; `route` had none |
| `revision_ciega_{route,rag}_lote2.csv`, `…_lote2_revisorB.csv` | Scale-up batch (route-0072…0304, rag-0061…0300): blank sheets and annotator B's labels. The route sheet was reviewed in a shuffled order (the case file interleaves labels round-robin, so file order leaks the label) |
| `desacuerdos_rag_lote2.csv` | Adjudication of the 2 `rag` disagreements of the scale-up batch |
| `compare_reviews.py` | Standard-library script that recomputes the comparison for any case file + review sheet |

## Results

| Decision | Agreement | Cohen's κ | Disagreements |
|---|---|---|---|
| `route.supervisor` | 71/71 (100 %) | 1.000 | 0 |
| `rag.chunk_relevance` | 57/60 (95 %) | 0.900 | 3 (`rag-0035`, `rag-0043`, `rag-0056`), all partial relevance |

| `route.supervisor`, scale-up batch | 233/233 (100 %) | 1.000 | 0 (17 doubtful agreements) |
| `rag.chunk_relevance`, scale-up batch | 238/240 (99.2 %) | 0.983 | 2 (`rag-0179`, `rag-0260`), closed in the third pass |

Caveat: annotators A and B are two separate instances of the same model, so their errors are
correlated. A human blind pass on the doubtful cases plus a random sample is still required
before scaling to ≥ 300 cases (see `acuerdo_revision.md`).

After the third pass (2026-11-02) the 3 `rag` disagreements are closed: `rag-0043` and
`rag-0056` became `true` under the partial-relevance rule, and `rag-0035` keeps A's `true`.
The rule and the `route` tie-breakers are in [`../README.md#labelling-guide`](../README.md#labelling-guide).
`desacuerdos_rag.csv` records the decision in its `decision_final` / `decidido_por` columns.

## Status and next actions (for whoever picks this up)

1. ~~Decide `desacuerdos_rag.csv`, update the case file and the labelling guide, run
   `cases-check`~~ — done.
2. **Human blind pass** (pending): label with the blank sheets the 28 cases B doubted, the 3
   adjudicated cases and a random sample of about 20 per decision; recompute with the
   script below (`--review <your sheet>`). Below 90 % human–A agreement, revise the guide
   before scaling.
3. Live pilot (`collect --limit 60` on `fast` and `accurate`), which must run on the host with
   Ollama. Its outputs go to `../runs/`.

Recompute the comparison:

```bash
python specs/typed-decision-models/data/review/compare_reviews.py \
  --cases specs/typed-decision-models/data/es/rag.chunk_relevance.jsonl \
  --review specs/typed-decision-models/data/review/revision_ciega_rag_revisorB.csv \
  --column "relevante_revisor (si/no)" --disagreements /tmp/desacuerdos_rag.csv  # same layout as desacuerdos_rag.csv
```

For `route.supervisor`, use `--cases …/route.supervisor.jsonl`,
`--review …/revision_ciega_route_revisorB.csv` and `--column ruta_revisor`.
