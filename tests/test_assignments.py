import importlib.util
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import ANY, patch

from flymanager.app import create_app
from flymanager.utils.mongo.access import (annotate_document_access,
                                           get_accessible_documents,
                                           update_document_assignment)


class FakeCollection:
    def __init__(self, database, name):
        self.database = database
        self.name = name

    def find(self, query=None):
        query = query or {}
        records = []
        for record in self.database.data.get(self.name, []):
            if all(record.get(key) == value for key, value in query.items()):
                records.append(dict(record))
        return records

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


class FakeDatabase:
    def __init__(self, initial_data=None):
        self.data = {
            name: [dict(item) for item in records]
            for name, records in (initial_data or {}).items()
        }

    def __getitem__(self, name):
        self.data.setdefault(name, [])
        return FakeCollection(self, name)


def _settings_payload():
    return {
        "lab_info": {
            "lab_name": "Test Lab",
            "admin_name": "Admin",
            "admin_email": "admin@example.com",
        },
        "theme": {
            "accent_color": "#0055aa",
            "dark_mode": False,
        },
    }


def _make_app(monkeypatch):
    monkeypatch.setenv("ENABLE_SCHEDULER", "0")
    monkeypatch.setenv("SECRET_KEY", "test-secret-key")
    monkeypatch.setenv("MAIL_SUPPRESS_SEND", "1")

    with patch("flymanager.app.get_settings", return_value=_settings_payload()):
        app = create_app()
    app.config.update(TESTING=True, WTF_CSRF_ENABLED=False)
    return app


def _stock_metadata_lookup(metadata_type, _db):
    metadata = {
        "types": ["Balancer"],
        "food_types": ["Molasses"],
        "provenances": ["Internal"],
        "genesX": [],
        "genes2nd": [],
        "genes3rd": [],
        "genes4th": [],
        "species": ["D. melanogaster"],
    }
    return metadata[metadata_type]


def _load_scanner_service_module():
    scanner_path = (
        Path(__file__).resolve().parents[1]
        / "flymanager"
        / "app"
        / "services"
        / "scanner.py"
    )
    spec = importlib.util.spec_from_file_location(
        "flymanager_scanner_test_module",
        scanner_path,
    )
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


def test_annotate_document_access_distinguishes_owner_and_assignee():
    document = {"UniqueID": "STK1", "User": "lead", "AssignedTo": "assistant"}

    lead_view = annotate_document_access(document, "lead")
    assistant_view = annotate_document_access(document, "assistant")

    assert lead_view["AssignmentScope"] == "assigned_out"
    assert lead_view["AssignmentScopeDetail"] == "Maintained by assistant"
    assert assistant_view["AssignmentScope"] == "incoming"
    assert assistant_view["AssignmentScopeDetail"] == "Owned by lead"


def test_get_accessible_documents_combines_owned_and_assigned_records_without_duplicates():
    database = FakeDatabase(
        {
            "stocks": [
                {"UniqueID": "OWN1", "User": "assistant", "AssignedTo": ""},
                {"UniqueID": "IN1", "User": "lead", "AssignedTo": "assistant"},
                {"UniqueID": "OUT1", "User": "assistant", "AssignedTo": "student"},
            ]
        }
    )

    documents = get_accessible_documents("stocks", "assistant", database, annotate=True)
    document_ids = {document["UniqueID"] for document in documents}

    assert document_ids == {"OWN1", "IN1", "OUT1"}


def test_update_document_assignment_only_allows_direct_reports():
    database = FakeDatabase(
        {
            "users": [
                {"Username": "lead", "ReportsTo": ""},
                {"Username": "assistant", "ReportsTo": "lead"},
                {"Username": "outsider", "ReportsTo": ""},
            ],
            "stocks": [
                {"UniqueID": "STK1", "User": "lead", "AssignedTo": "", "ModificationLog": "created"}
            ],
        }
    )

    success, error_message = update_document_assignment("stocks", "lead", "STK1", "outsider", database)
    assert success is False
    assert error_message == "Assignee must be one of your direct reports."

    success, error_message = update_document_assignment("stocks", "lead", "STK1", "assistant", database)
    assert success is True
    assert error_message is None
    updated_record = database["stocks"].find_one({"UniqueID": "STK1", "User": "lead"})
    assert updated_record["AssignedTo"] == "assistant"
    assert "Assignment updated to assigned to assistant" in updated_record["ModificationLog"]


