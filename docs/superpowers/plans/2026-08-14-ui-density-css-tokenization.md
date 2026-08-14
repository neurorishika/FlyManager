# UI Density + CSS Tokenization Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make every FlyManager surface ~15% denser while replacing all hardcoded spacing/type values with tokens and moving ~4,800 lines of inline template CSS into layered static files.

**Architecture:** A visual-regression harness is built first so every later change is mechanically verifiable. Then CSS is extracted from Jinja `<style>` blocks into layered static files in four verbatim, zero-visual-change moves. Only after the foundation is in place do the token values tighten — one small diff that delivers most of the density globally — followed by per-surface sweeps.

**Tech Stack:** Flask + Jinja2, Bootstrap 4.5.2 (vendored), plain CSS with custom properties, Docker Compose, pytest, Playwright (new dev dependency, for the regression harness).

**Spec:** [docs/superpowers/specs/2026-08-14-ui-density-css-tokenization-design.md](../specs/2026-08-14-ui-density-css-tokenization-design.md)

## Global Constraints

- **40px touch floor.** `--control-height: 40px` applied as `min-height` to `.btn`, `.form-control`, `.page-link`, `.nav-link`. No interactive control may compute below 40px tall at any viewport. The app is used mostly on tablets.
- **No `'unsafe-inline'` for styles.** `flymanager/app/security.py` sets `style-src: 'self' 'nonce-…'`. Never add a `style="…"` attribute to any template — the codebase currently has zero and they would be blocked. Per-request dynamic values stay in nonce'd `<style>` blocks that emit *only* CSS custom properties.
- **Cascade order is load-bearing.** Extracted `<link>` tags go exactly where the `<style>` block sat (line 20 of `base.html`, *after* `{% block head %}`), in original source order. Each extracted file carries its own `@media` rules at its own end.
- **Phase 0 is visually identical.** Tasks 2–5 must produce byte-identical screenshots. A visual diff in those tasks is a bug, not a judgement call.
- **Test baseline:** 16 pre-existing test failures. No task may add a 17th.
- **Spacing scale:** `--space-2xs:2px --space-xs:4px --space-sm:6px --space-md:8px --space-lg:12px --space-xl:16px --space-2xl:24px`
- **Type scale:** `--font-size-xs:0.72rem`, `sm→0.80rem`, `base→0.90rem`, `lg→1.05rem`, `xl→1.6rem`, `line-height:1.4`, `--page-gutter→16px`, headings ~18% down.

---

### Task 1: Visual-regression harness

Everything after this depends on being able to prove a change did or didn't alter rendering. The existing `driver.sh screenshot` only captures the *public login page*; this task adds authenticated, multi-viewport, multi-theme capture plus an automated tap-target assertion.

**Files:**
- Modify: `pyproject.toml` (dev dependency group, line 51-52)
- Create: `.claude/skills/run-flymanager/css_audit.py`
- Create: `.claude/skills/run-flymanager/shots/.gitignore`

**Interfaces:**
- Produces: `python .claude/skills/run-flymanager/css_audit.py capture <label>` writes PNGs to `shots/<label>/`; `… compare <label-a> <label-b>` exits 0 if every PNG is byte-identical, 1 with a per-page report otherwise; `… tapcheck` exits non-zero if any control computes under 40px.

Byte comparison rather than perceptual diffing is deliberate: Phase 0's requirement is *no change at all*, which a byte compare states exactly and cannot fudge. It also means any nondeterminism in rendering shows up as a false positive — which is why Step 6 gates on `capture`-ing twice and getting `IDENTICAL` before any real work starts.

- [ ] **Step 1: Add Playwright as a dev dependency**

In `pyproject.toml`, under `[tool.poetry.group.dev.dependencies]`:

```toml
[tool.poetry.group.dev.dependencies]
fakeredis = "^2.26"
playwright = "^1.47"
```

- [ ] **Step 2: Install it**

```bash
poetry install --with dev && poetry run playwright install chromium
```

Expected: chromium downloads, no dependency resolution errors.

- [ ] **Step 3: Write the harness**

Create `.claude/skills/run-flymanager/css_audit.py`:

