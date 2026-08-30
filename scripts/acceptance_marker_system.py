"""End-to-end acceptance pass over the marker system against a RUNNING app.

Unit tests mock or fake most of this; the point here is to drive the real
Flask app, real Mongo and real GridFS over HTTP the way a browser does,
including CSRF, sessions and redirects.

Usage (stack must already be up via driver.sh):

    python scripts/acceptance_marker_system.py

Exits non-zero on the first failure, printing what was expected.
This is a developer tool, not part of the pytest suite: it needs a live
stack, and it mutates the database it points at.
"""
import io
import json
import re
import subprocess
import sys
import time
import uuid

import requests
from PIL import Image

BASE = "http://localhost:5234"
USER = "devtest"
PASSWORD = "DevTestPassw0rd"
MONGO_CONTAINER = "flymanager-mongodb"
DB_NAME = "flymanager"

PASSED = []
FAILED = []


def check(name, condition, detail=""):
    if condition:
        PASSED.append(name)
        print(f"  PASS  {name}")
    else:
        FAILED.append((name, detail))
        print(f"  FAIL  {name}  {detail}")


def section(title):
    print(f"\n=== {title} ===")


def mongo(js):
    """Run a mongosh expression and return its stdout."""
    out = subprocess.run(
        ["docker", "exec", MONGO_CONTAINER, "mongosh", "--quiet", "--eval",
         f'const d = db.getSiblingDB("{DB_NAME}"); {js}'],
        capture_output=True, text=True, check=True)
    return out.stdout.strip()


def mongo_json(js):
    return json.loads(mongo(f"print(JSON.stringify({js}))"))


def png(size=(80, 60), color=(10, 20, 30)):
    out = io.BytesIO()
    Image.new("RGB", size, color).save(out, format="PNG")
    return out.getvalue()


def csrf(session):
    body = session.get(f"{BASE}/markers").text
    return re.search(r'<meta name="csrf-token" content="([^"]+)"', body).group(1)


def login():
    session = requests.Session()
    token = re.search(r'<meta name="csrf-token" content="([^"]+)"',
                      session.get(f"{BASE}/auth/login").text).group(1)
    response = session.post(f"{BASE}/auth/login", allow_redirects=False,
                            data={"username": USER, "password": PASSWORD,
                                  "csrf_token": token})
    if response.status_code not in (200, 302):
        sys.exit(f"login failed: {response.status_code}")
    return session


def signature():
    return mongo_json("d.settings.findOne({}) || {}").get("markerCatalogRevision", 0)


def image_revision():
    return mongo_json("d.settings.findOne({}) || {}").get("markerImageRevision", 0)


# --------------------------------------------------------------------------


def test_seed_integrity():
    section("Shipped seed integrity in the live database")
    counts = mongo_json(
        '({docs: d.marker_images.countDocuments({origin:"shipped"}),'
        ' files: d["marker_images.files"].countDocuments({}),'
        ' orphanDocs: d.marker_images.countDocuments({storageId: {$in: [null, ""]}})})')
    check("254 shipped entries", counts["docs"] == 254, str(counts))
    check("every entry has bytes behind it", counts["orphanDocs"] == 0, str(counts))

    dupes = mongo_json(
        'd.marker_images.aggregate([{$group:{_id:"$imageId",n:{$sum:1}}},'
        '{$match:{n:{$gt:1}}}]).toArray()')
    check("no duplicate imageIds", dupes == [], str(dupes[:3]))

    bad = mongo_json(
        'd.marker_images.find({$or:[{width:{$lte:0}},{bytes:{$lte:0}},'
        '{contentType:{$ne:"image/webp"}}]},{imageId:1}).toArray()')
    check("all entries are sane webp", bad == [], str(bad[:3]))


