# django-bom style guide

How the django-bom UI is built and how to extend it without making it harder for a host project to
restyle. It applies to humans and AI assistants alike. IndaBOM, the hosted product, layers its own
look on top; its guide lives in the indabom repo at `docs/style-guide.md` and depends on the
contract in this file.

## Principles

1. **Vanilla Materialize.** django-bom ships Materialize 1.0.0 (vendored in `bom/static/bom/css`),
   Roboto and Material Icons, jQuery and Materialize's JS. Use Materialize components and classes
   as they are. Add custom CSS only to fix a Materialize gap or to name a pattern that several
   pages share.
2. **Theme through tokens, not through rules.** Colors in django-bom CSS are read from a CSS custom
   property with a plain fallback: `var(--color-primary, #66bb6a)`. A host
   restyles the app by redefining the properties, never by fighting selectors. django-bom itself
   has no opinion about dark mode; it only has to keep working when the tokens change.
3. **Consistency over novelty.** One way to lay out a page header, a toolbar, a table, a menu, a
   chip. Before adding a class, look for the pattern in this guide and reuse it, and if the
   existing one is almost right, improve it where it lives so every page benefits.
4. **Incremental.** Legacy pages keep their markup until they are touched. When you touch one, bring
   it in line with this guide rather than adding a third way.

## Where CSS lives

In order of preference:

| Scope | Where | Rule of thumb |
|---|---|---|
| Shared by two or more pages, or a named pattern | `bom/static/bom/css/style.css` | Tokens, Materialize corrections, buttons, toolbars, tables, menus, chips, settings layout, print |
| One page's layout | `bom/static/bom/css/<page>.css`, linked in `{% block head %}` | `part-info.css`, `dashboard.css`. Promote a rule to `style.css` the second time it is needed |
| One include's layout | `<style>` block at the top of the component template | `components/manufacturer-part-list.html`. Keep it under about 60 lines, otherwise make a page file |
| One element, one value | `style=""` | Only values that come from data or that JS toggles (`display: none`). Never color, font or spacing that a class already provides |

`style.css` is loaded on every page before any page file, and a host loads its own stylesheet after
it, so later files win. Put a selector in the lowest layer that covers every use.

## Tokens

Defaults are declared once at the top of `style.css` under `:root`. A rule that reads one gives a
fallback equal to the default, so the stylesheet works even if the block is removed or overridden
piecemeal. Older rules still carry literal colors; convert them to tokens as you touch them. These names are the contract with hosts; IndaBOM already defines the same
names in its `indabom-colors.css`.

| Token | Default | Used for |
|---|---|---|
| `--color-background` | `#fff` | Page and card background |
| `--color-background-surface` | `#e0e0e0` | Borders, dividers, segmented-control track, status strip border |
| `--color-background-surface-hover` | `#f1f3f4` | Hover tint for rows, menu items, settings nav |
| `--color-text` | `#212121` | Body text, menu items |
| `--color-text-grey` | `#616161` | Secondary text, eyebrows, menu icons, disabled tabs |
| `--color-primary` | `#66bb6a` | Filled buttons, active tab, sorted column, selected nav |
| `--color-primary-surface` | `#dcefdf` | Tinted backgrounds that mean "selected" or "released" |
| `--color-primary-contrast` | `#2e7d32` | Primary text on a light background where `--color-primary` is too light |
| `--color-error` / `--color-error-surface` | `#f44336` / `#ffebee` | Destructive actions, rejected, errors |
| `--color-warning` / `--color-warning-surface` | `#f9a825` / `#fff3cd` | Draft, open requests, near-match warnings |
| `--color-info` / `--color-info-surface` | `#283593` / `#e3e8fb` | In review, proposal strip |
| `--color-table-stripe` | `#f5f5f5` | `table.striped` rows |

Rules:

- No new hex, `rgb()` or named color outside the `:root` block. Hover and pressed tints are
  translucent (`rgba(128, 128, 128, 0.12)`, `rgba(102, 187, 106, 0.3)`) so they read on both a light
  and a dark page without a second value.