```python
"""Visual-regression + tap-target harness for FlyManager CSS work.

Usage:
    python css_audit.py capture <label>
    python css_audit.py compare <label-a> <label-b>
    python css_audit.py tapcheck
"""
import sys
from pathlib import Path

from playwright.sync_api import sync_playwright

BASE = "http://127.0.0.1:5234"
USER, PASSWORD = "devtest", "DevTestPassw0rd"
SHOTS = Path(__file__).parent / "shots"

# (slug, path) — routes covering every surface the sweep touches.
PAGES = [
    ("home", "/"),
    ("stock-explorer", "/stock/explorer"),
    ("cross-explorer", "/cross/cross_explorer"),
    ("trays", "/tray/trays"),
    ("flip-schedule", "/flip/schedule"),
    ("flip", "/flip/"),
    ("add-stock", "/stock/add"),
    ("add-cross", "/cross/add_cross"),
    ("settings", "/settings"),
    ("standardization", "/stock/standardization_overview"),
]
VIEWPORTS = [("tablet", 1024, 768), ("desktop", 1600, 1000)]
THEMES = ["light", "dark"]
CONTROLS = ".btn, .form-control, .page-link, .nav-link"
MIN_TAP_PX = 40


def _login(page):
    page.goto(f"{BASE}/auth/login")
    page.fill("#username", USER)
    page.fill("#password", PASSWORD)
    page.click("button[type=submit]")
    page.wait_for_url(f"{BASE}/**")


def _settle(page):
    """Freeze animations/transitions so screenshots are deterministic."""
    page.add_style_tag(content="*,*::before,*::after{"
                               "transition:none!important;animation:none!important}")
    page.wait_for_timeout(250)


def capture(label):
    out = SHOTS / label
    out.mkdir(parents=True, exist_ok=True)
    with sync_playwright() as pw:
        browser = pw.chromium.launch()
        for theme in THEMES:
            for vp_name, width, height in VIEWPORTS:
                ctx = browser.new_context(viewport={"width": width, "height": height})
                ctx.add_init_script(
                    f"localStorage.setItem('theme', '{theme}')")
                page = ctx.new_page()
                _login(page)
                for slug, path in PAGES:
                    page.goto(f"{BASE}{path}", wait_until="networkidle")
                    _settle(page)
                    page.screenshot(
                        path=out / f"{slug}--{theme}--{vp_name}.png",
                        full_page=True)
                    print(f"captured {slug} {theme} {vp_name}")
                ctx.close()
        browser.close()


def compare(label_a, label_b):
    dir_a, dir_b = SHOTS / label_a, SHOTS / label_b
    failures = []
    for shot in sorted(dir_a.glob("*.png")):
        other = dir_b / shot.name
        if not other.exists():
            failures.append(f"{shot.name}: missing in {label_b}")
        elif shot.read_bytes() != other.read_bytes():
            failures.append(
                f"{shot.name}: differs ({shot.stat().st_size} vs "
                f"{other.stat().st_size} bytes)")
    if failures:
        print(f"DIFF: {len(failures)} of {len(list(dir_a.glob('*.png')))} pages")
        for line in failures:
            print(f"  {line}")
        return 1
    print(f"IDENTICAL: all {len(list(dir_a.glob('*.png')))} pages match")
    return 0


def tapcheck():
    violations = []
    with sync_playwright() as pw:
        browser = pw.chromium.launch()
        ctx = browser.new_context(viewport={"width": 1024, "height": 768})
        page = ctx.new_page()
        _login(page)
        for slug, path in PAGES:
            page.goto(f"{BASE}{path}", wait_until="networkidle")
            _settle(page)
            small = page.eval_on_selector_all(
                CONTROLS,
                """(els, floor) => els
                    .filter(e => e.offsetParent !== null)
                    .map(e => ({
                        h: Math.round(e.getBoundingClientRect().height),
                        sel: e.className || e.tagName,
                    }))
                    .filter(e => e.h > 0 && e.h < floor)""",
                MIN_TAP_PX)
            for item in small:
                violations.append(f"{slug}: {item['h']}px  {item['sel'][:60]}")
        browser.close()
    if violations:
        print(f"TAP-TARGET VIOLATIONS ({len(violations)}), floor {MIN_TAP_PX}px:")
        for line in violations:
            print(f"  {line}")
        return 1
    print(f"TAP-TARGETS OK: none below {MIN_TAP_PX}px")
    return 0


if __name__ == "__main__":
    cmd = sys.argv[1]
    if cmd == "capture":
        capture(sys.argv[2])
    else:
        sys.exit(compare(sys.argv[2], sys.argv[3]) if cmd == "compare" else tapcheck())
```

- [ ] **Step 4: Keep screenshots out of git**

Create `.claude/skills/run-flymanager/shots/.gitignore`:

```gitignore
*
!.gitignore
```

- [ ] **Step 5: Bring the stack up and prove the harness works**

```bash
./.claude/skills/run-flymanager/driver.sh up
./.claude/skills/run-flymanager/driver.sh bootstrap
poetry run python .claude/skills/run-flymanager/css_audit.py capture baseline
```

Expected: 40 PNGs (10 pages × 2 themes × 2 viewports) in `shots/baseline/`. If a route 404s, remove it from `PAGES` and note which in the commit message — do not leave a crashing entry.

- [ ] **Step 6: Prove `compare` detects both identity and difference**

```bash
poetry run python .claude/skills/run-flymanager/css_audit.py capture baseline2
poetry run python .claude/skills/run-flymanager/css_audit.py compare baseline baseline2
```

Expected: `IDENTICAL: all 40 pages match`. If this fails, the harness is non-deterministic and MUST be fixed before continuing — every later task depends on it. Likely culprits: an unfrozen animation, or live data (timestamps) on a page; drop that page from `PAGES` if so.

Then confirm it catches a real change:

```bash
echo 'body { letter-spacing: 0.5px; }' >> flymanager/app/static/css/floating_cart.css
poetry run python .claude/skills/run-flymanager/css_audit.py capture scratch
poetry run python .claude/skills/run-flymanager/css_audit.py compare baseline scratch
git checkout flymanager/app/static/css/floating_cart.css
```

Expected: `DIFF: N of 40 pages` — proving the harness has teeth.

- [ ] **Step 7: Record the tap-target starting point**

```bash
poetry run python .claude/skills/run-flymanager/css_audit.py tapcheck
```

Expected: some violations today (Bootstrap `.btn-sm` and `.page-link` are under 40px). Record the exact count in the commit message — Task 6 must drive it to zero.

- [ ] **Step 8: Commit**

```bash
git add pyproject.toml poetry.lock .claude/skills/run-flymanager/css_audit.py \
        .claude/skills/run-flymanager/shots/.gitignore
git commit -m "test: add visual-regression and tap-target harness for CSS work"
```

---

### Task 2: Extract tokens.css and base.css (no visual change)

**Files:**
- Create: `flymanager/app/static/css/tokens.css`
- Create: `flymanager/app/static/css/base.css`
- Modify: `flymanager/app/templates/base.html:20-135` (remove those rules), `:19-20` (add links)

**Interfaces:**
- Produces: `--accent-color` custom property, consumed by every later file in place of the 49 `{{ settings.theme.accent_color }}` interpolations.

- [ ] **Step 1: Capture the pre-change baseline**

```bash
poetry run python .claude/skills/run-flymanager/css_audit.py capture pre-task2
```

- [ ] **Step 2: Create `tokens.css` from base.html lines 21-78, verbatim**

Copy both `:root[data-theme="light"]` and `:root[data-theme="dark"]` blocks exactly as they are — **do not change any value in this task**. Replace only the two Jinja interpolations:

```css
/* tokens.css — design tokens. The single place scale values change. */
:root[data-theme="light"] {
    --bg-color: #f0f0f0;
    /* … all existing declarations, values UNCHANGED … */
    --header-bg: var(--accent-color);
    /* --accent-color itself is emitted by base.html from lab settings */
}
```

Both `--header-bg` and `--accent-color` currently read `{{ settings.theme.accent_color }}`; `--header-bg` becomes `var(--accent-color)` and the `--accent-color` declaration is dropped here (base.html supplies it).

- [ ] **Step 3: Create `base.css` from base.html lines 79-135, verbatim**

`body`, `h1`–`h5`, `small`, `.app-shell-container`, `a`, `a:hover`, `a:visited:not(.btn)`, `.btn-link`, `.btn-link:hover`, `a.btn`. Every `{{ settings.theme.accent_color }}` becomes `var(--accent-color)`. **No value changes.**

