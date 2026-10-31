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

This is a deliberate narrowing of ai-architecture §2 ("AI proposes, Django's forms commit"),
and the architecture doc should be amended to say so.

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

### 7. Tool surface

Read (scope `read`):

| tool | arguments | returns |
|---|---|---|
| `get_organization` | | number scheme, part-number lengths, currency, part classes with codes, sourcing provider configured? |
| `search_parts` | `query`, `part_class?`, `limit=25` | matches on part number, synopsis, MPN, manufacturer |
| `get_part` | `part_number`, `revision?` | part, latest/requested revision, properties, manufacturer parts with seller parts |
| `get_bom` | `part_number`, `revision?`, `flat=false`, `quantity=1` | indented or flat `PartBom`: subparts, counts, references, DNL, extended cost |
| `where_used` | `part_number` | assemblies containing the part |
| `price_bom` | `part_number`, `quantity` | per-line optimal seller and totals, from `PartRevision.flat()` + `SellerPart.optimal()` |
| `lookup_sourcing` | `manufacturer_part_number` | live offers via the organization's configured provider (BYOK); never persisted |
| `export_bom_csv` | `part_number`, `revision?` | the same CSV the UI exports |

Write (scope `write`):

| tool | form | notes |
|---|---|---|
| `create_part` | `PartForm*` + `PartRevisionForm` | `part_class`, `description`, optional `number_item`/`number_variation`, optional `manufacturer`/`mpn`, optional properties; returns assigned part number |
| `update_part_revision` | `PartRevisionForm` | draft revisions only; description and properties |
| `create_part_revision` | `PartRevisionNewForm` | copies the latest revision as a new draft |
| `add_subpart` | `AddSubpartForm` | `assembly_part_number`, `subpart_part_number`, `count`, `reference`, `do_not_load` |
| `update_subpart`, `remove_subpart` | `SubpartForm` | `remove_subpart` is `destructive_hint` |
| `add_manufacturer_part` | `ManufacturerPartForm` | creates `Manufacturer` by name if absent |
| `add_seller_part` | `SellerPartForm` | `unit_cost`, `minimum_order_quantity`, `lead_time_days`, `nre_cost`; creates `Seller` by name if absent |
| `upload_bom` | `BOMCSVForm` | CSV text for a parent part; the bulk path for a CAD export |

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

**Phase 2: usable by customers**
django-oauth-toolkit authorization server and the dual `TokenVerifier`; remaining write tools;
a docs page with client setup snippets; telemetry dashboard; amend ai-architecture §2.
Exit: a user adds IndaBOM as a claude.ai custom connector with Google sign-in and no copied token.

**Phase 3**
`purchase_package`, resources and prompts, and the REST API if integrator demand appears. The
service layer built in Phase 1 means the REST API is a second thin surface, not a prerequisite.
