import csv
import gzip
import logging
import re
from contextlib import nullcontext
from pathlib import Path
from unittest.mock import ANY, MagicMock, patch

import pandas as pd

from flymanager.app import create_app
from tests.mongo_fakes import FakeDatabase
from flymanager.utils.stock_sources import (
    _load_flybase_stock_indexes, build_external_stock_provider_link,
    build_stock_provider_metadata,
    collect_compatible_gene_metadata_from_flybase, enrich_stock_source_context,
    find_external_stock_matches, get_external_stock_record)


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
    monkeypatch.setenv("WARM_PAGE_CACHES_ON_STARTUP", "0")
    monkeypatch.delenv("FLYMANAGER_DOMAIN", raising=False)

    with patch("flymanager.app.get_settings", return_value=_settings_payload()):
        app = create_app()
    app.config.update(TESTING=True, WTF_CSRF_ENABLED=False)
    return app


def _extract_csrf_token(response_text):
    match = re.search(r'<meta name="csrf-token" content="([^"]+)"', response_text)
    assert match, "CSRF token meta tag not found"
    return match.group(1)


def _get_authenticated_csrf_token(client):
    with patch("flymanager.app.routes.flip.get_available_ports", return_value=[]):
        response = client.get("/flip/")
    return _extract_csrf_token(response.get_data(as_text=True))


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


def _stock_metadata_lookup(name, _db):
    defaults = {
        "types": ["Gal4.DBD"],
        "food_types": ["Molasses"],
        "provenances": ["Bloomington"],
        "genesX": ["w[1118]"],
        "genes2nd": [],
        "genes3rd": ["P{y[+t7.7] w[+mC]=R19H07-GAL4.DBD}attP2"],
        "genes4th": [],
        "species": ["D. melanogaster"],
    }
    return defaults.get(name, [])


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


def test_get_external_stock_record_rejects_semicolons_inside_rearrangement_notation(tmp_path, monkeypatch):
    stocks_path = tmp_path / "stocks.tsv.gz"
    _write_flybase_stocks_file(
        stocks_path,
        [
            [
                "FBst0300017",
                "Kyoto",
                "living stock ; FBsv:0000002",
                "Dmel",
                "Dp(1;Y)Bar[S] / In(1)dl-49, y[1] v[Of]",
                "Dp(1;Y)Bar[S] / In(1)dl-49, y[1] v[Of]",
                "100966",
            ]
        ],
    )

    monkeypatch.setattr("flymanager.utils.stock_sources.FLYBASE_STOCKS_PATH", stocks_path)

    stock_data, error = get_external_stock_record("KYOTO", "100966")

    assert error is None
    assert stock_data["supportStatus"] == "unsupported"
    assert stock_data["genotype"] == ""
    assert "format xchromosome" in stock_data["supportReason"]


def test_get_external_stock_record_rejects_multiple_top_level_homolog_states(tmp_path, monkeypatch):
    stocks_path = tmp_path / "stocks.tsv.gz"
    _write_flybase_stocks_file(
        stocks_path,
        [
            [
                "FBst0300001",
                "Kyoto",
                "living stock ; FBsv:0000002",
                "Dmel",
                "0 / C(1)RM, y[1] w[1] / C(1;Y)1, y[1] y[+] w[str]",
                "0 / C(1)RM, y[1] w[1] / C(1;Y)1, y[1] y[+] w[str]",
                "100949",
            ]
        ],
    )

    monkeypatch.setattr("flymanager.utils.stock_sources.FLYBASE_STOCKS_PATH", stocks_path)

    stock_data, error = get_external_stock_record("KYOTO", "100949")

    assert error is None
    assert stock_data["supportStatus"] == "unsupported"
    assert stock_data["genotype"] == ""
    assert stock_data["supportReason"]


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


