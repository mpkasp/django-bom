# BOM
![](https://github.com/mpkasp/django-bom/workflows/Django%20CI/badge.svg)

BOM is a simple Django app to manage a bill of materials. It supports multiple part numbering schemes, tracking
component sourcing information, estimates costs, and contains smart integrations with Mouser to pull in the latest
component pricing and Google Drive for part file management. BOM is written in Python 3 and Django.

[See a live example](https://www.indabom.com).

BOM can be added to an existing (or new) Django project, or stand alone on its own, which can be more convenient if you're interested in tweaking the tool. 

If you already have a django project, you can skip to [Add Django Bom To Your App](#add-django-bom-to-your-app), otherwise [Start From Scratch: Add to new Django project](#start-from-scratch-add-to-a-new-django-project) to add it to a new django project, or [Start From Scratch: Use as standalone Django project](#start-from-scratch-use-as-a-standalone-django-project).

## Table of contents
   * [Start From Scratch: Add to new Django project](#start-from-scratch-add-to-a-new-django-project)
   * [Add Django Bom To Your App](#add-django-bom-to-your-app)
   * [Start From Scratch: Use as standalone Django project](#start-from-scratch-use-as-a-standalone-django-project)
   * [Customize Base Template](#customize-base-template)
   * [Importing and exporting](#importing-and-exporting)
   * [Integrations](#integrations)
   * [MCP Server](#mcp-server)
   * [Contributing](#contributing)
   * [Installation pitfalls](#installation-pitfalls)
   
## Start From Scratch: Add to a new Django project
1. Create and activate a virtual environment (Python 3.10+):
```
python -m venv .venv
source .venv/bin/activate   # Windows: .venv\Scripts\activate
```

2. Install Django and set up your project:
```
pip install django
django-admin startproject mysite
cd mysite
python manage.py migrate
python manage.py createsuperuser # make a user for your development environment
```

3. Continue on to [Add Django Bom To Your App](#add-django-bom-to-your-app).

## Add Django Bom To Your App
django-bom is a [reusable django application](https://docs.djangoproject.com/en/1.11/intro/reusable-apps/). If you don't already have a django project, you can follow some quick steps below to get up and running, or read about creating your first django app [here](https://docs.djangoproject.com/en/1.11/intro/tutorial01/).

```
pip install django-bom
```

1. Add "bom" to your INSTALLED_APPS setting like this::

```
INSTALLED_APPS = [
    ...
    'bom',
    'social_django', # to enable google drive sync in bom
    'djmoney', # for currency
    'djmoney.contrib.exchange', # for currency
    'materializecssform',
]
```

2. Update your URLconf in your project urls.py like this::

```
path('bom/', include('bom.urls')),
```

And don't forget to import include:

```
from django.conf.urls import include
```

3. Update your settings.py to add the bom context processor `'bom.context_processors.bom_config',` to your TEMPLATES variable, and create a new empty dictionary BOM_CONFIG.

```
TEMPLATES = [
    {
        ...
        'OPTIONS': {
            'context_processors': [
                ...
                'bom.context_processors.bom_config',
            ],
        },
    },
]
```

and

```
BOM_CONFIG = {'standalone_mode': False}
```

4. Run `python manage.py migrate` to create the bom models.

5. Start the development server `python manage.py runserver` and visit http://127.0.0.1:8000/admin/
   to manage the bom (you'll need the Admin app enabled).

6. Visit http://127.0.0.1:8000/bom/ to begin.

   
## Start From Scratch: Use as a standalone Django project
1. Clone the repo and create a virtual environment:
```
git clone https://github.com/mpkasp/django-bom.git
cd django-bom
python -m venv .venv
source .venv/bin/activate   # Windows: .venv\Scripts\activate
```

2. Install dependencies and start the server:
```
pip install -e .
cp bom/local_settings.py.example bom/local_settings.py
python manage.py migrate
python manage.py runserver
```

## Customize Base Template
The base template can be customized to your pleasing. Just add the following configuration to your settings.py:

```
BOM_CONFIG = {
    'base_template': 'base.html',
}
```

where `base.html` is your base template.

## Theming and style

django-bom styles itself with Materialize plus a small `style.css` whose colors are CSS custom
properties (`--color-primary`, `--color-text-grey`, ...). A host project overrides them in a
stylesheet loaded after `bom/css/style.css`; no template or selector overrides are needed for a
re-color. Patterns, tokens and the checklist for UI changes are in
[docs/style-guide.md](docs/style-guide.md).

## Importing and exporting
Every export is a valid import: edit a CSV that BOM gave you and upload it on the **Import** page (`/import/`).
Rows that match existing records update the fields they provide, blank cells leave values alone, and nothing is
deleted except BOM lines you replace. Every import is previewed first and saved only when you confirm it.

| Type | Row identity | Creates | Export |
|---|---|---|---|
| Parts | `part_number`, else `manufacturer_part_number` | parts, revisions, manufacturer parts, one vendor price per row | Export all parts |
| Vendor prices | manufacturer part + `seller` + `minimum_order_quantity` | price breaks and vendors, never parts | Export all vendor prices |
| Bill of materials | `part_number` or `manufacturer_part_number` per line, nested by `level` | parts and revisions; syncs each listed sub-assembly's lines | indented BOM export |

The Import page detects the type from the file's columns or lets you pick one; `/import/?type=parts|prices|bom`
preselects it, and `&parent=<part number>` fills in the assembly a BOM import goes into. The former `upload-parts/`, `upload-bom/` and `upload-parts-help/` URLs redirect there.

## Integrations
### Mouser Integration
For part matching, make sure to add your Mouser api key. You can get your key [here](https://www.mouser.com/MyMouser/MouserSearchApplication.aspx).

### Google Drive Integration
Make sure to add the following to your settings.py:
```
AUTHENTICATION_BACKENDS = (
    'social_core.backends.google.GoogleOpenId',
    'social_core.backends.google.GoogleOAuth2',
    'social_core.backends.google.GoogleOAuth',
    'django.contrib.auth.backends.ModelBackend',
)

SOCIAL_AUTH_GOOGLE_OAUTH2_SCOPE = ['email', 'profile', 'https://www.googleapis.com/auth/drive', 'https://www.googleapis.com/auth/plus.login']
SOCIAL_AUTH_GOOGLE_OAUTH2_AUTH_EXTRA_ARGUMENTS = {
    'access_type': 'offline',
    'approval_prompt': 'auto'
}
``` 
And if you're using https on production add:
```
SOCIAL_AUTH_REDIRECT_IS_HTTPS = not DEBUG
```

### FIXER
Fixer.io is used to handle exchange rate calculations. This is helpful if you may be purchasing parts from another currency (especially via Mouser) and you still need to estimate your part costs.

To set this up you just need to add your API key to local_settings.py as shown in the example.

To update rates, migrate and run `python manage.py update_rates`. Some day we will need to add a (celerybeat?) task to update rates on a schedule. Explained more [here](https://github.com/django-money/django-money#working-with-exchange-rates).

## MCP Server
django-bom ships an [MCP](https://modelcontextprotocol.io) server so AI clients (Claude Code, Claude Desktop, Cursor, ...)
can read and build BOMs in a user's organization. Design notes live in [docs/mcp-server-design.md](docs/mcp-server-design.md).

Run it standalone with an ASGI server; Django is served alongside it:
```
uv run uvicorn bom.asgi:application
```
The server listens at `/mcp` (Streamable HTTP, stateless). In a host project, add `bom.mcp.asgi.mcp_routes()` to the
host's Starlette routes and enter `bom.mcp.asgi.mcp_lifespan` from the host app's lifespan.

Every request needs a bearer token or an OAuth sign-in. The in-app page `/help/ai/` ("Connect an AI assistant")
walks users through both.

**Sign in (recommended).** When the host runs an OAuth authorization server, connecting is one command and a browser
sign-in; the user then picks what the assistant may do (Read, Edit, or Edit and release):
```
claude mcp add --transport http indabom https://your-host/mcp
```
Then run `/mcp` in Claude Code, choose indabom and Authenticate. claude.ai, Claude Desktop and Cursor connect the same
way. Each connected app appears in Settings > Connected apps; revoking it also revokes its OAuth tokens.

To enable it, install [django-oauth-toolkit](https://django-oauth-toolkit.readthedocs.io) in the host (django-bom does
not depend on it), list `bom` before `oauth2_provider` in `INSTALLED_APPS` so the consent template in
`bom/templates/oauth2_provider/authorize.html` is used, and set two keys in `BOM_CONFIG`:
- `mcp_oauth_issuer_url`: the authorization server's base URL, e.g. `https://www.example.com`.
- `mcp_resource_url`: the public MCP URL, e.g. `https://mcp.example.com/mcp`. Tokens must be issued for this resource
  (RFC 8707; tokens with no resource are refused), and the MCP server publishes its RFC 9728 metadata at `/.well-known/oauth-protected-resource/mcp`.

Register the routes with `bom.mcp.asgi.mcp_routes()`. The toolkit needs the scopes `read`, `write` and `release`.
Without `mcp_oauth_issuer_url`, only pasted tokens are accepted and no OAuth metadata is published.

**Paste a token.** Create one under Settings > Connected apps > Personal access tokens (the plaintext is shown once), or issue one for a user from
the command line (the plaintext is printed once):
```
uv run python manage.py create_api_token <username> --name "Claude Code" --scopes read,write
```
A token acts as that user in their organization: role permissions still apply, and a `read`-only token can never write. Use `--scopes read,write,release` for a token that may also release revisions and approve change orders.
Connect a client that can set headers with the token in the `Authorization` header, e.g.:
```
claude mcp add --transport http indabom http://localhost:8000/mcp --header "Authorization: Bearer <token>"
```
Hosts can gate access (e.g. by subscription) with `BOM_CONFIG['mcp_access_check']`, a callable or its dotted
path, `(organization, user, scopes) -> error message or None`, that runs before every write.

### Change orders and token levels
Agents edit through the same services as the web app. Edits to Draft revisions are saved at once and recorded in the
part's history with who made them and which agent. A Released revision is changed with `revise_part` (the next Draft)
and then editing that Draft. A *change order* is a title, a reason and a list of working Draft revisions that are
reviewed and released together (design: [docs/change-orders-design.md](docs/change-orders-design.md)).

Token scopes set the level: `read`; `read,write` (Edit) can edit Drafts, revise, and build, submit and withdraw change
orders with `create_change_order`, `add_to_change_order`, `remove_from_change_order`, `submit_change_order`,
`withdraw_change_order`, `get_change_order` and `list_change_orders`; `read,write,release` (Edit and release) can also
call `release_revision`, `approve_change_order` and `reject_change_order`. The agent acts as its person, so role
permissions and the approval rules apply: it cannot approve a change order its person wrote or edited unless they are
the organization's only member, and the Admin override exists only in the web app.

Submitting a change order locks its revisions (In Review). The page at `/changes/<number>/` shows, per revision, what
differs from the revision it replaces, the unit-cost effect at 100 units and the Released assemblies that use the
replaced revision. Approving releases all of them together, children before parents, or none; rejecting sends the
change order back with a reason, its revisions become Drafts again, and the same change order is submitted again;
withdrawing leaves the Drafts as Drafts. The author, and anyone whose edits touched its revisions, cannot approve it
unless they are the only member of the organization or an Admin overriding in the web app (recorded).

Hosts can switch the feature with `BOM_CONFIG['change_orders_enabled']` (a callable of the organization, its dotted
path, or a bool; default on). Without it a Released part is revised and the new revision released directly. Set
`BOM_CONFIG['site_url']` so URLs returned to MCP clients are absolute.

Admins can require approval to release in Settings > Organization > Release approval (when the host enables change
orders). With approval required and more than one active member, the Release dialog offers Add to change order
instead of Release for Editors; an Admin may release directly by confirming an override that History records.

After a change order is approved, Draft parent assemblies are moved to the new revision automatically and the page's
**Cascade** panel offers a new Draft revision of each Released parent that still uses the old one.

### Editing, Revise, Release and History
Draft revisions are edited directly on the revision's **Edit** page (description, properties, BOM and manufacturer
parts). On a Released or In Review revision the primary button is **Revise**, which makes the next Draft. **Release**
opens a dialog with the diff against the previous revision, the unit cost before and after and the assemblies that use
the part (Draft ones follow automatically, Released ones can be revised with checkboxes), and releases everything in one
transaction. **Add to change order** in that dialog puts the Draft in a change order instead; approving the change order releases
its revisions. Every saved edit and release is recorded as an unnumbered Change on the part page's **History** tab and
under the **History** filter at `/changes/`; the **Change orders** nav item counts the ones under review. A status strip under the part header says where the revision stands, and **Compare with rev N** opens a diff against the previous revision. Design: [docs/editing-design.md](docs/editing-design.md).

## Contributing

Contributions welcome! Before contributing your work please read the [contributing readme](https://github.com/mpkasp/django-bom/blob/master/CONTRIBUTING.md).

Also reach out to mike@indabom.com to discuss features, and join our slack channel.

## Installation Pitfalls

### Windows
#### Sqlite
You may get an error during your `pip install -r requirements.txt` related to sqlite. This may be fixed by installing Visual C++ for python...

#### Cryptography
Sometimes you'll have issues installing cryptography, if this is the case you may just need to set up some environment variables. This [stackoverflow](https://stackoverflow.com/questions/46288737/error-while-installing-sqlite-using-pip-on-python-2-7-13) may help.
