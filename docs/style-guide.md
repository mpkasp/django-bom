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
| `--color-background-surface` | `#eee` | Borders, dividers, segmented-control track, status strip border |
| `--color-background-surface-hover` | `#f1f3f4` | Hover tint for rows, menu items, settings nav |
| `--color-text` | `#212121` | Body text, menu items |
| `--color-text-grey` | `#616161` | Secondary text, eyebrows, menu icons, disabled tabs |
| `--color-primary` | `#66bb6a` | Filled buttons, active tab, sorted column, selected nav |
| `--color-primary-surface` | `#c8e6c9` | Tinted backgrounds that mean "selected" or "released" |
| `--color-primary-contrast` | `#2e7d32` | Primary text on a light background where `--color-primary` is too light |
| `--color-error` / `--color-error-surface` | `#e53935` / `#ffebee` | Destructive actions, rejected, errors |
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
| `h1` | 2.5rem / 300 | Page title. On app pages use `.part-list-title` (2rem) or `.part-number` (2rem) instead of a bare `h1` |
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
- **Page header** (parts list, part page): an `.eyebrow` with the context (organization, "Part"),
  a title, then the actions on the right, all in one flex row that wraps on phones. See
  `.part-list-header` and `.part-header`. Pass `head_title` rather than `title` from the view so the
  base template does not also render its centered `h1`.
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
| Floating | `.fixed-action-btn` via `bom-action-btn.html` | Create part, Import. One per page, bottom right |

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
- Navigation holds places (Parts, Manufacturers, Vendors, Requests, Settings, Help), never actions.
  Actions live on the page they act on.

### Tables

- `<table class="highlight tight single-line">` inside `.responsive-table-wrapper`. `.tight` gives
  the shared 13px, 6px × 12px cells. Add `striped` only for long reference lists.
- Sortable tables add `tablesorter-materialize`; the header gets a pointer, a hover tint and an
  arrow, and the sorted column turns `--color-primary`. Mark non-sortable columns `sorter-false`.
- Links in the first column, numbers right-aligned with `.text-right`, long text truncated with a
  `[+]` modal (see `bom-indented.html`).
- Every table has an empty state: a single `<td colspan="99">` row in `.empty-state` wording
  ("No parts found. …") that says what to do next.
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

- Revision state uses `.chip.revision-chip-{draft,review,released,obsolete}`; the header's
  `.revision-picker` reuses the same tints. Draft = warning tint, In review = info tint, Released =
  primary tint, Obsolete = neutral.
- Change status uses `changes/change-status-chip.html`. Vendor and manufacturer approval use a
  Materialize `badge`. These three should converge on the revision chip tints (see *Known debt*);
  until then, new status chips use the revision chip classes.
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
- Action row: right-aligned, `btn-flat` Cancel then `btn btn-primary` Save, 4px apart.
- Dialogs are Materialize `.modal` with a `h5` title, a `.text-grey` sentence, the form, and the
  action row in `.modal-footer`. Destructive confirmations may use `confirm()`.
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

- **Hardcoded colors.** `part-info.css` and the newer parts of `style.css` use hex values for grey
  text, borders, chip tints and menu text. IndaBOM had to add a block of `.supports-dark-mode`
  overrides for them. Replace each with a token and the IndaBOM block can go.
- **Materialize color classes in templates.** About a dozen `btn green lighten-1` (should be
  `btn btn-primary`), `btn red lighten-1` (needs a `btn-danger`), `modal-close green lighten-1
  btn-flat` (a flat button with a filled background; use `btn-flat`), `teal-text text-darken-1` on
  section icons while every other accent is green (use `.text-primary`), and `grey-text`
  variants for secondary text (use `.text-grey`).
- **Three chip systems** (revision chips, change status chip, approval badges) with three
  palettes.
- **Button radius.** `.btn-outline`, `.revision-picker` and `.view-toggle` are pills; Materialize's
  `.btn` is 2px, so a standalone install shows both shapes side by side. Decide whether `.btn`
  becomes a pill in django-bom (IndaBOM already rounds it to 20px) or stays square.
- **Dead or duplicate CSS.** `part-info.css`: `.valign-wrapper-row`, `.quantity-input-col`,
  `.refresh-btn`, `.part-info-form-wrapper`, `.manage-btn-col` (declared twice). `style.css`:
  `.part-info-form`, `.tighter`, `.cell-collapsed`, `.primary-color`/`.dark-color`/`.light-color`,
  `.btn-warning`, `.info`, `.tabs` (declared twice), `.table-action` (overlaps `.icon-button`).
  Print blocks are repeated in `part-info.css` and `dashboard.css`.
- **Inline styles.** `settings.html` (26), `part-info.html` (22), `organization-create.html` (15),
  `edit-quantity-of-measure.html` (12), `edit-part-class.html` (10), `base.html` (10, the brand
  logo repeated four times).
- **Older list pages.** `sellers.html` and `manufacturers.html` still have the search row with a
  Submit button and an "Options" column heading; bring them to the parts-list header and filter
  row from `dashboard.html`.
- `sellers.html` used `.text-grey` before `style.css` defined it; it now resolves to the token.
