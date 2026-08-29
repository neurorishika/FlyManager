from unittest.mock import ANY, patch

from flymanager.app import create_app
from flymanager.app.services.flybase import refresh_flybase_reference_data
from flymanager.utils.mongo.operation_locks import OperationLockConflict


class FakeScheduler:
    def __init__(self):
        self.running = False
        self.jobs = []
        self.started = False
        self.app = None

    def init_app(self, app):
        self.app = app

    def add_job(self, **kwargs):
        self.jobs.append(kwargs)

    def start(self):
        self.started = True
        self.running = True


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
    monkeypatch.setenv("SECRET_KEY", "test-secret-key")
    monkeypatch.setenv("MAIL_SUPPRESS_SEND", "1")

    with patch("flymanager.app.get_settings", return_value=_settings_payload()):
        app = create_app()
    app.config.update(TESTING=True, WTF_CSRF_ENABLED=False)
    return app


def test_admin_settings_renders_last_flybase_sync_status(monkeypatch):
    monkeypatch.setenv("ENABLE_SCHEDULER", "0")
    app = _make_app(monkeypatch)

    with app.test_client() as client:
        with client.session_transaction() as sess:
            sess["username"] = "admin"

        with patch(
            "flymanager.app.routes.settings.get_user_profiles",
            return_value=[{"Username": "admin", "Initials": "AD", "ReportsTo": ""}],
        ), patch(
            "flymanager.app.routes.settings.flybase_service.get_flybase_reference_status",
            return_value={
                "has_sync": True,
                "release": "FB2026_01",
                "synced_at": "2026-04-17 11:15:00",
                "last_attempt_at": "2026-04-17 11:15:00",
                "last_attempt_status": "success",
                "last_error": "",
                "downloaded_count": 6,
                "skipped_count": 2,
                "total_files": 8,
                "supported_rows": 144,
                "days_since_sync": 0,
                "flybase_ingested_collection_count": 2,
                "flybase_ingested_documents": 19,
                "flybase_ingestion_collections": {
                    "flybase_phenotypes": 7,
                    "flybase_stock_alleles": 12,
                },
                "balancer_count": 6,
                "balancer_markdown_exists": True,
                "balancer_json_exists": True,
                "phenotype_cache_source_label": "Mongo ingest",
                "phenotype_cache_summary": {
                    "cached_alleles": 3,
                    "cached_constructs": 1,
                    "cached_split_genotypes": 0,
                },
                "manifest_path": "/tmp/LATEST_DOWNLOAD_MANIFEST.json",
                "examination_output_path": "/tmp/EXAMINATION_REPORT.md",
                "examination_exists": True,
                "status_state": "healthy",
                "status_label": "Healthy",
                "status_tone": "success",
                "status_message": "FlyBase reference data is current based on the latest successful sync from today.",
                "status_detail": "Release FB2026_01 synced at 2026-04-17 11:15:00.",
                "curation_report_exists": True,
                "unresolved_token_report": {
                    "ambiguous_unresolved_total": 4,
                    "ambiguous_unresolved_unique": 2,
                    "unknown_unresolved_total": 3,
                    "unknown_unresolved_unique": 2,
                    "top_ambiguous_unresolved_tokens": [("Zip", 3), ("Alias", 1)],
                    "top_unknown_unresolved_tokens": [("Mystery", 2), ("OddThing", 1)],
                },
            },
        ):
            response = client.get("/settings")

    body = response.get_data(as_text=True)
    assert response.status_code == 200
    assert "onsubmit=" not in body
    assert "data-confirm-message=\"This will discover the latest FlyBase release, redownload the configured FlyBase datasets, regenerate the local report files, and refresh FlyBase stock gene metadata. Continue?\"" in body
    assert "FlyBase sync health:" in body
    assert "Healthy" in body
    assert "Last synced release:" in body
    assert "FB2026_01" in body
    assert "6 downloaded, 2 reused, 8 files tracked, 144 compatible stock rows harvested" in body
    assert "Normalized ingestion:" in body
    assert "2 collections, 19 documents refreshed" in body
    assert "Phenotype cache source:" in body
    assert "Mongo ingest" in body
    assert "Reference Ingestion Summary" in body
    assert "flybase_phenotypes" in body
    assert "Unresolved Token Curation" in body
    assert "Top Ambiguous Tokens" in body
    assert "Mystery" in body
    assert "Automatic FlyBase refresh:" in body
    assert "1:30 AM" in body


