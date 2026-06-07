# Prismal Kokoro Deliberation — Interface Specification

## Metadata

| Field | Value |
|---|---|
| **Author** | Ernesto Crespo |
| **Status** | `IMPLEMENTED` |
| **Version** | 1.0 |
| **Date** | 2026-06-06 |
| **PLAN** | `specs/kokoro-deliberation/PLAN.md` |
| **Architecture** | `specs/kokoro-deliberation/ARCHITECTURE.md` |
| **TASKS** | `specs/kokoro-deliberation/TASKS.md` |

---

## Conventions

- All modules use `from __future__ import annotations`.
- Async where applicable (persona/judge LLM calls are `async`); pure helpers are `sync`.
- Frozen dataclasses / Pydantic models for value objects.
- Constructors accept `settings: Settings | None = None`.
- No module imports `openai`, `anthropic`, `google.generativeai`, `ollama`, etc.
  directly — everything via `prismal/providers/`.
- **Callable injection** in every agent (`generate_fn`, `judge_fn`,
  `tool_executor`, `agreement_fn`) so tests run without an LLM backend.
- Soul Markdown is **user-controlled content**: it only reaches a model through
  `SecurePromptBuilder`; never f-stringed into a prompt template.
- Custom errors live in `prismal/core/exceptions.py` (extension).
- All Kokoro runtime state lives under `state["metadata"]["kokoro"]`.

---

## Module Summary

| Module | Purpose |
|---|---|
| `prismal/souls/base.py` | `SoulMetadata`, `parse_soul_md()`, `_soul_md_body()`, `Soul` value object |
| `prismal/souls/manager.py` | `SoulsManager` — discover/load/validate souls across tiers |
| `prismal/souls/available/{spirit,mind,heart}/SOUL.md` | Three default personas |
| `prismal/agents/kokoro/soul_agent.py` | `SoulAgent` — persona-conditioned position generator |
| `prismal/agents/kokoro/judge.py` | `KokoroJudgeAgent` — verdict + optional action |
| `prismal/agents/kokoro/deliberation.py` | `deliberate()` — bounded multi-soul rounds (reuses `debate`) |
| `prismal/agents/subgraphs/kokoro/` | LangGraph subgraph + `build_*`/`register_*` |
| `prismal/core/config.py` | Settings extension (`kokoro_*`, `souls_dir`) |
| `prismal/core/exceptions.py` | `KokoroError` hierarchy |

---

## SPEC-KOK-SOUL-001: Soul model and parsing (`souls/base.py`)

### Types

```python
from pydantic import BaseModel, Field


class SoulMetadata(BaseModel):
    """Metadata describing a Kokoro soul (persona), parsed from SOUL.md frontmatter."""

    name: str = Field(..., description="Unique english id slug (snake_case), e.g. 'spirit'")
    alias_jp: str = Field(default="", description="Japanese alias, e.g. '魂' / 'tamashii'")
    description: str = Field(..., description="One-line description of the persona")
    role: str = Field(..., description="Deliberation lens, e.g. 'values', 'logic', 'empathy'")
    temperament: str = Field(default="balanced", description="Tone/voice hint for the persona")
    values: list[str] = Field(default_factory=list, description="Guiding priorities the soul argues from")
    version: str = Field(default="1.0.0", description="Semantic version of the soul")
    author: str = Field(default="unknown", description="Author handle")
    tags: list[str] = Field(default_factory=list, description="Categorisation tags")
    model: str = Field(default="", description="Optional per-soul model override (empty = default)")


@dataclass(frozen=True)
class Soul:
    """A fully-loaded soul: parsed metadata + the Markdown persona body."""

    metadata: SoulMetadata
    body: str          # the instructional persona text (post-frontmatter)
    source_dir: Path
```

### Functions

