# Kokoro — Persona-Driven Deliberation Agents

**Kokoro** (心 — "heart-mind-spirit") is Prismal's opt-in deliberation layer.
Three Markdown-authored personas (**souls**) argue a question toward agreement,
and a single **judge** ("the whole, more than the sum of its parts") renders
the final, accountable decision — optionally executing one tool action through
the existing security gates.

The design follows the Japanese concept of *kokoro* (the unity of thought,
emotion, and will) and the three-part AI Kokoro of *Terminator Zero*
(**Spirit / Mind / Heart** weighing a decision before acting).

> Specs: [`specs/kokoro-deliberation/`](../specs/kokoro-deliberation/) ·
> Example: [`examples/kokoro_deliberation.py`](../examples/kokoro_deliberation.py)

## Quick start

```bash
export PRISMAL_KOKORO_ENABLED=true
```

```python
from prismal.agents.subgraphs.kokoro import build_kokoro_subgraph, register_kokoro
from prismal.agents.subgraphs.factory import assemble_state_graph
from langchain_core.messages import HumanMessage

graph = assemble_state_graph(build_kokoro_subgraph()).compile()
result = await graph.ainvoke(
    {"messages": [HumanMessage(content="Should we migrate the platform now?")]}
)
print(result["messages"][-1].content)        # decision + rationale (+ dissent)
print(result["metadata"]["kokoro"]["verdict"])
```

With the supervisor (`kokoro_enabled=True`), deliberation intents
("deliberate on…", "weigh the perspectives", "have the panel decide", or any
mention of *kokoro*) route deterministically to the `kokoro` subgraph via
`intent_router.match_intent()`. With the flag off (the default) the framework
is byte-for-byte unchanged.

## Pipeline

```
load_souls → deliberate (spirit | mind | heart, ≤ N rounds) → judge → act → output
```

1. **load_souls** — `SoulsManager.load_triad()` resolves and validates the three
   souls (fail-fast: an invalid `SOUL.md` stops the run *before any LLM call*).
2. **deliberate** — round 1: three independent positions (concurrent);
   round r>1: each soul revises seeing only the *other* souls' positions.
   Early-stops when `pairwise_jaccard ≥ kokoro_agreement_threshold`; hard cap
   at `kokoro_max_rounds`.
3. **judge** — `KokoroJudgeAgent.judge()` renders a `Verdict` citing every lens
   (`lens_summaries` always has one entry per soul) plus retained dissent.
4. **act** — only when `kokoro_execute_actions=True`: the action passes
   `ActionInterceptor` first; a denial sets `action.blocked_reason` (no
   exception). Otherwise a pure pass-through.
5. **output** — appends the assistant message (decision + rationale
   [+ action result]).

All runtime state lives under `state["metadata"]["kokoro"]`.

## Souls — authoring personas without code

Souls mirror the `skills/` three-tier layout under `prismal/souls/`:

| Tier | Purpose |
|---|---|
| `available/` | Committed source souls (the three defaults live here) |
| `active/` | Runtime allow-list (gitignored): when non-empty, only these souls load |
| `custom/` | AI-generated souls (gitignored) |

A soul is one `SOUL.md` — YAML frontmatter + persona body:

```markdown
---
name: risk
description: The risk-management lens
role: risk
temperament: cautious, quantitative
values: [capital-preservation, downside-protection]
version: 1.0.0
author: acme
tags: [kokoro, soul]
---

You are **Risk**, one of the three voices of Kokoro...
```

Defaults shipped:

| id | alias | lens |
|---|---|---|
| `spirit` | 魂 *tamashii* | values, principles, long-term vision |
| `mind` | 知 *chi* | logic, evidence, analysis, feasibility |
| `heart` | 情 *jō* | empathy, human impact, stakeholder feelings |

Override the triad with `PRISMAL_KOKORO_SOULS='["risk", "growth", "customer"]'`
(exactly three ids).

## Settings

| Setting (`PRISMAL_*`) | Default | Purpose |
|---|---|---|
| `kokoro_enabled` | `False` | Master opt-in toggle (supervisor route + intents) |
| `souls_dir` | `""` (packaged `prismal/souls`) | Root of the souls tiers |
| `kokoro_souls` | `["spirit", "mind", "heart"]` | The three soul ids to convene |
| `kokoro_max_rounds` | `2` | Hard cap on deliberation rounds |
| `kokoro_agreement_threshold` | `0.6` | Early-stop agreement score (0–1) |
| `kokoro_execute_actions` | `False` | Allow the judge to execute one tool action |
| `kokoro_judge_model` | `""` | Optional judge model override |
| `soul_max_body_chars` | `20000` | Max soul body length (sanitizer cap) |

## Security model

- A `SOUL.md` body is **user-controlled content**: it reaches a model only
  through `SecurePromptBuilder` (canary tokens, `<user_input>` isolation,
  `InputSanitizer` length cap) — never f-stringed into a template. The same
  applies to the soul's frontmatter fields, the query, and prior positions.
- Soul loading is path-confined under `souls_dir` and size-capped.
- The judge is the **only** component allowed to request an action, and only
  through the `ActionInterceptor` gateway; execution is off by default.
- `AuditLogger` records every verdict and action **hash-first**
  (`kokoro_verdict` / `kokoro_action` events) — never the soul bodies or raw
  contents.

## Testing your own composition

Every backend is callable-injected, so the full pipeline runs without an LLM:

```python
async def fake_persona(messages): return "we agree"
async def fake_judge(messages): return '{"decision": "ship", "rationale": "...", "lens_summaries": {}, "dissent_retained": []}'

definition = build_kokoro_subgraph(generate_fn=fake_persona, judge_fn=fake_judge)
```

Injection points: `generate_fn` (per-soul positions), `agreement_fn` (defaults
to `pairwise_jaccard`), `judge_fn`, `tool_executor`, `souls_manager`,
`judge_agent`, plus `interceptor` / `audit` / `prompt_builder` on
`KokoroJudgeAgent`.

## Observability

OTel spans per stage — `kokoro.load_souls`, `kokoro.deliberate` (`rounds`,
`agreement`, `converged`), `kokoro.judge`, `kokoro.act` (`tool_name`,
`executed`, `blocked`) — and structured logs throughout.
