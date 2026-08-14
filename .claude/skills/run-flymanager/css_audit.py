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
# Cached session cookies, kept under shots/ so shots/.gitignore excludes it.
# Logging in writes a "Logged in" activity row that the home dashboard
# renders with a to-the-minute timestamp ("Last Activity" card); logging in
# fresh for every browser context (as this harness originally did — once per
# theme x viewport combination) made that timestamp advance between two
# otherwise-identical capture runs and broke byte-for-byte comparison. Doing
# the real form login exactly once and reusing the resulting storage_state
# for every subsequent context (across capture/tapcheck calls, even across
# separate invocations of this script) keeps that timestamp — and the
# activity log in general, since every other write_activity() call in the
# app is triggered by a POST this harness never makes — stable.
AUTH_STATE = SHOTS / "_auth_state.json"

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
    # wait_for_url(f"{BASE}/**") proved flaky against the login page itself
    # (it also matches BASE + "/auth/login"); wait for a concrete
    # post-login landmark instead.
    page.wait_for_selector(".header", timeout=15000)


def _ensure_auth_state(browser):
    """Make sure AUTH_STATE holds a valid, already-authenticated session,
    performing the one real login via _login() only if needed (no cached
    state file, or the cached one has expired/been invalidated)."""
    if AUTH_STATE.exists():
        ctx = browser.new_context(storage_state=str(AUTH_STATE), bypass_csp=True)
        page = ctx.new_page()
        page.goto(f"{BASE}/", wait_until="domcontentloaded")
        try:
            page.wait_for_selector(".header", timeout=3000)
            ctx.close()
            return
        except Exception:
            ctx.close()

    ctx = browser.new_context(bypass_csp=True)
    page = ctx.new_page()
    _login(page)
    AUTH_STATE.parent.mkdir(parents=True, exist_ok=True)
    ctx.storage_state(path=str(AUTH_STATE))
    ctx.close()


def _settle(page):
    """Freeze animations/transitions so screenshots are deterministic.

    NOTE: does NOT use page.add_style_tag() — the app's CSP
    (style-src 'self' 'nonce-...', no 'unsafe-inline') blocks a
    DOM-injected <style> element, which would silently fail to freeze
    animations and make screenshots nondeterministic. Determinism instead
    comes from bypass_csp=True on the browser context (so the injected
    style *would* work, though we still avoid relying on it) plus
    animations="disabled" on every screenshot call.
    """
    page.wait_for_timeout(250)


def capture(label):
    out = SHOTS / label
    out.mkdir(parents=True, exist_ok=True)
    with sync_playwright() as pw:
        browser = pw.chromium.launch()
        _ensure_auth_state(browser)
        for theme in THEMES:
            for vp_name, width, height in VIEWPORTS:
                ctx = browser.new_context(
                    storage_state=str(AUTH_STATE),
                    viewport={"width": width, "height": height},
                    bypass_csp=True,
                )
                ctx.add_init_script(
                    f"localStorage.setItem('theme', '{theme}')")
                page = ctx.new_page()
                for slug, path in PAGES:
                    page.goto(f"{BASE}{path}", wait_until="networkidle")
                    _settle(page)
                    page.screenshot(
                        path=out / f"{slug}--{theme}--{vp_name}.png",
                        full_page=True,
                        animations="disabled")
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
        _ensure_auth_state(browser)
        ctx = browser.new_context(
            storage_state=str(AUTH_STATE),
            viewport={"width": 1024, "height": 768},
            bypass_csp=True,
        )
        page = ctx.new_page()
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