def test_collect_compatible_gene_metadata_from_flybase_skips_rearrangement_false_positive_rows(tmp_path, monkeypatch):
    stocks_path = tmp_path / "stocks.tsv.gz"
    _write_flybase_stocks_file(
        stocks_path,
        [
            [
                "FBst0300017",
                "Kyoto",
                "living stock ; FBsv:0000002",
                "Dmel",
                "Dp(1;Y)Bar[S] / In(1)dl-49, y[1] v[Of]",
                "Dp(1;Y)Bar[S] / In(1)dl-49, y[1] v[Of]",
                "100966",
            ],
            [
                "FBst1234567",
                "Vienna",
                "living stock ; FBsv:0000002",
                "Dmel",
                "w[1118]; P{GD2813}v10004",
                "Vienna stock",
                "4321",
            ],
        ],
    )

    monkeypatch.setattr("flymanager.utils.stock_sources.FLYBASE_STOCKS_PATH", stocks_path)

    gene_components, summary = collect_compatible_gene_metadata_from_flybase()

    assert summary["total_rows"] == 2
    assert summary["dmel_rows"] == 2
    assert summary["supported_rows"] == 1
    assert summary["unsupported_rows"] == 1
    assert gene_components[0] == ["w[1118]"]
    assert gene_components[1] == ["P{GD2813}v10004"]
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
    assert stock_data["providerURL"] == "https://bdsc.indiana.edu/stocks/17"


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


def test_build_external_stock_provider_link_returns_vienna_search_url():
    provider_link = build_external_stock_provider_link("VIENNA", "4321", "FBst1234567")

    assert provider_link == {
        "url": "https://shop.vbc.ac.at/vdrc_store/catalogsearch/result/?q=4321",
        "label": "Open Vienna provider page",
        "kind": "provider",
    }


def test_build_stock_provider_metadata_infers_bdsc_from_bloomington_provenance():
    metadata = build_stock_provider_metadata(
        {
            "SourceID": "68654",
            "Provenance": "Bloomington",
        }
    )

    assert metadata == {
        "providerURL": "https://bdsc.indiana.edu/stocks/68654",
        "providerLinkLabel": "Open Bloomington provider page",
        "providerLinkKind": "provider",
    }


def test_build_stock_provider_metadata_infers_bdsc_from_serialized_bloomington_provenance():
    metadata = build_stock_provider_metadata(
        {
            "SourceID": "68654",
            "Provenance": '[{"value":"Bloomington"}]',
        }
    )

    assert metadata == {
        "providerURL": "https://bdsc.indiana.edu/stocks/68654",
        "providerLinkLabel": "Open Bloomington provider page",
        "providerLinkKind": "provider",
    }


def test_build_stock_provider_metadata_infers_bdsc_from_flybase_source_id_only(tmp_path, monkeypatch):
    stocks_path = tmp_path / "stocks.tsv.gz"
    _write_flybase_stocks_file(
        stocks_path,
        [
            [
                "FBst0068654",
                "Bloomington",
                "living stock ; FBsv:0000002",
                "Dmel",
                "w[1118]; P{y[+t7.7] w[+mC]=R19H07-GAL4.DBD}attP2",
                "R19H07-Gal4 DNA Binding Domain",
                "68654",
            ]
        ],
    )

    monkeypatch.setattr("flymanager.utils.stock_sources.FLYBASE_STOCKS_PATH", stocks_path)

    metadata = build_stock_provider_metadata(
        {
            "SourceID": "68654",
            "Genotype": "w[1118]; ; P{y[+t7.7] w[+mC]=R19H07-GAL4.DBD}attP2; ",
        }
    )

    assert metadata == {
        "providerURL": "https://bdsc.indiana.edu/stocks/68654",
        "providerLinkLabel": "Open Bloomington provider page",
        "providerLinkKind": "provider",
    }


def test_stock_source_lookups_reuse_cached_flybase_indexes(tmp_path, monkeypatch):
    stocks_path = tmp_path / "stocks.tsv.gz"
    _write_flybase_stocks_file(
        stocks_path,
        [
            [
                "FBst0068654",
                "Bloomington",
                "living stock ; FBsv:0000002",
                "Dmel",
                "w[1118]; P{R19H07}attP2",
                "R19H07",
                "68654",
            ]
        ],
    )

    monkeypatch.setattr("flymanager.utils.stock_sources.FLYBASE_STOCKS_PATH", stocks_path)
    monkeypatch.setattr(
        "flymanager.utils.stock_sources.FLYBASE_STOCKS_TEXT_PATH",
        tmp_path / "missing.tsv",
    )
    _load_flybase_stock_indexes.cache_clear()

    with patch(
        "flymanager.utils.stock_sources._iter_flybase_stock_rows",
        wraps=__import__("flymanager.utils.stock_sources", fromlist=["_iter_flybase_stock_rows"])._iter_flybase_stock_rows,
    ) as iter_rows:
        context = enrich_stock_source_context(
            {
                "SourceID": "68654",
                "Genotype": "w[1118]; ; P{R19H07}attP2; ",
            }
        )
        stock_data, error = get_external_stock_record("BDSC", "68654")

    assert error is None
    assert context["flyBaseStockID"] == "FBst0068654"
    assert stock_data["flyBaseStockID"] == "FBst0068654"
    assert iter_rows.call_count == 1
    _load_flybase_stock_indexes.cache_clear()


