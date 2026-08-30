import json
import re
from pathlib import Path
from unittest.mock import patch

from flymanager.app import create_app
from flymanager.utils.mongo.operation_locks import OperationLockConflict
from flymanager.utils.phenotypes.image_library import \
    select_phenotype_reference_images
from flymanager.utils.phenotypes.predictor import PHENOTYPE_CACHE_VERSION
from tests.mongo_fakes import FakeDatabase

MARKER_IMAGE_SEED = json.loads(
    Path("data/markers/images/index.json").read_text(encoding="utf-8")
)


def _seed_image_id(source_path):
    return next(row["imageId"] for row in MARKER_IMAGE_SEED
                if row["display"]["sourcePath"] == source_path)


def _source_path(match):
    return next(row["display"]["sourcePath"] for row in MARKER_IMAGE_SEED
                if row["imageId"] == match["image_id"])


def _stock_explorer_fake_db(records):
    """stock_explorer/stock_explorer_selection now query `db` directly
    (Task 12 - deterministic filtering/pagination pushed into Mongo), so
    tests seed a FakeDatabase instead of patching get_accessible_stocks.
    """
    seeded = []
    for record in records:
        record = dict(record)
        record.setdefault("User", "admin")
        record.setdefault("AssignedTo", "")
        seeded.append(record)
    return FakeDatabase({"stocks": seeded})


def _cross_explorer_fake_db(records):
    """cross_explorer/cross_explorer_selection now query `db` directly
    (Task 12 - deterministic filtering/pagination pushed into Mongo), so
    tests seed a FakeDatabase instead of patching get_accessible_crosses.
    """
    seeded = []
    for record in records:
        record = dict(record)
        record.setdefault("User", "admin")
        record.setdefault("AssignedTo", "")
        seeded.append(record)
    return FakeDatabase({"crosses": seeded})


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
    monkeypatch.delenv("FLYMANAGER_DOMAIN", raising=False)

    with patch("flymanager.app.get_settings", return_value=_settings_payload()):
        app = create_app()
    app.config.update(TESTING=True)
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


def _cross_metadata_lookup(metadata_type, _db):
    metadata = {
        "food_types": ["Molasses"],
    }
    return metadata[metadata_type]


class _FakeCollection:
    def __init__(self):
        self.calls = []

    def update_one(self, selector, update):
        self.calls.append((selector, update))


def _build_stock_explorer_record(index):
    padded = f"{index:03d}"
    return {
        "UniqueID": f"UID-{padded}",
        "AssignmentScope": "maintain",
        "AssignmentScopeLabel": "Maintain",
        "AssignmentScopeDetail": "Owned by you",
        "OwnerUser": "admin",
        "MaintainerUser": "admin",
        "AssignedTo": "",
        "StockSource": "BDSC",
        "SourceCollection": "Bloomington",
        "SourceID": padded,
        "Name": f"Stock-{padded}",
        "Genotype": "w[1118]; CyO/+; +; +",
        "AltReference": "",
        "Type": "Balancer",
        "FoodType": "Molasses",
        "Species": "D. melanogaster",
        "Status": "Healthy",
        "SeriesID": "1",
        "ReplicateID": "a",
        "Provenance": "Bloomington",
        "TrayID": "A",
        "TrayPosition": str(index),
        "Comments": "",
        "CreationDate": "2026-04-01",
        "LastFlipDate": "2026-04-10",
        "DataModifiedDate": "2026-04-12",
        "PhenotypeCache": {
            "version": PHENOTYPE_CACHE_VERSION,
            "computedAt": "2026-04-12T09:00:00Z",
            "genotype": "w[1118]; CyO/+; +; +",
            "prediction": {
                "best_guess_summary": "w, Cy",
                "confidence_label": "high",
            },
        },
    }


def _build_cross_explorer_record(index):
    padded = f"{index:03d}"
    return {
        "UniqueID": f"CROSS-{padded}",
        "AssignmentScope": "maintain",
        "AssignmentScopeLabel": "Maintain",
        "AssignmentScopeDetail": "Owned by you",
        "OwnerUser": "admin",
        "MaintainerUser": "admin",
        "AssignedTo": "",
        "MaleUniqueID": f"M-{padded}",
        "FemaleUniqueID": f"F-{padded}",
        "MaleGenotype": "w[1118]; CyO/+; +; +",
        "FemaleGenotype": "+; +; Sb[1]/+; +",
        "MaleSpecies": "D. melanogaster",
        "FemaleSpecies": "D. melanogaster",
        "Status": "Healthy",
        "FoodType": "Molasses",
        "Name": f"Cross-{padded}",
        "TrayID": "B",
        "TrayPosition": str(index),
        "Comments": "",
        "CreationDate": "2026-04-01",
        "LastFlipDate": "2026-04-10",
        "DataModifiedDate": "2026-04-12",
        "PhenotypeCache": {
            "version": PHENOTYPE_CACHE_VERSION,
            "computedAt": "2026-04-12T09:00:00Z",
            "maleGenotype": "w[1118]; CyO/+; +; +",
            "femaleGenotype": "+; +; Sb[1]/+; +",
            "simulationSummary": {
                "raw_class_count": 2,
                "viable_class_count": 1,
                "pruned_class_count": 1,
                "viable_fraction": 0.5,
                "identifiable_viable_fraction": 0.5,
                "confident_sortable_fraction": 0.5,
                "low_confidence_sorting_fraction": 0.0,
                "weighted_identifiability_score": 0.91,
                "estimated_sortable_targets_per_vial": 7.6,
                "visible_marker_class_count": 1,
                "best_sortable_class": {
                    "genotype": "w[1118]; CyO/+; +; +",
                    "sex": "female",
                    "phenotype_summary": "w, Cy",
                    "probability_percent": 50.0,
                    "viable_probability_percent": 100.0,
                    "identifiability": {
                        "selection_instructions": "Select flies with w, Cy.",
                    },
                    "yield_estimate": {
                        "expected_targets_per_vial": 7.6,
                    },
                },
            },
            "directionEvaluation": {
                "forward": {
                    "male_genotype": "w[1118]; CyO/+; +; +",
                    "female_genotype": "+; +; Sb[1]/+; +",
                    "score": 0.82,
                    "summary": {
                        "visible_marker_class_count": 1,
                    },
                },
                "reverse": {
                    "male_genotype": "+; +; Sb[1]/+; +",
                    "female_genotype": "w[1118]; CyO/+; +; +",
                    "score": 0.61,
                    "summary": {
                        "visible_marker_class_count": 1,
                    },
                },
                "recommended_direction": "forward",
                "recommended_cross": {
                    "male": "w[1118]; CyO/+; +; +",
                    "female": "+; +; Sb[1]/+; +",
                },
                "same_outcome": False,
                "rationale": ["Forward order is recommended because it produces a better phenotype-sorting outcome."],
                "operational_notes": ["Virgin-collection burden is not yet modeled explicitly; this recommendation is based on viability, sortable yield, and phenotype identifiability."],
            },
            "parentPhenotypes": {
                "male": {
                    "summary": "w, Cy",
                    "confidence_label": "high",
                    "warnings": [],
                },
                "female": {
                    "summary": "Sb",
                    "confidence_label": "high",
                    "warnings": [],
                },
                "summary": "Male w, Cy / Female Sb",
                "source_counts": {},
            },
            "predictedOffspring": [],
        },
    }


