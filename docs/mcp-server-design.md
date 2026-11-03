# MCP Server Design

Initial design for an MCP server over django-bom, hosted by IndaBOM. Builds on
[ai-architecture.md](ai-architecture.md) and replaces the sketch in Phase 2 of
[ai-implementation-plan.md](ai-implementation-plan.md) with concrete decisions.

Driving use case: an engineer working in an AI client (Claude Code, Claude Desktop, Cursor)
designing a new piece of hardware asks the agent to create parts, attach manufacturer parts and
pricing, assemble BOMs, and price a build, with the agent calling IndaBOM directly. The first such
engineer is the maintainer, but every decision below is made for an arbitrary organization.

## Decisions

### 1. The MCP server lives in django-bom; IndaBOM only mounts and deploys it

`bom/mcp/` is a module of the reusable app, so standalone django-bom users get it too.
IndaBOM contributes an ASGI entrypoint, a Cloud Run service, and the OAuth authorization server.

```
bom/
  services/            organization-scoped functions; the only thing tools call (ai-architecture §1)
  mcp/
    server.py          MCPServer instance, tool registration, token verification
    tools_read.py      search_parts, get_part, get_bom, where_used, price_bom, ...
    tools_write.py     create_part, add_subpart, add_manufacturer_part, ...
    auth.py            TokenVerifier: Bearer token -> (user, organization, scopes)
    serializers.py     compact dict renderings of Part / PartRevision / PartBom for the model
  models.py            + APIToken
```

### 2. Official `mcp` SDK, Streamable HTTP, stateless, JSON responses

Use the official Python SDK (`mcp` 2.x, `mcp.server.mcpserver.MCPServer`), not a third-party
Django wrapper. The SDK is the reference implementation of the protocol and the thing clients are
tested against; `django-mcp-server` was considered and rejected as an extra dependency over a
small surface.

Transport is Streamable HTTP with `stateless_http=True, json_response=True`. Cloud Run has no
sticky sessions and scales to zero, so no server-side session state and no long-lived SSE
streams. Every request carries its own Bearer token and is independently authenticated.

Tools are plain `def` functions. The SDK runs sync handlers on a worker thread, which is exactly
what the Django ORM needs (thread-local connections); no `sync_to_async` plumbing. Close the DB
connection at the end of each tool call (`django.db.close_old_connections`) since there is no
request/response cycle doing it for us.

### 3. Separate ASGI process, same image

IndaBOM's web app stays on gunicorn WSGI. The MCP server is a second Cloud Run service built
from the same image with a different command:

```
uvicorn indabom.mcp_asgi:application --host 0.0.0.0 --port $PORT
```