def test_every_shipped_image_is_retrievable():
    """Verify all 254 at the storage layer, where no rate limit applies.

    The HTTP route is capped at 60/min, so pulling 254 through it would
    measure the limiter rather than the data. This checks every stored
    image decodes and hashes correctly straight out of GridFS; the sampled
    HTTP test below proves the route on top of it.
    """
    section("Every shipped image is intact in GridFS (all 254)")
    import hashlib

    import gridfs
    import pymongo
    from bson import ObjectId
    from PIL import Image as PILImage

    client = pymongo.MongoClient("mongodb://127.0.0.1:27017/?directConnection=true")
    database = client[DB_NAME]
    bucket = gridfs.GridFSBucket(database, bucket_name="marker_images")

    missing, mismatched, undecodable, oversized = [], [], [], []
    records = list(database["marker_images"].find({"origin": "shipped"}))
    for record in records:
        try:
            # storageId is persisted as a string; GridFS wants the ObjectId,
            # which is exactly what GridFSImageStore.open converts back.
            data = bucket.open_download_stream(ObjectId(record["storageId"])).read()
        except Exception as exc:
            missing.append((record["imageId"], type(exc).__name__))
            continue
        if hashlib.sha256(data).hexdigest() != record["sha256"]:
            mismatched.append(record["imageId"])
        if len(data) != record["bytes"]:
            mismatched.append(record["imageId"])
        try:
            image = PILImage.open(io.BytesIO(data))
            image.verify()
            if max(image.size) > 1024:
                oversized.append((record["imageId"], image.size))
        except Exception:
            undecodable.append(record["imageId"])

    check(f"all {len(records)} shipped images have bytes", not missing, str(missing[:5]))
    check("all hashes and byte counts match", not mismatched, str(mismatched[:5]))
    check("all decode as valid images", not undecodable, str(undecodable[:5]))
    check("none exceeds the 1024px bound", not oversized, str(oversized[:5]))
    client.close()


def test_sampled_images_serve_over_http(session, sample=25):
    section(f"A sample of {sample} shipped images serves correctly over HTTP")
    import hashlib

    records = mongo_json(
        f'd.marker_images.find({{origin:"shipped"}},{{imageId:1,sha256:1}})'
        f'.limit({sample}).toArray().map(x=>({{i:x.imageId,s:x.sha256}}))')
    broken, mismatched = [], []
    for index, record in enumerate(records):
        if index and index % 20 == 0:
            time.sleep(20)  # stay under the route's 60/min limiter
        response = session.get(f"{BASE}/markers/images/{record['i']}")
        if response.status_code != 200:
            broken.append((record["i"], response.status_code))
        elif hashlib.sha256(response.content).hexdigest() != record["s"]:
            mismatched.append(record["i"])
    check("sampled images all return 200", not broken, str(broken[:5]))
    check("served bytes match stored sha256", not mismatched, str(mismatched[:5]))


def test_image_lifecycle(session):
    section("Image upload lifecycle, dedup and unbind")
    token = csrf(session)
    before_images = image_revision()
    payload = png(color=(uuid.uuid4().int % 200, 90, 40))

    response = session.post(f"{BASE}/markers/Sb/images", data={"csrf_token": token,
                            "caption": "acceptance"},
                            files={"image": ("a.png", payload, "image/png")})
    check("upload accepted", response.status_code == 200, str(response.status_code))
    entry = mongo_json('d.marker_images.findOne({origin:"user"}) || {}')
    check("stored as user origin", entry.get("origin") == "user", str(entry)[:200])
    check("bound to Sb", entry.get("match", {}).get("markerKeys") == ["Sb"],
          str(entry.get("match")))
    check("normalized to webp", entry.get("contentType") == "image/webp",
          str(entry.get("contentType")))
    check("image revision moved", image_revision() > before_images)

    image_id = entry["imageId"]

    # Same bytes, different marker -> one document, two keys.
    session.post(f"{BASE}/markers/CyO/images", data={"csrf_token": token},
                 files={"image": ("a.png", payload, "image/png")})
    after = mongo_json(f'd.marker_images.findOne({{imageId:"{image_id}"}}) || {{}}')
    check("identical bytes deduplicate to one document",
          mongo_json('d.marker_images.countDocuments({origin:"user"})') == 1)
    check("second marker appended to markerKeys",
          sorted(after.get("match", {}).get("markerKeys", [])) == ["CyO", "Sb"],
          str(after.get("match")))

    # Unbinding one marker must not destroy the shared image.
    session.post(f"{BASE}/markers/images/{image_id}/delete",
                 data={"csrf_token": token, "marker_key": "CyO"})
    after = mongo_json(f'd.marker_images.findOne({{imageId:"{image_id}"}}) || {{}}')
    check("unbind keeps the shared image alive", after.get("imageId") == image_id)
    check("unbind removed only that key",
          after.get("match", {}).get("markerKeys") == ["Sb"],
          str(after.get("match")))

    # Stale unbind (already removed) must not fall through to deletion.
    stale = session.post(f"{BASE}/markers/images/{image_id}/delete",
                         data={"csrf_token": token, "marker_key": "CyO"},
                         allow_redirects=False)
    check("stale unbind refused with 409", stale.status_code == 409,
          str(stale.status_code))
    check("image survived the stale unbind",
          mongo_json(f'd.marker_images.countDocuments({{imageId:"{image_id}"}})') == 1)

    # Final delete removes document and bytes.
    storage_id = after["storageId"]
    session.post(f"{BASE}/markers/images/{image_id}/delete",
                 data={"csrf_token": token, "marker_key": "Sb"})
    check("delete removed the document",
          mongo_json(f'd.marker_images.countDocuments({{imageId:"{image_id}"}})') == 0)
    check("delete removed the GridFS bytes",
          mongo_json(f'd["marker_images.files"].countDocuments({{_id: ObjectId("{storage_id}")}})') == 0)
    time.sleep(2)
    check("deleted image no longer serves",
          session.get(f"{BASE}/markers/images/{image_id}").status_code == 404)


