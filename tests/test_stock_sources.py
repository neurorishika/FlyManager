import csv
import gzip
import logging
from pathlib import Path
from unittest.mock import patch

import pandas as pd

from flymanager.app import create_app
from flymanager.utils.stock_sources import (
    collect_compatible_gene_metadata_from_flybase, get_external_stock_record)


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


def _write_flybase_stocks_file(path, rows):
    header = [
        "FBst",
        "collection_short_name",
        "stock_type_cv",
        "species",
        "FB_genotype",
        "description",
        "stock_number",
    ]

    with gzip.open(path, "wt", encoding="utf-8", newline="") as handle:
        writer = csv.writer(handle, delimiter="\t")
        writer.writerow(header)
        writer.writerows(rows)


def test_get_external_stock_record_reads_supported_flybase_collection_record(tmp_path, monkeypatch):
    stocks_path = tmp_path / "stocks.tsv.gz"
    _write_flybase_stocks_file(
        stocks_path,
        [
            [
                "FBst1234567",
                "Vienna",
                "living stock ; FBsv:0000002",
                "Dmel",
                "w[1]; cn[1]; st[1]; ci[1]",
                "Vienna stock",
                "4321",
            ]
        ],
    )

    monkeypatch.setattr("flymanager.utils.stock_sources.FLYBASE_STOCKS_PATH", stocks_path)

    stock_data, error = get_external_stock_record("VIENNA", "4321")

    assert error is None
    assert stock_data["stockSource"] == "VIENNA"
    assert stock_data["sourceCollection"] == "Vienna"
    assert stock_data["flyBaseStockID"] == "FBst1234567"
    assert stock_data["genotype"] == "w[1]; cn[1]; st[1]; ci[1]"
    assert stock_data["supportStatus"] == "supported"


def test_get_external_stock_record_marks_unsupported_flybase_record(tmp_path, monkeypatch):
    stocks_path = tmp_path / "stocks.tsv.gz"
    _write_flybase_stocks_file(
        stocks_path,
        [
            [
                "FBst7654321",
                "NIG-Fly",
                "living stock ; FBsv:0000002",
                "Dmel",
                "Hikone-A-W",
                "Hikone-A-W",
                "88",
            ]
        ],
    )

    monkeypatch.setattr("flymanager.utils.stock_sources.FLYBASE_STOCKS_PATH", stocks_path)

    stock_data, error = get_external_stock_record("NIG", "88")

    assert error is None
    assert stock_data["stockSource"] == "NIG"
    assert stock_data["sourceCollection"] == "NIG-Fly"
    assert stock_data["genotype"] == ""
    assert stock_data["rawGenotype"] == "Hikone-A-W"
    assert stock_data["supportStatus"] == "unsupported"
    assert "format xchromosome" in stock_data["supportReason"]


def test_get_external_stock_record_pads_missing_trailing_chromosomes_for_compatible_flybase_record(tmp_path, monkeypatch):
    stocks_path = tmp_path / "stocks.tsv.gz"
    _write_flybase_stocks_file(
        stocks_path,
        [
            [
                "FBst0329057",
                "Kyoto",
                "living stock ; FBsv:0000002",
                "Dmel",
                "w[1118]; PBac{y[+mDint2] w[+mC]=UAS-hINHBB.N}VK00033",
                "w[1118]; PBac{y[+mDint2] w[+mC]=UAS-hINHBB.N}VK00033",
                "118991",
            ]
        ],
    )

    monkeypatch.setattr("flymanager.utils.stock_sources.FLYBASE_STOCKS_PATH", stocks_path)

    stock_data, error = get_external_stock_record("KYOTO", "118991")

    assert error is None
    assert stock_data["stockSource"] == "KYOTO"
    assert stock_data["supportStatus"] == "supported"
    assert stock_data["genotype"] == "w[1118]; PBac{y[+mDint2] w[+mC]=UAS-hINHBB.N}VK00033; ; "
    assert stock_data["rawGenotype"] == "w[1118]; PBac{y[+mDint2] w[+mC]=UAS-hINHBB.N}VK00033"


def test_collect_compatible_gene_metadata_from_flybase_harvests_supported_components(tmp_path, monkeypatch):
    stocks_path = tmp_path / "stocks.tsv.gz"
    _write_flybase_stocks_file(
        stocks_path,
        [
            [
                "FBst0329057",
                "Kyoto",
                "living stock ; FBsv:0000002",
                "Dmel",
                "w[1118]; PBac{y[+mDint2] w[+mC]=UAS-hINHBB.N}VK00033",
                "w[1118]; PBac{y[+mDint2] w[+mC]=UAS-hINHBB.N}VK00033",
                "118991",
            ],
            [
                "FBst7654321",
                "NIG-Fly",
                "living stock ; FBsv:0000002",
                "Dmel",
                "Hikone-A-W",
                "Hikone-A-W",
                "88",
            ],
            [
                "FBst9999999",
                "Vienna",
                "living stock ; FBsv:0000002",
                "Dsim",
                "w[1]; cn[1]; st[1]; ci[1]",
                "Ignore non-Dmel",
                "999",
            ],
        ],
    )

    monkeypatch.setattr("flymanager.utils.stock_sources.FLYBASE_STOCKS_PATH", stocks_path)

    gene_components, summary = collect_compatible_gene_metadata_from_flybase()

    assert summary["total_rows"] == 3
    assert summary["dmel_rows"] == 2
    assert summary["supported_rows"] == 1
    assert summary["unsupported_rows"] == 1
    assert gene_components[0] == ["w[1118]"]
    assert gene_components[1] == ["PBac{y[+mDint2] w[+mC]=UAS-hINHBB.N}VK00033"]
    assert gene_components[2] == []
    assert gene_components[3] == []