- Add a token only when two components need the same value. A one-off stays a fallback-free value
  in the component's own rule only if it is translucent; otherwise it gets a token.
- No `@media (prefers-color-scheme)` in django-bom. Dark mode is the host's job and it works by
  swapping the tokens above.
- Materialize color classes (`green lighten-1`, `teal-text text-darken-1`, `red lighten-1`) set
  colors with `!important` and cannot be themed. Do not add them to new markup. Use the semantic
  classes below; the legacy uses are listed under *Known debt* and are migrated page by page.

Semantic helpers defined in `style.css`: `.btn-primary`, `.btn-danger`, `.btn-outline`,
`.btn-outline-danger`, `.icon-button`, `.text-grey`, `.text-primary`, `.text-error`. They mirror
IndaBOM's names so a host has nothing to rename.

## Typography

Roboto, light weights, with size doing the work that bold would otherwise do.

| Element | Size / weight | Notes |
|---|---|---|
| `h1` | 2.5rem / 300 | Page title. On app pages it sits in `.page-header`, which sets 2rem |
| `h2` | 2rem / 300 | Rare in the app |
| `h3` | 1.7rem / 300 | |
| `h4` | 1.4rem / 300 | Settings section titles (`.section-title`) |
| `h5` | 1.1rem / 400 | Card titles (`.overview-details h5`), dialog titles |
| `p`, `li` | 1.1rem / 300 | |
| `.eyebrow` | 0.7rem / 600, uppercase, 0.08em tracking | Small label above a value: Part, Revision, MPN, organization name |
| Tables (`.tight`) | 13px | Header and cells |
| Menus, chips, toolbars | 14px / 500 for labels | |

Do not add sizes. If a block needs to be quieter, use `.text-grey` or a smaller existing step, not a
new font-size.

## Spacing and layout

- Spacing steps: 4, 8, 12, 16, 24, 32px. Default `gap` is 8px between controls, 16px between cards,
  12px between a toolbar and its table.
- App pages wrap content in `.container-app` (90–95% width). Marketing and auth pages use
  Materialize's `.container`.
- **Page header**: an `.eyebrow` with the context, the `h1`, then the actions on the right, in a
  `.page-header` flex row that wraps on phones. A view that passes `title` gets
  `components/page-header.html` from `base.html` (organization eyebrow, plain title); hosts with
  their own base template include the same file. A page whose header carries a chip, a menu or
  actions (parts list, part page, manufacturer, vendor, change order) writes its own
  `.page-header`, puts the `h1` and its chip and menu in a `.page-header-row`, makes the eyebrow a
  link to the parent list where there is one, and passes `head_title` instead of `title` so the
  base template does not render a second header. Do not add another heading that repeats the title.
- Content under a header starts at the same left edge: use `.container-app`, not `.container`, and
  cap the width of a narrow form on its children rather than centering the page.
- **Tab content** sits in `.card-panel.tab-card`; a tab with several cards uses
  `.overview-cards` (auto-fit grid, 220px minimum) with `.overview-card`.
- **Settings and forms**: `.section` with an `h4.section-title` (leading icon in `.text-primary`),
  fields rendered with the `materializecss` filter in `.row`s, and a right-aligned action row with
  the filled button last. The settings page uses the `.settings-grid` master-detail layout.
- Phone width: everything wraps, nothing scrolls sideways except tables, which live in
  `.responsive-table-wrapper`.

## Components

### Buttons

One filled button per view, and it is the thing you came to do.

