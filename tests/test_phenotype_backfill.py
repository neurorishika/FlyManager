from unittest.mock import ANY, patch

from flymanager.app import create_app
from flymanager.utils.mongo.operation_locks import OperationLockConflict
from flymanager.utils.phenotypes.backfill import (
    backfill_cross_phenotype_cache, backfill_stock_phenotype_cache)
from flymanager.utils.phenotypes.predictor import PHENOTYPE_CACHE_VERSION


class FakeCollection:
    def __init__(self, records):
        self.records = list(records)
        self.updated = []

    def find(self, query, projection):
        del projection
        if not query:
            return list(self.records)

        if "$or" in query:
            allowed_users = set()
            allowed_assignees = set()
            for clause in query["$or"]:
                if "User" in clause:
                    allowed_users.update(clause["User"]["$in"])
                if "AssignedTo" in clause:
                    allowed_assignees.update(clause["AssignedTo"]["$in"])
            return [
                record
                for record in self.records
                if record.get("User") in allowed_users or record.get("AssignedTo") in allowed_assignees
            ]

        allowed_users = set(query["User"]["$in"])
        return [record for record in self.records if record.get("User") in allowed_users]

    def update_one(self, selector, update):
        self.updated.append((selector, update))

    def bulk_write(self, operations, ordered=True):
        del ordered
        for operation in operations:
            self.updated.append((operation._filter, operation._doc))


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


def test_backfill_stock_phenotype_cache_skips_valid_entries():
    collection = FakeCollection(
        [
            {
                "_id": 1,
                "User": "admin",
                "Genotype": "w[1118]; CyO/+; +; +",
                "PhenotypeCache": {
                    "version": PHENOTYPE_CACHE_VERSION,
                    "computedAt": "2026-04-12T09:00:00Z",
                    "genotype": "w[1118]; CyO/+; +; +",
                    "prediction": {"best_guess_summary": "w, Cy"},
                },
            },
            {
                "_id": 2,
                "User": "admin",
                "Genotype": "w[1118]; +; +; +",
            },
        ]
    )

    summary = backfill_stock_phenotype_cache(collection)

    assert summary == {"scanned": 2, "updated": 1, "skipped_valid": 1}
    assert collection.updated[0][0] == {"_id": 2}
    assert collection.updated[0][1]["$set"]["PhenotypeCache"]["genotype"] == "w[1118]; +; +; +"


def test_backfill_cross_phenotype_cache_honors_user_filter_and_dry_run():
    collection = FakeCollection(
        [
            {
                "_id": 1,
                "User": "admin",
                "MaleGenotype": "w[1118]; CyO/+; +; +",
                "FemaleGenotype": "+; +; Sb[1]/+; +",
            },
            {
                "_id": 2,
                "User": "tech",
                "MaleGenotype": "w[1118]; +; +; +",
                "FemaleGenotype": "+; +; +; +",
            },
        ]
    )

    summary = backfill_cross_phenotype_cache(
        collection,
        users=["tech"],
        dry_run=True,
    )

    assert summary == {"scanned": 1, "updated": 1, "skipped_valid": 0}
    assert collection.updated == []


def test_backfill_stock_phenotype_cache_targets_records_user_actually_maintains():
    collection = FakeCollection(
        [
            {
                "_id": 1,
                "User": "tech",
                "AssignedTo": "",
                "Genotype": "w[1118]; +; +; +",
            },
            {
                "_id": 2,
                "User": "admin",
                "AssignedTo": "tech",
                "Genotype": "w[1118]; CyO/+; +; +",
            },
            {
                "_id": 3,
                "User": "tech",
                "AssignedTo": "admin",
                "Genotype": "w[1118]; CyO/+; +; +",
            },
        ]
    )

    summary = backfill_stock_phenotype_cache(collection, users=["tech"], dry_run=True)

    assert summary == {"scanned": 2, "updated": 2, "skipped_valid": 0}


def test_logged_in_user_can_backfill_owned_phenotype_caches(monkeypatch):
    app = _make_app(monkeypatch)

    with app.test_client() as client:
        with client.session_transaction() as sess:
            sess["username"] = "tech"

        with patch(
            "flymanager.app.routes.settings.backfill_stock_phenotype_cache",
            return_value={"scanned": 3, "updated": 2, "skipped_valid": 1},
        ) as stock_backfill, patch(
            "flymanager.app.routes.settings.backfill_cross_phenotype_cache",
            return_value={"scanned": 2, "updated": 1, "skipped_valid": 1},
        ) as cross_backfill, patch(
            "flymanager.app.routes.settings.write_activity"
        ) as write_activity:
            response = client.post("/settings/backfill-my-phenotype-cache")

    assert response.status_code == 302
    stock_backfill.assert_called_once_with(ANY, users=["tech"], dry_run=False, force=False)
    cross_backfill.assert_called_once_with(ANY, users=["tech"], dry_run=False, force=False)
    write_activity.assert_called_once_with("tech", "Backfilled phenotype cache for maintained records", ANY)


def test_logged_in_user_sees_warning_when_owned_backfill_already_running(monkeypatch):
    app = _make_app(monkeypatch)

    with app.test_client() as client:
        with client.session_transaction() as sess:
            sess["username"] = "tech"

        with patch(
            "flymanager.app.routes.settings.hold_operation_lock",
            side_effect=OperationLockConflict("Your phenotype cache backfill is already running."),
        ), patch(
            "flymanager.app.routes.settings.backfill_stock_phenotype_cache"
        ) as stock_backfill, patch(
            "flymanager.app.routes.settings.backfill_cross_phenotype_cache"
        ) as cross_backfill:
            response = client.post("/settings/backfill-my-phenotype-cache", follow_redirects=True)

    body = response.get_data(as_text=True)
    assert response.status_code == 200
    assert "Your phenotype cache backfill is already running." in body
    stock_backfill.assert_not_called()
    cross_backfill.assert_not_called()