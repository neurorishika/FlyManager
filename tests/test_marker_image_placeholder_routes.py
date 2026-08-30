"""The rendered views show a card per marker, and images are zoomable."""
import io
import re
from unittest.mock import patch

import pytest
from PIL import Image

from flymanager.app import create_app, db
from flymanager.utils.phenotypes import image_catalog


def _app(monkeypatch):
    monkeypatch.setenv("ENABLE_SCHEDULER", "0")
    monkeypatch.setenv("SECRET_KEY", "test-secret-key")
    app = create_app()
    app.config["TESTING"] = True
    return app


@pytest.fixture(autouse=True)
def _restore_snapshot():
    yield
    image_catalog.refresh_image_catalog(db, force=True)


def _render(monkeypatch, prediction):
    """Render the standalone preview with a canned prediction."""
    app = _app(monkeypatch)
    with app.test_client() as client:
        with client.session_transaction() as session:
            session["username"] = "placeholder-test-user"
        with patch("flymanager.app.routes.stock.predict_individual_phenotype",
                   return_value=prediction):
            response = client.post("/stock/phenotype_preview",
                                   json={"genotype": "w[1118]; +; +; +",
                                         "sex": "female"})
    return response


def test_selector_output_reaches_the_preview_json(monkeypatch):
    app = _app(monkeypatch)
    with app.test_client() as client:
        with client.session_transaction() as session:
            session["username"] = "placeholder-test-user"
        response = client.post("/stock/phenotype_preview",
                               json={"genotype": "w[1118]; +; Sb[1]/TM3; +",
                                     "sex": "female"})
    assert response.status_code == 200
    images = (response.get_json().get("prediction") or {}).get("reference_images") or []
    assert images, "expected reference image rows"
    # Every row carries the new contract fields the templates rely on.
    for row in images:
        assert "has_image" in row
        assert "marker_key" in row
        if not row["has_image"]:
            assert row["image_url"] is None


@pytest.mark.parametrize("genotype", [
    "w[1118]; Sp/CyO; +; +",
    "w[1118]; +; Sb[1]/TM3, Ser[1]; +",
    "w[1118]; CyO/Sp; TM6B, Tb[1]/+; +",
])
def test_every_predicted_marker_gets_a_row(monkeypatch, genotype):
    """No marker may be dropped, whether or not the library can show it.

    Markers used to disappear two ways: a per-view image cap truncated the
    list, and a marker whose best image was already claimed by an earlier
    marker was skipped outright.
    """
    app = _app(monkeypatch)
    with app.test_client() as client:
        with client.session_transaction() as session:
            session["username"] = "placeholder-test-user"
        response = client.post("/stock/phenotype_preview",
                               json={"genotype": genotype, "sex": "female"})
    prediction = response.get_json().get("prediction") or {}
    rows = prediction.get("reference_images") or []
    expected = [m.get("display_label") for m in (prediction.get("expressed_markers") or [])]

    assert expected, "prediction produced no markers to check"
    assert [r["display_label"] for r in rows] == expected


def test_an_imageless_marker_reports_where_to_upload(monkeypatch):
    """The placeholder card needs a definition Key to link to."""
    app = _app(monkeypatch)
    with app.test_client() as client:
        with client.session_transaction() as session:
            session["username"] = "placeholder-test-user"
        # Drive the selector directly with a marker the library cannot
        # illustrate, rather than hunting for a genotype that yields one.
        from flymanager.utils.phenotypes.image_library import \
            select_phenotype_reference_images
        rows = select_phenotype_reference_images([
            {"display_label": "Sp", "phenotype_key": "wg_Sp",
             "body_part": "wing", "allele_token": "wg[Sp-1]"},
        ])
    assert rows[0]["has_image"] is False
    assert rows[0]["marker_key"] == "wg[Sp-1]"


def test_preview_page_includes_the_zoom_overlay(monkeypatch):
    app = _app(monkeypatch)
    with app.test_client() as client:
        with client.session_transaction() as session:
            session["username"] = "placeholder-test-user"
        page = client.get("/stock/phenotype_preview")
    assert page.status_code == 200
    assert b'id="phenotypeZoomOverlay"' in page.data
    assert b"marker_image_zoom.js" in page.data
    assert b"phenotype_images.css" in page.data
