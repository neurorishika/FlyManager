# UI Density + CSS Tokenization — Design

**Date:** 2026-08-14
**Status:** Approved for planning

## Problem

Users report the UI is not compact enough across the board: items take up too
much space, and too little fits on screen. They want tighter items, smaller
text, less spacing, and a focus on density.

A second problem surfaced while scoping the first. The styling that would have
to change is not in a place where it can be changed once:

- Spacing is entirely hardcoded — roughly 400 `padding` / `margin` / `gap`
  declarations with no shared scale.
- Type is half-tokenized — 62 uses of `--font-size-sm` alongside ~70 hardcoded
  `font-size` values.
- ~4,800 lines of CSS live inline in Jinja `<style>` blocks, versus 1,892 lines
  in real `.css` files. `base.html` alone carries 1,362 lines of what is
  effectively the global stylesheet.

Tightening density without fixing this means hunting the same value through ten
`<style>` blocks every time it needs to move again.

## Goals

1. Roughly 15% tighter across every surface, with no interactive target
   getting smaller.
2. Every hardcoded spacing and type value becomes a token, so future density
   changes are one edit in one file.
3. CSS moves out of templates into layered static files that browsers cache.

## Non-goals

- No per-user or per-lab density toggle. One tighter global default.
- No redesign, re-layout, or component restructuring beyond removing redundant
  chrome that exists only to consume space.
- No relocation or restructuring of `explorer_shared.css`, `stock_explorer.css`,
  `cross_explorer.css`, or `floating_cart.css`. They are already static and
  correctly located, so they are tokenized and retightened where they sit rather
  than folded into the new layered files.

## Constraints

**Tablet-first.** The app is used mostly on tablets and sometimes on desktop.
Density must come from spacing, typography, and chrome — never from shrinking
tap targets.

**Strict CSP.** `security.py` sets `style-src: 'self' 'nonce-…'` with no
`'unsafe-inline'`, and the codebase currently has zero `style="…"` attributes.
Per-request dynamic values therefore cannot become inline style attributes; they
must stay inside nonce'd `<style>` blocks. The design preserves this: dynamic
templates keep a small nonce'd block that emits *only* CSS custom properties,
and the static rules consuming those properties move to `.css` files.

Templates with genuinely dynamic CSS:

| Template | Dynamic values | Emitted as |
| --- | --- | --- |
| `base.html` | `settings.theme.accent_color` (49 uses) | `--accent-color` |
| `home.html` | `{{ columns }}`, `{{ value }}` | `--home-grid-columns`, `--progress-value` |
| `tray/view_tray.html` | `{{ tray.Rows }}`, `{{ tray.Columns }}` | `--tray-rows`, `--tray-cols` |

## Design

### The scales

All three live in `static/css/tokens.css`, defined on both
`:root[data-theme="light"]` and `:root[data-theme="dark"]`.

**Spacing** — 4px-based, replaces every hardcoded px value:

| Token | Value | Replaces today's |
| --- | --- | --- |
| `--space-2xs` | 2px | 2–3px |
| `--space-xs` | 4px | 4–5px |
| `--space-sm` | 6px | 6–8px |
| `--space-md` | 8px | 10px |
| `--space-lg` | 12px | 14–16px |
| `--space-xl` | 16px | 20–24px |
| `--space-2xl` | 24px | 30–40px |

**Type** — the largest single win, since headings and line-height waste the most
vertical space:

| Token | Now | Proposed |
| --- | --- | --- |
| `--font-size-xs` | *(new)* | `0.72rem` — badges, meta |
| `--font-size-sm` | → `0.88rem` | → `0.80rem` |
| `--font-size-base` | → `1rem` | → `0.90rem` |
| `--font-size-lg` | → `1.2rem` | → `1.05rem` |
| `--font-size-xl` | → `2rem` | → `1.6rem` |
| `h1`–`h5` | → 2.4rem … 1.1rem | ~18% down across the board |
| `line-height` | 1.5 | 1.4 |
| `--page-gutter` | → 24px | → 16px |

Sizes keep their existing fluid `clamp()` form; only the endpoints move.

**Controls — the touch floor:**

```css
--control-height: 40px;
--control-padding-x: var(--space-lg);
--control-gap: var(--space-md);
```

`--control-height` is applied as `min-height` to `.btn`, `.form-control`,
`.page-link`, and `.nav-link`. Their vertical padding shrinks — the box simply
stops growing past 40px — and horizontal padding drops to `--space-lg`. No tap
target ever goes below 40px. Density around controls comes from `--control-gap`.

Expected result on a tablet: roughly 7 list rows per screen where 5 fit today,
with no tap target smaller than it is now.

### File architecture

