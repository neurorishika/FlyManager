import datetime
from types import SimpleNamespace
from unittest.mock import patch

from flymanager.utils.mongo_records import (apply_updates_to_owned_document,
                                            build_existing_vial_timeline,
                                            build_initial_vial_timeline,
                                            build_owned_document,
                                            build_vial_update_properties,
                                            delete_owned_document,
                                            filter_by_indexes,
                                            flip_owned_document,
                                            generate_unique_id,
                                            get_missing_required_updates,
                                            get_owned_document,
                                            normalize_schedule_dates,
                                            parse_last_flip_date,
                                            prune_vial_schedule,
                                            rebuild_flip_log, require_fields)


class FakeCollection:
    def __init__(self, database, name):
        self.database = database
        self.name = name

    def find_one(self, query):
        for record in self.database.data.get(self.name, []):
            if all(record.get(key) == value for key, value in query.items()):
                return dict(record)
        return None

    def update_one(self, query, update):
        for record in self.database.data.get(self.name, []):
            if all(record.get(key) == value for key, value in query.items()):
                record.update(update.get("$set", {}))
                return SimpleNamespace(matched_count=1)
        return SimpleNamespace(matched_count=0)

    def delete_one(self, query):
        records = self.database.data.get(self.name, [])
        for index, record in enumerate(records):
            if all(record.get(key) == value for key, value in query.items()):
                del records[index]
                return SimpleNamespace(deleted_count=1)
        return SimpleNamespace(deleted_count=0)


class FakeDatabase:
    def __init__(self, initial_data=None):
        self.data = {
            name: [dict(item) for item in records]
            for name, records in (initial_data or {}).items()
        }

    def __getitem__(self, name):
        self.data.setdefault(name, [])
        return FakeCollection(self, name)


def test_generate_unique_id_retries_after_collision():
    database = FakeDatabase()
    seen_uids = []

    def fake_uid_exists(uid, _db):
        seen_uids.append(uid)
        return len(seen_uids) == 1

    with patch("flymanager.utils.mongo_records._uid_exists", side_effect=fake_uid_exists):
        uid = generate_unique_id(["scientist", "seed"], database)

    assert len(seen_uids) == 2
    assert seen_uids[0] != seen_uids[1]
    assert uid == seen_uids[-1]


def test_require_fields_raises_for_missing_required_value():
    try:
        require_fields({"present": True}, ["present", "missing"])
    except ValueError as exc:
        assert str(exc) == "missing is required"
    else:
        raise AssertionError("require_fields should raise when a field is missing")


def test_build_owned_document_merges_base_required_and_optional_values():
    document = build_owned_document(
        user="scientist",
        uid="stock-1",
        properties={"Required": "yes", "Optional": "maybe"},
        required_properties=["Required"],
        optional_properties=["Optional", "UnsetOptional"],
        base_document={"Name": "Reference"},
    )

    assert document == {
        "UniqueID": "stock-1",
        "User": "scientist",
        "Name": "Reference",
        "Required": "yes",
        "Optional": "maybe",
        "UnsetOptional": "",
    }


def test_get_missing_required_updates_uses_defaults_only_for_missing_fields():
    updates = get_missing_required_updates(
        {"Status": "Healthy"},
        ["Status", "FoodType"],
        {"FoodType": "Molasses"},
    )

    assert updates == {"FoodType": "Molasses"}


def test_rebuild_flip_log_preserves_reverse_chronological_output():
    flip_log = rebuild_flip_log(
        ["V1", "V2"],
        [
            datetime.datetime(2026, 1, 1, 10, 0),
            datetime.datetime(2026, 1, 8, 10, 0),
        ],
    )

    assert flip_log == "V2, 2026-01-08 10:00; V1, 2026-01-01 10:00"


def test_parse_last_flip_date_accepts_minute_and_second_precision():
    assert parse_last_flip_date("2026-01-02 12:30").isoformat() == "2026-01-02T00:00:00"
    assert parse_last_flip_date("2026-01-02 12:30:45").isoformat() == "2026-01-02T00:00:00"


def test_schedule_helpers_normalize_and_filter_values():
    normalized = normalize_schedule_dates([
        datetime.datetime(2026, 1, 2, 12, 30, 45)
    ])
    filtered = filter_by_indexes(["V1", "V2", "V3"], [1])

    assert normalized[0].isoformat() == "2026-01-02T00:00:00"
    assert filtered == ["V1", "V3"]


def test_build_initial_vial_timeline_creates_rebuilt_log_and_future_dates():
    timeline = build_initial_vial_timeline(
        "2026-01-01 10:00; 2026-01-08 10:00",
        7,
        10,
    )

    assert timeline["currently_alive_vials"] == ["V1", "V2"]
    assert timeline["flip_log"] == "V2, 2026-01-01 10:00; V1, 2026-01-08 10:00"
    assert timeline["first_flip_date"].isoformat() == "2026-01-08T10:00:00"