def test_upload_rejections(session):
    section("Upload validation and abuse resistance")
    token = csrf(session)

    cases = [
        ("non-image with .png name", ("evil.png", b"not an image at all", "image/png")),
        ("html masquerading as image", ("x.png", b"<html><script>1</script></html>", "image/png")),
        ("empty file", ("empty.png", b"", "image/png")),
    ]
    for name, spec in cases:
        before = mongo_json('d.marker_images.countDocuments({origin:"user"})')
        session.post(f"{BASE}/markers/Sb/images",
                     data={"csrf_token": token}, files={"image": spec})
        after = mongo_json('d.marker_images.countDocuments({origin:"user"})')
        check(f"rejected: {name}", after == before, f"{before} -> {after}")

    # A genuine image whose declared content type lies is still fine: we
    # decode, we do not sniff.
    response = session.post(f"{BASE}/markers/Sb/images", data={"csrf_token": token},
                            files={"image": ("x.txt", png(), "text/plain")})
    check("real image with a lying content type is accepted by decoding",
          mongo_json('d.marker_images.countDocuments({origin:"user"})') == 1,
          str(response.status_code))
    entry = mongo_json('d.marker_images.findOne({origin:"user"}) || {}')
    if entry:
        session.post(f"{BASE}/markers/images/{entry['imageId']}/delete",
                     data={"csrf_token": token, "marker_key": "Sb"})

    # Path traversal / unknown ids must never leak or 500.
    for bad in ["../../etc/passwd", "..%2f..%2fetc%2fpasswd", "img_nope", "'; drop"]:
        code = session.get(f"{BASE}/markers/images/{bad}").status_code
        check(f"no leak for id {bad!r}", code in (404, 400, 301, 308), str(code))

    check("upload to unknown marker 404s",
          session.post(f"{BASE}/markers/NoSuchMarker/images",
                       data={"csrf_token": token},
                       files={"image": ("a.png", png(), "image/png")},
                       allow_redirects=False).status_code == 404)


def test_auth_boundaries():
    section("Authentication and CSRF boundaries")
    anon = requests.Session()
    shipped = mongo_json('d.marker_images.findOne({origin:"shipped"},{imageId:1})')

    for path in ["/markers", f"/markers/images/{shipped['imageId']}", "/markers/Sb"]:
        response = anon.get(f"{BASE}{path}", allow_redirects=False)
        check(f"anonymous GET {path} is refused",
              response.status_code in (301, 302, 401), str(response.status_code))

    session = login()
    response = session.post(f"{BASE}/markers/Sb/images",
                            files={"image": ("a.png", png(), "image/png")},
                            allow_redirects=False)
    check("upload without CSRF token is rejected",
          response.status_code == 400, str(response.status_code))

    response = session.post(f"{BASE}/markers/images/{shipped['imageId']}/delete",
                            data={"csrf_token": csrf(session)},
                            allow_redirects=False)
    check("non-admin cannot delete a shipped image",
          response.status_code == 403, str(response.status_code))
    check("shipped image survived",
          mongo_json(f'd.marker_images.countDocuments({{imageId:"{shipped["imageId"]}"}})') == 1)


def test_signature_isolation(session):
    section("Image writes must not disturb the prediction cache signature")
    token = csrf(session)
    before_catalog = mongo_json('d.settings.findOne({}) || {}').get("markerCatalogRevision", 0)
    before_image = image_revision()

    session.post(f"{BASE}/markers/Sb/images", data={"csrf_token": token},
                 files={"image": ("sig.png", png(color=(5, 5, 200)), "image/png")})

    after_catalog = mongo_json('d.settings.findOne({}) || {}').get("markerCatalogRevision", 0)
    check("markerCatalogRevision unchanged by an image write",
          after_catalog == before_catalog, f"{before_catalog} -> {after_catalog}")
    check("markerImageRevision did move", image_revision() > before_image)

    entry = mongo_json('d.marker_images.findOne({origin:"user"}) || {}')
    if entry:
        session.post(f"{BASE}/markers/images/{entry['imageId']}/delete",
                     data={"csrf_token": token, "marker_key": "Sb"})


