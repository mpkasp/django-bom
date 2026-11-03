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
sign-in; the user then picks what the assistant may do (read only, propose changes for review, or propose and apply):
```
claude mcp add --transport http indabom https://your-host/mcp
```
Then run `/mcp` in Claude Code, choose indabom and Authenticate. claude.ai, Claude Desktop and Cursor connect the same
way. Each connected app appears in Settings > API Tokens as a *Connected app*; revoking it also revokes its OAuth tokens.

To enable it, install [django-oauth-toolkit](https://django-oauth-toolkit.readthedocs.io) in the host (django-bom does
not depend on it), list `bom` before `oauth2_provider` in `INSTALLED_APPS` so the consent template in
`bom/templates/oauth2_provider/authorize.html` is used, and set two keys in `BOM_CONFIG`:
- `mcp_oauth_issuer_url`: the authorization server's base URL, e.g. `https://www.example.com`.
- `mcp_resource_url`: the public MCP URL, e.g. `https://mcp.example.com/mcp`. Tokens must be issued for this resource
  (RFC 8707; tokens with no resource are refused), and the MCP server publishes its RFC 9728 metadata at `/.well-known/oauth-protected-resource/mcp`.

Register the routes with `bom.mcp.asgi.mcp_routes()`. The toolkit needs the scopes `read`, `write` and `apply`.
Without `mcp_oauth_issuer_url`, only pasted tokens are accepted and no OAuth metadata is published.

**Paste a token.** Create one under Settings > API Tokens (the plaintext is shown once), or issue one for a user from
the command line (the plaintext is printed once):
```
uv run python manage.py create_api_token <username> --name "Claude Code" --scopes read,write
```
A token acts as that user in their organization: role permissions still apply, and a `read`-only token can never write.
Connect a client that can set headers with the token in the `Authorization` header, e.g.:
```
claude mcp add --transport http indabom http://localhost:8000/mcp --header "Authorization: Bearer <token>"
```
Hosts can gate access (e.g. by subscription) with `BOM_CONFIG['mcp_access_check']`, a callable or its dotted
path, `(organization, user, scopes) -> error message or None`, that runs before every write.

### Changes and token levels
Every MCP edit is a *Change*: a bundle of staged items reviewed at `/changes/` and applied together
(design: [docs/changes-design.md](docs/changes-design.md)). Token scopes set the level: `read`; `read,write` can
only stage and submit changes for review (propose); `read,write,apply` can also apply them
(`create_api_token --scopes read,write,apply`). Write tools take an optional `change_id` to stage into a change from
`start_change`; without it a propose-level token submits a one-item change request and an apply-level token applies
it at once; `update_part_revision`, `revise_part` and `upload_bom` always need a `change_id`. Edits to Released or In Review revisions make the change an Engineering Change Order (ECO): applying it
creates a new Draft revision of each edited part (copying its assembly and properties) and a person's approval then
releases those revisions, children before parents, and it requires a second person to apply, as do protections in `ChangeSettings` (including a cost-delta threshold). The
review page shows validation, impact (assemblies that use the edited parts) and the unit-cost effect at 100 units,
and can rebase a stale change or revert an applied one. Hosts can switch the feature with
`BOM_CONFIG['changes_review_enabled']`, `BOM_CONFIG['mcp_apply_enabled']`, `BOM_CONFIG['eco_enabled']` and
`BOM_CONFIG['release_approval_enabled']` (callables of the organization or dotted paths; all default on), and set `BOM_CONFIG['site_url']` so change URLs
returned to MCP clients are absolute.

Admins set the protections (always on for Released and In Review revisions; optionally the primary manufacturer
part and a cost-delta threshold, and, when the host enables it, requiring approval to release) in Settings >
Organization. With approval required and more than one active member, the Release dialog offers Request approval
instead of Release; an Admin may release directly by confirming an override that History records.

After an applied Engineering Change Order, Draft parent assemblies are moved to the new revision automatically and
the review page's **Cascade** panel offers a new Draft revision of each Released parent that still uses the old one.

### Editing, Revise, Release and History
Draft revisions are edited directly on the revision's **Edit** page (description, properties, BOM and manufacturer
parts). On a Released or In Review revision the primary button is **Revise**, which makes the next Draft. **Release**
opens a dialog with the diff against the previous revision, the unit cost before and after and the assemblies that use
the part (Draft ones follow automatically, Released ones can be revised with checkboxes), and releases everything in one
transaction. **Request approval** in that dialog opens a change request instead; approving it releases the revisions.
A request that releases revisions is an Engineering Change Order. Every direct edit and release is recorded as an
unnumbered Change on the part page's **History** tab and under the **History** filter at `/changes/`; the **Requests**
nav item counts open change requests. A status strip under the part header says where the revision stands, and **Compare with rev N** opens a diff against the previous revision. Design: [docs/editing-design.md](docs/editing-design.md).

## Contributing

Contributions welcome! Before contributing your work please read the [contributing readme](https://github.com/mpkasp/django-bom/blob/master/CONTRIBUTING.md).

Also reach out to mike@indabom.com to discuss features, and join our slack channel.

## Installation Pitfalls

### Windows
#### Sqlite
You may get an error during your `pip install -r requirements.txt` related to sqlite. This may be fixed by installing Visual C++ for python...

#### Cryptography
Sometimes you'll have issues installing cryptography, if this is the case you may just need to set up some environment variables. This [stackoverflow](https://stackoverflow.com/questions/46288737/error-while-installing-sqlite-using-pip-on-python-2-7-13) may help.