def test_stock_view_renders_assigned_record_read_only(monkeypatch):
    app = _make_app(monkeypatch)
    assigned_stock = {
        "UniqueID": "UID1",
        "User": "lead",
        "OwnerUser": "lead",
        "AssignedTo": "assistant",
        "MaintainerUser": "assistant",
        "AssignmentScope": "incoming",
        "AssignmentScopeLabel": "Maintain",
        "AssignmentScopeDetail": "Owned by lead",
        "ViewerCanEdit": False,
        "ViewerCanMaintain": True,
        "SourceID": "SRC1",
        "StockSource": "OTHER",
        "SourceCollection": "",
        "FlyBaseStockID": "",
        "Genotype": "w[*]; ; ; ",
        "Name": "Assigned Stock",
        "AltReference": "",
        "Type": "Balancer",
        "FoodType": "Molasses",
        "Status": "Healthy",
        "SeriesID": "1",
        "ReplicateID": "a",
        "VialLifetime": "14",
        "FlipFrequency": "7",
        "DevelopmentalTime": "10",
        "Species": "D. melanogaster",
        "Comments": "",
        "Provenance": "Internal",
        "ExternalSupportStatus": "manual",
        "ExternalSupportReason": "",
        "TrayID": "T1",
        "TrayPosition": "1",
        "CreationDate": "2026-01-01 10:00",
        "LastFlipDate": "2026-01-02 10:00",
        "CurrentlyAliveVials": "V1",
        "FlipLog": "V1, 2026-01-02 10:00",
        "NextFlipDates": "2026-01-09",
        "NextEclosionDates": "2026-01-12",
        "DataModifiedDate": "2026-01-02 10:00",
        "ModificationLog": "created",
    }

    with app.test_client() as client:
        with client.session_transaction() as sess:
            sess["username"] = "assistant"

        with patch(
            "flymanager.app.routes.stock.get_metadata",
            side_effect=_stock_metadata_lookup,
        ), patch(
            "flymanager.app.routes.stock.get_accessible_stock",
            return_value=assigned_stock,
        ), patch(
            "flymanager.app.routes.stock.enrich_stock_source_context",
            return_value={
                "sourceType": "OTHER",
                "sourceCollection": "",
                "flyBaseStockID": "",
                "providerURL": "",
                "providerLinkLabel": "",
                "providerLinkKind": "",
            },
        ), patch(
            "flymanager.app.routes.stock._get_stock_phenotype_for_view",
            return_value={
                "best_guess_summary": "No marker phenotype predicted",
                "best_guess_basis": "shared",
                "female_summary": "No marker phenotype predicted",
                "male_summary": "No marker phenotype predicted",
                "female_construct_annotation_labels": [],
                "male_construct_annotation_labels": [],
                "female_split_system_labels": [],
                "male_split_system_labels": [],
                "shared_summary": "No marker phenotype predicted",
                "female_only_labels": [],
                "male_only_labels": [],
                "warnings": [],
                "confidence_label": "low",
            },
        ):
            response = client.get("/stock/view/UID1")

    page = response.get_data(as_text=True)
    assert response.status_code == 200
    assert "Owner & Maintainer" in page
    assert "Owned by lead" in page
    assert 'id="enableEditBtn"' not in page