def test_build_stock_provider_metadata_ignores_other_source_type_for_bdsc_record(tmp_path, monkeypatch):
    stocks_path = tmp_path / "stocks.tsv.gz"
    _write_flybase_stocks_file(
        stocks_path,
        [
            [
                "FBst0068654",
                "Bloomington",
                "living stock ; FBsv:0000002",
                "Dmel",
                "w[1118]; P{y[+t7.7] w[+mC]=R19H07-GAL4.DBD}attP2",
                "R19H07-Gal4 DNA Binding Domain",
                "68654",
            ]
        ],
    )

    monkeypatch.setattr("flymanager.utils.stock_sources.FLYBASE_STOCKS_PATH", stocks_path)

    metadata = build_stock_provider_metadata(
        {
            "SourceID": "68654",
            "StockSource": "OTHER",
            "Provenance": "Bloomington",
            "Genotype": "w[1118]; ; P{y[+t7.7] w[+mC]=R19H07-GAL4.DBD}attP2; ",
        }
    )

    assert metadata == {
        "providerURL": "https://bdsc.indiana.edu/stocks/68654",
        "providerLinkLabel": "Open Bloomington provider page",
        "providerLinkKind": "provider",
    }


def test_view_stock_infers_bdsc_source_from_bloomington_provenance(monkeypatch):
    app = _make_app(monkeypatch)
    legacy_stock = {
        "UniqueID": "UID1",
        "User": "admin",
        "ViewerCanEdit": False,
        "SourceID": "68654",
        "Name": "R19H07-Gal4 DNA Binding Domain",
        "AltReference": "Pioneer Interneuron Subset 1 Gal4.DBD",
        "Type": "Gal4.DBD",
        "FoodType": "Molasses",
        "Status": "Healthy",
        "SeriesID": "46",
        "ReplicateID": "a",
        "VialLifetime": 14,
        "FlipFrequency": 7,
        "DevelopmentalTime": 10,
        "Species": "D. melanogaster",
        "Comments": "",
        "Provenance": "Bloomington",
        "Genotype": "w[1118]; ; P{y[+t7.7] w[+mC]=R19H07-GAL4.DBD}attP2; ",
        "TrayID": "RM1",
        "TrayPosition": "66",
        "CurrentlyAliveVials": "V8, V9",
        "FlipLog": "",
        "NextFlipDates": "",
        "NextEclosionDates": "",
        "DataModifiedDate": "",
        "ModificationLog": "",
        "OwnerUser": "admin",
        "MaintainerUser": "admin",
        "AssignmentScopeLabel": "Maintain",
        "AssignmentScopeDetail": "Owned by you",
    }

    with app.test_client() as client:
        with client.session_transaction() as sess:
            sess["username"] = "admin"

        with patch(
            "flymanager.app.routes.stock.get_metadata",
            side_effect=_stock_metadata_lookup,
        ), patch(
            "flymanager.app.routes.stock.get_accessible_stock",
            return_value=legacy_stock,
        ):
            response = client.get("/stock/view/UID1")

    page = response.get_data(as_text=True)
    assert response.status_code == 200
    assert "BDSC / Bloomington" in page
    assert "https://bdsc.indiana.edu/stocks/68654" in page