```python
def parse_soul_md(soul_dir: Path) -> dict[str, object]:
    """Parse YAML frontmatter from SOUL.md (any case) in *soul_dir*. {} on any error."""

def _soul_md_body(soul_dir: Path) -> str:
    """Return the Markdown body of SOUL.md (everything after the --- frontmatter)."""

def _find_soul_md(soul_dir: Path) -> Path | None:
    """Return the SOUL.md path (case-insensitive on filename), or None."""

def load_soul(soul_dir: Path) -> Soul:
    """Load + validate a soul from a directory.

    Raises:
        SoulValidationError: missing SOUL.md, missing required metadata, body too
            large (> settings.soul_max_body_chars), or path outside souls_dir.
    """
```

> Implementation note: reuse the proven `skills/base.py` parsing helpers
> (`_find_skill_md`, frontmatter splitting) verbatim in spirit; the directory
> comparison MUST be case-insensitive on both the directory and the filename
> (the fix landed in `_zip_detect_prefix`).

## SPEC-KOK-SOUL-002: `SoulsManager` (`souls/manager.py`)

Mirrors `SkillsManager`'s tier layout: `available/` (committed source),
`active/` (runtime-enabled, gitignored), `custom/` (AI-generated, gitignored).

```python
class SoulsManager:
    def __init__(self, *, souls_root: Path | None = None, settings: Settings | None = None) -> None:
        """Default root: settings.souls_dir (→ prismal/souls)."""

    def list_souls(self) -> list[SoulMetadata]:
        """Discover all souls under available/ (and active/ when restricted)."""

    def load(self, soul_id: str) -> Soul:
        """Load a single soul by its `name` id. Raises SoulNotFoundError if absent."""

    def load_triad(self, ids: list[str] | None = None) -> list[Soul]:
        """Load exactly three souls (default: settings.kokoro_souls = [spirit, mind, heart]).

        Raises:
            KokoroConfigError: if the resolved list does not contain exactly 3 souls.
        """
```

## SPEC-KOK-AGT-001: `SoulAgent` (`agents/kokoro/soul_agent.py`)

A persona sub-agent. It produces **one position** on a query, conditioned on its
soul. The soul body is injected only through `SecurePromptBuilder`.

```python
PersonaGenerateFn = Callable[[str], Awaitable[str]]  # (secure_prompt) -> position text


class SoulAgent:
    def __init__(
        self,
        soul: Soul,
        *,
        generate_fn: PersonaGenerateFn | None = None,   # injected; default wires ProviderRegistry().get_llm()
        prompt_builder: SecurePromptBuilder | None = None,
        settings: Settings | None = None,
    ) -> None: ...

    @property
    def agent_id(self) -> str:                          # == soul.metadata.name
        ...

    async def position(self, query: str, *, prior: list[DebatePosition] | None = None) -> DebatePosition:
        """Produce this soul's position on *query*.

        Builds a secure prompt = system(role/temperament/values, isolated soul body)
        + the query + optional prior-round positions, calls generate_fn, and returns
        a DebatePosition(agent_id=self.agent_id, role=soul.role, content=..., round=...).

        Security: soul.body is wrapped via SecurePromptBuilder.build(user_content=body)
        with canary tokens; InputSanitizer caps length. Never concatenated raw.
        """
```

## SPEC-KOK-AGT-002: Deliberation (`agents/kokoro/deliberation.py`)

Bounded, agreement-seeking deliberation among exactly three `SoulAgent`s. Reuses
`pairwise_jaccard` (SPEC-PAT-002) for the agreement score and follows the shape of
`debate_round`/`DebateResult`.