| Kind | Classes | Use |
|---|---|---|
| Filled | `btn btn-primary` | Save, Edit, Release…, Add manufacturer part in an empty state |
| Outline | `btn-outline` | Secondary actions in a toolbar: Export, Done, Add manufacturer part next to a list |
| Outline, destructive | `btn-outline btn-outline-danger` | Remove all subparts |
| Flat | `btn-flat` | Cancel, dialog dismiss, tertiary links that must align with buttons |
| Icon | `icon-button` with `aria-label` and `tooltipped` | Expand, collapse, reset sort, overflow ⋮ |
| Destructive filled | `btn btn-danger` | Delete, with a `confirm()` |
| Floating | `.fixed-action-btn` via `bom-action-btn.html` | Create part, Import; pass `create_url` and `create_label` for a single create button. One per page, bottom right |

Add `waves-effect` to filled and outline buttons. Buttons that start a form submit are `<button
type="submit">`; buttons that navigate are `<a>`. Icons inside buttons use `material-icons left`.
Labels are sentence case ("Add manufacturer part"), an ellipsis means a dialog follows
("Release…").

### Menus

- An overflow menu is a `more_horiz` (row) or `more_vert` (page) `icon-button` opening a
  `dropdown-content`. Place a page menu beside the title; a row menu in the last column with no
  column heading.
- Items are neutral: body text color, grey icon, sentence case. A `<small class="grey-text">`
  under the label explains what the item does when the label alone is not enough.
- Group with `<li class="divider" tabindex="-1">`; label a group with
  `<li class="part-actions-heading" tabindex="-1">`. Destructive items use `.text-error`.
- An item that POSTs (Make primary on the Edit page) submits a hidden form placed after the menu.
- Navigation holds places (Parts, Manufacturers, Vendors, Requests, Settings, Help), never actions.
  Actions live on the page they act on.

### Tables

- `<table class="highlight tight single-line">` inside `.responsive-table-wrapper`. `.tight` gives
  the shared 13px, 6px × 12px cells. Add `striped` only for long reference lists.
- Sortable tables add `tablesorter-materialize`; the header gets a pointer, a hover tint and an
  arrow, and the sorted column turns `--color-primary`. Mark non-sortable columns `sorter-false`.
- Links in the first column, numbers right-aligned with `.text-right`, long text truncated with a
  `[+]` modal (see `bom-indented.html`).
- Filter row and pager: `components/list-filter.html` (search with clear and optional tips link;
  the part class field on the parts list) and `components/pagination.html`, which keeps `q` and
  `part_class` in its links.
