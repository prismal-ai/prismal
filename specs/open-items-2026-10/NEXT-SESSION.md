# Next session: Phase 3 data tasks and the TKE 30 % target

Written 2026-10-12, after release 3.20.0. Two independent tracks. Neither
needs a new spec to start: the DEC tasks are data and ops, and the TKE track
has its experiments laid out below. Work with strict TDD for any code change.

## 0. Before any live run (both tracks)

1. `powerprofilesctl get` must print `performance`. In `power-saver` the GPU
   is held at 661 MHz and qwen3:14b drops from about 7.9 tok/s to 3.3 tok/s.
2. Use `ollama_chat/<model>`, never `ollama/`. With `ollama/`, LiteLLM emulates
   tool calls as text and every local model fails a tool loop. Alternatively,
   set `PRISMAL_OLLAMA_CHAT_ENDPOINT=true`.
3. Run evals from a **detached worktree** (`git worktree add --detach
   ../prismal-measure <sha>`). Use the main `.venv` python with
   `PYTHONPATH=<worktree>`, and run each arm in a fresh directory with an
   empty `data/`, so that profile and memory writes do not leak between runs.
   Never switch branches in the tree a live run imports from.
4. Ollama is only near-deterministic across model reloads. A boundary case
   (`seq-001`) flipped in **every** arm, A included. Compare arms within one
   session, interleave them, and repeat any case whose result flips.
5. Estimate the duration and confirm it with the user before any run longer
   than about 10 minutes.

---

## Track A: Phase 3 data tasks (DEC)

Source of truth: `specs/typed-decision-models/TASKS.md` and
`DELTA-2026-10-ollama-backend.md`. Tooling already in the repo:

- `python -m prismal.decisions calibrate`: labeled JSONL plus raw scores give a
  digest-keyed calibration record.
- `python -m prismal.decisions bench`: latency and cost of one live backend.
- `specs/typed-decision-models/bench/ollama_decision_bench.py` and its
  `cases.*.sample.jsonl` files, which show the case format.
- Live contract:
  `PRISMAL_OLLAMA_CONTRACT=1 uv run pytest -m ollama tests/live/decisions/test_ollama_contract.py`.
- Local models: `tev1:4b` (digest 9b5bb969e46c) and `nimble:9b-q4_K_M`
  (572f1f4c801d).

### A1. DEC3-07: ≥ 300 labeled Spanish cases per decision (P1)

- **Goal.** For each decision integration (route, eval_judge, hitl, tool, rag,
  ctx, swarm, safety), at least 300 labeled Spanish cases. Include the HITL
  **safety slice**: the five one-fact questions plus `rule_flags`.
- **Steps.**
  1. Draft the cases in the `cases.sample.jsonl` format. Balance the labels per
     question, and include adversarial and borderline items. Never paste
     production data; synthesize or anonymize.
  2. Review the labels by hand. Two passes are recommended, and disagreements
     go to a third pass.
  3. Run each case on `ollama:fast` and `ollama:accurate` to collect raw scores.
  4. Run `calibrate`. It writes `<id>@<name>@<digest12>` records. Check the ECE
     and the margin against the gate thresholds.
  5. Measure accuracy with batching on and off. It writes
     `batch@<name>@<digest12>.json`, which **auto-disables batching when it
     drops more than 2 points** (DEC2-11).
- **Done when.** The records are committed. `promotion_ready` is evaluated per
  integration. TASKS.md lists DEC3-07 as DONE with the figures.
- **Watch out.**
  - Calibration keys pin the model digest. A model re-pull with a new digest
    makes the records stale, and they must be refit.
  - `enforce` without a valid record fails `Settings` load. That is by design.

### A2. DEC3-09: per-question latency profile (P2)

- **Goal.** p50 and p95 latency per question, per sub-tier, on the target
  host, at `OLLAMA_NUM_PARALLEL` = 1, 2 and 4. Also review the labels of the
  "double-charge" cases.
- **Steps.**
  1. `bench` with `n ≥ 200` per question.
  2. Repeat for each `OLLAMA_NUM_PARALLEL` value. Each change needs an Ollama
     restart and the user's go-ahead.
  3. Compare against each sub-tier's `decision_ollama_timeout_ms` budget.
- **Done when.** A table is in `docs/decisions.md#verified-facts`, the budget
  recommendations are adjusted if needed, and DEC3-09 is DONE.

### A3. DEC6-07: re-run the injection corpus live (P1, security)

- **Goal.** Run the append-only corpus
  `tests/security/decisions/test_injection_corpus.py` against the **real**
  Tev1 and Nimble models, not only the fakes.
- **Steps.** Run it under the live marker against both sub-tiers, and record
  every case that leads to `auto` instead of an escalation.
- **Invariant.** A failure is an escalation, never an "allow". Any live bypass
  goes **into the corpus as a new case** (append-only) before it is fixed.
- **Known limitation.** If a persuaded, calibrated model answers "no" to all
  five HITL questions and no rule fires, the result is `auto`. Calibration is
  the defence. Report it if it reproduces.