def test_view_stock_infers_bdsc_source_from_flybase_source_id_only(tmp_path, monkeypatch):
    stocks_path = tmp_path / "stocks.tsv.gz"
    _write_flybase_stocks_file(
        stocks_path,
        [
            [
                "FBst0068654",
                "Bloomington",
                "living stock ; FBsv:0000002",
                "Dmel",
                "w[1118]; P{y[+t7.7] w[+mC]=R19H07-GAL4.DBD}attP2",
                "R19H07-Gal4 DNA Binding Domain",
                "68654",
            ]
        ],
    )
    monkeypatch.setattr("flymanager.utils.stock_sources.FLYBASE_STOCKS_PATH", stocks_path)

    app = _make_app(monkeypatch)
    legacy_stock = {
        "UniqueID": "UID1A",
        "User": "admin",
        "ViewerCanEdit": False,
        "SourceID": "68654",
        "Name": "R19H07-Gal4 DNA Binding Domain",
        "AltReference": "Pioneer Interneuron Subset 1 Gal4.DBD",
        "Type": "Gal4.DBD",
        "FoodType": "Molasses",
        "Status": "Healthy",
        "SeriesID": "46",
        "ReplicateID": "a",
        "VialLifetime": 14,
        "FlipFrequency": 7,
        "DevelopmentalTime": 10,
        "Species": "D. melanogaster",
        "Comments": "",
        "Genotype": "w[1118]; ; P{y[+t7.7] w[+mC]=R19H07-GAL4.DBD}attP2; ",
        "TrayID": "RM1",
        "TrayPosition": "66",
        "CurrentlyAliveVials": "V8, V9",
        "FlipLog": "",
        "NextFlipDates": "",
        "NextEclosionDates": "",
        "DataModifiedDate": "",
        "ModificationLog": "",
        "OwnerUser": "admin",
        "MaintainerUser": "admin",
        "AssignmentScopeLabel": "Maintain",
        "AssignmentScopeDetail": "Owned by you",
    }

    with app.test_client() as client:
        with client.session_transaction() as sess:
            sess["username"] = "admin"

        with patch(
            "flymanager.app.routes.stock.get_metadata",
            side_effect=_stock_metadata_lookup,
        ), patch(
            "flymanager.app.routes.stock.get_accessible_stock",
            return_value=legacy_stock,
        ):
            response = client.get("/stock/view/UID1A")

    page = response.get_data(as_text=True)
    assert response.status_code == 200
    assert "BDSC / Bloomington" in page
    assert "https://bdsc.indiana.edu/stocks/68654" in page


def test_view_stock_ignores_other_source_type_for_bdsc_record(tmp_path, monkeypatch):
    stocks_path = tmp_path / "stocks.tsv.gz"
    _write_flybase_stocks_file(
        stocks_path,
        [
            [
                "FBst0068654",
                "Bloomington",
                "living stock ; FBsv:0000002",
                "Dmel",
                "w[1118]; P{y[+t7.7] w[+mC]=R19H07-GAL4.DBD}attP2",
                "R19H07-Gal4 DNA Binding Domain",
                "68654",
            ]
        ],
    )
    monkeypatch.setattr("flymanager.utils.stock_sources.FLYBASE_STOCKS_PATH", stocks_path)

    app = _make_app(monkeypatch)
    legacy_stock = {
        "UniqueID": "UID1B",
        "User": "admin",
        "ViewerCanEdit": False,
        "SourceID": "68654",
        "StockSource": "OTHER",
        "Name": "R19H07-Gal4 DNA Binding Domain",
        "AltReference": "Pioneer Interneuron Subset 1 Gal4.DBD",
        "Type": "Gal4.DBD",
        "FoodType": "Molasses",
        "Status": "Healthy",
        "SeriesID": "46",
        "ReplicateID": "a",
        "VialLifetime": 14,
        "FlipFrequency": 7,
        "DevelopmentalTime": 10,
        "Species": "D. melanogaster",
        "Comments": "",
        "Provenance": "Bloomington",
        "Genotype": "w[1118]; ; P{y[+t7.7] w[+mC]=R19H07-GAL4.DBD}attP2; ",
        "TrayID": "RM1",
        "TrayPosition": "66",
        "CurrentlyAliveVials": "V8, V9",
        "FlipLog": "",
        "NextFlipDates": "",
        "NextEclosionDates": "",
        "DataModifiedDate": "",
        "ModificationLog": "",
        "OwnerUser": "admin",
        "MaintainerUser": "admin",
        "AssignmentScopeLabel": "Maintain",
        "AssignmentScopeDetail": "Owned by you",
    }

    with app.test_client() as client:
        with client.session_transaction() as sess:
            sess["username"] = "admin"

        with patch(
            "flymanager.app.routes.stock.get_metadata",
            side_effect=_stock_metadata_lookup,
        ), patch(
            "flymanager.app.routes.stock.get_accessible_stock",
            return_value=legacy_stock,
        ):
            response = client.get("/stock/view/UID1B")

    page = response.get_data(as_text=True)
    assert response.status_code == 200
    assert "BDSC / Bloomington" in page
    assert "https://bdsc.indiana.edu/stocks/68654" in page