def _extract_csrf_token(response_text):
    match = re.search(r'<meta name="csrf-token" content="([^"]+)"', response_text)
    assert match, "CSRF token meta tag not found"
    return match.group(1)


def _get_authenticated_csrf_token(client):
    with patch("flymanager.app.routes.flip.get_available_ports", return_value=[]):
        response = client.get("/flip/")
    return _extract_csrf_token(response.get_data(as_text=True))


def test_stock_view_route_renders_phenotype_preview(monkeypatch, tmp_path):
    app = _make_app(monkeypatch)
    stock_record = {
        "UniqueID": "UID1",
        "User": "admin",
        "ViewerCanEdit": False,
        "AssignmentScopeLabel": "Maintain",
        "AssignmentScopeDetail": "Owned by you",
        "OwnerUser": "admin",
        "MaintainerUser": "admin",
        "AssignedTo": "",
        "SourceID": "17",
        "StockSource": "BDSC",
        "SourceCollection": "Bloomington",
        "FlyBaseStockID": "FBst0000017",
        "Genotype": "w[1118]; CyO/+; +; +",
        "Name": "Curly tester",
        "AltReference": "FBst0000017",
        "Type": "Balancer",
        "FoodType": "Molasses",
        "Status": "Healthy",
        "SeriesID": "1",
        "ReplicateID": "a",
        "VialLifetime": 14,
        "FlipFrequency": 7,
        "DevelopmentalTime": 10,
        "Species": "D. melanogaster",
        "Comments": "",
        "Provenance": "Bloomington",
        "TrayID": "A",
        "TrayPosition": "1",
        "CreationDate": "2026-04-01",
        "LastFlipDate": "2026-04-10",
        "CurrentlyAliveVials": 2,
        "FlipLog": "",
        "NextFlipDates": "",
        "NextEclosionDates": "",
        "DataModifiedDate": "2026-04-12",
        "ModificationLog": "",
        "PhenotypeCache": {
            "version": PHENOTYPE_CACHE_VERSION,
            "computedAt": "2026-04-12T09:00:00Z",
            "genotype": "w[1118]; CyO/+; +; +",
            "prediction": {
                "best_guess_summary": "w, Cy",
                "best_guess_basis": "shared",
                "female_summary": "w, Cy",
                "male_summary": "w, Cy",
                "female_construct_annotation_labels": ["GFP reporter"],
                "male_construct_annotation_labels": ["GFP reporter"],
                "female_split_system_labels": ["Split-GFP"],
                "male_split_system_labels": ["Split-GFP"],
                "shared_summary": "w, Cy",
                "shared_markers": [
                    {"display_label": "w", "phenotype_key": "w_loss", "body_part": "eye", "effect": "white eyes"},
                    {"display_label": "Cy", "phenotype_key": "Cy", "body_part": "wing", "effect": "curly wings"},
                ],
                "shared_marker_labels": ["w", "Cy"],
                "female_markers": [
                    {"display_label": "w", "phenotype_key": "w_loss", "body_part": "eye", "effect": "white eyes"},
                    {"display_label": "Cy", "phenotype_key": "Cy", "body_part": "wing", "effect": "curly wings"},
                ],
                "female_marker_labels": ["w", "Cy"],
                "male_markers": [
                    {"display_label": "w", "phenotype_key": "w_loss", "body_part": "eye", "effect": "white eyes"},
                    {"display_label": "Cy", "phenotype_key": "Cy", "body_part": "wing", "effect": "curly wings"},
                ],
                "male_marker_labels": ["w", "Cy"],
                "female_only_labels": [],
                "male_only_labels": [],
                "warnings": [],
                "confidence_label": "high",
            },
        },
    }

    with app.test_client() as client:
        with client.session_transaction() as sess:
            sess["username"] = "admin"

        with patch("flymanager.app.routes.stock.get_metadata", side_effect=_stock_metadata_lookup), patch(
            "flymanager.app.routes.stock.get_accessible_stock",
            return_value=stock_record,
        ):
            response = client.get("/stock/view/UID1")

    body = response.get_data(as_text=True)
    assert response.status_code == 200
    assert "Phenotype Preview" in body
    assert "w, Cy" in body
    assert "Reference image for w" in body
    assert "Reference image for Cy" in body
    assert f"/markers/images/{_seed_image_id('learning_to_fly/eye/w-.png')}" in body
    assert f"/markers/images/{_seed_image_id('learning_to_fly/wings/cy.png')}" in body
    assert "Learning to Fly" in body
    assert "Local phenotype image library" in body
    assert "Construct annotations: GFP reporter" in body
    assert "Split systems: Split-GFP" in body
    assert "Canonicalization Review" in body
    assert "Review Standardization" in body
    assert "/static/vendor/tagify/tagify.min.js" in body
    assert "/static/vendor/tagify/tagify.polyfills.min.js" in body
    assert "/static/vendor/tagify/tagify.css" in body
    assert "https://cdn.jsdelivr.net/npm/@yaireo/tagify" not in body
    assert "data-progress-form" in body
    assert "Refreshing this stock phenotype cache..." in body
    assert "field.classList.contains('tag-input')" in body
    assert "element.removeAttribute('disabled');" in body
    # Editing is per-field: every editable control ships an edit button that
    # unlocks just that control, driven by the shared field_edit.js module.
    # There is no page-wide "enable editing" mode.
    assert "/static/js/field_edit.js" in body
    assert 'data-edit-target="genotypeX"' in body
    assert 'data-edit-target="sourceID"' in body
    assert 'id="enableEditBtn"' not in body
    # tagify.css must load in <head>, before bootstrap-density.css, or
    # tagify's own light-theme defaults win and the tags go unreadable in
    # dark mode.
    assert body.index("vendor/tagify/tagify.css") < body.index("css/bootstrap-density.css")


def test_old_stock_phenotype_image_route_is_retired(monkeypatch):
    app = _make_app(monkeypatch)

    with app.test_client() as client:
        with client.session_transaction() as sess:
            sess["username"] = "admin"

        response = client.get("/stock/phenotype_image/learning_to_fly/wings/cy.png")

    assert response.status_code == 404


