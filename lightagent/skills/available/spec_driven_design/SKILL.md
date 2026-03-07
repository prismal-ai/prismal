---
name: spec-driven-design
description: >
  Methodology and templates for Spec-Driven Design (SDD) — a structured approach where specifications
  are the primary artifact before writing code. Use this skill whenever the user wants to plan a new
  feature, design an API, create a technical design document, define a data model, write a PRD,
  create an implementation plan, or start any software project with proper specifications. Also trigger
  when the user mentions "spec", "PRD", "API spec", "technical design", "data model spec",
  "implementation plan", "spec-driven", "design doc", "feature planning", "architecture document",
  or asks to plan before coding. This skill applies to both greenfield projects and significant changes
  to existing systems. Even if the user just says "I need to plan a new feature" or "help me document
  this before building it", use this skill.
---

# Spec-Driven Design (SDD) Skill

## What is SDD?

Spec-Driven Design is a development methodology where **specifications are the primary artifact** of the design process. Instead of jumping to code, the team invests deliberate time defining *what* to build, *why*, and *how* before writing the first line. A well-written spec reduces ambiguity, aligns the team, and serves as a verifiable contract between stakeholders, designers, and developers.

## The SDD Flow

```
PRD (The What) → API Spec (Contract) → Tech Design (The How) → Data Model → Implementation Plan (Phases)
```

Each phase builds on the previous one. The depth of documentation should be proportional to risk:

| Work Type                    | Documents Needed                        |
|------------------------------|-----------------------------------------|
| Bug fix                      | None (ticket is enough)                 |
| Internal refactor            | Tech Design lite (decisions + plan)     |
| Simple CRUD + API            | API Spec + Data Model                   |
| Medium feature (1-2 sprints) | PRD + API Spec + Data Model             |
| Complex feature (3+ sprints) | All 5 documents                         |
| New service/microservice     | All 5 documents                         |

## How to Use This Skill

When the user needs to create specs, follow this workflow:

1. **Understand scope**: Ask what they're building and assess complexity using the table above
2. **Determine which documents are needed**: Not everything needs all 5 specs
3. **Generate specs sequentially**: Each spec feeds the next one
4. **Validate coherence**: Cross-check specs for consistency

### Step-by-step for each document type:

**To create a PRD** → Read `references/01-PLANTILLA-PRD.md` and `references/06-GUIA-LLENADO.md` (Section 1)
**To create an API Spec** → Read `references/02-PLANTILLA-API-SPEC.md` and `references/06-GUIA-LLENADO.md` (Section 2)
**To create a Tech Design** → Read `references/03-PLANTILLA-TECHNICAL-DESIGN.md` and `references/06-GUIA-LLENADO.md` (Section 3)
**To create a Data Model** → Read `references/04-PLANTILLA-DATA-MODEL.md` and `references/06-GUIA-LLENADO.md` (Section 4)
**To create an Implementation Plan** → Read `references/05-PLANTILLA-IMPLEMENTATION-PLAN.md` and `references/06-GUIA-LLENADO.md` (Section 5)

## Core Principles

1. **Specs as living code**: Specs live in the repo (`/specs` or `/docs`), version-controlled with Git, updated when requirements change.

2. **Detail proportional to risk**: Not everything needs an exhaustive spec. Depth should match technical complexity, number of integrations, cost of fixing errors in production, and requirement ambiguity.

3. **Review before code**: Write spec → Review spec → Approve spec → Implement → Code review → Deploy. Code review verifies implementation matches the spec.

4. **Specs as AI input**: Well-written specs are excellent context for AI tools — a clear PRD generates user stories, an API spec generates integration tests, a tech design generates code scaffolding, a data model generates migrations.

## Anti-Patterns to Avoid

- **Spec as formality**: Writing the spec after the code → Block PRs without approved spec
- **Eternal spec**: Never approved, over-designed → Timebox reviews, accept iteration
- **Abandoned spec**: Written but never updated → Include updates in Definition of Done
- **Monolithic spec**: 50-page doc nobody reads → Split by component/feature
- **Spec without audience**: Written without thinking who reads it → Define audience in header

## Key Quality Checks

When generating or reviewing specs, verify:

- PRD: All MUST requirements have verifiable acceptance criteria; Out of Scope is explicitly defined
- API Spec: A frontend dev can build integration without asking questions; all errors documented with codes
- Tech Design: Key decisions have documented alternatives; error flows have compensation plans
- Data Model: Every index is linked to a critical query; financial data uses Decimal128 (never float)
- Plan: Each phase has verifiable "Done" criteria; dependencies between tasks are mapped

## Output Format

All specs should be generated as Markdown files following the templates in the references directory. Use the project structure:

```
project-root/
├── specs/
│   ├── prd/
│   │   └── feature-name.md
│   ├── api/
│   │   └── feature-api-v1.md
│   ├── technical/
│   │   └── feature-architecture.md
│   ├── data-model/
│   │   └── feature-schema.md
│   └── plans/
│       └── feature-phase-1.md
├── src/
└── tests/
```

## Cross-Spec Validation

When multiple specs exist for the same feature, check coherence:

1. Are all PRD requirements covered by the API Spec?
2. Are all API fields present in the Data Model?
3. Do state transitions in the API match the Tech Design?
4. Do Data Model indexes cover the queries implied by API endpoints?
5. Does the Implementation Plan reference all specs and cover all phases?

## Generating Specs with AI — Recommended Prompts

The filling guide (`references/06-GUIA-LLENADO.md`, Section 7) contains optimized prompts for:
- Generating PRD drafts from problem context
- Generating API Specs from approved PRDs
- Generating tests from API Specs
- Generating Data Models from Tech Designs
- Validating coherence across all specs

Read that section when the user wants to accelerate spec creation with AI assistance.
