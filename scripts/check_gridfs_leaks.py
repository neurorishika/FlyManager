"""Prove that no upload/delete route path leaks GridFS bytes.

An orphaned blob is inert (nothing references it) but it is still storage
that grows forever, and it rides every Mongo backup. Run against a live
stack; it leaves the database as it found it.
"""
import io
import json
import re
import subprocess
import sys

import requests
from PIL import Image

BASE = "http://localhost:5234"


def files():
    out = subprocess.run(
        ["docker", "exec", "flymanager-mongodb", "mongosh", "--quiet", "--eval",
         'print(db.getSiblingDB("flymanager")["marker_images.files"].countDocuments({}))'],
        capture_output=True, text=True).stdout.strip()
    return int(out)


def user_entry():
    out = subprocess.run(
        ["docker", "exec", "flymanager-mongodb", "mongosh", "--quiet", "--eval",
         'print(JSON.stringify(db.getSiblingDB("flymanager")'
         '.marker_images.findOne({origin:"user"})||{}))'],
        capture_output=True, text=True).stdout.strip()
    return json.loads(out)


def png(color):
    buffer = io.BytesIO()
    Image.new("RGB", (50, 50), color).save(buffer, format="PNG")
    return buffer.getvalue()


def main():
    session = requests.Session()
    token = re.search(r'content="([^"]+)"',
                      session.get(f"{BASE}/auth/login").text.split("csrf-token")[1]).group(1)
    session.post(f"{BASE}/auth/login",
                 data={"username": "devtest", "password": "DevTestPassw0rd",
                       "csrf_token": token})
    token = re.search(r'<meta name="csrf-token" content="([^"]+)"',
                      session.get(f"{BASE}/markers").text).group(1)

    baseline = files()
    failures = []

    def report(label, expected_delta):
        actual = files() - baseline
        status = "PASS" if actual == expected_delta else "FAIL"
        if status == "FAIL":
            failures.append(f"{label}: expected +{expected_delta}, got +{actual}")
        print(f"  {status}  {label} (files delta {actual:+d})")

    print(f"baseline GridFS files: {baseline}")

    # 1. Simple upload then delete.
    session.post(f"{BASE}/markers/Sb/images", data={"csrf_token": token},
                 files={"image": ("a.png", png((9, 9, 9)), "image/png")})
    report("upload stores exactly one blob", 1)
    entry = user_entry()
    session.post(f"{BASE}/markers/images/{entry['imageId']}/delete",
                 data={"csrf_token": token, "marker_key": "Sb"})
    report("delete reclaims the blob", 0)

    # 2. Identical bytes on two markers, unbound then deleted.
    payload = png((7, 7, 7))
    session.post(f"{BASE}/markers/Sb/images", data={"csrf_token": token},
                 files={"image": ("b.png", payload, "image/png")})
    session.post(f"{BASE}/markers/CyO/images", data={"csrf_token": token},
                 files={"image": ("b.png", payload, "image/png")})
    report("duplicate upload does not store a second blob", 1)
    entry = user_entry()
    session.post(f"{BASE}/markers/images/{entry['imageId']}/delete",
                 data={"csrf_token": token, "marker_key": "CyO"})
    report("unbinding one marker keeps the blob", 1)
    session.post(f"{BASE}/markers/images/{entry['imageId']}/delete",
                 data={"csrf_token": token, "marker_key": "Sb"})
    report("deleting the last binding reclaims it", 0)

    # 3. Rejected uploads must never reach storage.
    for label, data in [("garbage bytes", b"garbage"),
                        ("empty file", b""),
                        ("html", b"<html></html>")]:
        session.post(f"{BASE}/markers/Sb/images", data={"csrf_token": token},
                     files={"image": ("c.png", data, "image/png")})
        report(f"rejected upload ({label}) stores nothing", 0)

    # 4. Re-uploading the same bytes repeatedly must stay at one blob.
    payload = png((3, 200, 3))
    for _ in range(3):
        session.post(f"{BASE}/markers/Sb/images", data={"csrf_token": token},
                     files={"image": ("d.png", payload, "image/png")})
    report("three identical re-uploads store one blob", 1)
    entry = user_entry()
    session.post(f"{BASE}/markers/images/{entry['imageId']}/delete",
                 data={"csrf_token": token, "marker_key": "Sb"})
    report("cleanup returns to baseline", 0)

    print()
    if failures:
        for line in failures:
            print("FAILED:", line)
        return 1
    print("No GridFS leaks detected across any route path.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