- [ ] **Step 4: Link them and emit the accent variable**

In `base.html`, delete lines 21-135 from the `<style>` block and insert immediately before the (now shorter) `<style>` tag:

```html
    <link rel="stylesheet" href="{{ url_for('static', filename='css/tokens.css') }}">
    <link rel="stylesheet" href="{{ url_for('static', filename='css/base.css') }}">
    <style nonce="{{ csp_nonce }}">
        :root { --accent-color: {{ settings.theme.accent_color }}; }
```

The `<style>` block keeps its remaining rules below that line. **Position matters:** the links must sit after `{% block head %}` (line 19), exactly where the `<style>` block sits, or per-page CSS ordering changes.

- [ ] **Step 5: Verify identical rendering**

```bash
poetry run python .claude/skills/run-flymanager/css_audit.py capture task2
poetry run python .claude/skills/run-flymanager/css_audit.py compare pre-task2 task2
```

Expected: `IDENTICAL: all 40 pages match`. Any diff means a rule was dropped, reordered, or an interpolation missed — fix before committing.

- [ ] **Step 6: Commit**

```bash
git add flymanager/app/static/css/tokens.css flymanager/app/static/css/base.css \
        flymanager/app/templates/base.html
git commit -m "refactor(css): extract tokens.css and base.css from base.html"
```

---

### Task 3: Extract bootstrap-density.css (no visual change)

**Files:**
- Create: `flymanager/app/static/css/bootstrap-density.css`
- Modify: `flymanager/app/templates/base.html` (remove the Bootstrap-override rules, add one link)

**Interfaces:**
- Consumes: `--accent-color` from Task 2.
- Produces: the file where Task 6 adds the touch floor and component density.

- [ ] **Step 1: Capture baseline**

```bash
poetry run python .claude/skills/run-flymanager/css_audit.py capture pre-task3
```

- [ ] **Step 2: Move all Bootstrap component overrides, verbatim**

From the remaining `<style>` block, move these rule groups (originally lines 136-421 and 1124-1197), preserving their relative order:

- `.form-control` (+ `:disabled`, `[readonly]`, `:focus`), `.tagify`, `label`, `.form-group`
- all `.btn-*` variants: `primary`, `outline-info`, `info`, `warning`, `outline-warning`, `success`, `outline-secondary` with their `:hover`/`.active`/`.focus`/`.disabled` states
- `.page-link` (+ `:hover`, `:focus`), `.page-item.active .page-link`, `.page-item.disabled .page-link`
- `.nav-tabs`, `.nav-tabs .nav-link` (+ states)
- `.custom-control-input` states, `.custom-control-label::before`
- `.btn`, `.btn-sm`, `.input-group > .form-control:not(:last-child)`, `.input-group > .btn`
- `.card`, `.card-header`, `.nav-item .dropdown-menu`, `.nav-item .dropdown-item` (+ `:hover`)
- `.table`, `.table-responsive`, `.alert`, `.alert-info`, `.modal-content`, `.modal-dialog`, `.badge-info`, `.breadcrumb`

Replace every `{{ settings.theme.accent_color }}` with `var(--accent-color)`. **No value changes.**

- [ ] **Step 3: Move the `.modal-dialog` responsive rule**

From the `@media (max-width: 767px)` block (originally lines 1234-1339), move *only* the `.modal-dialog` rule into a `@media (max-width: 767px)` block at the **end** of `bootstrap-density.css`. Leave the rest of that media block in `base.html` for now.

- [ ] **Step 4: Add the link**

In `base.html`, after the `base.css` link:

```html
    <link rel="stylesheet" href="{{ url_for('static', filename='css/bootstrap-density.css') }}">
```

- [ ] **Step 5: Verify identical rendering**

```bash
poetry run python .claude/skills/run-flymanager/css_audit.py capture task3
poetry run python .claude/skills/run-flymanager/css_audit.py compare pre-task3 task3
```

Expected: `IDENTICAL: all 40 pages match`.

- [ ] **Step 6: Commit**

```bash
git add flymanager/app/static/css/bootstrap-density.css flymanager/app/templates/base.html
git commit -m "refactor(css): extract Bootstrap component overrides to bootstrap-density.css"
```

---

### Task 4: Extract layout.css (no visual change)

**Files:**
- Create: `flymanager/app/static/css/layout.css`
- Modify: `flymanager/app/templates/base.html`

- [ ] **Step 1: Capture baseline**

```bash
poetry run python .claude/skills/run-flymanager/css_audit.py capture pre-task4
```

- [ ] **Step 2: Move header, brand, nav, footer, and auth-shell rules, verbatim**

From the remaining `<style>` block, move (originally lines 422-912 and 1060-1123): `.header*`, `.app-brand*`, `.theme-toggle*`, `.main-content`, `.footer*`, `.auth-shell`, `.auth-card`, `.auth-logo`, `.auth-brand-mark`, `.auth-actions`, `.auth-form select.form-control`, `.auth-support`.

Replace accent-color interpolations with `var(--accent-color)`. **No value changes.**

- [ ] **Step 3: Move the header responsive rules**

Move the **entire** `@media (max-width: 991px)` block (all seven rules are `.header-*`) and the `.header-*` / `.app-brand-meta span` / `.theme-toggle-label` rules from the `@media (max-width: 767px)` block into matching media blocks at the **end** of `layout.css`.

Leave in `base.html`'s block for now: `body`, `.main-content`, `.app-panel`, `.app-form-grid.two-column`, `.app-detail-grid`, `.span-9` — Task 5 places those. (`.main-content` moves to `layout.css` with its media rule; the rest go to `components.css`/`base.css`.)

- [ ] **Step 4: Move `body`'s responsive rule to base.css**

The `@media (max-width: 767px)` block's `body` rule belongs with `body` — append a matching media block to the **end** of `base.css` containing it.

- [ ] **Step 5: Add the link**

```html
    <link rel="stylesheet" href="{{ url_for('static', filename='css/layout.css') }}">
```

- [ ] **Step 6: Verify identical rendering**

```bash
poetry run python .claude/skills/run-flymanager/css_audit.py capture task4
poetry run python .claude/skills/run-flymanager/css_audit.py compare pre-task4 task4
```