`indabom/mcp_asgi.py` is a Starlette app that calls `django.setup()`, mounts
`mcp.streamable_http_app()` at `/mcp`, and runs `mcp.session_manager.run()` in its lifespan
(a mounted sub-app's lifespan never runs on its own, so the host app must own it).
Served at `mcp.indabom.com/mcp`, sharing Cloud SQL with the web service.

Why not mount inside the main app: that would move the whole site to ASGI serving for the sake of
one endpoint. Isolating it means the MCP service can be deployed, scaled, and rolled back without
touching the site, and an MCP outage cannot take the site down.

### 4. Authentication: personal access tokens first, OAuth second

Both resolve to the same thing: an authenticated `User`, their `UserMeta.organization`, and a
scope set. Tools never see tokens; they receive an `Actor(user, organization, scopes)`.

**Phase A: `APIToken`** (new model in `bom/models.py`)

| field | notes |
|---|---|
| `user`, `organization` | token acts as this user in this organization; role comes from `UserMeta.role` |
| `name` | user-supplied label ("Claude Code laptop") |
| `prefix` (8 chars), `hashed_key` (sha256) | plaintext shown once at creation, never stored |
| `scopes` | list of `read`, `write`; new tokens default to `read` only (see §5) |
| `expires_at`, `last_used_at`, `revoked_at` | revocable, auditable |

Managed from a new API tab in `bom_settings`. Client config is a Bearer header:

```
claude mcp add --transport http indabom https://mcp.indabom.com/mcp --header "Authorization: Bearer <token>"
```

This works today for Claude Code, Cursor, Claude Desktop via `mcp-remote`, and any client that
can set a header. It does not work for claude.ai's custom-connector UI, which only offers OAuth
or no-auth, hence Phase B.

**Phase B: OAuth 2.1 via django-oauth-toolkit** (3.4+), installed in IndaBOM's web app as the
authorization server. DOT 3.4 ships what the MCP authorization spec needs: RFC 8414 server
metadata, RFC 9728 protected-resource metadata, RFC 7591 dynamic client registration, and
RFC 8707 resource indicators. The MCP service's `AuthSettings` points `issuer_url` at
`www.indabom.com` and `resource_server_url` at `https://mcp.indabom.com/mcp`. The
`TokenVerifier` accepts either an `APIToken` or a DOT `AccessToken` (same database, direct lookup,
no introspection call). Users then add IndaBOM in claude.ai's connector UI and sign in with their
normal Google login.

### 5. Authorization: existing roles, narrowed by token scope

Two scopes, `read` and `write`, stored as a list so finer scopes (`parts:write`,
`sourcing:write`) can be added later as data rather than a migration. One `write` scope is enough
for v1: role already separates part edits from schema and member management, and nobody has
asked for an agent that may edit BOMs but not pricing.

The effective permission for a tool call is `role permission AND token scope`:

- A `read`-only token on an Admin account can only read.
- A `write` token on a Viewer account can still only read, because `BomPerms` denies writes.
- Every write tool checks `actor.user.has_perm(BomPerms.X, actor.organization)` exactly as the
  views do through `OrganizationPermissionBackend`.

Every query is built with `Model.objects.for_user(actor.user)` / `.available_to(organization)`
inside `bom/services/`; the MCP layer holds no queries of its own. The cross-organization test
from the implementation plan (valid token, foreign part number, expect not-found) is mandatory.

### 6. Writes commit through the existing forms; the MCP client is the confirmation gate

This is a deliberate narrowing of the original ai-architecture §2 proposal model; §2 now describes
what shipped.

In the proposal model, the human reviews an `AIProposal` in the web UI. That fits features where
the model acts on untrusted input (an uploaded spreadsheet) on behalf of a user who is not in the
loop. It does not fit MCP: the user is sitting in the client issuing instructions, the client
already shows each tool call and asks before running non-read-only tools, and a session that adds
forty subparts cannot send the user to the browser forty times.

So MCP write tools commit directly, with these constraints:

- **Forms are the only write path.** `create_part` binds `PartFormSemiIntelligent` /
  `PartFormIntelligent` + `PartRevisionForm`; `add_subpart` binds `AddSubpartForm`;
  `add_manufacturer_part` binds `ManufacturerPartForm`; `add_seller_part` binds `SellerPartForm`;
  `upload_bom` binds `BOMCSVForm`. No tool calls `.save()` on a model. Immutability, part-number
  validation, and organization scoping stay in one place.
- **Form errors become `ToolError` text**, field by field, so the model can correct and retry.
  Any other exception is logged and the model sees only a generic failure.
- **Narrow, named tools only.** No `update_object`, no raw query tool (ai-architecture §6).
- **Annotations tell the client what is safe.** Read tools carry `read_only_hint=True`;
  `remove_subpart` carries `destructive_hint=True`. Clients use these to decide what to auto-approve.
- **Lifecycle transitions are not tools in v1.** Releasing or obsoleting a revision and deleting a
  part stay in the web UI. They are rare, consequential, and the UI already explains the rules.
- **Every write is logged** with actor, tool, arguments, and resulting object ids. This is the
  audit trail the proposal model would otherwise have provided.

MCP writes now go through Changes: each write tool stages an item (or runs a one-item change), and
`apply_change` is the only path to the database, subject to the token's `apply` scope, protections and the
second-person rule for Engineering Change Orders. The "commit directly" and "Forms are the only write path"
points above still hold inside `apply_change`, which calls the same services. See
[changes-design.md](changes-design.md).

### 7. Tool surface

Read (scope `read`):

| tool | arguments | returns |
|---|---|---|
| `get_organization` | | number scheme, part-number lengths, currency, part classes and property definitions, `token_level`, whether change review is enabled |
| `search_parts` | `query=''`, `part_class_code?`, `limit=25` | matches on part number, synopsis, MPN, manufacturer |
| `get_part` | `part_number`, `revision?` | part, latest/requested revision, properties, manufacturer parts with seller parts |
| `get_bom` | `part_number`, `revision?`, `quantity=1`, `flat=false` | indented or flat BOM: lines, counts, references, DNL, seller, cost, totals |
| `price_bom` | `part_number`, `quantity`, `revision?` | cost totals at the cheapest seller per line, and the lines with no price |
| `where_used` | `part_number` | assemblies containing the part, at every level |
| `lookup_sourcing` | `manufacturer_part_number` | live offers via the organization's configured provider (BYOK); never persisted |
| `export_bom_csv` | `part_number`, `revision?`, `layout='indented'` | the CSV text the UI export produces; layout is `indented`, `single_level` or `flat` |
| `get_change`, `list_changes` | `change_id`; `status='open'`, `limit=25` | a change with its items, flags and impact; changes by status |

Write (scope `write`; every tool stages into a Change, see [changes-design.md](changes-design.md)):

| tool | arguments | notes |
|---|---|---|
| `start_change` | `title`, `rationale=''` | starts the Change that the other write tools stage into |
| `submit_change`, `withdraw_change` | `change_id` | send a draft change for review; withdraw your own |
| `apply_change` | `change_id` | needs the `apply` scope; never releases or approves |
| `create_part` | `description`, `part_class_code?`, `number_item?`, `number_variation?`, `properties?`, `property_units?`, `manufacturer?`, `manufacturer_part_number?`, `link?`, `change_id?`, `ref=''` | applies through `PartForm*` + `PartRevisionForm`; returns the assigned part number |
| `update_part_revision` | `part_number`, `change_id`, `description?`, `properties?`, `revision?` | Draft revisions only, through `PartRevisionForm`; a Released or In Review revision returns an error that says to call `revise_part` |
| `revise_part` | `part_number`, `change_id` | the next Draft from the latest Released or In Review revision, as the UI's Revise; refused when a newer Draft exists |
| `add_subpart` | `assembly_part_number`, `subpart_part_number`, `count=1`, `reference=''`, `do_not_load=false`, `assembly_revision?`, `change_id?` | `AddSubpartForm` |
| `update_subpart`, `remove_subpart` | `assembly_part_number`, `subpart_part_number`, ..., `change_id?` | `SubpartForm`; `remove_subpart` is `destructive_hint` |
| `upload_bom` | `assembly_part_number`, `csv_text`, `change_id`, `mode='add'` | `mode` is `add` or `replace`; previews with `BOMCSVForm` and stages one subpart item per changed line; the bulk path for a CAD export |
| `add_manufacturer_part` | `part_number`, `manufacturer`, `manufacturer_part_number`, `link?`, `datasheet_url?`, `change_id?`, `ref=''` | `ManufacturerPartForm`; creates `Manufacturer` by name if absent |
| `add_seller_part` | `manufacturer_part_id`, `seller`, `unit_cost`, `currency?`, `minimum_order_quantity=1`, `lead_time_days?`, `nre_cost='0'`, ..., `change_id?` | `SellerPartForm`; creates `Seller` by name if absent |

`upload_bom` stages lines, not parts. The CSV is run through `BOMCSVForm` in a rolled-back transaction, and each
added, changed or removed line becomes an `add_subpart`, `update_subpart` or `remove_subpart` item, so a reviewer
accepts or rejects lines one at a time and a Released assembly follows the ECO path like any other subpart edit. A
CSV that has row errors, or that needs a part or revision that does not exist yet, stages nothing and the error
says why; create the parts first.

Explicit part numbers: `create_part` accepts optional `number_item` and `number_variation`,
exactly as `PartFormSemiIntelligent` does ("auto generated if blank"), and requires `number_item`
under the intelligent scheme where nothing can be auto-assigned. The form's format and uniqueness
checks apply, so a collision is a tool error. The tool description states that these arguments are
only for a number the user supplied or that already exists in another system; the model never
invents one (ai-architecture §4). Variation is auto-assigned unless given.

Deferred: `purchase_package` (implementation plan 2.4), MCP resources (`bom://parts/{number}`),
and prompts. Tools alone cover the use case; resources and prompts are added once tool telemetry
shows what clients reach for.

Output shaping: every tool returns a small dict (`part_number`, `revision`, `synopsis`,
`configuration`, ids), never a model dump. `get_bom` on a large assembly truncates with a count
and a `next` cursor rather than returning thousands of lines into the context window.

### 8. Telemetry

Each tool call emits one structured log line: tool, organization id, actor id, scopes,
duration, success/error class, and for writes the created ids. Cloud Logging already feeds
BigQuery for IndaBOM, so no new table. This is the dataset ai-architecture names as the input
to the embedded-chat decision.

### 9. Testing

- Services: unit tests in `bom/tests.py` using `helpers.create_user_and_organization`.
- Tools: tests call the tool functions directly with a fake `Actor`; no HTTP needed.
- Protocol: one test spins up the Starlette app with the SDK's in-memory client, lists tools,
  calls `get_organization` with a valid token and expects 401 without one.
- Cross-organization isolation test for every read tool.

### 10. Subscription gating (IndaBOM only)

django-bom never gates MCP access. `BOM_CONFIG` gains an `mcp_access_check` callable,
`(organization, user, scopes) -> str | None`, defaulting to always-allow. IndaBOM's setting
returns an error message pointing at the billing page when:

- the token has `write` scope and `organization.subscription` is not `SUBSCRIPTION_TYPE_PRO`,
  unless `user.is_staff`.

Free organizations can create tokens and use every read tool; that is the cheapest way to show the
value and the funnel into Pro. The verifier runs the check on every request, so a lapsed
subscription stops writes immediately without revoking tokens. `organization.subscription` is
already maintained by the Stripe webhook code in `indabom/stripe.py`.

## Phases

**Phase 1: usable by the maintainer**
`bom/services/` with the read paths; `APIToken` model and settings tab; MCP service with all read
tools plus `create_part`, `add_subpart`, `add_manufacturer_part`, `add_seller_part`; IndaBOM
ASGI entrypoint and Cloud Run service in `infra/`; connected from Claude Code via header.
Exit: a new product's parts and BOM created entirely from a chat session and visible in the UI.

**Phase 2: usable by customers** (spec below)
django-oauth-toolkit authorization server and the dual `TokenVerifier`; remaining write tools;
a docs page with client setup snippets; adoption milestones in the funnel export; amend
ai-architecture §2. The BigQuery log sink and dashboard are dropped for now (see Phase 2 spec).
Exit: a user adds IndaBOM as a claude.ai custom connector with Google sign-in and no copied token.

**Phase 3**
`purchase_package`, resources and prompts, and the REST API if integrator demand appears. The
service layer built in Phase 1 means the REST API is a second thin surface, not a prerequisite.

## Phase 2 spec

Goal: onboarding is one command and a browser sign-in.

```
claude mcp add --transport http indabom https://mcp.indabom.com/mcp
```

Then `/mcp`, choose indabom, Authenticate. The browser opens IndaBOM's normal Google sign-in, then a
consent page where the user picks what the assistant may do. claude.ai custom connectors work the same
way. Pasted personal tokens keep working unchanged.

### Split

- **IndaBOM** hosts the authorization server: django-oauth-toolkit (latest 3.x) installed in the web
  app, its views under `/o/`, RFC 8414 and RFC 9728 metadata at the domain root.
- **django-bom** owns everything a self-hoster could reuse: the consent template, the token verifier
  that accepts both kinds of token, connection records, the setup docs page and the new tools.
  django-bom never imports `oauth2_provider` unless it is in `INSTALLED_APPS`, and does not depend on
  it in `pyproject.toml`.

### Authorization server settings (IndaBOM)

- Authorization code with PKCE S256 only; refresh tokens on; public clients
  (`token_endpoint_auth_method: none`). No implicit, password or client-credentials grants.
- Open dynamic client registration (`AllowAllDCRPermission`) and Client ID Metadata Documents, so
  Claude Code, Claude Desktop, claude.ai and Cursor register themselves.
- Loopback redirects on any port (`ALLOW_LOCALHOST_LOOPBACK`) plus https redirects; nothing else.
- Scopes `read`, `write`, `apply`, the same names and meanings as `APIToken` scopes.
- Tokens are bound to the MCP resource (RFC 8707); the MCP service validates the audience.
- Abuse limits: a per-IP throttle on `/o/register/`, and a `cleanup_oauth_clients` management command
  that deletes dynamically registered applications with no grants or tokens after 7 days.
- `/o/authorize/` requires login, which is the existing Google sign-in.

### Consent page (django-bom)

django-bom ships `templates/oauth2_provider/authorize.html`, so it overrides the toolkit's template when
`bom` is listed before `oauth2_provider`. It shows the client's name, the organization, and one choice:

| Choice | Scopes granted |
|---|---|
| Read only | `read` |
| Propose changes for review | `read write` |
| Propose and apply changes | `read write apply` |

Choices the user's role or the host flags cannot use are hidden: a Viewer sees only Read; Apply needs
`mcp_apply_enabled`; writes need `mcp_access_check` to pass (on IndaBOM, Pro). The default selection is
the narrowest of the scopes the client requested and the user's ceiling.

### Connections are `APIToken` rows

The first time an OAuth access token is used, the verifier gets or creates an `APIToken` for that user,
organization and OAuth client (`APIToken.oauth_client_id`, new, blank for pasted tokens), named after
the client, with the granted scopes. Its `hashed_key` is random and never shown, so it can't be used as
a pasted token. Everything downstream keeps working unchanged: `Actor`, `Change.api_token`, agent
badges in History, `last_used_at`, and the Settings → API Tokens tab, which lists connections beside
personal tokens. Revoking a connection there also revokes that client's OAuth access and refresh tokens
for the user, and a revoked connection rejects its OAuth tokens.

### MCP service (django-bom)

- Replace `BearerTokenMiddleware` with the SDK's `token_verifier` and
  `AuthSettings(issuer_url, resource_server_url, required_scopes=['read'], validate_token_resource=True)`
  from `BOM_CONFIG['mcp_oauth_issuer_url']` and `BOM_CONFIG['mcp_resource_url']`. The SDK then publishes
  RFC 9728 metadata and answers unauthenticated requests with the 401 that makes clients offer
  Authenticate.
- The verifier accepts a pasted `ibom_` token (resource is the MCP URL) or a toolkit access token
  (looked up in the shared database, no introspection call; resource is the token's audience). Both
  return an SDK `AccessToken` whose claims carry the `APIToken` id; `Actor` resolves from it.
- Without `mcp_oauth_issuer_url` (self-hosted without the toolkit), only pasted tokens are accepted
  and no OAuth metadata is published.
- `mcp_access_check` and token-level enforcement are unchanged.

Implementation notes (where the shipped code differs from the text above):

- `BearerTokenMiddleware` is kept for hosts without `mcp_oauth_issuer_url`: the SDK's auth settings need an
  issuer, so that mode stays pasted-tokens-only with no metadata. `Actor` reads either path.
- A toolkit token with no RFC 8707 resource is refused rather than treated as unrestricted, so a token is only
  ever accepted when it names this MCP URL.
- `APIToken.revoke()` itself deletes the client's toolkit access and refresh tokens for the user, so every
  revocation path (Settings, shell, admin) behaves the same. A grant made after a revocation creates a fresh
  connection row; a grant made before it stays rejected.
- The protected resource metadata route lives at the domain root, outside `/mcp`, so hosts register
  `bom.mcp.asgi.mcp_routes()` instead of a single `Route`.
- The consent page computes its choices in a template tag (`bom_oauth`), not in the toolkit view.

### Remaining tools

| Tool | Kind | Notes |
|---|---|---|
| `export_bom_csv` | read | `part_number`, `revision?`, `layout`: indented, single_level or flat; the same CSV as the UI |
| `update_part_revision` | write | Draft only: description and properties; an immutable revision returns an error that says to call `revise_part` |
| `revise_part` | write | the next Draft from a Released or In Review revision (the UI's Revise) |
| `upload_bom` | write | `assembly_part_number`, `csv_text`, `mode` add or replace; previews through the import service and stages the result |

Write tools go through Changes like the existing ones: a propose token stages items for review, an apply
token may apply. Agents still never release or approve.

### Setup docs page (django-bom)

A "Connect an AI assistant" page, linked from Help and from Settings → API Tokens: the MCP URL, then
per-client steps for Claude Code, claude.ai, Claude Desktop and Cursor (OAuth first, pasted token as the
fallback), what each access level allows, and how to revoke.

### Adoption milestones instead of a log sink (IndaBOM)

The per-call log lines stay in Cloud Logging (30-day retention, searchable in Logs Explorer). The daily
funnel export adds two milestones per user, `first_agent_connection_at` (first `APIToken`) and
`first_agent_change_at` (first Change with an `api_token`), to `indabom_funnel.user_milestones`. A
BigQuery log sink is worth adding only if read-tool history beyond 30 days is needed.

### ai-architecture §2

Amend to describe what shipped: agents write Changes through the same engine people use, staged and
applied through the existing forms, with token levels as the ceiling and protections as the floor; there
is no separate `AIProposal` model.