def test_view_stock_infers_bdsc_source_from_serialized_bloomington_provenance(monkeypatch):
    app = _make_app(monkeypatch)
    legacy_stock = {
        "UniqueID": "UID2",
        "User": "admin",
        "ViewerCanEdit": False,
        "SourceID": "68654",
        "Name": "R19H07-Gal4 DNA Binding Domain",
        "AltReference": "Pioneer Interneuron Subset 1 Gal4.DBD",
        "Type": "Gal4.DBD",
        "FoodType": "Molasses",
        "Status": "Healthy",
        "SeriesID": "46",
        "ReplicateID": "a",
        "VialLifetime": 14,
        "FlipFrequency": 7,
        "DevelopmentalTime": 10,
        "Species": "D. melanogaster",
        "Comments": "",
        "Provenance": '[{"value":"Bloomington"}]',
        "Genotype": "w[1118]; ; P{y[+t7.7] w[+mC]=R19H07-GAL4.DBD}attP2; ",
        "TrayID": "RM1",
        "TrayPosition": "66",
        "CurrentlyAliveVials": "V8, V9",
        "FlipLog": "",
        "NextFlipDates": "",
        "NextEclosionDates": "",
        "DataModifiedDate": "",
        "ModificationLog": "",
        "OwnerUser": "admin",
        "MaintainerUser": "admin",
        "AssignmentScopeLabel": "Maintain",
        "AssignmentScopeDetail": "Owned by you",
    }

    with app.test_client() as client:
        with client.session_transaction() as sess:
            sess["username"] = "admin"

        with patch(
            "flymanager.app.routes.stock.get_metadata",
            side_effect=_stock_metadata_lookup,
        ), patch(
            "flymanager.app.routes.stock.get_accessible_stock",
            return_value=legacy_stock,
        ):
            response = client.get("/stock/view/UID2")

    page = response.get_data(as_text=True)
    assert response.status_code == 200
    assert "BDSC / Bloomington" in page
    assert "https://bdsc.indiana.edu/stocks/68654" in page


def test_find_external_stock_matches_prefers_cross_provider_matches(tmp_path, monkeypatch):
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
            ],
            [
                "FBst0000017",
                "Vienna",
                "living stock ; FBsv:0000002",
                "Dmel",
                "w[*]; cn[1]/CyO",
                "Vienna 4321",
                "4321",
            ],
            [
                "FBst9999999",
                "Kyoto",
                "living stock ; FBsv:0000002",
                "Dmel",
                "w[1118]; ci[1]",
                "Kyoto other",
                "9999",
            ],
        ],
    )

    monkeypatch.setattr("flymanager.utils.stock_sources.FLYBASE_STOCKS_PATH", stocks_path)

    matches = find_external_stock_matches(
        {
            "StockSource": "BDSC",
            "SourceID": "17",
            "FlyBaseStockID": "FBst0000017",
            "Genotype": "w[*]; CyO/cn[1]; ; ",
        }
    )

    assert len(matches) == 1
    assert matches[0]["stockSource"] == "VIENNA"
    assert matches[0]["sourceID"] == "4321"
    assert matches[0]["matchScore"] == 100
    assert "Same FlyBase stock ID" in matches[0]["matchReasons"]
    assert matches[0]["providerURL"] == "https://shop.vbc.ac.at/vdrc_store/catalogsearch/result/?q=4321"


def test_reverse_search_route_returns_candidates(monkeypatch):
    app = _make_app(monkeypatch)
    fake_db = MagicMock()
    fake_db.__getitem__.return_value.find_one.return_value = {
        "UniqueID": "UID1",
        "User": "admin",
        "StockSource": "BDSC",
        "SourceID": "17",
        "FlyBaseStockID": "FBst0000017",
        "Genotype": "w[*]; CyO/cn[1]; ; ",
    }

    with app.test_client() as client:
        with client.session_transaction() as sess:
            sess["username"] = "admin"

        with patch("flymanager.app.routes.stock.db", fake_db), patch(
            "flymanager.app.routes.stock.find_external_stock_matches",
            return_value=[
                {
                    "stockSource": "VIENNA",
                    "sourceCollection": "Vienna",
                    "sourceID": "4321",
                    "providerURL": "https://shop.vbc.ac.at/vdrc_store/catalogsearch/result/?q=4321",
                    "matchScore": 100,
                    "matchReasons": ["Same FlyBase stock ID"],
                }
            ],
        ):
            response = client.get("/stock/reverse_search/UID1")

    assert response.status_code == 200
    assert response.get_json()["count"] == 1
    assert response.get_json()["cached"] is False
    assert response.get_json()["cachedAt"]
    assert response.get_json()["candidates"][0]["stockSource"] == "VIENNA"


