"""The settings singleton is shared, so it can exist without its defaults.

Revision counters (markerCatalogRevision, markerImageRevision) are bumped
with find_one_and_update(..., upsert=True). On an empty database that
creates a document containing only the counter. get_settings used to
install defaults only when the document was entirely absent, so it
accepted that skeleton and lab_info was never created -- every template
reading settings.lab_info then raised UndefinedError on every page, for
the life of the deployment.

Slice B made this reachable at first boot by seeding marker images during
create_app, before any request had bootstrapped settings.
"""
import flymanager.app  # noqa: F401  (imported first: avoids a circular import)

from flymanager.utils.mongo.settings import DEFAULT_SETTINGS, get_settings, update_settings
from tests.mongo_fakes import FakeDatabase


def test_creates_defaults_when_no_document_exists():
    db = FakeDatabase({"settings": []})
    settings = get_settings(db)

    assert settings["lab_info"]["lab_name"]
    assert settings["theme"]
    assert db["settings"].count_documents({}) == 1


def test_backfills_defaults_into_a_counter_only_document():
    """The exact shape a revision bump leaves behind on a fresh database."""
    db = FakeDatabase({"settings": [{"markerImageRevision": 1}]})

    settings = get_settings(db)

    assert settings["lab_info"]["lab_name"] == DEFAULT_SETTINGS["lab_info"]["lab_name"]
    assert settings["theme"] == DEFAULT_SETTINGS["theme"]
    # The counter must survive the repair.
    assert settings["markerImageRevision"] == 1
    assert db["settings"].count_documents({}) == 1


def test_backfill_is_persisted_not_just_returned():
    db = FakeDatabase({"settings": [{"markerImageRevision": 1}]})
    get_settings(db)

    stored = db["settings"].find_one({})
    assert "lab_info" in stored, "defaults were returned but never written back"
    assert "theme" in stored


def test_does_not_overwrite_customised_values():
    db = FakeDatabase({"settings": [{
        "lab_info": {"lab_name": "Someone Else Lab"},
        "markerCatalogRevision": 7,
    }]})

    settings = get_settings(db)

    assert settings["lab_info"]["lab_name"] == "Someone Else Lab"
    assert settings["markerCatalogRevision"] == 7
    # ...while still filling in the section that was missing.
    assert settings["theme"] == DEFAULT_SETTINGS["theme"]


def test_repeated_calls_are_stable():
    db = FakeDatabase({"settings": [{"markerImageRevision": 3}]})
    first = get_settings(db)
    second = get_settings(db)

    assert first["lab_info"] == second["lab_info"]
    assert db["settings"].count_documents({}) == 1


def test_update_invalidates_short_lived_settings_cache():
    db = FakeDatabase({"settings": [{
        "lab_info": {"lab_name": "Original Lab"},
        "theme": DEFAULT_SETTINGS["theme"],
    }]})
    assert get_settings(db)["lab_info"]["lab_name"] == "Original Lab"

    assert update_settings(
        {"lab_info": {"lab_name": "Updated Lab"}}, db
    ) is True

    assert get_settings(db)["lab_info"]["lab_name"] == "Updated Lab"