- Every table has an empty state: `components/empty-row.html` with a `message` ("No parts
  found. …") and, where there is one, the `action_url` and `action_label` that fix it.
- The count ("1–50 of 120 parts") goes under the table in `.part-count`.

### Toolbars and view switches

`.table-toolbar` above a table or card list, with `.table-toolbar-start` (view switch, icon
buttons) and `.table-toolbar-end` (outline buttons, export menu). A choice between views is a
`.view-toggle` segmented control (`<span class="active">` for the current one, `<a>` for the
others, or `<label>`s driven by radio inputs as in `mcp-quickstart.html`). Never two filled
buttons for a toggle.

### Tabs

Materialize `.tabs`, styled flat: 40px tall, inset bottom line, 3px indicator in
`--color-primary`, no uppercase. Tab labels may carry a count after a middle dot ("Bill of
materials · 12"). Content for each tab goes in its own `.card-panel.tab-card`.

### Chips and status

- One chip palette in `style.css`: `.chip-ok` (primary tint), `.chip-draft` (warning tint),
  `.chip-review` (info tint), `.chip-neutral`. The part header's `.revision-picker` uses the same
  classes.
- Revision: Draft = `chip-draft`, In review = `chip-review`, Released = `chip-ok`, Obsolete =
  `chip-neutral`.
- Change order (`changes/change-status-chip.html`): Draft = `chip-draft`, Open = `chip-review`,
  Applied = `chip-ok`, Rejected and Withdrawn = `chip-neutral`. The nav count of open change orders
  is a Materialize `badge` with `chip-review`.
- Vendor and manufacturer approval (`components/approval-chip.html`): Approved = `chip-ok`,
  Probation = `chip-draft`, Restricted = `chip-neutral`. Pass `icon=True` for the inline icon used
  in sourcing rows (check for Approved, block for Restricted, nothing for Probation).
- A `.part-status-strip` under a page header explains where something stands in one sentence,
  with a leading icon and an optional right-aligned link (`.part-status-action`).

### Empty states and messages

- Empty tab or card: icon, one-line title, one sentence, then the one action that fixes it
  (`.sourcing-empty` is the model).
- Django messages render as `.alert.alert-<level>` cards in `base.html`; hosts may replace them
  with toasts. Errors are red surface with body text, warnings amber, success green.

### Forms and dialogs

- Render Django forms with the `materializecss` filter and column classes
  (`{{ form.email|materializecss:'s12 l6' }}`). Labels are the field's `label`; help text is
  `.text-grey` under the field.
- Action row: `components/form-actions.html` with `cancel_url` (and `submit_label` when Save is
  not the word): right-aligned `btn-flat` Cancel, a link to where the user came from, then
  `btn btn-primary` Save. The nav bar has no Cancel.
- Dialogs are Materialize `.modal` with a `h5` title, a `.text-grey` sentence, the form, and
  `.modal-footer` with `btn-flat` Cancel then `btn btn-primary` Save; a dialog that only shows text
  has a single `btn-flat` Close. The page initializes its modals once (`$('.modal').modal()`), not
  the include. Destructive confirmations use `confirm()`.
- Inline "add a row" affordances (`.add-vendor-link`) are flat links styled like a table row.

### Print

`@media print` rules belong in `style.css`: hide nav, footer, FAB, menus, toolbars and
`.printer-hide`; show `.printer-show`; let `.responsive-table-wrapper` overflow. Mark anything
screen-only with `.printer-hide` rather than adding a page print rule.

## Checklist for a UI change

- [ ] Reused an existing pattern from this guide, or improved it in `style.css` so other pages
      get it too.
- [ ] No new hex colors outside `:root`; no Materialize color classes on new elements; hover
      tints are translucent.
- [ ] No `prefers-color-scheme`, no `!important` unless beating a Materialize `!important`, and
      then a comment says which one.
- [ ] One filled button per view; destructive actions are red and confirmed.
- [ ] Tables use `highlight tight single-line` in `.responsive-table-wrapper`, have an empty
      state, and put the row menu last with no heading.
- [ ] Inline `style=""` only for data-driven or JS-toggled values.
- [ ] Checked at desktop and phone width, in IndaBOM's light and dark mode, and in print where the
      page is printable.
- [ ] Version bumped in `pyproject.toml`; Help page updated if a control moved.

## Known debt

Fix these as the pages they live on are touched; none needs its own project.

- **Hardcoded colors.** The literals left in `style.css` and `part-info.css` have no token with the
  same default: greys `#9e9e9e`, `#757575`, `#424242` and `#bdbdbd` (eyebrow, menu and status icons,
  part synopsis, disabled tab), the `#43a047` accent (tab indicator, outline button, sorted column),
  the light-green and lime alerts, and Materialize's `#26a69a` on the empty thumbnail hover. IndaBOM
  still overrides the menu icon, synopsis, status icon and menu heading for them.
- **Materialize color classes in templates.** About a dozen `btn green lighten-1` (should be
  `btn btn-primary`), `btn red lighten-1` (needs a `btn-danger`), `modal-close green lighten-1
  btn-flat` (a flat button with a filled background; use `btn-flat`), `teal-text text-darken-1` on
  section icons while every other accent is green (use `.text-primary`), and `grey-text`
  variants for secondary text (use `.text-grey`).
- **Chip tints** are literal light surfaces with dark ink rather than tokens, because the host's
  `*-surface` tokens are not paired with an ink colour.
- **Inline styles.** `settings.html` (23), `part-info.html` (21), `organization-create.html` (15),
  `base.html` (10, the brand logo repeated four times), `edit-quantity-of-measure.html` (9),
  `edit-part-class.html` (7).