```python
AgreementFn = Callable[[list[str]], float]   # default = pairwise_jaccard


@dataclass(frozen=True)
class DeliberationResult:
    positions: list[DebatePosition]          # all positions across all rounds
    final_positions: list[DebatePosition]    # last round, one per soul
    agreement_score: float                   # over final_positions' content
    rounds_completed: int
    converged: bool                          # agreement_score >= threshold


async def deliberate(
    query: str,
    souls: list[SoulAgent],                   # exactly 3
    *,
    max_rounds: int | None = None,            # default settings.kokoro_max_rounds (2)
    agreement_threshold: float | None = None, # default settings.kokoro_agreement_threshold (0.6)
    agreement_fn: AgreementFn | None = None,
    settings: Settings | None = None,
) -> DeliberationResult:
    """Run rounds until convergence or max_rounds.

    Round 1: each soul produces an independent position (concurrently).
    Round r>1: each soul revises given the other souls' previous positions.
    After each round compute agreement_score; stop early when >= threshold.

    Raises:
        KokoroConfigError: if len(souls) != 3.
    """
```

## SPEC-KOK-AGT-003: `KokoroJudgeAgent` (`agents/kokoro/judge.py`)

The judge — "the whole". Reviews the deliberation, renders a verdict, and (when
enabled) executes one action through the security stack.

```python
JudgeFn = Callable[[str], Awaitable[str]]                       # (secure_prompt) -> verdict json/text
ToolExecutor = Callable[[str, dict], Awaitable[str]]            # (tool_name, args) -> result


@dataclass(frozen=True)
class Verdict:
    decision: str                          # the chosen course of action / answer
    rationale: str                         # judge reasoning citing each lens
    lens_summaries: dict[str, str]         # {soul_id: how its view was weighed}
    dissent_retained: list[str]            # unresolved minority positions
    agreement_score: float
    action: KokoroAction | None            # populated only in action mode


@dataclass(frozen=True)
class KokoroAction:
    tool_name: str
    args: dict
    executed: bool
    result: str | None
    blocked_reason: str | None             # set when ActionInterceptor denies it


class KokoroJudgeAgent:
    def __init__(
        self,
        *,
        judge_fn: JudgeFn | None = None,
        tool_executor: ToolExecutor | None = None,
        interceptor: ActionInterceptor | None = None,
        audit: AuditLogger | None = None,
        prompt_builder: SecurePromptBuilder | None = None,
        settings: Settings | None = None,
    ) -> None: ...

    async def judge(self, query: str, deliberation: DeliberationResult) -> Verdict:
        """Render the verdict from the deliberation (no side effects).

        Builds a secure prompt summarising the query, each soul's final position,
        the agreement score and dissent, then calls judge_fn and parses a Verdict.
        """

    async def act(self, verdict: Verdict) -> Verdict:
        """Execute verdict.action when settings.kokoro_execute_actions is True.

        Calls ActionInterceptor.check(action) first; on deny, returns a Verdict
        whose action.executed is False and blocked_reason is set. On allow, runs
        tool_executor and records AuditLogger.log(...) (hash-first). When action
        execution is disabled or verdict.action is None, returns verdict unchanged.
        """
```

## SPEC-KOK-SG-001: Subgraph (`agents/subgraphs/kokoro/`)

```
load_souls_node → deliberate_node → judge_node → act_node → output_formatter_node
```

- `load_souls_node` — resolves the triad via `SoulsManager.load_triad()` and
  builds three `SoulAgent`s; writes them to `state["metadata"]["kokoro"]["souls"]`.
- `deliberate_node` — runs `deliberate()`; writes `DeliberationResult`.
- `judge_node` — runs `KokoroJudgeAgent.judge()`; writes `Verdict`.
- `act_node` — runs `KokoroJudgeAgent.act()` when `kokoro_execute_actions`;
  otherwise a pass-through.
- `output_formatter_node` — appends an assistant message with the decision +
  rationale (and action result when present).

```python
def build_kokoro_subgraph(settings: Settings | None = None) -> SubgraphDefinition:
    """Return the SubgraphDefinition (nodes/edges/entry_point) for kokoro."""

async def register_kokoro(registry: SubgraphRegistry | None = None) -> None:
    """Idempotently register the kokoro subgraph (mirrors register_debate_consensus)."""
```