def test_admin_settings_renders_failed_flybase_sync_warning(monkeypatch):
    monkeypatch.setenv("ENABLE_SCHEDULER", "0")
    app = _make_app(monkeypatch)

    with app.test_client() as client:
        with client.session_transaction() as sess:
            sess["username"] = "admin"

        with patch(
            "flymanager.app.routes.settings.get_user_profiles",
            return_value=[{"Username": "admin", "Initials": "AD", "ReportsTo": ""}],
        ), patch(
            "flymanager.app.routes.settings.flybase_service.get_flybase_reference_status",
            return_value={
                "has_sync": True,
                "release": "FB2026_01",
                "synced_at": "2026-04-01 10:00:00",
                "last_attempt_at": "2026-04-17 11:30:00",
                "last_attempt_status": "failed",
                "last_error": "network timeout",
                "downloaded_count": 6,
                "skipped_count": 2,
                "total_files": 8,
                "supported_rows": 144,
                "days_since_sync": 16,
                "flybase_ingested_collection_count": 2,
                "flybase_ingested_documents": 19,
                "phenotype_cache_source_label": "Mongo ingest",
                "manifest_path": "/tmp/LATEST_DOWNLOAD_MANIFEST.json",
                "examination_output_path": "/tmp/EXAMINATION_REPORT.md",
                "examination_exists": True,
                "status_state": "failed",
                "status_label": "Failed",
                "status_tone": "danger",
                "status_message": "The last FlyBase refresh attempt failed at 2026-04-17 11:30:00.",
                "status_detail": "Last successful sync: 2026-04-01 10:00:00 (16 days ago).",
            },
        ):
            response = client.get("/settings")

    body = response.get_data(as_text=True)
    assert response.status_code == 200
    assert "Failed" in body
    assert "network timeout" in body
    assert "header-status-badge" in body
    assert "dropdown-status-badge" in body


def test_data_operations_page_surfaces_flybase_sync_health(monkeypatch):
    monkeypatch.setenv("ENABLE_SCHEDULER", "0")
    app = _make_app(monkeypatch)

    with app.test_client() as client:
        with client.session_transaction() as sess:
            sess["username"] = "admin"

        with patch(
            "flymanager.app.services.flybase.get_flybase_reference_status",
            return_value={
                "has_sync": True,
                "release": "FB2026_01",
                "synced_at": "2026-04-01 10:00:00",
                "last_attempt_at": "2026-04-17 11:30:00",
                "last_attempt_status": "failed",
                "last_error": "network timeout",
                "downloaded_count": 6,
                "skipped_count": 2,
                "total_files": 8,
                "supported_rows": 144,
                "days_since_sync": 16,
                "flybase_ingested_collection_count": 2,
                "flybase_ingested_documents": 19,
                "phenotype_cache_source_label": "Mongo ingest",
                "manifest_path": "/tmp/LATEST_DOWNLOAD_MANIFEST.json",
                "examination_output_path": "/tmp/EXAMINATION_REPORT.md",
                "examination_exists": True,
                "status_state": "failed",
                "status_label": "Failed",
                "status_tone": "danger",
                "status_message": "The last FlyBase refresh attempt failed at 2026-04-17 11:30:00.",
                "status_detail": "Last successful sync: 2026-04-01 10:00:00 (16 days ago).",
            },
        ):
            response = client.get("/data/upload")

    body = response.get_data(as_text=True)
    assert response.status_code == 200
    assert "Workbook Operations" in body
    assert "Export Workbook" in body
    assert "Import Workbook" in body
    assert "FlyBase sync failed." in body
    assert "Normalized ingestion:" in body
    assert "2 collections, 19 documents" in body
    assert "Phenotype cache source:" in body
    assert "Mongo ingest" in body
    assert "Open Admin Settings" in body