- **Done when.** Results are recorded in `docs/decisions.md` and DEC6-07 is
  DONE. The non-loopback guard part is already done.

### A4. DEC0-08: reproduce the Laya benchmarks (P3, needs a GPU)

- **Goal.** Reproduce the vendor numbers recorded in `docs/decisions.md`:
  - T4 at 33 ms, batched at 7.2 ms per question, CPU at 193–464 ms;
  - bf16 within ±0.073 of fp16;
  - raw ECE of 0.466;
  - degradation beyond 20 options.

  Also confirm the RLCD fine-tune cost (about 4 h on 2×T4).
- **Blocker.** It needs the `[laya]` extra and a GPU host. On this laptop, try
  ROCm (Ollama already runs on ROCm) before renting a T4.
- **Done when.** The figures are recorded next to the vendor numbers, or the
  task is marked "not reproducible here" with the reason.

---

## Track B: reaching the TKE ≥ 30 % target

### Where we are

Results from the 3.20.0 run, recorded in `docs/token-efficiency.md` (TKE9-04).

| Suite | A tokens/case | Best safe arm (output budget 400 + TOON) |
|---|---|---|
| `long_loop` (lookups batched, 13 calls in 4 turns) | 10,338 | −16.6 % |
| `long_loop_sequential` (one turn per hop) | 19,053 | −19.9 % |

- In-loop masking with `keep_turns=2` reaches −36 % but solves 0/3
  aggregation cases. The model never calls `read_artifact`, and the ReAct caps
  (coder 5, researcher 4 iterations) leave no turn to re-read.
- What is left is mostly fixed cost: the supervisor and agent prompts take
  about 1,350 tokens per call, on 5–7 calls per case.
- TKE2 (prompt caching) **cannot be measured on Ollama**. On a caching
  provider it is the largest lever, because the PLAN target for cached
  workloads is ≥ 50 % input-cost reduction.

### Experiments, in order of expected value

Each one is a TDD change plus an A/B inside one session, using the same
harness: `tests/eval/sets/long_loop*.yaml`, run as described in §0.

1. **Measure prompt caching on a caching provider.** No code change. Run the
   same suites on Anthropic or OpenAI (or OpenRouter) with
   `prompt_cache_enabled` and `prompt_layout=cache_stable`.
   - The supervisor and agent system prompts (about 1,350 tokens per call)
     are exactly the stable prefix TKE2 caches.
   - Expected result: a cache-hit ratio of 60 % or more on supervisor turns,
     and an input-cost reduction well beyond 30 %.
   - The ≥ 30 % long-loop target is in *tokens*, so report both: tokens, and
     cost-weighted tokens with cache reads at their discounted price.
   - This needs API keys and budget approval from the user.
2. **Trim the fixed prompt cost.** Measure the supervisor and agent system
   prompts token by token (`SecurePromptBuilder` output). Candidates:
   - drop the routing instructions of disabled routes from the supervisor
     prompt (`effective_valid_routes` already knows which ones apply);
   - shorten tool descriptions;
   - with `cache_stable`, check that the tail `<turn_context>` holds nothing
     that belongs in the prefix.

   Each prompt change must keep **red-team leak parity**
   (`tests/unit/security/test_canary_leak_cache_stable.py`). Target: −10 % per
   call, which compounds with item 3.
3. **Lower the output budget per tool.** Set `tool_output_max_tokens_by_tool`
   per tool, keeping the head+tail excerpt (the total sits in the tail).
   - Sweep 400 → 250 → 150 tokens on `get_order` / `get_linked_order` and stop
     where the solve rate drops.
   - This is pure configuration, so first add an eval set whose answers need
     *middle* fields, not only the tail, to avoid tuning to the toolkit's
     layout.
4. **Lazy `read_artifact`.** Today the schema is only offered with masking.
   Better: offer it only after the first `artifact=` pointer appears in the
   loop. That needs `react_loop` to re-bind the model mid-loop, which the TKE3
   deferred-tool path already does through `rebind_llm`. Every agent node
   would have to pass `rebind_llm=lambda ts: llm.bind_tools(ts)` (nine call
   sites, plus an AST guard as for the compactor). This removes the measured
   2–3 % schema cost and the answer flips.
5. **Make masking usable for aggregation**, if items 1–3 fall short.
   - Per-agent `max_iterations` headroom when masking is on, for example
     `+N` re-read turns.
   - Make the stub carry the fields the task needs (a short summary, not just
     a pointer), via the existing optional `relevance_fn` / DEC hook.
   - Measure first whether qwen3 ever calls `read_artifact` once it has spare
     turns. If it does not, masking stays a search-then-act tool and the docs
     already say so.

### Definition of done for Track B

- One suite reaches ≥ 30 % fewer tokens per successful task at equal solve
  rate, inside one session, with 2 repetitions. Alternatively, the PLAN §4
  target is formally amended to cost-weighted tokens, with the user's sign-off.
- `docs/token-efficiency.md` is updated, and the baseline is refreshed only if
  the A arm changed.