def test_predictions_use_images(session):
    time.sleep(20)
    section("Predictions surface both uploaded and shipped images")
    token = csrf(session)
    payload = png(size=(1400, 1000), color=(220, 30, 30))
    session.post(f"{BASE}/markers/Sb/images", data={"csrf_token": token,
                 "caption": "prediction probe"},
                 files={"image": ("p.png", payload, "image/png")})
    entry = mongo_json('d.marker_images.findOne({origin:"user"}) || {}')

    response = session.post(f"{BASE}/stock/phenotype_preview",
                            json={"genotype": "w[1118]; +; Sb[1]/TM3; +",
                                  "sex": "female"})
    check("phenotype preview returns 200", response.status_code == 200,
          str(response.status_code))
    images = (response.json().get("prediction") or {}).get("reference_images") or []
    by_label = {i["display_label"]: i for i in images}

    check("prediction produced reference images", len(images) >= 2, str(len(images)))
    check("uploaded image wins for its marker at the exact-key score",
          by_label.get("Sb", {}).get("match_score") == 1000, str(by_label.get("Sb")))
    check("uploaded image is the one served for Sb",
          by_label.get("Sb", {}).get("image_url", "").endswith(entry.get("imageId", "!")),
          str(by_label.get("Sb")))
    fuzzy = [i for label, i in by_label.items() if label != "Sb"]
    check("shipped images still match fuzzily alongside it",
          any(0 < i["match_score"] < 1000 for i in fuzzy),
          str([(i["display_label"], i["match_score"]) for i in fuzzy]))

    # Every URL a prediction hands the browser must actually resolve.
    codes = {i["image_url"]: session.get(BASE + i["image_url"]).status_code
             for i in images}
    check("every prediction image URL resolves",
          set(codes.values()) == {200}, str(codes))

    if entry:
        session.post(f"{BASE}/markers/images/{entry['imageId']}/delete",
                     data={"csrf_token": token, "marker_key": "Sb"})


def test_marker_crud_still_works(session):
    section("Slice A marker CRUD still functions alongside images")
    token = csrf(session)
    key = f"AcceptTest{uuid.uuid4().hex[:6]}"
    before = mongo_json('d.settings.findOne({}) || {}').get("markerCatalogRevision", 0)

    response = session.post(f"{BASE}/markers", headers={"X-CSRFToken": token}, json={
        "Key": key, "kind": "gene_marker",
        "match": {"token": key},
        "payload": {"body_part": "eye", "effect": "acceptance test",
                    "display_label": key, "phenotype_key": key,
                    "dominance": "dominant", "scoring_confidence": 0.5},
    })
    check("marker create accepted", response.status_code in (200, 201, 302),
          str(response.status_code)[:200])
    created = mongo_json(f'd.marker_definitions.findOne({{Key:"{key}"}}) || {{}}')
    check("marker persisted", created.get("Key") == key, str(created)[:200])

    after = mongo_json('d.settings.findOne({}) || {}').get("markerCatalogRevision", 0)
    check("a marker write DOES move markerCatalogRevision", after > before,
          f"{before} -> {after}")

    check("new marker is visible on its detail page",
          session.get(f"{BASE}/markers/{key}").status_code == 200)

    # An image can be attached to a user-created marker.
    upload = session.post(f"{BASE}/markers/{key}/images",
                          data={"csrf_token": csrf(session)},
                          files={"image": ("u.png", png(color=(3, 240, 3)), "image/png")})
    check("image attaches to a user-created marker", upload.status_code == 200,
          str(upload.status_code))
    attached = mongo_json(f'd.marker_images.findOne({{"match.markerKeys":"{key}"}}) || {{}}')
    check("attachment recorded", attached.get("imageId") is not None, str(attached)[:150])

    # Deleting the marker must not strand the image as unreachable garbage
    # without at least leaving it inspectable.
    session.post(f"{BASE}/markers/{key}/delete", data={"csrf_token": csrf(session)})
    check("marker deleted",
          mongo_json(f'd.marker_definitions.countDocuments({{Key:"{key}"}})') == 0)

    if attached.get("imageId"):
        mongo(f'd.marker_images.deleteOne({{imageId:"{attached["imageId"]}"}})')


def main():
    session = login()
    test_seed_integrity()
    test_every_shipped_image_is_retrievable()
    test_sampled_images_serve_over_http(session)
    test_image_lifecycle(session)
    test_upload_rejections(session)
    test_auth_boundaries()
    test_signature_isolation(session)
    test_predictions_use_images(session)
    test_marker_crud_still_works(session)

    print(f"\n{'=' * 60}")
    print(f"passed: {len(PASSED)}   failed: {len(FAILED)}")
    for name, detail in FAILED:
        print(f"  FAILED  {name}: {detail}")
    return 1 if FAILED else 0


if __name__ == "__main__":
    sys.exit(main())