def test_manifest_entries_override_filename_fallback_and_expose_attribution(monkeypatch):
    _make_app(monkeypatch)
    matches = select_phenotype_reference_images(
        [{"display_label": "Mio", "phenotype_key": "Dr_Mio", "allele_token": "Dr[Mio]", "body_part": "eye", "effect": "rough eyes"}],
    )

    assert _source_path(matches[0]) == "holtzman_and_kaufman/eye/DrMio.png"
    assert matches[0]["source_name"] == "Holtzman and Kaufman"
    assert matches[0]["credit"]


def test_manifest_entries_do_not_cross_match_wrong_body_part_or_stem(monkeypatch):
    _make_app(monkeypatch)
    matches = select_phenotype_reference_images(
        [{
            "display_label": "Sp",
            "phenotype_key": "wg_Sp",
            "body_part": "wing",
            "effect": "spade-shaped notched wings",
        }],
    )

    assert matches == []

    matches = select_phenotype_reference_images(
        [{
            "display_label": "Sp",
            "phenotype_key": "wg_Sp",
            "effect": "spade-shaped notched wings",
        }],
    )

    assert matches == []


def test_repo_phenotype_image_manifest_accounts_for_every_library_image():
    assert len(MARKER_IMAGE_SEED) == 254
    assert len({row["file"] for row in MARKER_IMAGE_SEED}) == 254


def test_repo_learning_to_fly_body_color_images_are_reachable(monkeypatch):
    _make_app(monkeypatch)
    ebony_matches = select_phenotype_reference_images(
        [{"display_label": "e", "phenotype_key": "e", "body_part": "body", "effect": "ebony body color"}],
    )
    yellow_matches = select_phenotype_reference_images(
        [{"display_label": "y", "phenotype_key": "y", "body_part": "body", "effect": "yellow body color"}],
    )

    assert _source_path(ebony_matches[0]) == "learning_to_fly/bristles/e.png"
    assert _source_path(yellow_matches[0]) == "learning_to_fly/bristles/y.png"


def test_repo_curated_learning_to_fly_alleles_are_reachable(monkeypatch):
    _make_app(monkeypatch)
    roi_matches = select_phenotype_reference_images(
        [{"display_label": "Roi", "phenotype_key": "amos_Roi", "allele_token": "amos[Roi-1]", "body_part": "eye", "effect": "roughoid eye morphology"}],
    )
    bc_matches = select_phenotype_reference_images(
        [{"display_label": "Bc", "phenotype_key": "PPO1_Bc", "allele_token": "PPO1[Bc]", "body_part": "body", "effect": "black-cells larval body phenotype"}],
    )
    me_matches = select_phenotype_reference_images(
        [{"display_label": "me", "phenotype_key": "l2me_1", "allele_token": "l(2)me[1]", "body_part": "eye", "effect": "eye morphology phenotype"}],
    )
    wa_matches = select_phenotype_reference_images(
        [{"display_label": "wa", "phenotype_key": "wa", "body_part": "eye", "effect": "white-apricot eye color"}],
    )

    assert _source_path(roi_matches[0]) == "learning_to_fly/eye/roi.png"
    assert _source_path(bc_matches[0]) == "learning_to_fly/larva/bc.png"
    assert _source_path(me_matches[0]) == "learning_to_fly/eye/me.png"
    assert _source_path(wa_matches[0]) == "learning_to_fly/eye/wa.png"


def test_repo_non_eye_mini_white_control_panels_are_not_curated_matches(monkeypatch):
    _make_app(monkeypatch)
    control_panel_paths = {
        "learning_to_fly/bristles/w+.png",
        "learning_to_fly/haltere/w+.png",
        "learning_to_fly/larva/w+.png",
        "learning_to_fly/pupa/w+.png",
        "learning_to_fly/shoulder/w+.png",
        "learning_to_fly/shoulder/w+_2.png",
        "learning_to_fly/wings/w+.png",
        "learning_to_fly/wings/w+_2.png",
    }

    indexed = {entry["display"]["sourcePath"]: entry for entry in MARKER_IMAGE_SEED}
    assert control_panel_paths <= indexed.keys()

    matches = select_phenotype_reference_images(
        [{"display_label": "mini-white", "phenotype_key": "mini_white", "body_part": "eye", "effect": "pigmented eyes from construct marker"}],
    )

    assert _source_path(matches[0]) == "learning_to_fly/eye/w+.png"


def test_composite_mini_white_rescue_marker_uses_eye_reference_image(monkeypatch):
    _make_app(monkeypatch)
    matches = select_phenotype_reference_images(
        [{
            "display_label": "mini-white pale orange",
            "phenotype_key": "epistasis:w_mini_white_rescue",
            "body_part": "eye",
            "effect": "pale orange eye pigmentation from mini-white rescue in a white-eye background",
        }],
    )

    assert _source_path(matches[0]) == "learning_to_fly/eye/w+.png"


def test_stock_explorer_route_shows_best_guess_phenotype(monkeypatch):
    app = _make_app(monkeypatch)
    stock_record = {
        "UniqueID": "UID1",
        "AssignmentScope": "maintain",
        "AssignmentScopeLabel": "Maintain",
        "AssignmentScopeDetail": "Owned by you",
        "StockSource": "BDSC",
        "SourceCollection": "Bloomington",
        "SourceID": "17",
        "Name": "Curly tester",
        "Genotype": "w[1118]; CyO/+; +; +",
        "AltReference": "",
        "Type": "Balancer",
        "FoodType": "Molasses",
        "Species": "D. melanogaster",
        "Status": "Healthy",
        "SeriesID": "1",
        "ReplicateID": "a",
        "Provenance": "Bloomington",
        "TrayID": "A",
        "TrayPosition": "1",
        "Comments": "",
        "CreationDate": "2026-04-01",
        "LastFlipDate": "2026-04-10",
        "DataModifiedDate": "2026-04-12",
        "PhenotypeCache": {
            "version": PHENOTYPE_CACHE_VERSION,
            "computedAt": "2026-04-12T09:00:00Z",
            "genotype": "w[1118]; CyO/+; +; +",
            "prediction": {
                "best_guess_summary": "w, Cy",
                "confidence_label": "high",
            },
        },
    }

    with app.test_client() as client:
        with client.session_transaction() as sess:
            sess["username"] = "admin"

        with patch(
            "flymanager.app.routes.stock.db", _stock_explorer_fake_db([stock_record])
        ), patch(
            "flymanager.app.routes.stock.get_flip_in", return_value="5 days"
        ), patch(
            "flymanager.app.routes.stock.get_eclosion_in", return_value="2 days"
        ):
            response = client.get("/stock/explorer")

    body = response.get_data(as_text=True)
    assert response.status_code == 200
    assert "Phenotype:" in body
    assert "w, Cy" in body
    assert "w, Cy (High)" in body
    assert 'class="btn btn-outline-secondary">Phenotype Sandbox</a>' not in body
    assert 'href="/stock/phenotype_preview"' in body
    assert 'href="/reviewer"' in body
    assert ">Reviewer<" in body


