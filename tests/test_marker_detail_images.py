"""The marker detail page shows what the rest of the app shows.

Every shipped image has an empty match.markerKeys, and this page used to
resolve images through an exact-key index built only from that field, so it
showed nothing for markers whose photos render fine on a stock page.
"""
from unittest.mock import patch

import pytest

from tests.test_marker_routes import (app, fake_db, _client, _reset_catalog,  # noqa: F401
                                      _settings_payload)
from flymanager.utils.phenotypes import image_catalog


@pytest.fixture(autouse=True)
def _freeze_images(monkeypatch):
    """Pin the installed snapshot for the duration of a request.

    `create_app`'s before_request hook calls `maybe_refresh_image_catalog(db)`
    on every request against the real module-global db. The test database has
    254 images at revision 13, so the first client.get() after installing a
    revision-0 test snapshot would recompile from Mongo and throw it away --
    intermittently, since the probe is throttled to 30s. `_reset_catalog` in
    test_marker_routes already does exactly this for the marker catalog.

    Patching the module attribute works because the hook imports the name
    inside the function body, so it resolves at call time.
    """
    monkeypatch.setattr(image_catalog, "maybe_refresh_image_catalog",
                        lambda db, **kwargs: None)
    yield
    image_catalog.set_image_catalog_for_testing(
        {"entries": [], "revision": -1})


def _entry(image_id, *, keys=(), aliases=(), stem="", body="wing",
           origin="shipped"):
    return {"imageId": image_id, "storageId": "s", "sha256": "0" * 64,
            "origin": origin, "UploadedBy": "alice",
            "match": {"markerKeys": list(keys), "aliases": list(aliases),
                      "stem": stem, "bodyPart": body, "manifestEntry": False,
                      "sourceCollection": "library"},
            "display": {"sortOrder": 0, "priority": 0}}


def _install(*entries):
    image_catalog.set_image_catalog_for_testing(
        image_catalog.compile_image_catalog(list(entries)))


def test_a_shipped_alias_matched_image_now_appears(app, fake_db):
    """Cy's photos are bound by alias, not by key. This is the bug."""
    _install(_entry("img_seed", aliases=["cy"], stem="cy"))
    with patch("flymanager.app.routes.markers.db", fake_db):
        body = _client(app, fake_db).get("/markers/Cy").data.decode()
    assert "/markers/images/img_seed" in body


def test_a_matched_image_gets_no_delete_control(app, fake_db):
    """delete_marker_image destroys an image outright when it has one marker
    key, so offering Delete for something nobody bound here would let one
    click remove a shipped photo from every view in the app."""
    _install(_entry("img_seed", aliases=["cy"], stem="cy"))
    with patch("flymanager.app.routes.markers.db", fake_db):
        body = _client(app, fake_db, "admin").get("/markers/Cy").data.decode()
    assert "/markers/images/img_seed" in body
    assert "img_seed/delete" not in body


def test_an_attached_upload_does_get_a_delete_control(app, fake_db):
    _install(_entry("img_mine", keys=["Cy"], stem="cy", origin="user"))
    with patch("flymanager.app.routes.markers.db", fake_db):
        body = _client(app, fake_db, "alice").get("/markers/Cy").data.decode()
    assert "img_mine/delete" in body


def test_a_non_admin_gets_no_delete_control_for_someone_elses_upload(app, fake_db):
    """The gate must mirror delete_marker_image, which 403s here. A button
    that can only produce a 403 is worse than no button."""
    _install(_entry("img_theirs", keys=["Cy"], stem="cy", origin="user"))
    with patch("flymanager.app.routes.markers.db", fake_db):
        body = _client(app, fake_db, "bob").get("/markers/Cy").data.decode()
    assert "/markers/images/img_theirs" in body
    assert "img_theirs/delete" not in body


def test_a_non_admin_gets_no_delete_control_for_an_attached_shipped_image(app, fake_db):
    """upsert_image_entry binds by content hash, so a user uploading bytes
    identical to a shipped photo attaches their key to the shipped document.
    Attached does not imply deletable."""
    _install(_entry("img_ship", keys=["Cy"], stem="cy", origin="shipped"))
    with patch("flymanager.app.routes.markers.db", fake_db):
        body = _client(app, fake_db, "alice").get("/markers/Cy").data.decode()
    assert "img_ship/delete" not in body