## SPEC-KOK-INT-001: Supervisor + intent integration

- `settings.kokoro_enabled` (default `False`). When `True`,
  `get_async_compiled_graph()` wires a single `kokoro` supervisor route;
  `effective_valid_routes` / `build_system_prompt` gate on the flag.
- `intent_router.match_intent()` returns `kokoro` for deliberation intents
  (regex over phrases like "deliberate", "weigh perspectives", "have them decide",
  "panel decide", configurable). Deterministic, ahead of LLM supervision.
- When `kokoro_enabled` is `False`, behavior is byte-for-byte unchanged.

## SPEC-KOK-CFG-001: Settings (`core/config.py` extension)

| Setting | Type | Default | Purpose |
|---|---|---|---|
| `kokoro_enabled` | `bool` | `False` | Master opt-in toggle |
| `souls_dir` | `Path` | `prismal/souls` | Root of the souls tiers |
| `kokoro_souls` | `list[str]` | `["spirit", "mind", "heart"]` | The three soul ids to convene |
| `kokoro_max_rounds` | `int` | `2` | Hard cap on deliberation rounds |
| `kokoro_agreement_threshold` | `float` | `0.6` | Early-stop agreement score (0–1) |
| `kokoro_execute_actions` | `bool` | `False` | Allow the judge to execute a tool action |
| `kokoro_judge_model` | `str` | `""` | Optional judge model override |
| `soul_max_body_chars` | `int` | `20000` | Max soul body length (sanitizer cap) |

Env prefix `PRISMAL_` (e.g. `PRISMAL_KOKORO_ENABLED`).

## SPEC-KOK-ERR-001: Exceptions (`core/exceptions.py` extension)

```python
class KokoroError(PrismalError): ...
class SoulValidationError(KokoroError): ...
class SoulNotFoundError(KokoroError): ...
class KokoroConfigError(KokoroError): ...
class DeliberationError(KokoroError): ...
class JudgeError(KokoroError): ...
```

## SPEC-KOK-SOUL-003: `SOUL.md` format (the three defaults)

```markdown
---
name: spirit
alias_jp: 魂 (tamashii)
description: The values-and-vision lens of Kokoro
role: values
temperament: principled, long-horizon, calm
values: [integrity, human-dignity, long-term-good]
version: 1.0.0
author: prismal
tags: [kokoro, soul, spirit]
---

You are **Spirit** (魂), one of the three voices of Kokoro...
(persona instructions: how this voice reasons, what it prioritises, how it
argues with Mind and Heart, and how it concedes.)
```

Defaults shipped:

| id | alias | role / lens |
|---|---|---|
| `spirit` | 魂 *tamashii* | values, principles, long-term vision |
| `mind` | 知 *chi* | logic, evidence, analysis, feasibility |
| `heart` | 情 *jō* | empathy, human impact, stakeholder feelings |

## Acceptance Criteria (per requirement)

| Requirement | Acceptance criterion |
|---|---|
| RF-KOK-01 | `parse_soul_md` returns the frontmatter dict; `{}` on missing/invalid |
| RF-KOK-04 | A `SoulAgent.position()` call routes the soul body through `SecurePromptBuilder` (verified by a spy) |
| RF-KOK-05/06 | `deliberate()` stops at the first round where `agreement_score ≥ threshold`; never exceeds `max_rounds` |
| RF-KOK-07 | `Verdict.lens_summaries` has one entry per soul; `dissent_retained` lists minority views |
| RF-KOK-08 | With `kokoro_execute_actions=False`, `act()` never calls `tool_executor`; with `True`, denial path sets `blocked_reason` |
| RF-KOK-10 | With `kokoro_enabled=False`, the compiled graph has no `kokoro` route (snapshot test) |
| RF-KOK-11 | The full subgraph runs end-to-end with injected fakes and no provider import |