def test_stock_explorer_route_recomputes_stale_cache_summary(monkeypatch):
    app = _make_app(monkeypatch)
    stock_record = {
        "UniqueID": "UID1",
        "AssignmentScope": "maintain",
        "AssignmentScopeLabel": "Maintain",
        "AssignmentScopeDetail": "Owned by you",
        "StockSource": "BDSC",
        "SourceCollection": "Bloomington",
        "SourceID": "17",
        "Name": "Mini-white tester",
        "Genotype": "w[1118]; P{w[+mC]=Orco-GAL4.W}11.17/+; +; +",
        "AltReference": "",
        "Type": "Balancer",
        "FoodType": "Molasses",
        "Species": "D. melanogaster",
        "Status": "Healthy",
        "SeriesID": "1",
        "ReplicateID": "a",
        "Provenance": "Bloomington",
        "TrayID": "A",
        "TrayPosition": "1",
        "Comments": "",
        "CreationDate": "2026-04-01",
        "LastFlipDate": "2026-04-10",
        "DataModifiedDate": "2026-04-12",
        "PhenotypeCache": {
            "version": PHENOTYPE_CACHE_VERSION - 1,
            "computedAt": "2026-04-12T09:00:00Z",
            "genotype": "w[1118]; P{w[+mC]=Orco-GAL4.W}11.17/+; +; +",
            "prediction": {
                "best_guess_summary": "w, mini-white",
                "confidence_label": "high",
            },
        },
    }

    with app.test_client() as client:
        with client.session_transaction() as sess:
            sess["username"] = "admin"

        with patch(
            "flymanager.app.routes.stock.db", _stock_explorer_fake_db([stock_record])
        ), patch(
            "flymanager.app.routes.stock.get_flip_in", return_value="5 days"
        ), patch(
            "flymanager.app.routes.stock.get_eclosion_in", return_value="2 days"
        ):
            response = client.get("/stock/explorer")

    body = response.get_data(as_text=True)
    assert response.status_code == 200
    assert "mini-white pale orange" in body
    assert "Refresh in record" not in body


def test_stock_explorer_reuses_source_context_for_provider_metadata(monkeypatch):
    app = _make_app(monkeypatch)
    stock_record = _build_stock_explorer_record(1)
    source_context = {
        "sourceType": "BDSC",
        "sourceCollection": "Bloomington",
        "flyBaseStockID": "FBst0000001",
        "providerURL": "https://bdsc.indiana.edu/stocks/001",
        "providerLinkLabel": "Open Bloomington provider page",
        "providerLinkKind": "provider",
    }

    with app.test_client() as client:
        with client.session_transaction() as sess:
            sess["username"] = "admin"

        with patch(
            "flymanager.app.routes.stock.db", _stock_explorer_fake_db([stock_record])
        ), patch(
            "flymanager.app.routes.stock.get_flip_in", return_value="5 days"
        ), patch(
            "flymanager.app.routes.stock.get_eclosion_in", return_value="2 days"
        ), patch(
            "flymanager.app.routes.stock.enrich_stock_source_context",
            return_value=source_context,
        ) as enrich_source_context, patch(
            "flymanager.app.routes.stock.build_stock_provider_metadata_from_context",
            return_value={
                "providerURL": source_context["providerURL"],
                "providerLinkLabel": source_context["providerLinkLabel"],
                "providerLinkKind": source_context["providerLinkKind"],
            },
        ) as provider_metadata_from_context:
            response = client.get("/stock/explorer")

    assert response.status_code == 200
    assert enrich_source_context.call_count == 1
    provider_metadata_from_context.assert_called_once_with(source_context)


def test_stock_explorer_paginates_second_page(monkeypatch):
    app = _make_app(monkeypatch)
    stocks = [_build_stock_explorer_record(index) for index in range(1, 26)]

    with app.test_client() as client:
        with client.session_transaction() as sess:
            sess["username"] = "admin"

        with patch(
            "flymanager.app.routes.stock.db", _stock_explorer_fake_db(stocks)
        ), patch(
            "flymanager.app.routes.stock.get_flip_in", return_value="5 days"
        ), patch(
            "flymanager.app.routes.stock.get_eclosion_in", return_value="2 days"
        ):
            response = client.get("/stock/explorer?page=2&per_page=20")

    body = response.get_data(as_text=True)
    assert response.status_code == 200
    assert "Stock-021" in body
    assert "Stock-001" not in body
    assert "Showing 21-25 of 25 stocks" in body
    assert '<option value="20" selected>' in body


def test_stock_explorer_supports_fifty_per_page(monkeypatch):
    app = _make_app(monkeypatch)
    stocks = [_build_stock_explorer_record(index) for index in range(1, 61)]

    with app.test_client() as client:
        with client.session_transaction() as sess:
            sess["username"] = "admin"

        with patch(
            "flymanager.app.routes.stock.db", _stock_explorer_fake_db(stocks)
        ), patch(
            "flymanager.app.routes.stock.get_flip_in", return_value="5 days"
        ), patch(
            "flymanager.app.routes.stock.get_eclosion_in", return_value="2 days"
        ):
            response = client.get("/stock/explorer?per_page=50")

    body = response.get_data(as_text=True)
    assert response.status_code == 200
    assert "Stock-001" in body
    assert "Stock-050" in body
    assert "Stock-051" not in body
    assert "Showing 1-50 of 60 stocks" in body
    assert '<option value="50" selected>' in body


def test_stock_explorer_selection_returns_all_filtered_items(monkeypatch):
    app = _make_app(monkeypatch)
    stock_one = _build_stock_explorer_record(1)
    stock_two = _build_stock_explorer_record(2)
    stock_two["FoodType"] = "Cornmeal"

    with app.test_client() as client:
        with client.session_transaction() as sess:
            sess["username"] = "admin"
            sess["stock_filter_state"] = {"filterFoodType": "Molasses"}

        with patch(
            "flymanager.app.routes.stock.db", _stock_explorer_fake_db([stock_two, stock_one])
        ):
            response = client.get("/stock/explorer/selection")

    data = response.get_json()
    assert response.status_code == 200
    assert data["count"] == 1
    assert data["items"] == [
        {
            "id": "UID-001",
            "identifier": "Tray A-1",
            "name": "Stock-001",
            "quantity": 1,
            "uid": "UID-001",
        }
    ]