def test_the_page_renders_the_shared_macro_not_a_bespoke_grid(app, fake_db):
    _install(_entry("img_seed", aliases=["cy"], stem="cy"))
    with patch("flymanager.app.routes.markers.db", fake_db):
        body = _client(app, fake_db).get("/markers/Cy").data.decode()
    assert "stock-phenotype-image-grid" in body
    assert "js-phenotype-image-zoom" in body


def test_a_user_caption_survives_the_move_to_the_shared_macro(app, fake_db):
    """The caption is the only metadata the upload form collects; the old
    bespoke grid rendered it and the shared macro did not."""
    entry = _entry("img_mine", keys=["Cy"], stem="cy", origin="user")
    entry["display"]["caption"] = "scored under the scope at 20x"
    _install(entry)
    with patch("flymanager.app.routes.markers.db", fake_db):
        body = _client(app, fake_db, "alice").get("/markers/Cy").data.decode()
    assert "scored under the scope at 20x" in body
    assert "Uploaded by alice" in body


def test_an_unresolvable_marker_page_shows_a_does_not_resolve_message(app, fake_db):
    """Orco-LexA is a real shipped alias whose target isn't a definition Key
    and has no gene stem to fall back to, so it resolves to one group with
    marker=None (images=[]). The template used a for-loop {% else %}, which
    only fires when image_groups is empty overall -- never for an unresolved
    group inside a one-item list -- so this panel used to render blank."""
    _install()
    with patch("flymanager.app.routes.markers.db", fake_db):
        body = _client(app, fake_db).get("/markers/Orco-LexA").data.decode()
    assert "does not resolve to" in body
    assert "the image library knows about" in body


def test_a_marker_with_no_images_shows_the_placeholder_card(app, fake_db):
    _install()
    with patch("flymanager.app.routes.markers.db", fake_db):
        body = _client(app, fake_db).get("/markers/Cy").data.decode()
    assert "stock-phenotype-image-card-empty" in body
    assert "No image yet" in body


def test_an_admin_gets_a_delete_control_for_an_attached_shipped_image(app, fake_db):
    """This is the one destructive render path: an admin can delete a shipped
    document (and its GridFS bytes) for the whole lab, not just unbind it."""
    _install(_entry("img_ship", keys=["Cy"], stem="cy", origin="shipped"))
    with patch("flymanager.app.routes.markers.db", fake_db):
        body = _client(app, fake_db, "admin").get("/markers/Cy").data.decode()
    assert "img_ship/delete" in body


def test_an_upload_made_on_a_balancer_page_gets_a_working_remove_control(app, fake_db):
    """upload_marker_image binds to the page's own Key ('CyO'), not to any of
    the carried markers ('Cy', 'pr', 'cn') the balancer resolves to. This was
    the fully-broken case: the upload was invisible on the page it was made
    from, and even once visible, posting the resolved group key against
    delete_marker_image 409s because that key was never in markerKeys."""
    _install(_entry("img_mine", keys=["CyO"], stem="cy", origin="user"))
    with patch("flymanager.app.routes.markers.db", fake_db):
        body = _client(app, fake_db, "alice").get("/markers/CyO").data.decode()
    assert "/markers/images/img_mine" in body
    assert "img_mine/delete" in body
    assert 'name="marker_key" value="CyO"' in body


def test_a_balancer_page_shows_its_carried_markers(app, fake_db):
    _install(_entry("img_cy", aliases=["cy"], stem="cy"))
    with patch("flymanager.app.routes.markers.db", fake_db):
        body = _client(app, fake_db).get("/markers/CyO").data.decode()
    assert "/markers/images/img_cy" in body
    # CyO carries Cy, pr and cn -- each gets its own labelled section.
    for carried in ("Cy", "pr", "cn"):
        assert f'data-marker-group="{carried}"' in body