def test_admin_settings_renders_phenotype_cache_backfill_action(monkeypatch):
    monkeypatch.setenv("ENABLE_SCHEDULER", "0")
    app = _make_app(monkeypatch)

    with app.test_client() as client:
        with client.session_transaction() as sess:
            sess["username"] = "admin"

        with patch(
            "flymanager.app.routes.settings.get_user_profiles",
            return_value=[{"Username": "admin", "Initials": "AD", "ReportsTo": ""}],
        ), patch(
            "flymanager.app.routes.settings.flybase_service.get_flybase_reference_status",
            return_value={
                "has_sync": False,
                "status_state": "unknown",
                "status_label": "Unknown",
                "status_tone": "secondary",
                "status_message": "No FlyBase reference sync has completed yet.",
                "status_detail": "Run a sync to populate reference data.",
            },
        ):
            response = client.get("/settings")

    body = response.get_data(as_text=True)
    assert response.status_code == 200
    assert "Phenotype Cache Backfill" in body
    assert "Backfill All Phenotype Caches" in body
    assert "data-progress-form" in body
    assert "Backfilling phenotype caches for all users..." in body


def test_admin_can_backfill_phenotype_cache_for_all_users(monkeypatch):
    monkeypatch.setenv("ENABLE_SCHEDULER", "0")
    app = _make_app(monkeypatch)

    with app.test_client() as client:
        with client.session_transaction() as sess:
            sess["username"] = "admin"

        with patch(
            "flymanager.app.routes.settings.backfill_stock_phenotype_cache",
            return_value={"scanned": 5, "updated": 2, "skipped_valid": 3},
        ) as stock_backfill, patch(
            "flymanager.app.routes.settings.backfill_cross_phenotype_cache",
            return_value={"scanned": 4, "updated": 1, "skipped_valid": 3},
        ) as cross_backfill, patch(
            "flymanager.app.routes.settings.write_activity"
        ) as write_activity:
            response = client.post("/settings/backfill-all-phenotype-cache")

    assert response.status_code == 302
    stock_backfill.assert_called_once_with(ANY, users=None, dry_run=False, force=False)
    cross_backfill.assert_called_once_with(ANY, users=None, dry_run=False, force=False)
    write_activity.assert_called_once_with("admin", "Backfilled phenotype cache for all users", ANY)


def test_admin_refresh_flybase_reference_data_skips_when_lock_exists(monkeypatch):
    monkeypatch.setenv("ENABLE_SCHEDULER", "0")
    app = _make_app(monkeypatch)

    with app.test_client() as client:
        with client.session_transaction() as sess:
            sess["username"] = "admin"

        with patch(
            "flymanager.app.routes.settings.hold_operation_lock",
            side_effect=OperationLockConflict("A FlyBase reference refresh is already running."),
        ), patch(
            "flymanager.app.routes.settings.flybase_service.manual_refresh_flybase_reference_data"
        ) as refresh_action:
            response = client.post("/refresh-flybase-reference-data", follow_redirects=True)

    body = response.get_data(as_text=True)
    assert response.status_code == 200
    assert "A FlyBase reference refresh is already running." in body
    refresh_action.assert_not_called()


def test_create_app_registers_monthly_flybase_refresh_job(monkeypatch):
    monkeypatch.setenv("ENABLE_SCHEDULER", "1")
    fake_scheduler = FakeScheduler()

    with patch("flymanager.app.scheduler", fake_scheduler), patch(
        "flymanager.app.get_settings", return_value=_settings_payload()
    ):
        app = create_app()

    assert fake_scheduler.app is app
    assert fake_scheduler.started is True

    jobs_by_id = {job["id"]: job for job in fake_scheduler.jobs}
    assert "monthly_flybase_reference_refresh_job" in jobs_by_id
    assert jobs_by_id["monthly_flybase_reference_refresh_job"]["trigger"] == "cron"
    assert jobs_by_id["monthly_flybase_reference_refresh_job"]["day"] == 1
    assert jobs_by_id["monthly_flybase_reference_refresh_job"]["hour"] == 1
    assert jobs_by_id["monthly_flybase_reference_refresh_job"]["minute"] == 30


