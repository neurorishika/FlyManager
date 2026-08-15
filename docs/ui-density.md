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

**Other tracked, non-`.tray-cell` violations:** a handful of pre-existing
controls sit below the 40px floor and predate this project — plain
Bootstrap `.dropdown-item.bulk-status-item` in the explorer bulk-status
menus (~30.4px, not nested under `.header-menu` so it gets none of that
selector's density treatment) and Bootstrap's default
`.custom-control-label` checkbox/radio label (~17.9px, several
templates — the native `<input>` is the actual hit target, not the text
label). Both are recorded in `css_audit.py`'s `TAP_TARGET_KNOWN_EXCLUSIONS`
with measured sizes and are excluded from `tapcheck`'s pass/fail gate
rather than silently passing or silently failing on a pre-existing
condition this pass didn't cause. `.breadcrumb-item a` (~20.2px,
`view_tray.html`) and the explorer's raw row-selection checkboxes
(~13–18px, one per table row) are pre-existing too but aren't matched by
`CONTROLS` at all, so `tapcheck` structurally can't see them; they're
noted in a comment next to `TAP_TARGET_KNOWN_EXCLUSIONS` for the record.
None of the four were resized in the final review's fix wave — see
`.superpowers/sdd/2026-08-14-ui-density-css-tokenization/final-fix-report.md`.

**`tapcheck` checks width as well as height.** A control that is tall
enough but too narrow (a square icon-only button, say) is exactly as hard
to hit reliably as one that's too short, so both dimensions gate the same
40px floor. `CONTROLS` was also widened to include `.dropdown-item`,
`.filter-chip`, `.flip-status-option`, `.column-selector-option`,
`.guide-pill`, `.close`, and `.custom-control-label` — the final review
found three genuinely new sub-floor regressions this project caused
(`.header-menu .dropdown-item`, `.flip-status-option`, `.filter-chip`)
that the original `CONTROLS` list never caught, partly because none of
them carried a class in that list and partly because two of the three are
hidden at rest (a closed dropdown, a flip-status panel that only shows
after a scan) so no screenshot ever showed them either — a double blind
spot. All three (plus `.column-selector-option`, fixed alongside them
since the fix was equally cheap) now carry `min-height: var(--control-height)`
with restored `--space-lg` padding.

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
- **Fluid spacing `clamp()`s** — not just the display-heading font-sizes
  above, the same pattern shows up for a handful of *spacing* properties
  that intentionally scale with viewport width rather than sitting on the
  fixed `--space-*` step scale: `layout.css` `.main-content` padding
  (`clamp(16px, 2vw, 28px) 0 clamp(28px, 4vw, 44px)`) and `.auth-shell`
  margin (`clamp(12px, 4vw, 32px) auto`); `components.css` `.app-panel`
  padding (`clamp(16px, 2vw, 24px)`) and `.app-upload-shell`/
  `.app-empty-shell` margin (`clamp(12px, 4vw, 28px) auto`). Like
  `tokens.css`'s own token definitions, these are themselves `clamp()`
  expressions containing raw px — the fluid behavior is the point, not a
  leak.
- **Static font-sizes kept as literals because every candidate token grows
  the text** (`layout.css`): `.app-brand-name` (`1.02rem`),
  `.app-brand-meta` (`0.7rem`), `.header-eyebrow` (`0.68rem`),
  `.header-action-button`/`.header-nav-link` (`0.88rem`, both — the same
  literal, same token decision, per the consistency rule). A density pass
  must never render a converted font-size *larger* than the value it
  replaced at any viewport width, and for each of these the nearest token
  by raw-distance failed that test:
  - `.app-brand-name` (1.02rem/16.32px): `--font-size-lg` peaks at
    1.05rem, above the original above ~640px viewport width. The next
    token down, `--font-size-base`, peaks at 0.90rem — always safely
    smaller, but a ~12% shrink to the brand wordmark, a decorative/identity
    element rather than a density-relevant control. Kept as a literal
    rather than force a disproportionate cosmetic change onto a piece of
    UI this pass isn't really about.
  - `.app-brand-meta` (0.7rem) and `.header-eyebrow` (0.68rem) both sit
    at/inside `--font-size-xs`'s own range (0.68–0.72rem) — the smallest
    token on the scale — so tokenizing either would grow it above ~360–
    640px rather than shrink it. There is no smaller token to step down
    to. Kept as literals.
  - `.header-action-button`/`.header-nav-link` (0.88rem): the next token
    down from `--font-size-base` (which grows this above ~620px) is
    `--font-size-sm`, capping at 0.80rem. These are the app's primary
    touch/nav controls on a tablet-first app — legibility here matters
    more than on decorative text, and 0.80rem read too small for primary
    navigation labels in visual review. Kept as a literal rather than
    either grow the text (density regression) or shrink it past what's
    comfortable to read on a touch control.