def test_cross_view_renders_assigned_record_read_only(monkeypatch):
    app = _make_app(monkeypatch)
    assigned_cross = {
        "UniqueID": "CRS1",
        "User": "lead",
        "OwnerUser": "lead",
        "AssignedTo": "assistant",
        "MaintainerUser": "assistant",
        "AssignmentScope": "incoming",
        "AssignmentScopeLabel": "Maintain",
        "AssignmentScopeDetail": "Owned by lead",
        "ViewerCanEdit": False,
        "ViewerCanMaintain": True,
        "MaleUniqueID": "M1",
        "FemaleUniqueID": "F1",
        "MaleGenotype": "w[1118]; CyO/+; +; +",
        "FemaleGenotype": "+; +; Sb[1]/+; +",
        "MaleSpecies": "D. melanogaster",
        "FemaleSpecies": "D. melanogaster",
        "TrayID": "T1",
        "TrayPosition": "1",
        "Status": "Healthy",
        "FoodType": "Molasses",
        "Name": "Assigned Cross",
        "Comments": "",
        "VialLifetime": 14,
        "FlipFrequency": 7,
        "DevelopmentalTime": 10,
        "MaxCrossLifetime": 30,
        "CreationDate": "2026-01-01 10:00",
        "LastFlipDate": "2026-01-02 10:00",
        "CurrentlyAliveVials": "V1",
        "FlipLog": "",
        "NextFlipDates": "",
        "NextEclosionDates": "",
        "DataModifiedDate": "2026-01-02 10:00",
        "ModificationLog": "created",
    }

    with app.test_client() as client:
        with client.session_transaction() as sess:
            sess["username"] = "assistant"

        with patch(
            "flymanager.app.routes.cross.get_metadata",
            side_effect=lambda name, _db: ["Molasses"] if name == "food_types" else [],
        ), patch(
            "flymanager.app.routes.cross.get_all_genotypes",
            return_value=["w[1118]; CyO/+; +; +", "+; +; Sb[1]/+; +"],
        ), patch(
            "flymanager.app.routes.cross.get_accessible_cross",
            return_value=assigned_cross,
        ), patch(
            "flymanager.app.routes.cross._get_cross_phenotype_for_view",
            return_value=(
                {
                    "male": {
                        "summary": "w, Cy",
                        "confidence_label": "high",
                        "construct_annotation_labels": [],
                        "split_system_labels": [],
                        "warnings": [],
                    },
                    "female": {
                        "summary": "Sb",
                        "confidence_label": "high",
                        "construct_annotation_labels": [],
                        "split_system_labels": [],
                        "warnings": [],
                    },
                    "summary": "Male w, Cy / Female Sb",
                    "source_counts": {},
                },
                [],
                {"cached_at": "", "is_cached": False},
            ),
        ):
            response = client.get("/cross/view_cross/CRS1")

    page = response.get_data(as_text=True)
    assert response.status_code == 200
    assert "Owned by lead" in page
    assert "Maintenance Assignment" not in page
    assert ">Save Changes<" not in page


def test_settings_page_allows_admin_as_reporting_manager(monkeypatch):
    app = _make_app(monkeypatch)
    user_profiles = [
        {"Username": "admin", "Initials": "AD", "ReportsTo": ""},
        {"Username": "rmohanta", "Initials": "RM", "ReportsTo": ""},
    ]

    with app.test_client() as client:
        with client.session_transaction() as sess:
            sess["username"] = "admin"

        with patch(
            "flymanager.app.routes.settings.get_user_profiles",
            return_value=user_profiles,
        ):
            response = client.get("/settings")

    page = response.get_data(as_text=True)
    assert response.status_code == 200
    assert '<option value="admin"' in page


def test_stock_assignment_route_updates_owned_stock(monkeypatch):
    app = _make_app(monkeypatch)

    with app.test_client() as client:
        with client.session_transaction() as sess:
            sess["username"] = "lead"

        with patch(
            "flymanager.app.routes.stock.get_accessible_stock",
            return_value={"UniqueID": "STK1", "User": "lead", "ViewerCanEdit": True},
        ), patch(
            "flymanager.app.routes.stock.update_document_assignment",
            return_value=(True, None),
        ) as update_mock, patch(
            "flymanager.app.routes.stock.write_activity"
        ) as activity_mock:
            response = client.post("/stock/assign/STK1", data={"assignee": "assistant"})

    assert response.status_code == 302
    update_mock.assert_called_once_with("stocks", "lead", "STK1", "assistant", ANY)
    activity_mock.assert_called_once()


def test_cross_assignment_route_updates_owned_cross(monkeypatch):
    app = _make_app(monkeypatch)

    with app.test_client() as client:
        with client.session_transaction() as sess:
            sess["username"] = "lead"

        with patch(
            "flymanager.app.routes.cross.get_accessible_cross",
            return_value={"UniqueID": "CRS1", "User": "lead", "ViewerCanEdit": True},
        ), patch(
            "flymanager.app.routes.cross.update_document_assignment",
            return_value=(True, None),
        ) as update_mock, patch(
            "flymanager.app.routes.cross.write_activity"
        ) as activity_mock:
            response = client.post("/cross/assign_cross/CRS1", data={"assignee": "assistant"})

    assert response.status_code == 302
    update_mock.assert_called_once_with("crosses", "lead", "CRS1", "assistant", ANY)
    activity_mock.assert_called_once()