def test_standardization_reviewer_route_summarizes_stocks_and_cross_parents(monkeypatch):
    app = _make_app(monkeypatch)
    clean_stock = _build_stock_explorer_record(1)
    flagged_stock = _build_stock_explorer_record(2)
    flagged_stock["Name"] = "Needs Review"
    flagged_stock["Genotype"] = "w; CyO/Sp; OrCo-LexA/TM3;"
    cross_record = _build_cross_explorer_record(1)
    cross_record["Name"] = "Parent Review"
    cross_record["MaleGenotype"] = "w[1118]; CyO/+; +; +"
    cross_record["FemaleGenotype"] = "+; +; FRT40A/OrCo-LexA; +"

    with app.test_client() as client:
        with client.session_transaction() as sess:
            sess["username"] = "admin"

        with patch(
            "flymanager.app.routes.main.get_accessible_stocks",
            return_value=[clean_stock, flagged_stock],
        ), patch(
            "flymanager.app.routes.main.get_accessible_crosses",
            return_value=[cross_record],
        ), patch(
            "flymanager.app.services.stock_standardization.review_stock_standardization",
            side_effect=[
                {"issue_count": 0, "summary": {}, "issues": []},
                {
                    "issue_count": 2,
                    "summary": {"unresolved_token": 2},
                    "issues": [
                        {
                            "token": "OrCo-LexA",
                            "recommended_replacement": "P{Orco-LexA-VP16}unspecified",
                        },
                        {
                            "token": "Sp",
                            "recommended_replacement": "",
                        },
                    ],
                },
                {"issue_count": 0, "summary": {}, "issues": []},
                {
                    "issue_count": 2,
                    "summary": {"unresolved_token": 1, "standard_format_unmodeled": 1},
                    "issues": [
                        {
                            "token": "FRT40A",
                            "recommended_replacement": "",
                        },
                        {
                            "token": "OrCo-LexA",
                            "recommended_replacement": "P{Orco-LexA-VP16}unspecified",
                        },
                    ],
                },
            ],
        ):
            response = client.get("/reviewer")

    body = response.get_data(as_text=True)
    assert response.status_code == 200
    assert "Stock and Cross Parent Reviewer" in body
    assert "2 stocks and 2 cross parents." in body
    assert "2 already clean under the current reviewer rules." in body
    assert "3 unresolved bare tokens and 1 unmodeled allele-format tokens." in body
    assert "Needs Review" in body
    assert "Female Parent" in body
    assert "Cross" in body
    assert "OrCo-LexA" in body
    assert "Sp" in body
    assert "FRT40A" in body
    assert "OrCo-LexA -> P{Orco-LexA-VP16}unspecified" in body
    assert "/stock/view/UID-002" in body
    assert "/cross/view_cross/CROSS-001" in body


def test_standardization_reviewer_paginates_second_page(monkeypatch):
    app = _make_app(monkeypatch)
    stocks = [_build_stock_explorer_record(index) for index in range(1, 26)]

    with app.test_client() as client:
        with client.session_transaction() as sess:
            sess["username"] = "admin"

        with patch(
            "flymanager.app.routes.main.get_accessible_stocks",
            return_value=stocks,
        ), patch(
            "flymanager.app.routes.main.get_accessible_crosses",
            return_value=[],
        ), patch(
            "flymanager.app.services.stock_standardization.review_stock_standardization",
            return_value={"issue_count": 0, "summary": {}, "issues": []},
        ):
            response = client.get("/reviewer?page=2&per_page=20")

    body = response.get_data(as_text=True)
    assert response.status_code == 200
    assert "Stock-021" in body
    assert "Stock-001" not in body
    assert "Showing 21-25 of 25 targets" in body
    assert '<option value="20" selected>' in body