Expected: `IDENTICAL: all 40 pages match`. A diff at narrow viewports specifically means a media rule landed in a file that loads before the base rule it overrides — check load order.

- [ ] **Step 7: Commit**

```bash
git add flymanager/app/static/css/layout.css flymanager/app/static/css/base.css \
        flymanager/app/templates/base.html
git commit -m "refactor(css): extract header, footer, and auth layout to layout.css"
```

---

### Task 5: Extract components.css and finish base.html (no visual change)

This empties the 1,362-line `<style>` block down to a 3-line variable emitter.

**Files:**
- Create: `flymanager/app/static/css/components.css`
- Modify: `flymanager/app/templates/base.html`

**Interfaces:**
- Produces: `{% block page_css %}{% endblock %}` — the hook later tasks use for per-page stylesheets that must override component rules.

- [ ] **Step 1: Capture baseline**

```bash
poetry run python .claude/skills/run-flymanager/css_audit.py capture pre-task5
```

- [ ] **Step 2: Move the shared app component system, verbatim**

Everything still left in the block except the accent emitter: `.app-detail-grid` (+ `> *`), `.span-3` through `.span-9`, `.app-view-field`, `.app-view-value` (+ `.multiline`), `.app-empty-shell`, `.app-empty-code`, `.app-settings-grid`, `.app-action-card`, `.app-form-progress` (+ all `[data-tone]` variants and `::before`), `.app-inline-spinner`, `.app-progress-submit`, `.app-form-grid` (+ `.two-column`), `.app-inline-note`, `.app-panel`, `.app-kicker`, `.app-page-subtitle`, `.species-display`, `.job-status-banner` (+ `--visible`), `.job-status-row` (+ `--succeeded`, `--failed`, `__label`, `__status`, `__download`), `.is-hidden`.

- [ ] **Step 3: Move the remaining responsive rules**

Append to the **end** of `components.css` a `@media (max-width: 767px)` block with the leftovers: `.app-panel`, `.app-form-grid.two-column`, `.app-detail-grid`, `.span-9`.

- [ ] **Step 4: Reduce base.html's style block and add the page hook**

The head region should now read:

```html
    <link rel="stylesheet" href="{{ url_for('static', filename='vendor/bootstrap/bootstrap-4.5.2.min.css') }}">
    <link rel="stylesheet" href="{{ url_for('static', filename='vendor/fontawesome/css/all.min.css') }}">
    <link rel="stylesheet" href="{{ url_for('static', filename='css/floating_cart.css') }}">
    {% block head %}{% endblock %}
    <link rel="stylesheet" href="{{ url_for('static', filename='css/tokens.css') }}">
    <link rel="stylesheet" href="{{ url_for('static', filename='css/base.css') }}">
    <link rel="stylesheet" href="{{ url_for('static', filename='css/bootstrap-density.css') }}">
    <link rel="stylesheet" href="{{ url_for('static', filename='css/layout.css') }}">
    <link rel="stylesheet" href="{{ url_for('static', filename='css/components.css') }}">
    {% block page_css %}{% endblock %}
    <style nonce="{{ csp_nonce }}">
        :root { --accent-color: {{ settings.theme.accent_color }}; }
    </style>
```

`{% block head %}` stays where it is so the existing explorer stylesheets keep their current (overridden) precedence. `{% block page_css %}` is new and loads last, so per-page files added in Tasks 8–11 win over component rules.

- [ ] **Step 5: Verify identical rendering**

```bash
poetry run python .claude/skills/run-flymanager/css_audit.py capture task5
poetry run python .claude/skills/run-flymanager/css_audit.py compare pre-task5 task5
```

Expected: `IDENTICAL: all 40 pages match`.

- [ ] **Step 6: Confirm the block is actually empty of rules**

```bash
awk '/<style/{s=1} /<\/style>/{s=0} s' flymanager/app/templates/base.html | wc -l
```

Expected: 3 lines. If larger, rules were missed.

- [ ] **Step 7: Run the test suite**

```bash
MONGO_URI="mongodb://127.0.0.1:27017" MONGO_DB_NAME="flymanager_test" ENABLE_SCHEDULER=0 \
  poetry run pytest tests/ -q
```

Expected: the 16 known failures, no new ones.

- [ ] **Step 8: Commit**

```bash
git add flymanager/app/static/css/components.css flymanager/app/templates/base.html
git commit -m "refactor(css): extract app component system, empty base.html style block

base.html's inline <style> goes from 1362 lines to a 3-line accent-color
emitter. Adds {% block page_css %} for per-page sheets that must override
component rules."
```

---

### Task 6: Apply the density scales

The single highest-leverage task: most of the 15% lands here, globally.

**Files:**
- Modify: `flymanager/app/static/css/tokens.css`, `base.css`, `bootstrap-density.css`

- [ ] **Step 1: Capture the pre-density reference**

```bash
poetry run python .claude/skills/run-flymanager/css_audit.py capture pre-density
```

- [ ] **Step 2: Add the three scales to tokens.css**

Add to **both** `:root[data-theme="light"]` and `:root[data-theme="dark"]` (they duplicate every declaration today; keep that structure):

```css
    /* Spacing scale — 4px based. Replaces all hardcoded px. */
    --space-2xs: 2px;
    --space-xs: 4px;
    --space-sm: 6px;
    --space-md: 8px;
    --space-lg: 12px;
    --space-xl: 16px;
    --space-2xl: 24px;

    /* Controls — density never shrinks a tap target below --control-height. */
    --control-height: 40px;
    --control-padding-x: var(--space-lg);
    --control-gap: var(--space-md);
```

- [ ] **Step 3: Tighten the type and gutter tokens**

Replace the existing declarations in both theme blocks:

```css
    --page-gutter: clamp(8px, 1.2vw, 16px);
    --font-size-xs: clamp(0.68rem, 0.66rem + 0.1vw, 0.72rem);
    --font-size-sm: clamp(0.74rem, 0.72rem + 0.14vw, 0.80rem);
    --font-size-base: clamp(0.84rem, 0.81rem + 0.18vw, 0.90rem);
    --font-size-lg: clamp(0.94rem, 0.90rem + 0.30vw, 1.05rem);
    --font-size-xl: clamp(1.15rem, 1.00rem + 0.80vw, 1.60rem);
```

- [ ] **Step 4: Tighten headings and line-height in base.css**