def test_refresh_flybase_reference_data_rebuilds_phenotype_cache_and_renews_records(monkeypatch, tmp_path):
    monkeypatch.setenv("ENABLE_SCHEDULER", "0")
    app = _make_app(monkeypatch)

    fake_bundle = {
        "release": "FB2026_01",
        "downloads": {
            "stocks": {"filename": "stocks_FB2026_01.tsv"},
        },
    }

    with patch("flymanager.app.db", {"stocks": object(), "crosses": object()}), patch(
        "flymanager.app.services.flybase.discover_latest_flybase_downloads",
        return_value=fake_bundle,
    ), patch(
        "flymanager.app.services.flybase.download_flybase_bundle",
        return_value={"release": "FB2026_01", "results": []},
    ), patch(
        "flymanager.app.services.flybase.examine_flybase_directory",
        return_value={"summary": {}},
    ), patch(
        "flymanager.app.services.flybase.render_flybase_examination_markdown",
        return_value="# report\n",
    ), patch(
        "flymanager.app.services.flybase.audit_bloomington_csv",
        return_value={
            "csv_path": str(tmp_path / "bloomington.csv"),
            "ambiguous_unresolved_total": 2,
            "ambiguous_unresolved_unique": 1,
            "unknown_unresolved_total": 1,
            "unknown_unresolved_unique": 1,
            "top_ambiguous_unresolved_tokens": [("Zip", 2)],
            "top_unknown_unresolved_tokens": [("Mystery", 1)],
        },
    ), patch(
        "flymanager.app.services.flybase.render_unresolved_token_curation_markdown",
        return_value="# unresolved\n",
    ), patch(
        "flymanager.app.services.flybase.update_gene_metadata_from_flybase",
        return_value={"supported_rows": 12},
    ), patch(
        "flymanager.app.services.flybase.discover_bdsc_balancers",
        return_value={"definitions": {"summary": {"total_balancers": 6}}},
    ), patch(
        "flymanager.app.services.flybase.write_balancer_report_files",
        return_value={
            "json_path": str(tmp_path / "BALANCER_DEFINITIONS.json"),
            "markdown_path": str(tmp_path / "BALANCER_REPORT.md"),
        },
    ), patch(
        "flymanager.app.services.flybase.ingest_balancer_definitions",
        return_value={"inserted": 6},
    ), patch(
        "flymanager.app.services.flybase.ingest_flybase_bundle",
        return_value={"collections": {"flybase_phenotypes": {"inserted": 7}}},
    ) as ingest_bundle, patch(
        "flymanager.app.services.flybase.build_flybase_phenotype_cache",
        return_value={
            "cache_path": str(tmp_path / "PHENOTYPE_EVIDENCE_CACHE.json"),
            "rebuilt": True,
            "source_kind": "mongo_ingest",
            "summary": {"cached_alleles": 3},
        },
    ) as phenotype_cache_builder, patch(
        "flymanager.app.services.flybase.backfill_stock_phenotype_cache",
        return_value={"scanned": 5, "updated": 5, "skipped_valid": 0},
    ) as stock_backfill, patch(
        "flymanager.app.services.flybase.backfill_cross_phenotype_cache",
        return_value={"scanned": 3, "updated": 3, "skipped_valid": 0},
    ) as cross_backfill, patch(
        "flymanager.app.services.flybase.backfill_stock_standardization_cache",
        return_value={"scanned": 5, "updated": 5, "skipped_valid": 0},
    ) as stock_standardization_backfill, patch(
        "flymanager.app.services.flybase.backfill_cross_standardization_cache",
        return_value={"scanned": 3, "updated": 3, "skipped_valid": 0},
    ) as cross_standardization_backfill:
        report = refresh_flybase_reference_data(app, data_dir=tmp_path)

    ingest_bundle.assert_called_once_with(tmp_path, ANY)
    phenotype_cache_builder.assert_called_once_with(data_dir=tmp_path, force=True, db=ANY)
    stock_backfill.assert_called_once()
    cross_backfill.assert_called_once()
    stock_standardization_backfill.assert_called_once()
    cross_standardization_backfill.assert_called_once()
    assert report["balancer_ingestion_report"]["db"]["inserted"] == 6
    assert report["flybase_ingestion_report"]["collections"]["flybase_phenotypes"]["inserted"] == 7
    assert report["phenotype_cache_report"]["source_kind"] == "mongo_ingest"
    assert report["phenotype_cache_report"]["rebuilt"] is True
    assert report["phenotype_backfill_summary"]["stocks"]["updated"] == 5
    assert report["unresolved_token_report"]["top_ambiguous_unresolved_tokens"] == [("Zip", 2)]