```text
static/css/
  tokens.css              both :root theme blocks + all three scales
  base.css                body, headings, links, small, .app-shell-container
  bootstrap-density.css   .btn* .form-control .card .table .alert .modal
                          .page-link .nav-tabs .badge .breadcrumb .custom-control
  layout.css              .header* .app-brand* .footer .auth-shell
  components.css          .app-detail-grid .app-view-field .app-view-value
                          .app-form-grid .app-action-card .app-empty-shell
                          .app-inline-* .job-status-* .species-display
  pages/home.css
  pages/view_stock.css
  pages/view_tray.css
  pages/view_cross.css
  pages/flip.css
  pages/flip_schedule.css
  pages/user_guide.css
  pages/phenotype_preview.css
  pages/standardization_overview.css
  explorer_shared.css     ← kept, tokenized in place
  stock_explorer.css      ← kept, tokenized in place
  cross_explorer.css      ← kept, tokenized in place
  floating_cart.css       ← kept, tokenized in place
```

Global files link from `base.html`'s head in cascade order: `tokens.css`,
`base.css`, `bootstrap-density.css`, `layout.css`, `components.css` — all after
the Bootstrap vendor file so overrides win without specificity hacks. Page files
link from each template's existing `{% block head %}`, the pattern
`stock_explorer.html` already uses.

`bootstrap-density.css` is load-bearing: Bootstrap's own `.card`, `.table`, and
`.form-group` padding is a large share of the wasted space and lives in the
vendor file, where tokens alone cannot reach it.

### Chrome reduction

Beyond smaller numbers, these consume space structurally and are trimmed as
encountered during the sweep:

- Section headers on detail pages that restate the page title.
- Panel titles sized as headings where a `--font-size-sm` label reads the same.
- Nested wrapper elements that contribute only padding.

This is limited to elements that exist to consume space. Anything that changes
what information a page shows is out of scope for this work.

## Phasing

Each phase is independently reviewable and revertible, and is verified before
the next begins.

**Phase 0 — Extract, no visual change.** Create `tokens.css` with today's exact
values, plus `base.css`, `bootstrap-density.css`, `layout.css`, and
`components.css`, moving rules out of `base.html` verbatim. Reduce `base.html`'s
`<style>` block to the `--accent-color` emitter. *Success: pages render
identically. Any visual diff in this phase is a mistake, which is exactly why it
ships alone.*

**Phase 1 — Apply density to the foundation.** Move the token values to the
scales above and add the Bootstrap density rules. This is where most of the 15%
lands, globally, in one small diff.

**Phase 2 — Explorers.** Tokenize `explorer_shared.css`, `stock_explorer.css`,
`cross_explorer.css`; tighten list rows and pagination.

**Phase 3 — Home.** Extract `home.html`'s 1,196 inline lines to `pages/home.css`
with a dynamic-var emitter; tokenize and tighten cards, stats, and tiles.

**Phase 4 — Detail pages.** `view_stock.html`, `view_tray.html`,
`view_cross.html`, `flip.html`, `flip_schedule.html`, plus remaining page
extractions. `view_tray.html` keeps its grid-dimension emitter.

**Phase 5 — Forms.** `add_stock`, `add_cross`, `edit_tray`, filter panels;
`.form-group` rhythm and label sizing.

## Verification

Per phase, via the `run-flymanager` skill against the running app:

1. Screenshot the affected pages at tablet width (1024×768) and desktop width,
   in both light and dark themes.
2. Confirm no computed tap target is under 40px on the touched pages.
3. Confirm no horizontal overflow at 1024px.
4. Run the test suite; the 16 known pre-existing failures are the baseline, and
   no new failure is acceptable.

Phase 0 additionally requires before/after screenshots to be visually identical.

## Risks

**Extraction drops or reorders a rule.** ~4,800 lines move. Mitigated by Phase 0
shipping the move with zero intended visual change, so any diff is signal.

**Cascade order changes behavior.** Rules currently inline in `base.html` sit
after the vendor stylesheet; extracted files must preserve that. Mitigated by
explicit link ordering and Phase 0's identical-render check.

**Static assets are cached stale after deploy.** Checked: there is no
cache-busting query string, no `SEND_FILE_MAX_AGE_DEFAULT` override, and no
`Cache-Control`/`expires` rule in `deploy/` or `docker/`. Flask therefore serves
static files with ETag and Last-Modified and no `max-age`, so browsers
revalidate on each load and pick up changes. The four existing `.css` files
already ship this way without incident, so moving more CSS into `static/`
inherits working behavior rather than introducing a new risk. No action needed.

**15% reads as too tight on real data.** The scales are single-file tokens, so
tuning is one edit — this is the payoff of doing tokenization alongside rather
than after.