```css
        body { line-height: 1.4; }
        h1, .h1 { font-size: clamp(1.40rem, 1.20rem + 1.15vw, 1.95rem); }
        h2, .h2 { font-size: clamp(1.20rem, 1.03rem + 0.82vw, 1.65rem); }
        h3, .h3 { font-size: clamp(1.00rem, 0.90rem + 0.58vw, 1.28rem); }
        h4, .h4 { font-size: clamp(0.90rem, 0.84rem + 0.37vw, 1.07rem); }
        h5, .h5 { font-size: clamp(0.84rem, 0.81rem + 0.23vw, 0.92rem); }
```

Set `margin-bottom: var(--space-md)` on `h1`–`h5` (Bootstrap's default is `0.5rem` plus browser margins).

- [ ] **Step 5: Add the touch floor and component density to bootstrap-density.css**

Append:

```css
/* ---- Density layer -------------------------------------------------
   Vertical padding shrinks; min-height holds every tap target at 40px.
   Tablet is the primary device — never let a control compute smaller. */

.btn, .btn-sm, .form-control, .page-link, .nav-link {
    min-height: var(--control-height);
    padding-top: var(--space-xs);
    padding-bottom: var(--space-xs);
    padding-left: var(--control-padding-x);
    padding-right: var(--control-padding-x);
    line-height: 1.3;
}

/* Buttons and pagination center their label within the 40px box. */
.btn, .page-link, .nav-link {
    display: inline-flex;
    align-items: center;
    justify-content: center;
}

.btn-sm { font-size: var(--font-size-sm); padding-left: var(--space-md); padding-right: var(--space-md); }

.form-group { margin-bottom: var(--space-md); }
label { margin-bottom: var(--space-2xs); font-size: var(--font-size-sm); }

.card { margin-bottom: var(--space-lg); }
.card-body { padding: var(--space-lg); }
.card-header { padding: var(--space-md) var(--space-lg); font-size: var(--font-size-sm); }

.table td, .table th { padding: var(--space-sm) var(--space-md); }
.table { margin-bottom: var(--space-lg); }

.alert { padding: var(--space-md) var(--space-lg); margin-bottom: var(--space-lg); }
.badge { padding: var(--space-2xs) var(--space-sm); font-size: var(--font-size-xs); }
.breadcrumb { padding: var(--space-sm) var(--space-md); margin-bottom: var(--space-md); }
.modal-body { padding: var(--space-lg); }
.modal-header, .modal-footer { padding: var(--space-md) var(--space-lg); }
.pagination { margin-bottom: 0; }
.nav-tabs .nav-link { padding-left: var(--space-lg); padding-right: var(--space-lg); }
.input-group > .btn { min-height: var(--control-height); }
```

- [ ] **Step 6: Verify the tap-target floor is now met**

```bash
poetry run python .claude/skills/run-flymanager/css_audit.py tapcheck
```

Expected: `TAP-TARGETS OK: none below 40px`. This must pass — it is the task's primary gate, and Task 1 recorded a non-zero starting count for comparison. If `.btn-sm` still violates, its `min-height` is being overridden by a later rule; find it with devtools rather than adding `!important`.

- [ ] **Step 7: Review the density change visually**

```bash
poetry run python .claude/skills/run-flymanager/css_audit.py capture density
poetry run python .claude/skills/run-flymanager/css_audit.py compare pre-density density
```

Expected: `DIFF` on all 40 pages — that is the point of this task. Open several `shots/density/*.png` alongside `shots/pre-density/*.png` and confirm: text is smaller but readable, nothing overlaps, nothing is clipped, and no page scrolls horizontally at 1024px.

- [ ] **Step 8: Run the test suite**

```bash
MONGO_URI="mongodb://127.0.0.1:27017" MONGO_DB_NAME="flymanager_test" ENABLE_SCHEDULER=0 \
  poetry run pytest tests/ -q
```

Expected: 16 known failures, no new ones.

- [ ] **Step 9: Commit**

```bash
git add flymanager/app/static/css/tokens.css flymanager/app/static/css/base.css \
        flymanager/app/static/css/bootstrap-density.css
git commit -m "feat(ui): apply density scales globally

Type down ~10-18%, line-height 1.5->1.4, page gutter halved, Bootstrap
component padding tightened. All interactive controls hold a 40px
min-height floor for tablet use."
```

---

### Task 7: Tokenize and tighten the explorers

**Files:**
- Modify: `flymanager/app/static/css/explorer_shared.css` (102 spacing declarations), `stock_explorer.css` (31), `cross_explorer.css` (1), `floating_cart.css` (27)

- [ ] **Step 1: Capture baseline**

```bash
poetry run python .claude/skills/run-flymanager/css_audit.py capture pre-task7
```

- [ ] **Step 2: Replace every hardcoded spacing value with a token**

Map by the spec's table — `2-3px`→`--space-2xs`, `4-5px`→`--space-xs`, `6-8px`→`--space-sm`, `10px`→`--space-md`, `14-16px`→`--space-lg`, `20-24px`→`--space-xl`, `30-40px`→`--space-2xl`. Note this both tokenizes *and* tightens: a `16px` gap becoming `var(--space-lg)` is 12px. That is intended.

For `rem`-valued paddings like `padding: 0.28rem 0.6rem` (explorer_shared.css:156), convert to the nearest tokens: `padding: var(--space-xs) var(--space-md)`.

- [ ] **Step 3: Replace hardcoded font sizes with type tokens**

`0.68-0.74rem`→`--font-size-xs`, `0.75-0.82rem`→`--font-size-sm`, `0.85-0.95rem`→`--font-size-base`, `1-1.1rem`→`--font-size-lg`, larger→`--font-size-xl`.

- [ ] **Step 4: Confirm no hardcoded values remain**

```bash
grep -nE "(padding|margin|gap|font-size)[a-z-]*:\s*[0-9]" flymanager/app/static/css/explorer_shared.css \
     flymanager/app/static/css/stock_explorer.css flymanager/app/static/css/cross_explorer.css \
     flymanager/app/static/css/floating_cart.css
```

Expected: no output, except `0` values (`margin: 0`, `padding: 0`) which stay as-is — a token for zero adds nothing.

- [ ] **Step 5: Verify explorers visually and check tap targets**

```bash
poetry run python .claude/skills/run-flymanager/css_audit.py capture task7
poetry run python .claude/skills/run-flymanager/css_audit.py compare pre-task7 task7
poetry run python .claude/skills/run-flymanager/css_audit.py tapcheck
```

Expected: diffs confined to `stock-explorer` and `cross-explorer` shots; `TAP-TARGETS OK`. Open the explorer shots and confirm more rows fit without cramping, and that pagination and filter chips still align.

- [ ] **Step 6: Commit**

```bash
git add flymanager/app/static/css/
git commit -m "feat(ui): tokenize and tighten explorer and floating-cart CSS"
```

---

### Task 8: Extract and tighten home

**Files:**
- Create: `flymanager/app/static/css/pages/home.css`
- Modify: `flymanager/app/templates/home.html` (1,196 inline style lines)

- [ ] **Step 1: Capture baseline**

```bash
poetry run python .claude/skills/run-flymanager/css_audit.py capture pre-task8
```

- [ ] **Step 2: Move the static rules out, verbatim first**

Move everything from `home.html`'s `<style>` block into `pages/home.css` **except** rules containing `{{ columns }}` or `{{ value }}`. Add to `home.html`:

```html
{% block page_css %}
<link rel="stylesheet" href="{{ url_for('static', filename='css/pages/home.css') }}">
{% endblock %}
```

If `home.html` already defines `{% block head %}`, keep it — `page_css` is a separate block.

- [ ] **Step 3: Convert the dynamic rules to custom properties**

The remaining `<style>` block emits variables only. Where a rule was `grid-template-columns: repeat({{ columns }}, 1fr)`, the block becomes:

```html
<style nonce="{{ csp_nonce }}">
    .home-grid { --home-grid-columns: {{ columns }}; }
</style>
```

and `pages/home.css` gains the static rule that consumes it:

```css
.home-grid { grid-template-columns: repeat(var(--home-grid-columns), 1fr); }
```

Apply the same pattern to `{{ value }}`. Use the real class names from the template — do not invent selectors.

- [ ] **Step 4: Verify the move alone changed nothing**

```bash
poetry run python .claude/skills/run-flymanager/css_audit.py capture task8-move
poetry run python .claude/skills/run-flymanager/css_audit.py compare pre-task8 task8-move
```

Expected: `IDENTICAL`. Do this before tightening, so a broken move can't hide inside an intentional diff.

- [ ] **Step 5: Tokenize and tighten**

Apply the same mapping as Task 7 to all 115 spacing declarations and every `font-size` in `pages/home.css`. Additionally, per the spec's chrome-reduction goal: drop section headers that restate the page title, and demote panel titles styled as headings to `--font-size-sm` labels where the heading adds nothing.

- [ ] **Step 6: Verify**

```bash
poetry run python .claude/skills/run-flymanager/css_audit.py capture task8
poetry run python .claude/skills/run-flymanager/css_audit.py compare task8-move task8
poetry run python .claude/skills/run-flymanager/css_audit.py tapcheck
grep -nE "(padding|margin|gap|font-size)[a-z-]*:\s*[0-9]" flymanager/app/static/css/pages/home.css
```

Expected: diffs confined to `home` shots, `TAP-TARGETS OK`, and no hardcoded values except zeros. Review the home shots at both viewports and themes.

- [ ] **Step 7: Commit**

```bash
git add flymanager/app/static/css/pages/home.css flymanager/app/templates/home.html
git commit -m "feat(ui): extract home CSS to static file, tokenize and tighten"
```

---

### Task 9: Extract and tighten stock and cross detail pages

**Files:**
- Create: `flymanager/app/static/css/pages/view_stock.css`, `pages/view_cross.css`
- Modify: `flymanager/app/templates/stock/view_stock.html` (405 style lines), `cross/view_cross.html` (48)

- [ ] **Step 1: Capture baseline**

Add the two detail routes to `PAGES` in `css_audit.py` first — they need a real record ID. Find one:

```bash
docker exec flymanager-mongodb mongosh --quiet --eval \
  'db.getSiblingDB("flymanager").stocks.findOne({}, {UniqueID:1})'
```

Add `("view-stock", "/stock/<that-uid>")` to `PAGES`, then:

```bash
poetry run python .claude/skills/run-flymanager/css_audit.py capture pre-task9
```

- [ ] **Step 2: Move both style blocks out, verbatim**

Neither template has dynamic CSS (only `{{ csp_nonce }}`), so both `<style>` blocks move entirely and are deleted. Add to each template:

```html
{% block page_css %}
<link rel="stylesheet" href="{{ url_for('static', filename='css/pages/view_stock.css') }}">
{% endblock %}
```

(and `view_cross.css` in the other).

- [ ] **Step 3: Verify the move alone changed nothing**

```bash
poetry run python .claude/skills/run-flymanager/css_audit.py capture task9-move
poetry run python .claude/skills/run-flymanager/css_audit.py compare pre-task9 task9-move
```

Expected: `IDENTICAL`.

- [ ] **Step 4: Tokenize, tighten, and cut chrome**

Apply the Task 7 mapping to all 51 spacing declarations in `view_stock.css` and the rest in `view_cross.css`. Detail pages are where chrome reduction pays most: remove section headers restating the page title, and collapse wrapper elements that contribute only padding.

- [ ] **Step 5: Verify**

```bash
poetry run python .claude/skills/run-flymanager/css_audit.py capture task9
poetry run python .claude/skills/run-flymanager/css_audit.py compare task9-move task9
poetry run python .claude/skills/run-flymanager/css_audit.py tapcheck
grep -nE "(padding|margin|gap|font-size)[a-z-]*:\s*[0-9]" flymanager/app/static/css/pages/view_stock.css \
     flymanager/app/static/css/pages/view_cross.css
```

Expected: diffs confined to the detail shots, `TAP-TARGETS OK`, no hardcoded values except zeros.

- [ ] **Step 6: Commit**

```bash
git add flymanager/app/static/css/pages/ flymanager/app/templates/stock/view_stock.html \
        flymanager/app/templates/cross/view_cross.html .claude/skills/run-flymanager/css_audit.py
git commit -m "feat(ui): extract stock and cross detail CSS, tokenize and tighten"
```

---

### Task 10: Extract and tighten tray, flip, and flip schedule

`view_tray.html` is the most delicate template in the plan — its grid dimensions are per-record.

**Files:**
- Create: `flymanager/app/static/css/pages/view_tray.css`, `pages/flip.css`, `pages/flip_schedule.css`
- Modify: `flymanager/app/templates/tray/view_tray.html` (656 style lines), `flip.html` (349), `flip_schedule.html` (302)

- [ ] **Step 1: Capture baseline**

Add a real tray route to `PAGES` the same way as Task 9:

```bash
docker exec flymanager-mongodb mongosh --quiet --eval \
  'db.getSiblingDB("flymanager").trays.findOne({}, {_id:1})'
poetry run python .claude/skills/run-flymanager/css_audit.py capture pre-task10
```

- [ ] **Step 2: Move flip.css and flip_schedule.css out, verbatim**

Both have only `{{ csp_nonce }}` — entire blocks move, `{% block page_css %}` links added.

- [ ] **Step 3: Convert view_tray's grid to custom properties**

`view_tray.html` uses `{{ tray.Rows }}` and `{{ tray.Columns }}`. Its `<style>` block reduces to:

```html
<style nonce="{{ csp_nonce }}">
    .tray-grid {
        --tray-rows: {{ tray.Rows }};
        --tray-cols: {{ tray.Columns }};
    }
</style>
```

and `pages/view_tray.css` consumes them:

```css
.tray-grid {
    display: grid;
    grid-template-rows: repeat(var(--tray-rows), 1fr);
    grid-template-columns: repeat(var(--tray-cols), 1fr);
}
```

Use the template's real selectors. Every other rule in the block moves verbatim.

- [ ] **Step 4: Verify the moves alone changed nothing**

```bash
poetry run python .claude/skills/run-flymanager/css_audit.py capture task10-move
poetry run python .claude/skills/run-flymanager/css_audit.py compare pre-task10 task10-move
```

Expected: `IDENTICAL`. Pay particular attention to the tray shot — a wrong grid variable produces a visibly collapsed layout.

- [ ] **Step 5: Tokenize and tighten all three**

Apply the Task 7 mapping. Tray wells are size-constrained by their grid, so tighten the surrounding chrome (headers, legends, controls) rather than the cells themselves.

- [ ] **Step 6: Verify**

```bash
poetry run python .claude/skills/run-flymanager/css_audit.py capture task10
poetry run python .claude/skills/run-flymanager/css_audit.py compare task10-move task10
poetry run python .claude/skills/run-flymanager/css_audit.py tapcheck
grep -nE "(padding|margin|gap|font-size)[a-z-]*:\s*[0-9]" flymanager/app/static/css/pages/view_tray.css \
     flymanager/app/static/css/pages/flip.css flymanager/app/static/css/pages/flip_schedule.css
```

Expected: diffs confined to tray/flip shots, `TAP-TARGETS OK`, no hardcoded values except zeros. Tray wells are tap targets — confirm they did not shrink below 40px.

- [ ] **Step 7: Commit**

```bash
git add flymanager/app/static/css/pages/ flymanager/app/templates/tray/view_tray.html \
        flymanager/app/templates/flip.html flymanager/app/templates/flip_schedule.html \
        .claude/skills/run-flymanager/css_audit.py
git commit -m "feat(ui): extract tray and flip CSS, tokenize and tighten"
```

---

### Task 11: Extract and tighten the remaining pages

**Files:**
- Create: `flymanager/app/static/css/pages/user_guide.css`, `pages/phenotype_preview.css`, `pages/standardization_overview.css`
- Modify: `flymanager/app/templates/utilities/user_guide.html` (255), `stock/phenotype_preview.html` (138), `stock/standardization_overview.html` (97)

- [ ] **Step 1: Capture baseline**

```bash
poetry run python .claude/skills/run-flymanager/css_audit.py capture pre-task11
```

- [ ] **Step 2: Move all three blocks out, verbatim**

All three contain only `{{ csp_nonce }}` — entire blocks move, `{% block page_css %}` links added, `<style>` blocks deleted entirely.

- [ ] **Step 3: Verify the move alone changed nothing**

```bash
poetry run python .claude/skills/run-flymanager/css_audit.py capture task11-move
poetry run python .claude/skills/run-flymanager/css_audit.py compare pre-task11 task11-move
```

Expected: `IDENTICAL`.

- [ ] **Step 4: Tokenize and tighten**

Apply the Task 7 mapping to all three files.

- [ ] **Step 5: Verify**

```bash
poetry run python .claude/skills/run-flymanager/css_audit.py capture task11
poetry run python .claude/skills/run-flymanager/css_audit.py compare task11-move task11
poetry run python .claude/skills/run-flymanager/css_audit.py tapcheck
grep -rnE "(padding|margin|gap|font-size)[a-z-]*:\s*[0-9]" flymanager/app/static/css/pages/
```

Expected: `TAP-TARGETS OK`, no hardcoded values except zeros.

- [ ] **Step 6: Commit**

```bash
git add flymanager/app/static/css/pages/ flymanager/app/templates/utilities/user_guide.html \
        flymanager/app/templates/stock/phenotype_preview.html \
        flymanager/app/templates/stock/standardization_overview.html
git commit -m "feat(ui): extract remaining page CSS, tokenize and tighten"
```

---

### Task 12: Tighten forms and close out

Forms are the last surface. This task also proves the whole codebase is clean.

**Files:**
- Modify: `flymanager/app/static/css/bootstrap-density.css`, `components.css`
- Modify: `flymanager/app/templates/stock/add_stock.html`, `cross/add_cross.html`, `tray/edit_tray.html`, `tray/add_tray.html`, `stock/filter_stock.html`, `cross/filter_cross.html` (only if they carry spacing markup)
- Create: `docs/ui-density.md`

- [ ] **Step 1: Capture baseline**

```bash
poetry run python .claude/skills/run-flymanager/css_audit.py capture pre-task12
```

- [ ] **Step 2: Tighten form rhythm in components.css**

`.app-form-grid`, `.app-form-grid.two-column`, and `.app-inline-note` carry the form layout. Set their gaps to `var(--space-md)` and their margins to `var(--space-md)`. Labels already dropped to `--font-size-sm` in Task 6.

- [ ] **Step 3: Confirm no hardcoded values remain anywhere**

```bash
grep -rnE "(padding|margin|gap|font-size)[a-z-]*:\s*[0-9]" flymanager/app/static/css/ \
  --include="*.css" | grep -v vendor | grep -vE ":\s*0(px)?\s*;"
```

Expected: no output. This is the goal-2 acceptance check for the entire project — every remaining spacing and type value is a token.

- [ ] **Step 4: Confirm templates carry no CSS rules**

```bash
for f in $(find flymanager/app/templates -name "*.html" -not -path "*/.old/*"); do
  n=$(awk '/<style/{s=1} s{c++} /<\/style>/{s=0} END{print c+0}' "$f")
  [ "$n" -gt 6 ] && echo "$n  $f"
done
```

Expected: no output. Every surviving `<style>` block is a short variable emitter (base.html 3 lines, home.html and view_tray.html ~5).

- [ ] **Step 5: Document the system**

Create `docs/ui-density.md`:

````markdown
# UI density and CSS structure

All spacing and type values are tokens defined in
`flymanager/app/static/css/tokens.css`. To make the whole UI tighter or
roomier, change the scale there — do not add hardcoded px to a component.

## Layers (load order, defined in `base.html`)

| File | Owns |
| --- | --- |
| `tokens.css` | Theme colors + spacing, type, and control scales |
| `base.css` | Element defaults: body, headings, links |
| `bootstrap-density.css` | Overrides for vendored Bootstrap 4.5 components |
| `layout.css` | Header, brand, nav, footer, auth shell |
| `components.css` | The shared `.app-*` component system |
| `pages/*.css` | One file per page, loaded via `{% block page_css %}` |

Each file carries its own `@media` rules at its end.

## Rules

- **Never add a `style="…"` attribute.** The CSP has no `'unsafe-inline'`;
  it will be blocked. Per-request values go in a nonce'd `<style>` block
  that emits only custom properties, consumed by a static rule.
- **Never let a control compute below 40px tall.** FlyManager is used
  mostly on tablets. `--control-height` is the floor; get density from
  spacing and type instead.
- **Page CSS goes in `{% block page_css %}`**, which loads after the
  component layer so page rules win.

## Verifying a CSS change

```bash
./.claude/skills/run-flymanager/driver.sh up
poetry run python .claude/skills/run-flymanager/css_audit.py capture before
# … make the change …
poetry run python .claude/skills/run-flymanager/css_audit.py capture after
poetry run python .claude/skills/run-flymanager/css_audit.py compare before after
poetry run python .claude/skills/run-flymanager/css_audit.py tapcheck
```
````

- [ ] **Step 6: Full verification**

```bash
poetry run python .claude/skills/run-flymanager/css_audit.py capture final
poetry run python .claude/skills/run-flymanager/css_audit.py compare baseline final
poetry run python .claude/skills/run-flymanager/css_audit.py tapcheck
MONGO_URI="mongodb://127.0.0.1:27017" MONGO_DB_NAME="flymanager_test" ENABLE_SCHEDULER=0 \
  poetry run pytest tests/ -q
```

Expected: `DIFF` on all pages versus the original `baseline` (the whole point), `TAP-TARGETS OK`, and exactly the 16 known test failures.

- [ ] **Step 7: Confirm no horizontal overflow at tablet width**

```bash
poetry run python - <<'PY'
from playwright.sync_api import sync_playwright
import sys
sys.path.insert(0, ".claude/skills/run-flymanager")
from css_audit import PAGES, BASE, _login, _settle
bad = []
with sync_playwright() as pw:
    b = pw.chromium.launch()
    p = b.new_context(viewport={"width": 1024, "height": 768}).new_page()
    _login(p)
    for slug, path in PAGES:
        p.goto(f"{BASE}{path}", wait_until="networkidle"); _settle(p)
        if p.evaluate("document.documentElement.scrollWidth > window.innerWidth + 1"):
            bad.append(slug)
    b.close()
print("OVERFLOW:", bad if bad else "none")
PY
```

Expected: `OVERFLOW: none`.

- [ ] **Step 8: Commit**

```bash
git add flymanager/app/static/css/ flymanager/app/templates/ docs/ui-density.md
git commit -m "feat(ui): tighten form rhythm, document the CSS token system"
```

- [ ] **Step 9: Review the result against the original complaint**

Open `shots/final/` beside `shots/baseline/` and confirm on the explorer shots that roughly 7 list rows now fit where 5 did. If density still reads as insufficient, the fix is a single edit to `tokens.css` — that is what the tokenization bought.

---

## Self-Review

**Spec coverage:**

| Spec requirement | Task |
| --- | --- |
| Spacing scale (7 steps) | 6 |
| Type scale + headings + line-height + gutter | 6 |
| Control tokens + 40px touch floor | 6, verified every task |
| `tokens.css` / `base.css` | 2 |
| `bootstrap-density.css` | 3, 6 |
| `layout.css` | 4 |
| `components.css` | 5 |
| `pages/*.css` (9 files) | 8, 9, 10, 11 |
| Explorer/cart files tokenized in place | 7 |
| Dynamic vars: accent, home, tray | 2, 8, 10 |
| Phase 0 zero-visual-change | 2, 3, 4, 5 |
| Chrome reduction | 8, 9 |
| Per-phase screenshots, 2 viewports, 2 themes | 1, all tasks |
| Tap-target verification | 1, all tasks |
| No horizontal overflow at 1024px | 12 |
| Test suite baseline (16 failures) | 5, 6, 12 |

No gaps.

**Note on one spec deviation:** the spec listed six phases; this plan uses twelve tasks. Phase 0 is split into four independently verifiable extractions (Tasks 2–5) because a single 4,800-line move cannot be meaningfully reviewed, and Phase 4 is split by template group (Tasks 9–11) because `view_tray.html`'s dynamic grid carries different risk than the static pages. The phase boundaries and their gates are otherwise unchanged.

**Placeholder scan:** No TBDs. Every code step carries real code. The one place exact selectors are not pre-written is the dynamic-variable conversion in Tasks 8 and 10, where the instruction is explicitly to use the template's real class names rather than invented ones — the pattern is fully shown with real variable names.

**Type consistency:** `capture`/`compare`/`tapcheck` keep the same signatures throughout. `PAGES`, `BASE`, `_login`, `_settle` are defined in Task 1 and imported by name in Task 12. Token names are identical across the spec, Task 6, and the mapping instructions in Tasks 7–12.
