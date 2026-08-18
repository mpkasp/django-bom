# Lithium BoM UI inventory

Baseline captured from templates and styles during the Tailwind v4 migration.
Live product screenshots were not taken in this environment (no seeded production data). Use this inventory plus print CSS review as the pre-migration record.

## Stack (before)

- Django templates, RTL (`lang="fa" dir="rtl"`)
- Materialize CSS v1.0 + jQuery 3.4.1
- `django-materializecss-form` (118 call sites / 25 templates)
- TableSorter, TreeTable, Jalali date picker, Materialize autocomplete

## Pages by complexity

| Page | Template | Usage | Complexity | Notes |
|------|----------|-------|------------|-------|
| Dashboard / part list | `dashboard.html` | Highest | High | Search, bulk actions, dense table, print |
| Part info | `part-info.html` | Highest | High | Tabs, BOM tree, sourcing, print |
| Create / edit part | `create-part.html`, `edit-part.html` | High | High | Multi-form + `part-revision-display.html` |
| Report | `report.html` | High | High | Dates (Jalali), search, print |
| Large BOM | `components/bom-indented.html` | High | High | TreeTable + TableSorter |
| Login / signup | `registration/login.html`, `signup.html` | Auth | Low | Form pilot |
| Settings | `settings.html` | Medium | High | Tabs, part classes, users |
| Sellers / manufacturers | `sellers.html`, `manufacturers.html`, `*-info.html` | Medium | Medium | Search + tables |
| Upload | `upload-parts.html`, `upload-bom.html` | Medium | Medium | File inputs |
| Help | `help.html`, `search-help.html` | Low | Low | Static |

## Print surfaces

- Global `@media print` in `style.css` (navbar/footer hide, part-list borders, IRNazanin)
- `dashboard.css` hides search, actions, FAB
- `part-info.css` part-detail print rules

## Dual-stack rule

A page loads **either** Tailwind (`app.css` + Alpine) **or** Materialize — never both.
After this migration, all app pages use Tailwind only.

## Embed contract

`BOM_CONFIG['base_template']` still wraps the app. External bases must:

1. Provide `{% block content %}`, `{% block head %}`, `{% block script %}`, `{% block menu %}`
2. Include `{% static 'bom/css/app.css' %}` (not Materialize)
3. Keep `dir="rtl"` (or inherit from a parent that does)
4. Not load `materialize.min.css` / `materialize.min.js` on the same page as `app.css`
