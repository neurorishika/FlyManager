"""Visual-regression + tap-target harness for FlyManager CSS work.

Usage:
    python css_audit.py capture <label>
    python css_audit.py compare <label-a> <label-b>
    python css_audit.py tapcheck
"""
import sys
from pathlib import Path

from playwright.sync_api import TimeoutError as PlaywrightTimeoutError
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
    ("user-guide", "/user_guide"),
    # Task 9/11: static-record IDs from the dev seed data, used only so
    # these detail/preview pages have something real to render.
    ("view-stock", "/stock/view/0034051b64"),
    ("view-cross", "/cross/view_cross/6a08c91d7a14d0afb319ed06"),
    ("phenotype-preview", "/stock/phenotype_preview"),
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
        except PlaywrightTimeoutError:
            # Cached session is stale/expired (or the file is malformed enough
            # that Playwright accepted it but the server doesn't) - fall
            # through and do a real login below. Deliberately narrow: a
            # genuinely broken AUTH_STATE file should raise loudly instead of
            # being silently treated as "just needs a fresh login".
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


# Delay between successive page.goto() calls within a capture/tapcheck run,
# to stay clear of the app's per-route rate limits (see security.py /
# flask-limiter). Task 6 hit the limiter mid-capture; a real screenshot of a
# 429 page is byte-identical across runs, so `compare` would report a false
# IDENTICAL on two equally-broken captures instead of catching the problem.
# The delay is a mitigation; _load() below is what actually catches it.
INTER_PAGE_DELAY_MS = 400


class RateLimitedOrErrorPage(RuntimeError):
    pass


def _load(page, slug, path):
    """Navigate to BASE+path and fail loudly if the response was not a
    real, successful render of the page — rather than silently writing a
    screenshot (capture) or measuring (tapcheck) a "Too Many Requests" or
    other error placeholder.

    Checks two independent signals:
      1. The HTTP status of the navigation response must be 200. Flask's
         default handling of a flask-limiter RateLimitExceeded is a 429;
         other failures (5xx, unexpected redirects to an error route) are
         caught the same way.
      2. The page must actually contain the app chrome (`.header`), which
         every authenticated page in PAGES renders via base.html. A 200
         response that is nonetheless an error page (no such route ships
         here today, but this is what the brief calls "consider also
         asserting an expected element is present") would still be caught.
    """
    response = page.goto(f"{BASE}{path}", wait_until="networkidle")
    if response is None or response.status != 200:
        status = response.status if response is not None else "no response"
        raise RateLimitedOrErrorPage(
            f"{slug} ({path}): navigation returned status {status}, "
            f"expected 200 — likely rate-limited or errored")
    _settle(page)
    try:
        page.wait_for_selector(".header", timeout=3000)
    except PlaywrightTimeoutError:
        raise RateLimitedOrErrorPage(
            f"{slug} ({path}): status 200 but '.header' app chrome not "
            f"found — likely an error placeholder page")
    page.wait_for_timeout(INTER_PAGE_DELAY_MS)


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
                    _load(page, slug, path)
                    page.screenshot(
                        path=out / f"{slug}--{theme}--{vp_name}.png",
                        full_page=True,
                        animations="disabled")
                    print(f"captured {slug} {theme} {vp_name}")
                ctx.close()
        browser.close()


def _expected_filenames():
    """The exact screenshot filenames a complete capture() run should produce,
    derived from PAGES/THEMES/VIEWPORTS rather than a hardcoded count so this
    stays correct as pages are added/removed (Tasks 9/10 in particular)."""
    return {
        f"{slug}--{theme}--{vp_name}.png"
        for slug, _ in PAGES
        for theme in THEMES
        for vp_name, _, _ in VIEWPORTS
    }


def compare(label_a, label_b):
    """Byte-compare two capture() output directories.

    Refuses to report IDENTICAL (or DIFF) unless both directories actually
    hold the full expected set of screenshots. A capture() that crashed or
    was interrupted partway through leaves a truncated directory on disk;
    without this check, comparing an empty/truncated directory against a
    complete one (or against another equally-truncated one) would silently
    report a false IDENTICAL - the worst failure mode for a harness every
    later CSS task gates on.
    """
    dir_a, dir_b = SHOTS / label_a, SHOTS / label_b
    expected = _expected_filenames()
    names_a = {p.name for p in dir_a.glob("*.png")} if dir_a.is_dir() else set()
    names_b = {p.name for p in dir_b.glob("*.png")} if dir_b.is_dir() else set()

    problems = []
    for label, names in ((label_a, names_a), (label_b, names_b)):
        if not names:
            problems.append(f"{label}: no screenshots found ({SHOTS / label})")
            continue
        missing = sorted(expected - names)
        extra = sorted(names - expected)
        if missing:
            problems.append(
                f"{label}: missing {len(missing)}/{len(expected)} expected "
                f"file(s): {missing}")
        if extra:
            problems.append(f"{label}: {len(extra)} unexpected file(s): {extra}")

    if names_a and names_b and names_a != names_b:
        only_a = sorted(names_a - names_b)
        only_b = sorted(names_b - names_a)
        if only_a:
            problems.append(f"present only in {label_a}, not {label_b}: {only_a}")
        if only_b:
            problems.append(f"present only in {label_b}, not {label_a}: {only_b}")

    if problems:
        print(
            f"INVALID COMPARE: {label_a} vs {label_b} do not both hold the "
            f"expected {len(expected)} screenshots "
            f"({len(PAGES)} pages x {len(THEMES)} themes x {len(VIEWPORTS)} "
            f"viewports):")
        for line in problems:
            print(f"  {line}")
        return 1

    failures = []
    for name in sorted(names_a):
        a_bytes = (dir_a / name).read_bytes()
        b_bytes = (dir_b / name).read_bytes()
        if a_bytes != b_bytes:
            failures.append(
                f"{name}: differs ({len(a_bytes)} vs {len(b_bytes)} bytes)")
    if failures:
        print(f"DIFF: {len(failures)} of {len(names_a)} pages")
        for line in failures:
            print(f"  {line}")
        return 1
    print(f"IDENTICAL: all {len(names_a)} pages match")
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
            # tapcheck() never calls page.screenshot(), so the
            # animations="disabled" freeze capture() gets is not available
            # here - determinism for the DOM measurements below rests solely
            # on _settle()'s 250ms wait_for_timeout (invoked inside _load()).
            _load(page, slug, path)
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
