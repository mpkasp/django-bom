# AI Architecture

Guiding document for AI features in django-bom / IndaBOM. The intent is that any individual
feature — BOM import mapping, property extraction, natural-language search, agent access —
drops into this structure without inventing new machinery.

Read this before starting an AI feature. If a feature doesn't fit, change this document
deliberately rather than working around it.

## Principles

### 1. One capability layer, many surfaces

All AI-reachable operations live in `bom/services/` as plain Python functions: typed inputs,
typed outputs, organization-scoped, no HTTP and no LLM calls. For example
`price_bom(part_revision, quantity, organization)` or `purchase_package(part_revision, build_quantity, organization)`.

Every surface — HTML views, REST API, MCP server, embedded chat, background jobs — calls
these services. Nothing else re-implements the queries.

This matters most for multi-tenancy. `OrganizationManager` scoping is the security boundary
of the whole app. If four surfaces each write their own queries, one of them eventually leaks
another organization's parts. One layer means one place to enforce it and one place to test it.

### 2. AI proposes, Django's forms commit

No AI code path calls `.save()` on a domain model. AI writes an `AIProposal` — an
organization-scoped record holding a proposed change as JSON, its status, and its provenance.

A human accepts a proposal, and the acceptance path runs the payload through the **existing
forms** (`PartFormSemiIntelligent`, `SubpartForm`, `BOMCSVForm`, `PartRevisionPropertyDefinitionForm`, …).
That way `PartRevision.is_immutable()`, organization scoping, part-number validation, and every
existing validator still run, with no second implementation to keep in sync.

Two things fall out of this for free: an audit trail, which is not optional in a PLM context,
and a safety story that doesn't depend on the model behaving.

### 3. BYOK, per organization

`bom/third_party_apis/ai/` mirrors `bom/third_party_apis/sourcing/` in structure: an
`AIProvider` ABC with normalized DTOs in `base.py`, `build_provider(name, organization)`,
`credentials_from_organization`, `provider_credential_schema`, `provider_is_configured`.

Credentials are stored per organization in `EncryptedTextField` (`bom/fields.py`), exactly as
`sourcing_api_key` / `sourcing_api_secret` are today. No global keys in `BOM_CONFIG`.

Consequences: hosted IndaBOM's unit economics stay flat as usage grows, the open-source app
never requires a vendor account to be useful, and swapping or adding a provider is a new module
rather than a refactor.

### 4. Deterministic core, AI at the edges

Money, quantities, MOQ rollups, and part numbers are never computed by a model.
`SellerPart.optimal()`, `SellerPart.order_quantity()`, `PartRevision.flat()`, and
`Part.assign_part_number()` remain authoritative.

The model's job is mapping, extraction, classification, and language: turning fuzzy input into
structured input, and structured data into prose. Any number a user sees came from Python.

The test to apply when designing a feature: *could a hallucination change a price or a part
number?* If yes, restructure until the answer is no. The sourcing module already sets the
precedent — an AI cross-reference feature proposes candidate MPNs, and the existing provider
call supplies the actual stock and price.

### 5. AI work is asynchronous and resumable

LLM calls, datasheet parsing, and multi-hundred-line BOM mapping will not finish inside a
request. Every AI operation is enqueued, produces a row, and is polled by the client — the
pattern `SourcingMatchBOM` in `bom/views/json_views.py` already uses.

Write against the `django.tasks` API (`@task`, `.enqueue()`, the `TASKS` setting). Note that
Django 6.0 ships only `ImmediateBackend` and `DummyBackend` and **no worker**; a third-party
database-backed backend supplies the queue and worker process. Database-backed rather than
broker-backed keeps deployment to one extra process and no Redis.

### 6. Untrusted content boundary

Uploaded BOMs, datasheets, and customer spreadsheets are untrusted input. Their contents are
data, never instructions.

Practically: agent-reachable write tools stay narrow, explicitly named, and confirmation-gated.
There is no generic "run this query" or "update this object" tool. Retrieval results are clearly
delimited in prompts.

### 7. Prompts and evals are versioned artifacts

Prompts live in the repo with a version identifier recorded on every `AIProposal`, so a
regression can be traced to a prompt change. `bom/test_files/` becomes the eval corpus — real
BOM exports with expected mappings, run as a scored suite, not as pass/fail unit tests.

## Feature shapes

Every planned feature is one of two shapes, which is why the substrate above is shared:

**Extraction / mapping** — unstructured input becomes a proposed structured change, a human
confirms, existing forms write it. Covers BOM import mapping, property extraction from
datasheets, part-class suggestion, synopsis templates.

**Retrieval / reasoning** — a question becomes organization-scoped service calls and then an
answer or artifact. Covers natural-language search, BOM Q&A, duplicate detection, purchase
packages, ECO summaries.

## Surfaces

The REST API, the MCP server, and an embedded chat are not alternatives. They are three
renderings of the same service layer, and the architecture above is what makes choosing between
them a late, cheap, reversible decision.

| Surface | Cost to build | Reaches | Notes |
|---|---|---|---|
| REST API | Medium — the real work | Integrators, n8n/Make | Prerequisite for the others. Generic HTTP nodes mean no custom n8n connectors needed. |
| MCP server | Low, once services exist | Users already in an AI client | Wrapper over services. No token cost to us. Strong differentiation. |
| Embedded chat | High — UI plus agent loop | Everyone, zero setup | BYOK resolves the token cost. Best for non-technical users. |

Recommended order: **API → MCP → embedded chat**. Beyond the dependency, MCP is a cheap way to
learn which tools people actually reach for; those tool-call logs are the product spec for the
embedded chat, which is otherwise a guess. If user research later shows the audience won't run
MCP clients, flipping to chat-first costs little — the service layer is the same either way.

## Deliberately out of scope

**Placing orders.** Generating a purchase package (grouped by distributor, MOQ-rounded, priced)
is in scope. Executing a purchase is not — it means payment handling and liability for an
agent's mistakes. Stop at cart deep-links, PO CSV, or RFQ email. The user keeps the final click.

**Persisting distributor pricing.** Unchanged from the sourcing module's existing rule.

**Global API keys.** See principle 3.