def test_get_external_stock_record_prefers_flybase_for_bdsc_when_supported(tmp_path, monkeypatch):
    stocks_path = tmp_path / "stocks.tsv.gz"
    _write_flybase_stocks_file(
        stocks_path,
        [
            [
                "FBst0000017",
                "Bloomington",
                "living stock ; FBsv:0000002",
                "Dmel",
                "w[*]; cn[1]/CyO",
                "Bloomington 17",
                "17",
            ]
        ],
    )

    monkeypatch.setattr("flymanager.utils.stock_sources.FLYBASE_STOCKS_PATH", stocks_path)
    monkeypatch.setattr(
        "flymanager.utils.stock_sources.get_bloomington_data",
        lambda: (_ for _ in ()).throw(AssertionError("legacy Bloomington path should not run")),
    )

    stock_data, error = get_external_stock_record("BDSC", "17")

    assert error is None
    assert stock_data["stockSource"] == "BDSC"
    assert stock_data["sourceCollection"] == "Bloomington"
    assert stock_data["flyBaseStockID"] == "FBst0000017"
    assert stock_data["rawGenotype"] == "w[*]; cn[1]/CyO"
    assert stock_data["supportStatus"] == "supported"
    assert stock_data["genotype"] == "w[*]; CyO/cn[1]; ; "


def test_get_external_stock_record_falls_back_to_legacy_bloomington_for_bdsc(monkeypatch, caplog):
    bloomington_frame = pd.DataFrame(
        [
            {
                "Stk #": 17,
                "Genotype": "w[*]; cn[1]/CyO",
                "Ch # all": "1;2",
            }
        ]
    )

    monkeypatch.setattr(
        "flymanager.utils.stock_sources.get_bloomington_data",
        lambda: bloomington_frame,
    )
    monkeypatch.setattr(
        "flymanager.utils.stock_sources.get_stock_genotype",
        lambda stock_id: ("w[*]; cn[1]/CyO; ; ", None),
    )
    monkeypatch.setattr(
        "flymanager.utils.stock_sources._find_flybase_stock_row",
        lambda **kwargs: {
            "FBst": "FBst0000017",
            "collection_short_name": "Bloomington",
            "stock_number": "17",
            "FB_genotype": "legacy-incompatible",
            "description": "Bloomington 17",
            "species": "Dmel",
        },
    )

    caplog.set_level(logging.INFO, logger="flymanager.utils.stock_sources")

    stock_data, error = get_external_stock_record("BDSC", "17")

    assert error is None
    assert stock_data["stockSource"] == "BDSC"
    assert stock_data["sourceCollection"] == "Bloomington"
    assert stock_data["flyBaseStockID"] == "FBst0000017"
    assert stock_data["supportStatus"] == "supported"
    assert stock_data["genotype"] == "w[*]; cn[1]/CyO; ; "
    assert "falling back to legacy Bloomington normalization because FlyBase genotype is not yet compatible" in caplog.text
    assert "resolved via legacy Bloomington fallback with supportStatus=supported" in caplog.text


def test_get_external_stock_record_logs_when_bdsc_uses_legacy_fallback_without_flybase_row(monkeypatch, caplog):
    bloomington_frame = pd.DataFrame(
        [
            {
                "Stk #": 22,
                "Genotype": "w[1118]; P{GD6943}v38377",
                "Ch # all": "1;2",
            }
        ]
    )

    monkeypatch.setattr(
        "flymanager.utils.stock_sources.get_bloomington_data",
        lambda: bloomington_frame,
    )
    monkeypatch.setattr(
        "flymanager.utils.stock_sources.get_stock_genotype",
        lambda stock_id: ("w[1118]; P{GD6943}v38377; ; ", None),
    )
    monkeypatch.setattr(
        "flymanager.utils.stock_sources._find_flybase_stock_row",
        lambda **kwargs: None,
    )

    caplog.set_level(logging.INFO, logger="flymanager.utils.stock_sources")

    stock_data, error = get_external_stock_record("BDSC", "22")

    assert error is None
    assert stock_data["flyBaseStockID"] == ""
    assert stock_data["supportStatus"] == "supported"
    assert "falling back to legacy Bloomington normalization because no FlyBase Bloomington row was found" in caplog.text
    assert "resolved via legacy Bloomington fallback with supportStatus=supported" in caplog.text


def test_get_external_stock_route_returns_lookup_payload(monkeypatch):
    app = _make_app(monkeypatch)

    with app.test_client() as client:
        with client.session_transaction() as sess:
            sess["username"] = "admin"

        with patch(
            "flymanager.app.routes.stock.get_external_stock_record",
            return_value=(
                {
                    "stockSource": "VIENNA",
                    "sourceCollection": "Vienna",
                    "sourceID": "4321",
                    "flyBaseStockID": "FBst1234567",
                    "genotype": "w[1]; cn[1]; st[1]; ci[1]",
                    "rawGenotype": "w[1]; cn[1]; st[1]; ci[1]",
                    "supportStatus": "supported",
                    "supportReason": "",
                    "species": "D. melanogaster",
                    "provenance": "Vienna",
                    "name": "Vienna stock",
                    "altReference": "FBst1234567",
                },
                None,
            ),
        ):
            response = client.get("/stock/get_external/VIENNA/4321")

    assert response.status_code == 200
    assert response.get_json()["sourceCollection"] == "Vienna"
    assert response.get_json()["supportStatus"] == "supported"