def test_flybase_refresh_updates_the_marker_catalog_before_rebuilding_caches(monkeypatch, tmp_path):
    """The monthly scheduler tick runs outside any request, so nothing else
    refreshes this process's catalog before it force-rebuilds every cache."""
    monkeypatch.setenv("ENABLE_SCHEDULER", "0")
    app = _make_app(monkeypatch)

    fake_bundle = {
        "release": "FB2026_01",
        "downloads": {
            "stocks": {"filename": "stocks_FB2026_01.tsv"},
        },
    }

    calls = []

    def _fake_refresh_catalog(db, **kwargs):
        calls.append("refresh_catalog")

    def _fake_stock_backfill(*args, **kwargs):
        calls.append("backfill_stock_phenotype_cache")
        return {"scanned": 5, "updated": 5, "skipped_valid": 0}

    with patch("flymanager.app.db", {"stocks": object(), "crosses": object()}), patch(
        "flymanager.app.services.flybase.discover_latest_flybase_downloads",
        return_value=fake_bundle,
    ), patch(
        "flymanager.app.services.flybase.download_flybase_bundle",
        return_value={"release": "FB2026_01", "results": []},
    ), patch(
        "flymanager.app.services.flybase.examine_flybase_directory",
        return_value={"summary": {}},
    ), patch(
        "flymanager.app.services.flybase.render_flybase_examination_markdown",
        return_value="# report\n",
    ), patch(
        "flymanager.app.services.flybase.audit_bloomington_csv",
        return_value={
            "csv_path": str(tmp_path / "bloomington.csv"),
            "ambiguous_unresolved_total": 2,
            "ambiguous_unresolved_unique": 1,
            "unknown_unresolved_total": 1,
            "unknown_unresolved_unique": 1,
            "top_ambiguous_unresolved_tokens": [("Zip", 2)],
            "top_unknown_unresolved_tokens": [("Mystery", 1)],
        },
    ), patch(
        "flymanager.app.services.flybase.render_unresolved_token_curation_markdown",
        return_value="# unresolved\n",
    ), patch(
        "flymanager.app.services.flybase.update_gene_metadata_from_flybase",
        return_value={"supported_rows": 12},
    ), patch(
        "flymanager.app.services.flybase.discover_bdsc_balancers",
        return_value={"definitions": {"summary": {"total_balancers": 6}}},
    ), patch(
        "flymanager.app.services.flybase.write_balancer_report_files",
        return_value={
            "json_path": str(tmp_path / "BALANCER_DEFINITIONS.json"),
            "markdown_path": str(tmp_path / "BALANCER_REPORT.md"),
        },
    ), patch(
        "flymanager.app.services.flybase.ingest_balancer_definitions",
        return_value={"inserted": 6},
    ), patch(
        "flymanager.app.services.flybase.ingest_flybase_bundle",
        return_value={"collections": {"flybase_phenotypes": {"inserted": 7}}},
    ), patch(
        "flymanager.app.services.flybase.build_flybase_phenotype_cache",
        return_value={
            "cache_path": str(tmp_path / "PHENOTYPE_EVIDENCE_CACHE.json"),
            "rebuilt": True,
            "source_kind": "mongo_ingest",
            "summary": {"cached_alleles": 3},
        },
    ), patch(
        "flymanager.utils.phenotypes.marker_catalog.refresh_catalog",
        side_effect=_fake_refresh_catalog,
    ), patch(
        "flymanager.app.services.flybase.backfill_stock_phenotype_cache",
        side_effect=_fake_stock_backfill,
    ), patch(
        "flymanager.app.services.flybase.backfill_cross_phenotype_cache",
        return_value={"scanned": 3, "updated": 3, "skipped_valid": 0},
    ), patch(
        "flymanager.app.services.flybase.backfill_stock_standardization_cache",
        return_value={"scanned": 5, "updated": 5, "skipped_valid": 0},
    ), patch(
        "flymanager.app.services.flybase.backfill_cross_standardization_cache",
        return_value={"scanned": 3, "updated": 3, "skipped_valid": 0},
    ):
        refresh_flybase_reference_data(app, data_dir=tmp_path)

    assert calls == ["refresh_catalog", "backfill_stock_phenotype_cache"], (
        "the marker catalog must be refreshed before the first cache-writing "
        "backfill runs, or a stale worker snapshot recomputes against the "
        "wrong marker set"
    )