def test_reverse_search_route_uses_cached_candidates(monkeypatch):
    app = _make_app(monkeypatch)
    stock = {
        "UniqueID": "UID1",
        "User": "admin",
        "StockSource": "BDSC",
        "SourceID": "17",
        "FlyBaseStockID": "FBst0000017",
        "Genotype": "w[*]; CyO/cn[1]; ; ",
    }

    with app.test_client() as client:
        with client.session_transaction() as sess:
            sess["username"] = "admin"

        with patch(
            "flymanager.app.routes.stock.get_accessible_stock",
            return_value=stock,
        ), patch(
            "flymanager.app.routes.stock._get_valid_provider_match_cache",
            return_value={
                "candidates": [
                    {
                        "stockSource": "VIENNA",
                        "sourceCollection": "Vienna",
                        "sourceID": "4321",
                        "providerURL": "https://shop.vbc.ac.at/vdrc_store/catalogsearch/result/?q=4321",
                        "matchScore": 100,
                        "matchReasons": ["Same FlyBase stock ID"],
                    }
                ],
                "count": 1,
                "cached": True,
                "cachedAt": "2026-04-17 10:00",
            },
        ), patch(
            "flymanager.app.routes.stock.find_external_stock_matches",
            side_effect=AssertionError("cache should be used before recomputing"),
        ):
            response = client.get("/stock/reverse_search/UID1")

    assert response.status_code == 200
    assert response.get_json()["cached"] is True
    assert response.get_json()["count"] == 1
    assert response.get_json()["cachedAt"] == "2026-04-17 10:00"


def test_reverse_search_route_refresh_bypasses_cache(monkeypatch):
    app = _make_app(monkeypatch)
    stock = {
        "UniqueID": "UID1",
        "User": "admin",
        "StockSource": "BDSC",
        "SourceID": "17",
        "FlyBaseStockID": "FBst0000017",
        "Genotype": "w[*]; CyO/cn[1]; ; ",
    }

    with app.test_client() as client:
        with client.session_transaction() as sess:
            sess["username"] = "admin"

        with patch(
            "flymanager.app.routes.stock.get_accessible_stock",
            return_value=stock,
        ), patch(
            "flymanager.app.routes.stock._get_valid_provider_match_cache",
            return_value={
                "candidates": [],
                "count": 0,
                "cached": True,
                "cachedAt": "2026-04-17 09:00",
            },
        ), patch(
            "flymanager.app.routes.stock.find_external_stock_matches",
            return_value=[
                {
                    "stockSource": "VIENNA",
                    "sourceCollection": "Vienna",
                    "sourceID": "4321",
                    "providerURL": "https://shop.vbc.ac.at/vdrc_store/catalogsearch/result/?q=4321",
                    "matchScore": 100,
                    "matchReasons": ["Same FlyBase stock ID"],
                }
            ],
        ), patch(
            "flymanager.app.routes.stock._store_provider_match_cache",
            return_value={
                "candidates": [
                    {
                        "stockSource": "VIENNA",
                        "sourceCollection": "Vienna",
                        "sourceID": "4321",
                        "providerURL": "https://shop.vbc.ac.at/vdrc_store/catalogsearch/result/?q=4321",
                        "matchScore": 100,
                        "matchReasons": ["Same FlyBase stock ID"],
                    }
                ],
                "count": 1,
                "cached": False,
                "cachedAt": "2026-04-17 11:00",
            },
        ):
            response = client.get("/stock/reverse_search/UID1?refresh=1")

    assert response.status_code == 200
    assert response.get_json()["cached"] is False
    assert response.get_json()["count"] == 1
    assert response.get_json()["cachedAt"] == "2026-04-17 11:00"