## `layout.css` and `components.css` are now tokenized (Task 13)

`layout.css` (header/nav/footer/auth shell) and `components.css` (shared
`.app-*` system) were extracted verbatim out of `base.html`'s original
inline `<style>` block (Tasks 4–5) and never given the tokenize-and-tighten
pass every `pages/*.css` file got — a real, previously-documented gap
against the "no hardcoded spacing/type values anywhere" goal. Task 13
closed it in two commits:

1. **Tokenize at parity** — every literal that exactly equalled an
   existing token value (2/4/6/8/12/16/24px, and the 40px
   `--control-height` floor on `.header-action-button`/`.header-nav-link`,
   previously hardcoded rather than tracking the token) became a `var(...)`
   with zero value change, confirmed `IDENTICAL` via a full 56-shot
   capture/compare.
2. **Tighten** — every remaining hardcoded spacing/type value was mapped
   onto the scale using the project's standard bucketing (2-3px→2xs,
   4-5px→xs, 6-8px→sm, 10px→md, 14-16px→lg, 20-24px→xl, 30-40px→2xl, with
   in-between values assigned to the nearer bucket), including static
   `rem` font-sizes retuned onto the fluid `--font-size-*` clamp tokens
   where a token existed that stayed **at or below** the original value at
   every viewport width (e.g. `.app-form-section-title` 1rem →
   `var(--font-size-base)`, capping at 0.90rem — a genuine static→fluid
   behavior change, but strictly a shrink; `.header-session-copy` 0.83rem
   and `.theme-toggle-label` 0.82rem both → `var(--font-size-sm)`, capping
   at 0.80rem, after an initial pass wrongly picked `--font-size-base`
   (peaks at 0.90rem) by nearest-boundary-distance without checking
   direction — caught in review and fixed). Five font-size conversions
   where *no* token satisfied "never larger than the original" were kept
   as literals instead — see the new exceptions below.

The acceptance grep against these two files now returns 11 lines: the 6
`clamp()` lines listed above plus the 5 literal-font-size exceptions just
below (was 124 total / 52 + 22 in these two files before Task 13, then
briefly 6 after Task 13's first pass before the direction-check review
added the 5 font-size literals back). `.header-action-button` and
`.header-nav-link` keep `min-height: var(--control-height)`; their padding
was tightened but verified — by driving a real browser at 900px and
600px, since the harness only captures ≥1024px — to still measure exactly
40px tall at both widths. `.app-view-value`'s `min-height: 44px` (not
matched by the acceptance grep, and not itself a `--control-height`-style
floor) was left untouched; only its padding was tightened.

`.job-status-row` and `.app-form-progress` only render mid-submit or while
a background job runs, so no harness screenshot ever shows them; Task 13
verified their tightened spacing by injecting the exact DOM
`job_status_banner.js` produces (same classes, same markup) into a live
page and inspecting the render directly, rather than relying on `compare`.

See `.superpowers/sdd/2026-08-14-ui-density-css-tokenization/task-13-report.md`
for the full before/after value table and per-rule reasoning.