def test_cross_view_route_renders_parent_and_offspring_phenotypes(monkeypatch, tmp_path):
    app = _make_app(monkeypatch)
    cross_record = {
        "UniqueID": "CROSS1",
        "User": "admin",
        "ViewerCanEdit": False,
        "AssignmentScopeLabel": "Maintain",
        "AssignmentScopeDetail": "Owned by you",
        "OwnerUser": "admin",
        "MaintainerUser": "admin",
        "AssignedTo": "",
        "MaleUniqueID": "M1",
        "FemaleUniqueID": "F1",
        "MaleGenotype": "w[1118]; CyO/+; +; +",
        "FemaleGenotype": "+; +; Sb[1]/+; +",
        "MaleSpecies": "D. melanogaster",
        "FemaleSpecies": "D. melanogaster",
        "TrayID": "B",
        "TrayPosition": "3",
        "Status": "Healthy",
        "FoodType": "Molasses",
        "Name": "Test cross",
        "Comments": "",
        "VialLifetime": 14,
        "FlipFrequency": 7,
        "DevelopmentalTime": 10,
        "MaxCrossLifetime": 30,
        "CreationDate": "2026-04-01",
        "LastFlipDate": "2026-04-10",
        "CurrentlyAliveVials": 3,
        "FlipLog": "",
        "NextFlipDates": "",
        "NextEclosionDates": "",
        "DataModifiedDate": "2026-04-12",
        "ModificationLog": "",
        "PhenotypeCache": {
            "version": PHENOTYPE_CACHE_VERSION,
            "computedAt": "2026-04-12T09:00:00Z",
            "maleGenotype": "w[1118]; CyO/+; +; +",
            "femaleGenotype": "+; +; Sb[1]/+; +",
            "simulationSummary": {
                "raw_class_count": 2,
                "viable_class_count": 1,
                "pruned_class_count": 1,
                "viable_fraction": 0.5,
                "identifiable_viable_fraction": 0.5,
                "confident_sortable_fraction": 0.5,
                "low_confidence_sorting_fraction": 0.0,
                "weighted_identifiability_score": 0.91,
                "estimated_sortable_targets_per_vial": 7.6,
                "visible_marker_class_count": 1,
                "best_sortable_class": {
                    "genotype": "w[1118]; CyO/+; +; +",
                    "sex": "female",
                    "phenotype_summary": "w, Cy",
                    "probability_percent": 50.0,
                    "viable_probability_percent": 100.0,
                    "identifiability": {
                        "selection_instructions": "Select flies with w, Cy.",
                    },
                    "yield_estimate": {
                        "expected_targets_per_vial": 7.6,
                    },
                },
            },
            "directionEvaluation": {
                "forward": {
                    "male_genotype": "w[1118]; CyO/+; +; +",
                    "female_genotype": "+; +; Sb[1]/+; +",
                    "score": 0.82,
                    "summary": {
                        "visible_marker_class_count": 1,
                    },
                },
                "reverse": {
                    "male_genotype": "+; +; Sb[1]/+; +",
                    "female_genotype": "w[1118]; CyO/+; +; +",
                    "score": 0.61,
                    "summary": {
                        "visible_marker_class_count": 1,
                    },
                },
                "recommended_direction": "forward",
                "recommended_cross": {
                    "male": "w[1118]; CyO/+; +; +",
                    "female": "+; +; Sb[1]/+; +",
                },
                "same_outcome": False,
                "rationale": ["Forward order is recommended because it produces a better phenotype-sorting outcome."],
                "operational_notes": ["Virgin-collection burden is not yet modeled explicitly; this recommendation is based on viability, sortable yield, and phenotype identifiability."],
            },
            "parentPhenotypes": {
                "male": {
                    "summary": "w, Cy",
                    "expressed_markers": [
                        {"display_label": "w", "phenotype_key": "w_loss", "body_part": "eye", "effect": "white eyes"},
                        {"display_label": "Cy", "phenotype_key": "Cy", "body_part": "wing", "effect": "curly wings"},
                    ],
                    "marker_labels": ["w", "Cy"],
                    "confidence_label": "high",
                    "construct_annotation_labels": ["GAL4 driver"],
                    "split_system_labels": [],
                    "warnings": [],
                },
                "female": {
                    "summary": "Sb",
                    "expressed_markers": [
                        {"display_label": "Sb", "phenotype_key": "Sb", "body_part": "bristle", "effect": "short, thick bristles"},
                    ],
                    "marker_labels": ["Sb"],
                    "confidence_label": "high",
                    "construct_annotation_labels": ["GFP reporter"],
                    "split_system_labels": ["Split-GFP"],
                    "warnings": [],
                },
                "summary": "Male w, Cy / Female Sb",
                "source_counts": {},
            },
            "predictedOffspring": [
                {
                    "genotype": "w[1118]; CyO/+; +; +",
                    "sex": "female",
                    "probability": 0.5,
                    "probability_percent": 50.0,
                    "viable_probability": 1.0,
                    "viable_probability_percent": 100.0,
                    "is_viable": True,
                    "pruned": False,
                    "prune_reasons": [],
                    "phenotype_summary": "w, Cy",
                    "phenotype_confidence": "high",
                    "yield_estimate": {
                        "expected_targets_per_vial": 7.6,
                        "yield_label": "moderate",
                        "recommended_parallel_vials": 2,
                    },
                    "validation": {
                        "viable": True,
                        "fertile": True,
                        "maintainable": True,
                        "requires_recombination": False,
                        "impossible_reasons": [],
                        "unsupported_reasons": [],
                        "issues": [],
                    },
                    "phenotype": {
                        "summary": "w, Cy",
                        "expressed_markers": [
                            {"display_label": "w", "phenotype_key": "w_loss", "body_part": "eye", "effect": "white eyes"},
                            {"display_label": "Cy", "phenotype_key": "Cy", "body_part": "wing", "effect": "curly wings"},
                        ],
                        "marker_labels": ["w", "Cy"],
                        "warnings": [],
                        "construct_annotation_labels": ["GFP reporter"],
                        "split_system_labels": ["Split-GFP"],
                        "viability_status": "likely_viable",
                        "fertility_status": "likely_fertile",
                    },
                    "identifiability": {
                        "label": "Distinct from sibling classes",
                        "distinguishing_features": ["w", "Cy"],
                        "selection_instructions": "Select flies with w, Cy.",
                        "low_confidence_features": [],
                        "exclusion_features": [],
                        "warnings": [],
                    },
                },
                {
                    "genotype": "+; +; +; +",
                    "sex": "male",
                    "probability": 0.5,
                    "probability_percent": 50.0,
                    "viable_probability": 0.0,
                    "viable_probability_percent": 0.0,
                    "is_viable": False,
                    "pruned": True,
                    "prune_reasons": ["Phenotype projection predicts this class is not likely viable."],
                    "phenotype_summary": "No marker phenotype predicted",
                    "phenotype_confidence": "low",
                    "yield_estimate": {
                        "expected_targets_per_vial": 0.0,
                        "yield_label": "low",
                        "recommended_parallel_vials": 1,
                    },
                    "validation": {
                        "viable": False,
                        "fertile": False,
                        "maintainable": False,
                        "requires_recombination": False,
                        "impossible_reasons": ["Phenotype computation predicts inviability for this target genotype."],
                        "unsupported_reasons": [],
                        "issues": ["Phenotype computation predicts inviability for this target genotype."],
                    },
                    "phenotype": {
                        "summary": "No marker phenotype predicted",
                        "expressed_markers": [],
                        "marker_labels": [],
                        "warnings": ["Current phenotype model predicts an inviability risk for this genotype."],
                        "construct_annotation_labels": [],
                        "split_system_labels": [],
                        "viability_status": "likely_inviable",
                        "fertility_status": "likely_fertile",
                    },
                    "identifiability": {
                        "label": "Not a viable class",
                        "distinguishing_features": [],
                        "selection_instructions": "No unique visual sort is predicted for this class.",
                        "low_confidence_features": [],
                        "exclusion_features": [],
                        "warnings": ["This class is pruned as inviable and is not a practical sorting target."],
                    },
                }
            ],
        },
    }

    with app.test_client() as client:
        with client.session_transaction() as sess:
            sess["username"] = "admin"

        with patch("flymanager.app.routes.cross.get_metadata", side_effect=_cross_metadata_lookup), patch(
            "flymanager.app.routes.cross.get_accessible_cross",
            return_value=cross_record,
        ), patch(
            "flymanager.app.routes.cross.get_all_genotypes",
            return_value=["w[1118]; CyO/+; +; +", "+; +; Sb[1]/+; +"],
        ):
            response = client.get("/cross/view_cross/CROSS1")

    body = response.get_data(as_text=True)
    assert response.status_code == 200
    assert "Male phenotype preview" in body
    assert "Female phenotype preview" in body
    assert "w, Cy" in body
    assert "Sb" in body
    assert "Reference image for w" in body
    assert "Reference image for Sb" in body
    assert "Learning to Fly" in body
    assert "Construct annotations: GAL4 driver" in body
    assert "Construct annotations: GFP reporter" in body
    assert "Split systems: Split-GFP" in body
    assert "Sortable Outcome Summary" in body
    assert "Reciprocal Evaluation" in body
    assert "Recommended direction:" in body
    assert "Forward order is recommended because it produces a better phenotype-sorting outcome." in body
    assert "Best sortable class:" in body
    assert "Sortability: Distinct from sibling classes" in body
    assert "Sort instruction: Select flies with w, Cy." in body
    assert "Pruned from viable pool." in body
    assert "Prune reasons: Phenotype projection predicts this class is not likely viable." in body
    assert "data-progress-form" in body
    assert "Refreshing this cross phenotype cache..." in body
    assert re.search(r'id="maleGenotype"[^>]*disabled', body) is None
    assert re.search(r'id="femaleGenotype"[^>]*disabled', body) is None
    assert re.search(r'id="foodType"[^>]*disabled', body) is None
    assert "input._tagify" not in body
    # Same per-field edit contract as the stock viewer (shared field_edit.js).
    assert "/static/js/field_edit.js" in body
    assert 'data-edit-target="maleGenotype"' in body
    assert 'data-edit-target="comments"' in body
    # tagify.css must load in <head>, before bootstrap-density.css, or
    # tagify's own light-theme defaults win and the tags go unreadable in
    # dark mode.
    assert body.index("vendor/tagify/tagify.css") < body.index("css/bootstrap-density.css")


