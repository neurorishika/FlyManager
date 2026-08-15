# UI density and CSS structure

All spacing and type values are tokens defined in
`flymanager/app/static/css/tokens.css`. To make the whole UI tighter or
roomier, change the scale there — do not add hardcoded px/rem to a
component or page file.

## Layers — the *real* load order, read straight from `base.html`

`base.html`'s `<head>` links CSS in this exact order:

```text
1. vendor/bootstrap-4.5.2.min.css, vendor/fontawesome
2. floating_cart.css                    <- unconditional link, base.html:18
3. {% block head %}{% endblock %}       <- page-supplied, BEFORE the layer below
4. tokens.css
5. base.css
6. bootstrap-density.css
7. layout.css
8. components.css
9. {% block page_css %}{% endblock %}   <- page-supplied, AFTER the layer above
10. inline <style> (accent-color custom property only)
```

| File | Owns | Linked from |
| --- | --- | --- |
| `floating_cart.css` | The floating order-cart bar (every page) | **base.html:18, unconditionally** — before even `{% block head %}`, so before `tokens.css`/`layout.css`/`components.css` |
| `explorer_shared.css` | Shared filter-bar/card/search-icon styling for both explorers | `{% block head %}` in `stock_explorer.html`/`cross_explorer.html` — **before** `tokens.css`/`bootstrap-density.css`/`layout.css`/`components.css` |
| `stock_explorer.css` / `cross_explorer.css` | Per-explorer page rules | Same `{% block head %}`, same explorer templates — **before** the component layer |
| `tokens.css` | Theme colors (light/dark) + the spacing, type, and control scales | Unconditional, step 4 |
| `base.css` | Element defaults: `body`, headings, `.app-shell-container` | Unconditional, step 5 |
| `bootstrap-density.css` | Theming + density overrides for vendored Bootstrap 4.5 components | Unconditional, step 6 |
| `layout.css` | Header, brand, nav, footer, auth shell | Unconditional, step 7 |
| `components.css` | The shared `.app-*` component system (panels, form grids, chips, empty states, job-status widgets) | Unconditional, step 8 |
| `pages/*.css` (`view_cross.css`, `view_tray.css`, `home.css`, `flip.css`, `tray_management.css`, etc.) | One file per page | `{% block page_css %}` in that page's template — **after** the entire component layer above |

Each file carries its own `@media` rules at its end, rather than a separate
responsive stylesheet.

## `{% block page_css %}` vs `{% block head %}` — they are opposites

These two blocks are **not** interchangeable, and using the wrong one
silently flips which rule wins a specificity tie:

- **`{% block head %}`** renders at step 3, **before** `tokens.css`,
  `bootstrap-density.css`, `layout.css`, and `components.css`. A rule
  linked here **loses** any equal-specificity tie against the global
  layer, because the global layer loads later. This is where
  `explorer_shared.css`/`stock_explorer.css`/`cross_explorer.css`
  currently link from — which is *why* they were vulnerable to the
  search-icon collision below, not despite it.
- **`{% block page_css %}`** renders at step 9, **after** the entire
  global layer. A rule linked here **wins** any equal-specificity tie
  against `bootstrap-density.css`/`layout.css`/`components.css`. This is
  the block every `pages/*.css` file should use, and the one to reach for
  by default — put a new page stylesheet here unless you have a specific
  reason (matching `floating_cart.css`'s or the explorers' existing
  early-load behavior) to do otherwise.

This ordering matters because of a CSS trap: when two rules have equal
specificity, the one that loads **later** wins, regardless of which one is
"more specific in spirit." Three real collisions were found and fixed
during this project because of it:

1. **Icon/label gap** — a global `.btn, .page-link, .nav-link` flex rule
   (added late in `bootstrap-density.css`) collapsed the whitespace between
   an icon and its label on ~98 buttons. Fixed by adding an explicit `gap`
   to that same rule.