**Scope note:** the "5 literal-font-size exceptions" / "11 lines" figures
above are scoped to `layout.css` and `components.css` only (the two files
Task 13 closed the tokenization gap on). They are not a project-wide
count — running the acceptance grep across the rest of `pages/*.css`
turns up a few more pre-existing, already-justified literals not listed
in this file until the final whole-branch review found them undocumented:
`pages/view_tray.css`'s `.tray-stat-card` (`1.75rem`, already commented
in-file as a bespoke headline-stat value, same category as
`.stock-summary-value`) and `pages/home.css`'s `.tray-mini-cell`
(`0.5rem`, a single-character label inside a ~14px tray-heatmap swatch —
below `--font-size-xs`, the smallest token, with no token that fits a
label this small without growing the swatch; now commented in-file too).
`pages/user_guide.css`'s `.guide-section { scroll-margin-top: 96px; }` is
a different kind of exception — it offsets in-page anchor scrolling by
the sticky header's height so a jumped-to section doesn't render partly
hidden underneath it, the same category as `floating_cart.css`'s
`calc()` offset above, not a spacing-rhythm value. A pre-existing, undocumented
`@media (max-width: 767px) { body { font-size: clamp(0.88rem, ..., 0.96rem); } }`
override in `base.css` was removed outright in the same review rather
than added here: it predated `--font-size-base`'s current, tighter
`clamp()` and, left in place, made body text render *larger* on the
smallest screens than on desktop (measured 900px → 14.4px, 767px →
15.36px, 600px → 15.24px) — density running backwards on a tablet-first
app, on a viewport range the capture harness (≥1024px only) structurally
can't see. `body` now relies solely on `var(--font-size-base)`, which is
already a responsive `clamp()`.

## `layout.css`/`components.css` were tokenized at parity, not tightened (fixed)

Task 13 (above) genuinely tokenized `layout.css`/`components.css`, but
its "tighten" pass systematically chose the *parity* token over the
*tightened* one for the same source literal, even though every
`pages/*.css` file had already tightened the identical literal one step
down the scale. The literal itself never changed — only which token it
was assigned to — so this shipped as a silent, page-invisible
inconsistency: the same shared widget rendered looser in the header/
footer/chrome than in its page-local counterpart, and the chrome layer
never picked up the density gain the rest of the project did.