def test_reverse_search_route_recomputes_when_cache_is_expired(monkeypatch):
    app = _make_app(monkeypatch)
    stock = {
        "UniqueID": "UID1",
        "User": "admin",
        "StockSource": "BDSC",
        "SourceID": "17",
        "FlyBaseStockID": "FBst0000017",
        "Genotype": "w[*]; CyO/cn[1]; ; ",
        "ProviderMatchCache": {
            "signature": "sig-1",
            "candidates": [
                {
                    "stockSource": "VIENNA",
                    "sourceCollection": "Vienna",
                    "sourceID": "1111",
                }
            ],
            "cachedAt": "2026-01-01 00:00",
        },
    }

    with app.test_client() as client:
        with client.session_transaction() as sess:
            sess["username"] = "admin"

        with patch(
            "flymanager.app.routes.stock.get_accessible_stock",
            return_value=stock,
        ), patch(
            "flymanager.app.routes.stock._build_provider_match_cache_signature",
            return_value="sig-1",
        ), patch(
            "flymanager.app.routes.stock.find_external_stock_matches",
            return_value=[
                {
                    "stockSource": "VIENNA",
                    "sourceCollection": "Vienna",
                    "sourceID": "4321",
                    "providerURL": "https://shop.vbc.ac.at/vdrc_store/catalogsearch/result/?q=4321",
                    "matchScore": 100,
                    "matchReasons": ["Same FlyBase stock ID"],
                }
            ],
        ), patch(
            "flymanager.app.routes.stock.db"
        ):
            response = client.get("/stock/reverse_search/UID1")

    assert response.status_code == 200
    assert response.get_json()["cached"] is False
    assert response.get_json()["count"] == 1


def test_view_stock_embeds_cached_provider_matches(monkeypatch):
    app = _make_app(monkeypatch)
    stock = {
        "UniqueID": "UIDCACHE",
        "User": "admin",
        "ViewerCanEdit": False,
        "SourceID": "17",
        "StockSource": "BDSC",
        "Name": "Cached stock",
        "AltReference": "FBst0000017",
        "Type": "Gal4.DBD",
        "FoodType": "Molasses",
        "Status": "Healthy",
        "SeriesID": "46",
        "ReplicateID": "a",
        "VialLifetime": 14,
        "FlipFrequency": 7,
        "DevelopmentalTime": 10,
        "Species": "D. melanogaster",
        "Comments": "",
        "Provenance": "Bloomington",
        "FlyBaseStockID": "FBst0000017",
        "Genotype": "w[*]; CyO/cn[1]; ; ",
        "TrayID": "RM1",
        "TrayPosition": "66",
        "CurrentlyAliveVials": "V8, V9",
        "FlipLog": "",
        "NextFlipDates": "",
        "NextEclosionDates": "",
        "DataModifiedDate": "",
        "ModificationLog": "",
        "OwnerUser": "admin",
        "MaintainerUser": "admin",
        "AssignmentScopeLabel": "Maintain",
        "AssignmentScopeDetail": "Owned by you",
    }

    with app.test_client() as client:
        with client.session_transaction() as sess:
            sess["username"] = "admin"

        with patch(
            "flymanager.app.routes.stock.get_metadata",
            side_effect=_stock_metadata_lookup,
        ), patch(
            "flymanager.app.routes.stock.get_accessible_stock",
            return_value=stock,
        ), patch(
            "flymanager.app.routes.stock._get_valid_provider_match_cache",
            return_value={
                "candidates": [
                    {
                        "stockSource": "VIENNA",
                        "sourceCollection": "Vienna",
                        "sourceID": "4321",
                        "providerURL": "https://shop.vbc.ac.at/vdrc_store/catalogsearch/result/?q=4321",
                        "matchScore": 100,
                        "matchReasons": ["Same FlyBase stock ID"],
                        "name": "Vienna 4321",
                    }
                ],
                "count": 1,
                "cached": True,
                "cachedAt": "2026-04-17 10:00",
            },
        ):
            response = client.get("/stock/view/UIDCACHE")

    page = response.get_data(as_text=True)
    assert response.status_code == 200
    assert "Vienna 4321" in page


def test_admin_can_refresh_provider_match_cache_for_all_stocks(monkeypatch):
    app = _make_app(monkeypatch)

    with app.test_client() as client:
        with client.session_transaction() as sess:
            sess["username"] = "admin"

        with patch(
            "flymanager.app.routes.settings._run_provider_match_cache_refresh",
            return_value={
                "scanned": 5,
                "refreshed": 5,
                "errors": 0,
                "candidate_matches": 12,
            },
        ) as refresh_provider_cache, patch(
            "flymanager.app.routes.settings.hold_operation_lock",
            return_value=nullcontext(),
        ), patch(
            "flymanager.app.routes.settings.write_activity"
        ) as write_activity:
            response = client.post("/settings/refresh-all-provider-caches")

    assert response.status_code == 302
    refresh_provider_cache.assert_called_once_with()
    write_activity.assert_called_once_with("admin", "Refreshed provider match cache for all stocks", ANY)


