# AI Implementation Plan

Phased plan for the work described in [ai-architecture.md](ai-architecture.md). Phases 0–2 are
the active scope; everything else is explicitly shelved at the bottom.

Each phase lists exit criteria. Don't start the next phase until they hold.

---

## Phase 0 — Foundations

No user-visible AI feature ships in this phase. The goal is that Phase 1 is a feature, not a
feature plus an infrastructure project.

### 0.1 Service layer skeleton

Create `bom/services/` and move (not copy) a few existing read paths into it as the pattern
reference — `price_bom`, `search_parts`, `where_used` are good candidates since
`PartRevision.flat()`, `where_used_recursive()`, and `SellerPart.optimal()` already do the work
and just need an organization-scoped entry point.

Rule established here: services take an `organization`, return dataclasses or model instances,
and never touch `request`.

### 0.2 Background task infrastructure

The largest genuinely new piece, and the only one with a deployment consequence.

Django is pinned at 5.2.16, so `django.tasks` (Django 6.0) isn't importable yet. Use the
standalone `django-tasks` package — the DEP 0014 reference implementation, same `@task` /
`.enqueue()` / `TASKS` API, and unlike Django's built-ins it ships a database-backed backend and
a worker management command. Migrating to `django.tasks` when the project moves to Django 6 is
then close to an import change. *(Confirm the package's current backend and worker command names
at implementation time.)*

Database-backed rather than broker-backed is deliberate: no Redis, no new managed service, and
the database is already there.

- Add the dependency and `TASKS` config to `bom/settings.py` and `local_settings.py.example`
- Document the worker process — **IndaBOM's deployment gains a second process** alongside the
  web server. Plan for it in the host environment before Phase 1 needs it.
- Tests configure the immediate/synchronous backend so the suite stays deterministic

### 0.3 AI provider abstraction (BYOK)

`bom/third_party_apis/ai/`, structured to match `sourcing/`:

- `base.py` — `AIProvider` ABC, normalized request/response DTOs, `CredentialField` schema
- `anthropic.py` — first provider
- `__init__.py` — `build_provider(name, organization)`, `provider_is_configured`,
  `provider_credential_schema`

Model changes on `AbstractOrganization`, alongside the existing sourcing fields:
`ai_provider` (choices), `ai_api_key` (`EncryptedTextField`), and an `ai_enabled` flag.

### 0.4 `AIProposal` model

Organization-scoped. Fields: `kind`, `payload` (JSON), `status`
(pending / accepted / rejected / expired), `created_by`, optional generic target reference, and
provenance — `provider`, `model`, `prompt_version`, token counts, latency.

Register in admin. This is the audit trail; treat its schema as a stable contract.

### 0.5 Settings UI

An AI tab in `bom_settings` (`bom/views/views.py:374`), reusing the `SourcingSettingsForm`
pattern for credential entry and provider selection, including the write-only secret handling
already solved there.

### 0.6 Prompt versioning and eval harness

Prompts as versioned files in the repo. A scored eval runner over `bom/test_files/` — which
already holds 20+ real BOM and parts CSVs, several deliberately malformed. Scored, reported,
and not wired into the unit-test pass/fail gate.

**Exit criteria:** an organization can save an AI key through the settings UI; a trivial task
enqueues, runs on the worker, calls the provider, and writes an `AIProposal`; `manage.py test bom`
passes; the eval runner executes and reports a baseline.

---

## Phase 1 — Import mapper

Turn "reformat your spreadsheet and try again" into "here's what I think your columns mean."

The central design constraint: **the existing `BOMCSVForm` and `PartCSVForm` are not forked or
modified.** AI produces a canonical CSV plus a mapping; the accepted result flows through today's
import path unchanged. If that constraint starts to strain, the answer is a small refactor of the
form's entry point, never a parallel importer.

### 1.1 Ingestion and preview (deterministic, no AI)