| Source literal | pages/*.css token | layout.css/components.css token (before fix) |
| --- | --- | --- |
| 16px | `--space-lg` (12px), ×31 | `--space-xl` (16px) |
| 18px | `--space-lg` (12px), ×26 | `--space-xl` (16px) |
| 12px | `--space-md` (8px), ×54 | `--space-lg` (12px) |
| 8px | `--space-sm` (6px), ×46 | `--space-md` (8px) |

Fixed by moving every one of those chrome-layer declarations to the same
tightened token `pages/*.css` already used — the specific `gap`/
`padding`/`margin` declarations this covers are `layout.css` lines
28–47, 113–116, 177–180, 233–266, 361–431, 486–542, and `components.css`
lines 19–58, 84–178, 310–313 (declaration list, not every line in that
range — decorative `border-radius`/`box-shadow` literals in the same
neighborhood were left untouched; only spacing tokens moved). The fluid
`clamp()` spacing exceptions above were **not** touched — they're
deliberately parity, not a drift bug. The `991px`/`767px` `@media`
overrides in `layout.css` that shadow `.header-panel`/`.header-main`/
`.header-brand-row` were moved in lockstep with their now-tightened base
rules so the responsive step-down stays a real step down rather than
collapsing to a no-op; verified by driving a real browser at 900px,
767px, and 600px (the capture harness only reaches ≥1024px) — no
horizontal overflow, header/nav still wraps correctly, tap-target floor
still holds at every width checked.

## Line-height is now tokenized

`--line-height-*` tokens were added to `tokens.css` (both theme blocks —
line-height doesn't vary by theme, but the other scales live there so
this one does too, for one lookup location): `--line-height-100` through
`--line-height-150` in 0.05 steps by *name* (`100` = 1, `150` = 1.5, etc,
since there's no natural t-shirt sizing for this scale), plus
`--line-height-base` (1.3) for body copy specifically. Before this, `line-height`
was the one remaining type property with zero tokens — 38 hardcoded
values across 11 distinct numbers, spread across every layer from
`base.css` down through `pages/*.css`. All 38 now reference a token.

`body`'s line-height was tightened from a pre-existing `1.4` to
`--line-height-base` (1.3) as part of this — the only *value* change in
the line-height tokenization (every other hardcoded number kept its
existing value, just moved onto a `var(...)`). Line-height carries zero
tap-target risk: control sizing is driven by `--control-height`/padding,
never by text metrics, so tightening body leading cannot push anything
below the 40px floor.

## Explorer/global CSS collisions (Important 6, final review)

`base.html`'s inline `<style>` block was always positioned after
`{% block head %}` in the real DOM order, so the explorer stylesheets
(`explorer_shared.css`, loaded from `{% block head %}` in both explorer
templates) have always lost equal-specificity ties against the
unconditional global layer (`bootstrap-density.css`/`layout.css`/
`components.css`, loaded later at steps 6–8). This project's file split
didn't create that ordering — it only made the resulting collisions
`grep`-able. Two were found and fixed in the final review:

- **`.explorer-filter-control`** (search/select filter inputs, reached
  through every `templates/filter_field.html`-rendered dropdown) declared
  its own `min-height: 38px`/`height: 38px`/`border-radius: 10px`/
  `border`/`background`/vertical padding, all of which were silently and
  *always* overridden by `bootstrap-density.css`'s later, equal-specificity
  `.form-control` rules — including the sub-floor `min-height: 38px`,
  which lied about the control's own geometry (it has always actually
  rendered at 40px, never at 38px). Fixed by deleting the dead
  declarations rather than raising specificity to make them win: the
  40px result already winning is the *correct* one (38px would sit below
  the tap-target floor), so only `color`/`box-shadow`/`font-size` — the
  properties that do actually apply — were kept.
- **`.column-selector-toggle`** ("Columns" pill button on both
  explorers) also carries `.btn.btn-outline-secondary` in the template,
  so it matches `bootstrap-density.css`'s density-layer `.btn, .form-control, ...`
  rule directly. That rule's `padding-left`/`padding-right` longhands
  (added as part of this project's density work) newly clobber the
  toggle's own `padding-inline` shorthand — a genuinely *new* regression,
  not a pre-existing one — and its `min-height: var(--control-height)`
  (40px) also wins over the toggle's intended 52px pill sizing. Fixed by
  raising the toggle's own rule to `.btn.column-selector-toggle`
  (specificity (0,2,0)), which now wins regardless of load order: the
  pill renders at its intended 52px/999px-radius/`padding-inline` sizing
  again, matching its sibling pill controls.

## `--code-bg`/`--code-color` (final review, minor)

`pages/user_guide.css`'s `code { background-color: var(--code-bg); color: var(--code-color); }`
referenced two tokens that were never defined anywhere in `tokens.css`,
so `<code>` blocks in the User Guide rendered with browser-default
(unstyled) colors in both themes — pre-existing, not something this
project's tokenization introduced, but a real gap against "every color
is a token." Defined in both theme blocks in `tokens.css`.

## Measured density delta (final review's fix wave)

Real `document.body.scrollHeight` at 1024px, `devtest` login, before vs.
after the chrome-layer bucketing fix + line-height tightening above
(same content, same seed data — only these two CSS changes in between):

| Page | Before | After | Δ |
| --- | --- | --- | --- |
| home | 5318px | 5201px | −2.2% |
| stock-explorer | 1818px | 1760px | −3.2% |
| cross-explorer | 1377px | 1339px | −2.8% |
| flip-schedule | 1122px | 1098px | −2.1% |
| flip | 1518px | 1483px | −2.3% |
| add-stock | 1688px | 1634px | −3.2% |
| add-cross | 1459px | 1418px | −2.8% |
| standardization | 1419px | 1361px | −4.1% |
| user-guide | 7372px | 7064px | −4.2% |
| view-stock | 5431px | 5255px | −3.2% |
| view-cross | 10000px | 9560px | −4.4% |
| phenotype-preview | 1009px | 983px | −2.6% |
| view-tray | 2776px | 2740px | −1.3% |

Real, measured range: **roughly 1–4% additional page-height reduction**
from this fix wave alone, on top of whatever Task 13 already delivered.
This is the actual number, not the final review's own ~4–7% estimate for
the combined chrome-bucketing-fix + line-height change — the estimate
was directionally right (both changes help, chrome bucketing more than
line-height) but ran higher than what was actually measured here. Do not
read this table as "the project reached 15% density" — it is the delta
from this specific fix wave only, layered on top of whatever the
preceding 13 tasks already achieved.