def test_build_existing_vial_timeline_uses_only_alive_vials_for_source_dates():
    timeline = build_existing_vial_timeline(
        "V2, 2026-01-08 10:00; V1, 2026-01-01 10:00",
        ["V2"],
        7,
        10,
    )

    assert timeline["currently_alive_vials"] == ["V2"]
    assert [date.isoformat() for date in timeline["source_dates"]] == ["2026-01-08T10:00:00"]
    assert timeline["first_flip_date"].isoformat() == "2026-01-01T10:00:00"


def test_prune_vial_schedule_applies_early_flip_and_max_lifetime_rules():
    currently_alive_vials, next_flip_dates, next_eclosion_dates = prune_vial_schedule(
        currently_alive_vials=["V1", "V2"],
        source_dates=[
            datetime.datetime(2026, 5, 1, 10, 0),
            datetime.datetime(2026, 5, 8, 10, 0),
        ],
        all_flip_dates=[
            datetime.datetime(2026, 5, 1, 10, 0),
            datetime.datetime(2026, 5, 8, 10, 0),
        ],
        next_flip_dates=[
            datetime.datetime(2026, 5, 8, 10, 0),
            datetime.datetime(2026, 5, 15, 10, 0),
        ],
        next_eclosion_dates=[
            datetime.datetime(2026, 5, 11, 10, 0),
            datetime.datetime(2026, 5, 18, 10, 0),
        ],
        vial_lifetime=30,
        last_flip_timestamp="2026-05-01 10:00",
        flip_frequency=7,
        first_flip_date=datetime.datetime(2026, 5, 1, 10, 0),
        max_lifetime_days=12,
    )

    assert currently_alive_vials == ["V1", "V2"]
    assert [date.isoformat() for date in next_flip_dates] == ["2026-05-08T00:00:00"]
    assert [date.isoformat() for date in next_eclosion_dates] == ["2026-05-11T00:00:00", "2026-05-18T00:00:00"]


def test_build_vial_update_properties_marks_empty_vials_as_unmaintained():
    update_properties = build_vial_update_properties([], [], [], flip_log="V1, 2026-01-01 10:00")

    assert update_properties == {
        "CurrentlyAliveVials": "",
        "NextFlipDates": "",
        "NextEclosionDates": "",
        "FlipLog": "V1, 2026-01-01 10:00",
        "Status": "No longer maintained",
    }


def test_get_owned_document_falls_back_to_admin_records():
    database = FakeDatabase(
        {
            "stocks": [
                {"UniqueID": "admin-stock", "User": "admin", "Name": "Reference"}
            ]
        }
    )

    document = get_owned_document(
        "stocks", "scientist", "admin-stock", database, admin_include=True
    )

    assert document == {
        "UniqueID": "admin-stock",
        "User": "admin",
        "Name": "Reference",
    }


def test_apply_updates_to_owned_document_updates_data_and_log():
    database = FakeDatabase(
        {
            "crosses": [
                {
                    "UniqueID": "cross-1",
                    "User": "scientist",
                    "Name": "Original",
                    "ModificationLog": "older entry",
                }
            ]
        }
    )

    success, original_document = apply_updates_to_owned_document(
        "crosses",
        "scientist",
        "cross-1",
        database,
        {"Name": "Updated"},
    )

    updated_document = get_owned_document("crosses", "scientist", "cross-1", database)

    assert success is True
    assert original_document["Name"] == "Original"
    assert updated_document["Name"] == "Updated"
    assert "Name updated to Updated" in updated_document["ModificationLog"]
    assert updated_document["ModificationLog"].endswith("older entry")
    assert "DataModifiedDate" in updated_document


def test_flip_owned_document_updates_flip_state_and_commentary():
    database = FakeDatabase(
        {
            "stocks": [
                {
                    "UniqueID": "stock-1",
                    "User": "scientist",
                    "FlipLog": "2026-01-01 10:00",
                    "LastFlipDate": "2026-01-01 10:00",
                    "Status": "Healthy",
                    "Comments": "",
                    "ModificationLog": "stock created",
                }
            ]
        }
    )

    updated_document = flip_owned_document(
        "stocks",
        "scientist",
        "stock-1",
        database,
        "2026-01-02T12:30",
        new_status="Showing Issues",
        added_comment="check vials",
    )

    assert updated_document["LastFlipDate"] == "2026-01-02 12:30"
    assert updated_document["FlipLog"].startswith("V1, 2026-01-02 12:30")
    assert updated_document["CurrentlyAliveVials"] == "V1"
    assert updated_document["Status"] == "Showing Issues"
    assert updated_document["Comments"] == "check vials"
    assert "Status changed from Healthy to Showing Issues" in updated_document["ModificationLog"]
    assert "Comments added: check vials" in updated_document["ModificationLog"]


def test_delete_owned_document_removes_matching_record():
    database = FakeDatabase(
        {"crosses": [{"UniqueID": "cross-1", "User": "scientist"}]}
    )

    success = delete_owned_document("crosses", "scientist", "cross-1", database)

    assert success is True
    assert get_owned_document("crosses", "scientist", "cross-1", database) is None