def test_tray_assignment_route_updates_all_active_tray_items(monkeypatch):
    app = _make_app(monkeypatch)
    fake_db = FakeDatabase(
        {
            "stocks": [
                {"UniqueID": "STK1", "User": "lead", "TrayID": "T1", "Status": "Healthy"},
                {"UniqueID": "STK2", "User": "lead", "TrayID": "T1", "Status": "No longer maintained"},
            ],
            "crosses": [
                {"UniqueID": "CRS1", "User": "lead", "TrayID": "T1", "Status": "Healthy"},
            ],
        }
    )

    with app.test_client() as client:
        with client.session_transaction() as sess:
            sess["username"] = "lead"

        with patch(
            "flymanager.app.routes.tray.db",
            fake_db,
        ), patch(
            "flymanager.app.routes.tray.get_tray",
            return_value={"UniqueID": "tray-key", "TrayID": "T1", "User": "lead"},
        ), patch(
            "flymanager.app.routes.tray.bulk_update_document_assignments",
            return_value=(2, None),
        ) as update_mock, patch(
            "flymanager.app.routes.tray.write_activity"
        ) as activity_mock:
            response = client.post("/tray/assign_tray/tray-key", data={"assignee": "assistant"})

    assert response.status_code == 302
    update_mock.assert_called_once_with(
        "lead", "assistant", [("stocks", "STK1"), ("crosses", "CRS1")], fake_db,
    )
    activity_mock.assert_called_once()


def test_shared_tray_view_hides_owner_controls(monkeypatch):
    app = _make_app(monkeypatch)

    with app.test_client() as client:
        with client.session_transaction() as sess:
            sess["username"] = "assistant"

        with patch(
            "flymanager.app.routes.tray.get_accessible_tray",
            return_value={
                "UniqueID": "tray-key",
                "TrayID": "T1",
                "Name": "Lead Tray",
                "User": "lead",
                "OwnerUser": "lead",
                "Rows": 5,
                "Columns": 5,
                "Description": "",
            },
        ), patch(
            "flymanager.app.routes.tray.get_tray_occupancy",
            return_value={},
        ), patch(
            "flymanager.app.routes.tray.get_accessible_stocks",
            return_value=[],
        ), patch(
            "flymanager.app.routes.tray.get_accessible_crosses",
            return_value=[],
        ):
            response = client.get("/tray/tray/tray-key")

    page = response.get_data(as_text=True)
    assert response.status_code == 200
    assert "Owned by lead." in page
    assert "Edit Tray" not in page
    assert "Delete Tray" not in page


def test_scanner_lookup_returns_assigned_stock_payload():
    scanner_service = _load_scanner_service_module()

    with patch.object(
        scanner_service,
        "get_accessible_stock",
        return_value={
            "UniqueID": "STK1",
            "Name": "Assigned Stock",
            "Genotype": "w[1118]; CyO/+; +; +",
            "Status": "Healthy",
            "SeriesID": "1",
            "ReplicateID": "a",
            "TrayID": "T1",
            "TrayPosition": "1",
            "FoodType": "Molasses",
            "Provenance": "Internal",
            "AltReference": "",
        },
    ) as stock_lookup, patch.object(
        scanner_service,
        "get_accessible_cross",
        return_value=None,
    ) as cross_lookup:
        item_type, payload = scanner_service.lookup_uid_result("assistant", "STK1")

    assert item_type == "stock"
    assert payload["uniqueID"] == "STK1"
    stock_lookup.assert_called_once()
    cross_lookup.assert_not_called()


def test_scanner_lookup_returns_assigned_cross_payload():
    scanner_service = _load_scanner_service_module()

    with patch.object(
        scanner_service,
        "get_accessible_stock",
        return_value=None,
    ) as stock_lookup, patch.object(
        scanner_service,
        "get_accessible_cross",
        return_value={
            "UniqueID": "CRS1",
            "Name": "Assigned Cross",
            "Status": "Healthy",
            "MaleGenotype": "w[1118]; CyO/+; +; +",
            "FemaleGenotype": "+; +; Sb[1]/+; +",
            "TrayID": "T1",
            "TrayPosition": "2",
            "FoodType": "Molasses",
        },
    ) as cross_lookup:
        item_type, payload = scanner_service.lookup_uid_result("assistant", "CRS1")

    assert item_type == "cross"
    assert payload["uniqueID"] == "CRS1"
    stock_lookup.assert_called_once()
    cross_lookup.assert_called_once()