2. **Explorer search icon clearance** — `.explorer-filter-search` set
   `padding-left: 36px` to clear an absolutely-positioned magnifier icon,
   but a later, equal-specificity `.form-control` rule in
   `bootstrap-density.css` reset it, since `bootstrap-density.css` loads
   after `explorer_shared.css`. Fixed by raising the search rule's
   specificity to `.form-control.explorer-filter-search` so it wins
   regardless of file order.
3. **User Guide table-of-contents centering** — a blanket
   `.btn, .page-link, .nav-link { justify-content: center; }` centered the
   User Guide's left-aligned sidebar links, which also use `.nav-link`.
   Fixed by narrowing the centering rule to
   `.btn, .page-link, .nav-tabs .nav-link` and leaving plain `.nav-link`
   uncentered.

Lesson: when adding a global rule late in the cascade (which
`bootstrap-density.css`/`components.css`/`layout.css` all effectively are,
relative to page CSS that only overrides specific selectors), check every
existing narrower rule with the same specificity before assuming your
change is additive.

## The 40px control floor

`--control-height: 40px` is a hard floor for every button, input, select,
page-link, and nav-link (`bootstrap-density.css`'s density layer). Density
comes from tightening padding, gap, and font-size around that floor, never
from shrinking the control itself. This exists because FlyManager is used
mostly on tablets at the bench — a control below ~40px CSS pixels becomes
hard to hit reliably with a gloved thumb.

**Known, deliberate exception:** `.tray-cell` (the individual cells in the
tray grid, `pages/view_tray.css`) is exempt from the 40px floor. Tray grids
run up to 50 columns wide; a 40px-tall cell floor would make large trays
comically wide before they'd even show meaningfully. `tapcheck` (below)
excludes `.tray-cell` from its scan for exactly this reason — this is not
an oversight to "fix."

## The rebuild requirement

The app's `Dockerfile` does a plain `COPY . .` — there is **no bind mount**
of the source tree into the running container. Editing a CSS or template
file on disk changes nothing in the running app until the image is
rebuilt. Before capturing screenshots, running `tapcheck`, or otherwise
judging a CSS change "done," always run:

```bash
./.claude/skills/run-flymanager/driver.sh up
```

Comparing screenshots against a stale image is worse than not comparing at
all — it silently reports `IDENTICAL` for a change that never shipped.

## Verifying a CSS change

```bash
./.claude/skills/run-flymanager/driver.sh up
# some pages (e.g. the cross detail view) only render meaningful content
# once dev-fixtures has set up ownership/assignment on the seed data:
./.claude/skills/run-flymanager/driver.sh dev-fixtures

poetry run python .claude/skills/run-flymanager/css_audit.py capture before
# … make the change, then rebuild …
./.claude/skills/run-flymanager/driver.sh up
poetry run python .claude/skills/run-flymanager/css_audit.py capture after
poetry run python .claude/skills/run-flymanager/css_audit.py compare before after
poetry run python .claude/skills/run-flymanager/css_audit.py tapcheck
```

`compare` refuses to report `IDENTICAL`/`DIFF` unless both captures hold
the full expected set of screenshots (currently 14 pages × 2 themes × 2
viewports = 56 files) — a truncated or crashed capture fails loudly
instead of silently comparing partial data.

`MASKED_SELECTORS` (in `css_audit.py`) exists because a few widgets render
text that is relative to wall-clock time (e.g. "Cached 3h ago" on the
provider-cache badge, or scheduler "next run" countdowns). Comparing raw
screenshots of those regions would show a spurious `DIFF` on every run
regardless of any CSS change, since the text itself changes every time the
capture runs. The harness paints those regions with a solid magenta mask
(`#FF00FF`) before screenshotting so they compare stably — a solid magenta
block in a capture is the mask working as intended, not a rendering bug.

The screenshot harness only covers pages reachable at ≥1024px width while
logged in as `devtest`; anything behind a `@media (max-width: 767px)` rule,
a mid-submit state (`.job-status-banner`, `.app-form-progress`), or a route
that needs specific seed-data ownership must be verified by direct
inspection (reading the CSS against the rendered DOM, or a throwaway
Playwright script) rather than trusted from `compare`'s "IDENTICAL"/"DIFF"
output alone.

## Known, already-justified "hardcoded value" exceptions

Running the project-wide acceptance grep —

```bash
grep -rnE "(padding|margin|gap|font-size)[a-z-]*:[^;]*[0-9]+(\.[0-9]+)?(px|rem|em)" \
  flymanager/app/static/css/ --include="*.css" | grep -v vendor
```

— will always turn up some matches. Do not "fix" these into tokens; each
is deliberate:

- **`clamp(...)` display/heading sizes** (`base.css` h1–h5,
  `components.css` `.app-section-title`/`.app-empty-code`, and the
  large display headings in `pages/home.css`, `pages/flip.css`,
  `pages/flip_schedule.css`, `pages/view_stock.css`,
  `pages/standardization_overview.css`, `pages/user_guide.css`). These are
  fluid, viewport-responsive display sizes, not the fixed body/UI type
  scale that `--font-size-*` covers, and `tokens.css`'s own token
  definitions are themselves `clamp()` expressions containing raw
  px/rem — that's the source of truth, not a leak.
- **Relative `em` sizes** in `pages/view_tray.css` (`.tray-cell` label
  sizing at `0.8em`–`0.95em`). These scale with the tray cell's own
  computed font-size (itself already token-driven), not with the page's
  base type scale, so a `--font-size-*` token would be the wrong unit
  here.
- **The explorer search icon's `padding-left: 36px`**
  (`explorer_shared.css` `.form-control.explorer-filter-search`). Clears a
  36px-wide absolutely-positioned magnifier icon; the value is the icon's
  geometry, not a spacing rhythm choice.
- **Tray-grid cell geometry** (`pages/view_tray.css` `.tray-grid`,
  `.tray-cell`, and their `@media` variants) — fixed pixel cell
  dimensions driven by the tray's row/column count, exempt from both the
  spacing scale and the 40px control floor (see above).
- **Floating-cart `calc()` offsets** (`floating_cart.css`
  `padding-bottom: calc(clamp(...) + 104px)` and its mobile variant). The
  added constant is the measured height of the floating cart bar itself so
  page content doesn't render underneath it, not a spacing choice.

## Known gap: `layout.css` and `components.css` were extracted but never fully tokenized

Unlike `pages/*.css` (each given both an extraction task and a matching
"tokenize and tighten" pass), `layout.css` (header/nav/footer/auth shell)
and `components.css` (shared `.app-*` system) only ever received a
byte-for-byte extraction out of `base.html`'s original inline `<style>`
block. A handful of clearly generic, exact-token-value spacing rules in
`components.css` and the dead pre-density rules and `.modal-dialog` margin
in `bootstrap-density.css` were tokenized as part of closing out this
project, but most of `layout.css`'s header/nav chrome (pill padding, badge
sizing, brand lockup gaps, etc.) and a number of `components.css` rules
that don't have an exact-pixel token match (chip padding, form-section
gaps, job-status-row padding, etc.) remain hardcoded. None of these are
new — they've rendered unchanged since before this project started — but
they are a real gap against the "no hardcoded spacing/type values
anywhere" goal, not a documented exception.

As of this writing the acceptance grep above returns **124 matches**:
**52 in `layout.css`**, **22 in `components.css`** (18 after excluding
the 4 `clamp(...)` lines already listed as justified exceptions), and the
rest already accounted for by the exceptions above. A full risk-classified
breakdown of every one of those 70 lines (which are floor-coupled,
which are breakpoint-coupled, which are collision traps, which are safe
first candidates) lives in the Task 12 report's review addendum
(`.superpowers/sdd/2026-08-14-ui-density-css-tokenization/task-12-report.md`)
— read that before scoping the follow-up pass rather than re-deriving it.

Retokenizing the header/nav chrome safely needs its own dedicated pass
with per-breakpoint visual review (like Tasks 7–11 got for the page
files), since it's globally visible on every page and several of its
values are decorative geometry (pill radii, badge sizes) tuned as a set
rather than independently. Do not assume every remaining match in those
two files is "fine" — check whether it's on the exception list above
before leaving it alone.