Accept `.xlsx` in addition to `.csv`. Extract headers, sample rows, and sheet structure into a
normalized preview payload. Useful on its own and it's the model's input.

### 1.2 Mapping proposal

A task sends the preview to the provider and writes an `AIProposal` of kind
`bom_import_mapping`: source column → canonical header from `bom/csv_headers.py`, per-field
confidence, and unmapped columns.

### 1.3 Review UI

A confirmation screen showing proposed mappings against real sample rows, every field editable.
Low-confidence mappings flagged. This screen is the safety mechanism from principle 2 — it is not
optional and not skippable.

### 1.4 Commit

Accepted mapping renders a canonical CSV, which is fed to the existing form. Errors surface
through the existing `add_form_error_messages` path.

### 1.5 Structural inference

The part that removes the most manual work, layered on once 1.1–1.4 are solid:

- Infer `level` from indentation or reference-designator grouping when no level column exists
  — currently `_parse_level` hard-fails without one unless a parent part is supplied
- Expand reference-designator ranges (`R1-R8, R12`) into counts
- Normalize manufacturer name variants against existing `Manufacturer` rows for the
  organization, proposing matches rather than silently creating duplicates

**Exit criteria:** unmodified Altium, KiCad, and OrCAD exports import without hand-editing;
those exports are added to `bom/test_files/` with expected mappings; the eval suite scores
mapping accuracy and the number is recorded; a rejected proposal leaves zero database side effects.

---

## Phase 2 — API and MCP

### 2.1 Token authentication

Organization-scoped, role-aware API tokens validated through the existing `BomPerms` and
`OrganizationPermissionBackend`. A token that reads BOMs but cannot release revisions is the
default posture. Tokens hashed at rest, shown once, revocable, with last-used tracking.

### 2.2 REST API

`django-ninja` over DRF: lighter, and its schema model maps cleanly onto the `Offer` /
`PriceBreak` dataclasses that already exist in the sourcing module. Read-only first — parts,
revisions, BOM tree, where-used, sourcing match — every endpoint a thin call into `bom/services/`.

Versioned under `/api/v1/`.

### 2.3 MCP server

ASGI app mounted alongside Django, sharing the ORM and calling the same services. Read-only
tools to start: `search_parts`, `get_bom`, `where_used`, `price_bom`.

Instrument tool-call frequency from day one — that telemetry is the input to the embedded-chat
decision.

### 2.4 Purchase package

The "generate orders from BOMs" payoff, and mostly assembly of existing pieces: group
`PartRevision.flat()` by seller, apply `SellerPart.order_quantity()` at the target build
quantity, emit per-distributor line items with price breaks. Ships as a service, an endpoint, an
MCP tool, and an export.

Output is a cart deep-link, PO CSV, or RFQ — never a placed order.

### 2.5 Narrow write operations

Create part, add subpart, upload BOM. Each routed through its existing form, each
confirmation-gated, none generic.

### 2.6 Workflow tool documentation

n8n and Make example workflows against the REST API. No custom connectors — generic HTTP nodes
are sufficient, and bespoke nodes are maintenance debt for no reach.

**Exit criteria:** a scoped token drives a full BOM read and a purchase package over HTTP; the
same operations work from an MCP client; an attempt to cross organizations with a valid token
fails in tests; a published n8n example runs end to end.

---

## Shelved

Not started until Phases 0–2 are done, listed roughly in expected-value order. Each becomes
substantially cheaper once the foundations exist.

- Property extraction from datasheets and MPNs into `PartRevisionProperty`
- Part-class suggestion for semi-intelligent numbering
- Duplicate detection via embeddings over `searchable_synopsis`
- Alternate / second-source cross-referencing, validated through the sourcing providers
- Natural-language search compiled to a constrained filter DSL
- ECO and change summaries from revision diffs plus `where_used_full()`
- Per-class synopsis templates, resolving the `generate_synopsis` TODO
- Embedded chat — scoped by the Phase 2 tool telemetry