def test_stock_refresh_route_skips_duplicate_refresh(monkeypatch):
    app = _make_app(monkeypatch)
    app.config["WTF_CSRF_ENABLED"] = False
    stock_record = {"UniqueID": "UID1", "User": "admin", "Genotype": "w[1118]; CyO/+; +; +"}

    with app.test_client() as client:
        with client.session_transaction() as sess:
            sess["username"] = "admin"

        with patch(
            "flymanager.app.routes.stock.get_accessible_stock",
            return_value=stock_record,
        ), patch(
            "flymanager.app.routes.stock.hold_operation_lock",
            side_effect=OperationLockConflict("A phenotype refresh for stock UID1 is already running."),
        ), patch(
            "flymanager.app.routes.stock.build_stock_phenotype_cache"
        ) as build_cache:
            response = client.post(
                "/stock/view/UID1/refresh_phenotype",
                follow_redirects=False,
            )

    assert response.status_code == 302
    build_cache.assert_not_called()


def test_stock_standardization_reviewer_route_returns_review_payload(monkeypatch):
    app = _make_app(monkeypatch)
    review_payload = {
        "genotype": "w; CyO/Sp; OrCo-LexA/TM3;",
        "issue_count": 2,
        "summary": {"unresolved_token": 2},
        "issues": [
            {
                "token": "OrCo-LexA",
                "issue_type": "unresolved_token",
                "issue_label": "Bare token needs review",
                "occurrence_count": 1,
                "recommended_replacement": "P{Orco-LexA-VP16}unspecified",
                "recommended_source": "reviewed_alias",
                "ambiguous_candidates": [],
                "search_query": "OrCo-LexA",
                "fuzzy_candidates": [
                    {
                        "canonical_token": "P{Orco-LexA-VP16}unspecified",
                        "candidate_kind": "construct",
                        "display_label": "P{Orco-LexA-VP16}unspecified",
                        "description": "Orco LexA driver insertion.",
                        "flybase_id": "FBal9990001",
                        "gene_symbol": "Orco",
                        "insertion_symbol": "P{Orco-LexA-VP16}",
                        "regulatory_region_symbol": "Orco",
                        "encoded_product_symbol": "LexA-VP16",
                        "stocks_number": 0,
                        "source": "FlyBase allele description",
                        "match_score": 100,
                        "match_basis": "P{Orco-LexA-VP16}",
                    }
                ],
            }
        ],
    }

    with app.test_client() as client:
        with client.session_transaction() as sess:
            sess["username"] = "admin"

        with patch(
            "flymanager.app.routes.stock.get_accessible_stock",
            return_value={"UniqueID": "UID1", "Genotype": "w; CyO/Sp; OrCo-LexA/TM3;", "ViewerCanEdit": True},
        ), patch(
            "flymanager.app.routes.stock.review_stock_standardization",
            return_value=review_payload,
        ):
            response = client.get("/stock/review_standardization/UID1")

    data = response.get_json()
    assert response.status_code == 200
    assert data["unique_id"] == "UID1"
    assert data["reviewable"] is True
    assert data["issues"][0]["recommended_replacement"] == "P{Orco-LexA-VP16}unspecified"


def test_cross_refresh_route_skips_duplicate_refresh(monkeypatch):
    app = _make_app(monkeypatch)
    app.config["WTF_CSRF_ENABLED"] = False
    cross_record = {
        "UniqueID": "CROSS1",
        "User": "admin",
        "MaleGenotype": "w[1118]; CyO/+; +; +",
        "FemaleGenotype": "+; +; Sb[1]/+; +",
    }

    with app.test_client() as client:
        with client.session_transaction() as sess:
            sess["username"] = "admin"

        with patch(
            "flymanager.app.routes.cross.get_accessible_cross",
            return_value=cross_record,
        ), patch(
            "flymanager.app.routes.cross.hold_operation_lock",
            side_effect=OperationLockConflict("A phenotype refresh for cross CROSS1 is already running."),
        ), patch(
            "flymanager.app.routes.cross.build_cross_phenotype_cache"
        ) as build_cache:
            response = client.post(
                "/cross/view_cross/CROSS1/refresh_phenotype",
                follow_redirects=False,
            )

    assert response.status_code == 302
    build_cache.assert_not_called()


def test_standalone_phenotype_preview_route_renders_prediction(monkeypatch, tmp_path):
    app = _make_app(monkeypatch)

    with app.test_client() as client:
        with client.session_transaction() as sess:
            sess["username"] = "admin"

        with patch("flymanager.app.routes.stock.get_metadata", side_effect=_stock_metadata_lookup):
            response = client.get("/stock/phenotype_preview?genotype=w[1118];+;+;+&sex=male")

    body = response.get_data(as_text=True)
    assert response.status_code == 200
    assert "Standalone Phenotype Preview" in body
    assert "Chromosome X" in body
    assert "Normalized Preview Genotype" in body
    assert "Viability:" in body
    assert "/static/vendor/tagify/tagify.min.js" in body
    assert "/static/vendor/tagify/tagify.polyfills.min.js" in body
    assert "/static/vendor/tagify/tagify.css" in body
    assert "https://cdn.jsdelivr.net/npm/@yaireo/tagify" not in body
    assert "<textarea" not in body


def test_standalone_phenotype_preview_route_accepts_tagify_form(monkeypatch, tmp_path):
    app = _make_app(monkeypatch)

    with app.test_client() as client:
        with client.session_transaction() as sess:
            sess["username"] = "admin"

        with patch("flymanager.app.routes.stock.get_metadata", side_effect=_stock_metadata_lookup):
            response = client.post(
                "/stock/phenotype_preview",
                data={
                    "genotypeX": '[{"value":"w[1118]"}]',
                    "genotype2": '[{"value":"+"}]',
                    "genotype3": '[{"value":"+"}]',
                    "genotype4": '[{"value":"+"}]',
                    "sex": "male",
                },
            )

    body = response.get_data(as_text=True)
    assert response.status_code == 200
    assert "w[1118]; +; +; +" in body
    assert "Predicted Phenotype" in body


def test_standalone_phenotype_preview_route_returns_json(monkeypatch, tmp_path):
    app = _make_app(monkeypatch)

    with app.test_client() as client:
        with client.session_transaction() as sess:
            sess["username"] = "admin"

        response = client.post(
            "/stock/phenotype_preview",
            json={"genotype": "w[1118]; +; +; +", "sex": "male"},
        )

    data = response.get_json()
    assert response.status_code == 200
    assert data["status"] == "success"
    assert data["prediction"]["summary"] == "w"
    assert data["prediction"]["viability_status"] == "likely_viable"


