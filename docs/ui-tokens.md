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
| `--color-primary` | `#00713C` | Actions, links, focus, status |
| `--color-primary-hover` | `#005a30` | Hover |
| `--color-primary-light` | `#e8f5e9` | Selected row, chips |
| `--color-canvas` | `#f6f3ec` | Warm off-white page background |
| `--color-surface` | `#ffffff` | Panels, tables |
| `--color-surface-muted` | `#f3efe6` | Header rows, hover |
| `--color-border` | `#e4ddd0` | Hairlines |
| `--color-ink` | `#1c241d` | Body text |
| `--color-ink-muted` | `#5c675e` | Labels, secondary |
| `--color-danger` | `#b42318` | Destructive actions / errors |

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
