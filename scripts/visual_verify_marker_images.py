"""Visual verification of the marker image system against real data.

Screenshots are only half of it: a broken <img> still screenshots as a
tidy empty box. So every marker image on every page is also checked with
naturalWidth > 0, which is the browser telling us it actually decoded the
bytes it fetched.

Requires a running stack and a devtest login (driver.sh bootstrap).
Writes PNGs to .claude/skills/run-flymanager/shots/marker-verify/.
"""
import io
import json
import re
import subprocess
import sys
from pathlib import Path

import requests
from PIL import Image, ImageDraw
from playwright.sync_api import sync_playwright

BASE = "http://localhost:5234"
USER, PASSWORD = "devtest", "DevTestPassw0rd"
OUT = Path(".claude/skills/run-flymanager/shots/marker-verify")

FINDINGS = []


def note(ok, label, detail=""):
    FINDINGS.append((ok, label, detail))
    print(f"  {'PASS' if ok else 'FAIL'}  {label}  {detail}")


def mongo(js):
    return subprocess.run(
        ["docker", "exec", "flymanager-mongodb", "mongosh", "--quiet", "--eval",
         f'const d=db.getSiblingDB("flymanager"); {js}'],
        capture_output=True, text=True).stdout.strip()


def probe_image():
    """A deliberately unmistakable image, so an upload is obvious on sight."""
    image = Image.new("RGB", (600, 400), (255, 0, 200))
    draw = ImageDraw.Draw(image)
    draw.rectangle([20, 20, 580, 380], outline=(255, 255, 255), width=12)
    draw.text((60, 180), "UPLOADED PROBE IMAGE", fill=(255, 255, 255))
    buffer = io.BytesIO()
    image.save(buffer, format="PNG")
    return buffer.getvalue()


def upload_probe(marker_key):
    session = requests.Session()
    token = re.search(r'content="([^"]+)"',
                      session.get(f"{BASE}/auth/login").text.split("csrf-token")[1]).group(1)
    session.post(f"{BASE}/auth/login",
                 data={"username": USER, "password": PASSWORD, "csrf_token": token})
    token = re.search(r'<meta name="csrf-token" content="([^"]+)"',
                      session.get(f"{BASE}/markers").text).group(1)
    response = session.post(f"{BASE}/markers/{marker_key}/images",
                            data={"csrf_token": token, "caption": "visual probe"},
                            files={"image": ("probe.png", probe_image(), "image/png")})
    entry = json.loads(mongo('print(JSON.stringify(d.marker_images.findOne({origin:"user"})||{}))'))
    return session, entry, response.status_code


def audit_images(page, slug):
    """Every marker image on the page must have actually decoded."""
    data = page.evaluate("""() => {
        const imgs = [...document.querySelectorAll('img')]
          .filter(i => (i.currentSrc || i.src || '').includes('/markers/images/'));
        return imgs.map(i => ({
            src: (i.currentSrc || i.src),
            w: i.naturalWidth, h: i.naturalHeight,
            shown: !!(i.offsetWidth || i.offsetHeight),
        }));
    }""")
    broken = [d for d in data if d["w"] == 0]
    hidden = [d for d in data if d["w"] > 0 and not d["shown"]]
    note(not broken, f"{slug}: no broken marker images ({len(data)} found)",
         str([b['src'].rsplit('/', 1)[-1] for b in broken][:4]))
    if data:
        note(not hidden, f"{slug}: all marker images are visible",
             str(len(hidden)))
    return data


def shoot(page, slug):
    OUT.mkdir(parents=True, exist_ok=True)
    path = OUT / f"{slug}.png"
    page.screenshot(path=str(path), full_page=True)
    return path


def main():
    marker_key = sys.argv[1] if len(sys.argv) > 1 else "Tb"

    print(f"\n=== Uploading a probe image to marker {marker_key} ===")
    _, entry, status = upload_probe(marker_key)
    note(status in (200, 302) and entry.get("imageId"),
         "probe upload stored", f"{status} {entry.get('imageId')}")
    probe_id = entry.get("imageId")

    stocks = json.loads(mongo(
        'print(JSON.stringify(d.stocks.find({},{UniqueID:1,Genotype:1,_id:0})'
        '.limit(60).toArray()))'))
    target = next((s for s in stocks if marker_key in (s.get("Genotype") or "")), None)

    with sync_playwright() as playwright:
        browser = playwright.chromium.launch()
        context = browser.new_context(viewport={"width": 1600, "height": 1000})
        page = context.new_page()

        page.goto(f"{BASE}/auth/login")
        page.fill("#username", USER)
        page.fill("#password", PASSWORD)
        page.click("button[type=submit]")
        page.wait_for_load_state("networkidle")
        note("/auth/login" not in page.url, "logged in", page.url)

        print("\n=== Marker catalog ===")
        page.goto(f"{BASE}/markers", wait_until="networkidle")
        note(page.title() != "", "markers page loaded", page.url)
        shoot(page, "01-markers-catalog")

        print(f"\n=== Marker detail: {marker_key} ===")
        page.goto(f"{BASE}/markers/{marker_key}", wait_until="networkidle")
        audit_images(page, "marker-detail")
        body = page.content()
        note(probe_id in body, "uploaded probe appears on its marker page", probe_id)
        note("visual probe" in body, "caption is rendered")
        shoot(page, "02-marker-detail-with-upload")

        print("\n=== Phenotype preview with a real genotype ===")
        page.goto(f"{BASE}/stock/phenotype_preview", wait_until="networkidle")
        shoot(page, "03-phenotype-preview-empty")

        if target:
            print(f"\n=== Real stock view: {target['UniqueID']} ===")
            page.goto(f"{BASE}/stock/view/{target['UniqueID']}", wait_until="networkidle")
            page.wait_for_timeout(1500)
            found = audit_images(page, "stock-view")
            note(any(probe_id in f["src"] for f in found) if probe_id else False,
                 "uploaded image wins on a real stock prediction",
                 f"{len(found)} images: " + str([f['src'].rsplit('/', 1)[-1] for f in found]))
            shoot(page, "04-real-stock-view")
        else:
            note(False, f"no real stock contains {marker_key}", "")

        print("\n=== Cross explorer and a real cross ===")
        page.goto(f"{BASE}/cross/cross_explorer", wait_until="networkidle")
        shoot(page, "05-cross-explorer")
        crosses = json.loads(mongo(
            'print(JSON.stringify(d.crosses.find({},{UniqueID:1,_id:0}).limit(1).toArray()))'))
        if crosses:
            page.goto(f"{BASE}/cross/view_cross/{crosses[0]['UniqueID']}",
                      wait_until="networkidle")
            page.wait_for_timeout(1500)
            audit_images(page, "cross-view")
            shoot(page, "06-real-cross-view")

        print("\n=== Stock explorer (real collection) ===")
        page.goto(f"{BASE}/stock/explorer", wait_until="networkidle")
        note("explorer" in page.url, "stock explorer loaded", page.url)
        shoot(page, "07-stock-explorer")

        print("\n=== Dark theme spot check ===")
        page.emulate_media(color_scheme="dark")
        page.goto(f"{BASE}/markers/{marker_key}", wait_until="networkidle")
        shoot(page, "08-marker-detail-dark")

        browser.close()

    print("\n" + "=" * 60)
    failed = [f for f in FINDINGS if not f[0]]
    print(f"checks passed: {len(FINDINGS) - len(failed)}   failed: {len(failed)}")
    for _, label, detail in failed:
        print(f"  FAILED  {label}  {detail}")
    print(f"screenshots in {OUT}")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
