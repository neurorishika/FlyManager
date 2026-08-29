import flymanager.app  # noqa: F401  (see test_bulk_operations.py for why)

import pytest

from flymanager.utils.mongo.marker_definitions import (
    MarkerDefinitionError, bump_marker_catalog_revision,
    can_edit_marker_definition, create_marker_definition,
    delete_marker_definition, get_marker_definition, list_marker_definitions,
    promote_marker_definition, update_marker_definition)
from flymanager.utils.phenotypes import marker_catalog
from tests.mongo_fakes import FakeDatabase


@pytest.fixture(autouse=True)
def _reset_catalog():
    marker_catalog.reset_catalog()
    marker_catalog.reset_refresh_state()
    yield
    marker_catalog.reset_catalog()
    marker_catalog.reset_refresh_state()


@pytest.fixture
def db():
    return FakeDatabase({"marker_definitions": [], "settings": [{}], "activity": []})


def _definition(key="zz", **overrides):
    document = {
        "Key": key,
        "kind": "gene_marker",
        "match": {"symbol": key},
        "payload": {"body_part": "wing", "effect": "zigzag wings",
                    "dominance": "dominant", "display_label": key,
                    "phenotype_key": key, "chromosome": 3,
                    "scoring_confidence": 0.8},
        "provenance": {"source": "user"},
    }
    document.update(overrides)
    return document


def test_create_stores_attribution_and_bumps_the_revision(db):
    created = create_marker_definition(db, _definition(), username="alice")
    assert created["origin"] == "user"
    assert created["CreatedBy"] == "alice"
    assert created["CreatedAt"]
    assert marker_catalog.read_catalog_revision(db) == 1
    assert db["activity"].count_documents({}) == 1


def test_create_rejects_a_duplicate_key_with_409(db):
    create_marker_definition(db, _definition(), username="alice")
    with pytest.raises(MarkerDefinitionError) as exc:
        create_marker_definition(db, _definition(), username="bob")
    assert exc.value.status_code == 409


def test_create_rejects_an_invalid_document_with_400(db):
    with pytest.raises(MarkerDefinitionError) as exc:
        create_marker_definition(db, {"Key": "x", "kind": "nope"}, username="alice")
    assert exc.value.status_code == 400


def test_creating_a_key_that_exists_in_the_shipped_catalog_records_an_override(db):
    created = create_marker_definition(db, _definition("Cy"), username="alice")
    assert created["overridesShipped"]["key"] == "Cy"
    assert created["overridesShipped"]["shippedVersion"] == \
        marker_catalog.load_shipped_catalog()["catalogVersion"]


def test_update_by_the_creator_succeeds_and_bumps_the_revision(db):
    create_marker_definition(db, _definition(), username="alice")
    updated = update_marker_definition(
        db, "zz", _definition(payload={"display_label": "zz", "effect": "edited"}),
        username="alice")
    assert updated["payload"]["effect"] == "edited"
    assert updated["UpdatedBy"] == "alice"
    assert marker_catalog.read_catalog_revision(db) == 2


def test_update_by_another_user_is_forbidden(db):
    create_marker_definition(db, _definition(), username="alice")
    with pytest.raises(MarkerDefinitionError) as exc:
        update_marker_definition(db, "zz", _definition(), username="bob")
    assert exc.value.status_code == 403


def test_admin_can_update_anyones_definition(db):
    create_marker_definition(db, _definition(), username="alice")
    assert update_marker_definition(db, "zz", _definition(), username="admin")


def test_a_curated_definition_is_admin_only(db):
    create_marker_definition(db, _definition(), username="alice")
    promote_marker_definition(db, "zz", username="admin")
    with pytest.raises(MarkerDefinitionError) as exc:
        update_marker_definition(db, "zz", _definition(), username="alice")
    assert exc.value.status_code == 403
    assert update_marker_definition(db, "zz", _definition(), username="admin")


def test_promotion_requires_admin_and_records_the_curator(db):
    create_marker_definition(db, _definition(), username="alice")
    with pytest.raises(MarkerDefinitionError) as exc:
        promote_marker_definition(db, "zz", username="alice")
    assert exc.value.status_code == 403

    promoted = promote_marker_definition(db, "zz", username="admin")
    assert promoted["origin"] == "curated"
    assert promoted["CuratedBy"] == "admin"
    assert promoted["CuratedAt"]


def test_delete_removes_a_user_definition(db):
    create_marker_definition(db, _definition(), username="alice")
    result = delete_marker_definition(db, "zz", username="alice")
    assert result["restored_shipped"] is False
    assert get_marker_definition(db, "zz") is None


def test_deleting_an_override_restores_the_shipped_definition(db):
    create_marker_definition(db, _definition("Cy"), username="alice")
    result = delete_marker_definition(db, "Cy", username="alice")
    assert result["restored_shipped"] is True
    restored = get_marker_definition(db, "Cy")
    assert restored["origin"] == "shipped"
    assert restored["payload"]["effect"] != "zigzag wings"


def test_delete_of_a_missing_key_is_404(db):
    with pytest.raises(MarkerDefinitionError) as exc:
        delete_marker_definition(db, "nope", username="alice")
    assert exc.value.status_code == 404


def test_a_shipped_definition_cannot_be_edited_or_deleted_directly(db):
    """Both paths go through _require_editable, which reports a shipped key as
    409 ("create an override instead") rather than 404: the key does exist,
    it is just not editable in place."""
    with pytest.raises(MarkerDefinitionError) as exc:
        update_marker_definition(db, "Sb", _definition("Sb"), username="admin")
    assert exc.value.status_code == 409
    with pytest.raises(MarkerDefinitionError) as delete_exc:
        delete_marker_definition(db, "Sb", username="admin")
    assert delete_exc.value.status_code == 409


def test_list_merges_shipped_and_overlay_with_edit_flags(db):
    create_marker_definition(db, _definition(), username="alice")
    rows = list_marker_definitions(db)
    by_key = {row["Key"]: row for row in rows}
    assert by_key["Cy"]["origin"] == "shipped"
    assert by_key["Cy"]["editable_by_user"] is False
    assert by_key["zz"]["origin"] == "user"
    assert by_key["zz"]["editable_by_user"] is True


def test_list_filters_by_kind_origin_and_search(db):
    create_marker_definition(db, _definition(), username="alice")
    assert {row["Key"] for row in list_marker_definitions(db, origin="user")} == {"zz"}
    assert all(row["kind"] == "balancer"
               for row in list_marker_definitions(db, kind="balancer"))
    assert {row["Key"] for row in list_marker_definitions(db, search="zigzag")} == {"zz"}


def test_can_edit_marker_definition_rules():
    user_row = {"origin": "user", "CreatedBy": "alice"}
    curated_row = {"origin": "curated", "CreatedBy": "alice"}
    shipped_row = {"origin": "shipped"}
    assert can_edit_marker_definition(user_row, "alice")
    assert can_edit_marker_definition(user_row, "admin")
    assert not can_edit_marker_definition(user_row, "bob")
    assert not can_edit_marker_definition(curated_row, "alice")
    assert can_edit_marker_definition(curated_row, "admin")
    assert not can_edit_marker_definition(shipped_row, "admin")


def test_bump_is_monotonic(db):
    assert bump_marker_catalog_revision(db) == 1
    assert bump_marker_catalog_revision(db) == 2