def test_stock_explorer_embeds_cached_provider_matches(monkeypatch):
    app = _make_app(monkeypatch)
    stock = {
        "UniqueID": "UIDEXP",
        "User": "admin",
        "ViewerCanEdit": False,
        "ViewerCanMaintain": True,
        "ViewerOwnsRecord": True,
        "AssignmentScope": "owned",
        "AssignmentScopeLabel": "Maintain",
        "AssignmentScopeDetail": "Owned by you",
        "OwnerUser": "admin",
        "AssignedTo": "",
        "MaintainerUser": "admin",
        "SourceID": "17",
        "StockSource": "BDSC",
        "SourceCollection": "Bloomington",
        "Name": "Cached stock",
        "AltReference": "FBst0000017",
        "Type": "Gal4.DBD",
        "FoodType": "Molasses",
        "Status": "Healthy",
        "SeriesID": "46",
        "ReplicateID": "a",
        "VialLifetime": 14,
        "FlipFrequency": 7,
        "DevelopmentalTime": 10,
        "Species": "D. melanogaster",
        "Comments": "",
        "Provenance": "Bloomington",
        "FlyBaseStockID": "FBst0000017",
        "Genotype": "w[*]; CyO/cn[1]; ; ",
        "TrayID": "RM1",
        "TrayPosition": "66",
        "CurrentlyAliveVials": "V8, V9",
        "FlipLog": "",
        "NextFlipDates": "",
        "NextEclosionDates": "",
        "CreationDate": "",
        "LastFlipDate": "",
        "DataModifiedDate": "",
        "ModificationLog": "",
    }

    with app.test_client() as client:
        with client.session_transaction() as sess:
            sess["username"] = "admin"

        with patch(
            # stock_explorer now queries `db` directly (Task 12 -
            # deterministic filtering/pagination pushed into Mongo), so this
            # seeds a FakeDatabase instead of patching get_accessible_stocks.
            "flymanager.app.routes.stock.db",
            FakeDatabase({"stocks": [{**stock, "AssignedTo": stock.get("AssignedTo", "")}]}),
        ), patch(
            "flymanager.app.routes.stock._get_valid_provider_match_cache",
            return_value={
                "candidates": [
                    {
                        "stockSource": "VIENNA",
                        "sourceCollection": "Vienna",
                        "sourceID": "4321",
                        "providerURL": "https://shop.vbc.ac.at/vdrc_store/catalogsearch/result/?q=4321",
                        "matchScore": 100,
                        "matchReasons": ["Same FlyBase stock ID"],
                        "name": "Vienna 4321",
                    }
                ],
                "count": 1,
                "cached": True,
                "cachedAt": "2026-04-17 10:00",
                "cacheCountLabel": "1 cached match",
                "cacheStatusLabel": "Cached 2h ago",
                "cacheAgeLabel": "2h ago",
                "primaryMatchLabel": "Vienna 4321",
            },
        ):
            response = client.get("/stock/explorer")

    page = response.get_data(as_text=True)
    assert response.status_code == 200
    assert "1 cached match" in page
    assert "Cached 2h ago" in page
    assert "Top match: Vienna 4321" in page
    assert 'data-provider-matches-loaded="true"' in page


def test_mark_ordered_route_updates_comments_with_order_note(monkeypatch):
    app = _make_app(monkeypatch)

    class FakeCollection:
        def find_one(self, query):
            if query == {"UniqueID": "UID1", "User": "admin"}:
                return {
                    "UniqueID": "UID1",
                    "User": "admin",
                    "Comments": "Existing comment",
                }
            return None

    class FakeDatabase:
        def __getitem__(self, name):
            assert name == "stocks"
            return FakeCollection()

    with app.test_client() as client:
        with client.session_transaction() as sess:
            sess["username"] = "admin"

        csrf_token = _get_authenticated_csrf_token(client)

        with patch("flymanager.app.routes.stock.db", FakeDatabase()), patch(
            "flymanager.app.routes.stock.edit_stock",
            return_value=True,
        ) as edit_stock_mock, patch(
            "flymanager.app.routes.stock.write_activity"
        ):
            response = client.post(
                "/stock/mark_ordered",
                json={"items": [{"uid": "UID1", "note": "Use PO-42"}]},
                headers={"X-CSRFToken": csrf_token},
            )

    assert response.status_code == 200
    assert response.get_json()["results"]["success"] == [{"uid": "UID1"}]
    edit_stock_mock.assert_called_once_with(
        "admin",
        "UID1",
        ANY,
        {
            "Status": "Ordered",
            "Comments": "Order note: Use PO-42; Existing comment",
        },
        refresh_vials=False,
    )