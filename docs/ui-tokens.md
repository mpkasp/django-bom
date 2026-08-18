# Lithium BoM design tokens

Dense operations UI. Lithium green is an action/status color, not page chrome.

## Typography

| Token | Family | Use |
|-------|--------|-----|
| `--font-sans` | **Vazirmatn** (self-hosted variable) | UI, Persian + Latin |
| `--font-print` | **IRNazanin** | Print only |

Roboto is not used for the new UI.

## Color

| Token | Value | Role |
|-------|-------|------|
### Brand ramp

Anchored on Lithium green at step 600. Use 600 for actions, 700 for hover, 50 for tinted
backgrounds, 800/900 only for text on light tints.

| Step | 50 | 100 | 200 | 300 | 400 | 500 | 600 | 700 | 800 | 900 |
|------|----|-----|-----|-----|-----|-----|-----|-----|-----|-----|
| Hex | `#ecf7f1` | `#d2ebdd` | `#a6d8bd` | `#71bd99` | `#3f9f76` | `#1a8659` | `#00713c` | `#005c33` | `#04492a` | `#063b24` |

### Neutrals

Near-neutral greys carrying a trace of the brand hue — cohesive with green without turning olive.

| Token | Value | Role |
|-------|-------|------|
| `--color-canvas` | `#f7f9f8` | Page background |
| `--color-surface` | `#ffffff` | Panels, tables, navbar |
| `--color-surface-muted` | `#f1f4f2` | Hover, inset blocks |
| `--color-surface-sunken` | `#e9edeb` | Table headers |
| `--color-row-stripe` | `#f7f9f8` | Alternating table rows |
| `--color-border-subtle` | `#edf1ef` | Row separators |
| `--color-border` | `#dde3e0` | Default hairlines |
| `--color-border-strong` | `#c2cbc6` | Header underline, dividers |
| `--color-ink` | `#131a16` | Body text (16.6:1 on white) |
| `--color-ink-muted` | `#4d5a53` | Labels, secondary (7.9:1) |
| `--color-ink-subtle` | `#7c8a83` | Placeholder / disabled only — below AA for body copy |

### Semantic

Each state has a text tone, a saturated fill for solid buttons and dots, a tint, and a border.

| State | Text | Fill | Tint | Border |
|-------|------|------|------|--------|
| Danger | `#b42318` | `#d92d20` | `#fef3f2` | `#fecdca` |
| Warning | `#b54708` | `#f79009` | `#fffaeb` | `#fedf89` |
| Success | `#067647` | `#17b26a` | `#ecfdf3` | `#abefc6` |
| Info | `#175cd3` | `#2e90fa` | `#eff8ff` | `#b2ddff` |

Success sits slightly brighter than brand green so a "saved" toast does not read as chrome.

### Categorical

For part statuses, revision states, and charts. Spaced in luminance so it stays legible in
grayscale print.

| Token | Value |
|-------|-------|
| `--color-cat-green` | `#00713c` |
| `--color-cat-blue` | `#175cd3` |
| `--color-cat-violet` | `#6941c6` |
| `--color-cat-teal` | `#0e7490` |
| `--color-cat-amber` | `#b54708` |
| `--color-cat-rose` | `#c01048` |
| `--color-cat-slate` | `#475467` |

Never encode status by hue alone — pair with a label or icon for print and color vision deficiency.

### Elevation

| Token | Use |
|-------|-----|
| `--shadow-panel` | Cards, table wrappers |
| `--shadow-raised` | Popovers, sticky bars |
| `--shadow-overlay` | Modals, dropdowns |

## Layout

- Page width: ~95% up to 90rem
- Cards/panels: filters, summaries, form groups only — not every table
- Tables: high density, sticky headers, subtle row separators, tabular numbers
- Actions: contextual action bar on data pages (no FAB)
- Spacing and inset: CSS **logical** properties (`ms-`, `me-`, `ps-`, `pe-`, `text-start`)

## Focus

`focus-visible:outline-2 outline-offset-2 outline-primary` on buttons; `focus:ring-primary` on fields.

## Print

IRNazanin body, hide chrome, bordered tables, A5 page size (existing product behavior).