def test_cross_explorer_route_shows_parent_phenotype_summary(monkeypatch):
    app = _make_app(monkeypatch)
    cross_record = {
        "UniqueID": "CROSS1",
        "AssignmentScope": "maintain",
        "AssignmentScopeLabel": "Maintain",
        "AssignmentScopeDetail": "Owned by you",
        "MaleUniqueID": "M1",
        "FemaleUniqueID": "F1",
        "MaleGenotype": "w[1118]; CyO/+; +; +",
        "FemaleGenotype": "+; +; Sb[1]/+; +",
        "MaleSpecies": "D. melanogaster",
        "FemaleSpecies": "D. melanogaster",
        "Status": "Healthy",
        "FoodType": "Molasses",
        "Name": "Test cross",
        "TrayID": "B",
        "TrayPosition": "3",
        "Comments": "",
        "CreationDate": "2026-04-01",
        "LastFlipDate": "2026-04-10",
        "DataModifiedDate": "2026-04-12",
        "PhenotypeCache": {
            "version": PHENOTYPE_CACHE_VERSION,
            "computedAt": "2026-04-12T09:00:00Z",
            "maleGenotype": "w[1118]; CyO/+; +; +",
            "femaleGenotype": "+; +; Sb[1]/+; +",
            "parentPhenotypes": {
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
            "predictedOffspring": [],
        },
    }

    with app.test_client() as client:
        with client.session_transaction() as sess:
            sess["username"] = "admin"

        with patch(
            "flymanager.app.routes.cross.db", _cross_explorer_fake_db([cross_record])
        ), patch(
            "flymanager.app.routes.cross.get_flip_in", return_value="5 days"
        ), patch(
            "flymanager.app.routes.cross.get_eclosion_in", return_value="2 days"
        ):
            response = client.get("/cross/cross_explorer")

    body = response.get_data(as_text=True)
    assert response.status_code == 200
    assert "Phenotype:" in body
    assert "Male w, Cy / Female Sb" in body


def test_cross_explorer_supports_all_page_size(monkeypatch):
    app = _make_app(monkeypatch)
    crosses = [_build_cross_explorer_record(index) for index in range(1, 26)]

    with app.test_client() as client:
        with client.session_transaction() as sess:
            sess["username"] = "admin"

        with patch(
            "flymanager.app.routes.cross.db", _cross_explorer_fake_db(crosses)
        ), patch(
            "flymanager.app.routes.cross.get_flip_in", return_value="5 days"
        ), patch(
            "flymanager.app.routes.cross.get_eclosion_in", return_value="2 days"
        ):
            response = client.get("/cross/cross_explorer?per_page=all")

    body = response.get_data(as_text=True)
    assert response.status_code == 200
    assert "Cross-001" in body
    assert "Cross-025" in body
    assert "Showing all 25 crosses" in body
    assert '<option value="all" selected>' in body


def test_cross_explorer_supports_hundred_per_page(monkeypatch):
    app = _make_app(monkeypatch)
    crosses = [_build_cross_explorer_record(index) for index in range(1, 121)]

    with app.test_client() as client:
        with client.session_transaction() as sess:
            sess["username"] = "admin"

        with patch(
            "flymanager.app.routes.cross.db", _cross_explorer_fake_db(crosses)
        ), patch(
            "flymanager.app.routes.cross.get_flip_in", return_value="5 days"
        ), patch(
            "flymanager.app.routes.cross.get_eclosion_in", return_value="2 days"
        ):
            response = client.get("/cross/cross_explorer?per_page=100")

    body = response.get_data(as_text=True)
    assert response.status_code == 200
    assert "Cross-001" in body
    assert "Cross-100" in body
    assert "Cross-101" not in body
    assert "Showing 1-100 of 120 crosses" in body
    assert '<option value="100" selected>' in body


def test_cross_explorer_selection_returns_all_filtered_items(monkeypatch):
    app = _make_app(monkeypatch)
    cross_one = _build_cross_explorer_record(1)
    cross_two = _build_cross_explorer_record(2)
    cross_two["FoodType"] = "Cornmeal"

    with app.test_client() as client:
        with client.session_transaction() as sess:
            sess["username"] = "admin"
            sess["filter_state"] = {"filterFoodType": "Molasses"}

        with patch(
            "flymanager.app.routes.cross.db", _cross_explorer_fake_db([cross_two, cross_one])
        ):
            response = client.get("/cross/cross_explorer/selection")

    data = response.get_json()
    assert response.status_code == 200
    assert data["count"] == 1
    assert data["items"] == [
        {
            "id": "CROSS-001",
            "identifier": "Tray B-1",
            "name": "Cross-001",
            "quantity": 1,
            "uid": "CROSS-001",
        }
    ]


def test_stock_refresh_phenotype_route_updates_cached_payload(monkeypatch):
    app = _make_app(monkeypatch)
    app.config["WTF_CSRF_ENABLED"] = False
    stock_record = {
        "UniqueID": "UID1",
        "User": "admin",
        "Genotype": "w[1118]; CyO/+; +; +",
    }
    fake_collection = _FakeCollection()

    with app.test_client() as client:
        with client.session_transaction() as sess:
            sess["username"] = "admin"

        with patch("flymanager.app.routes.stock.get_accessible_stock", return_value=stock_record), patch(
            "flymanager.app.routes.stock.db", {"stocks": fake_collection}
        ), patch("flymanager.app.routes.stock.write_activity"):
            response = client.post(
                "/stock/view/UID1/refresh_phenotype",
            )

    assert response.status_code == 302
    assert fake_collection.calls
    selector, update = fake_collection.calls[0]
    assert selector == {"UniqueID": "UID1", "User": "admin"}
    assert update["$set"]["PhenotypeCache"]["genotype"] == "w[1118]; CyO/+; +; +"


def test_cross_refresh_phenotype_route_updates_cached_payload(monkeypatch):
    app = _make_app(monkeypatch)
    app.config["WTF_CSRF_ENABLED"] = False
    cross_record = {
        "UniqueID": "CROSS1",
        "User": "admin",
        "MaleGenotype": "w[1118]; CyO/+; +; +",
        "FemaleGenotype": "+; +; Sb[1]/+; +",
    }
    fake_collection = _FakeCollection()

    with app.test_client() as client:
        with client.session_transaction() as sess:
            sess["username"] = "admin"

        with patch("flymanager.app.routes.cross.get_accessible_cross", return_value=cross_record), patch(
            "flymanager.app.routes.cross.db", {"crosses": fake_collection}
        ), patch("flymanager.app.routes.cross.write_activity"):
            response = client.post(
                "/cross/view_cross/CROSS1/refresh_phenotype",
            )

    assert response.status_code == 302
    assert fake_collection.calls
    selector, update = fake_collection.calls[0]
    assert selector == {"UniqueID": "CROSS1", "User": "admin"}
    assert update["$set"]["PhenotypeCache"]["maleGenotype"] == "w[1118]; CyO/+; +; +"
    assert update["$set"]["PhenotypeCache"]["femaleGenotype"] == "+; +; Sb[1]/+; +"
