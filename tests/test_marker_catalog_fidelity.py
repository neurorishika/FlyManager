"""The shipped catalog must reproduce the pre-migration hardcoded dictionaries
exactly. tests/fixtures/legacy_marker_dictionaries.json is a frozen snapshot of
those dictionaries taken before they were deleted; regenerate it only if you
intend to change shipped marker behaviour."""
import json
from pathlib import Path

from flymanager.utils.phenotypes.marker_catalog import (compile_catalog,
                                                        load_shipped_catalog)

FIXTURE_PATH = Path(__file__).parent / "fixtures" / "legacy_marker_dictionaries.json"


def _legacy():
    return json.loads(FIXTURE_PATH.read_text(encoding="utf-8"))


def _snapshot():
    return compile_catalog(load_shipped_catalog(), [])


def test_gene_marker_dictionary_round_trips():
    assert _snapshot()["gene_markers"] == _legacy()["VISUAL_MARKER_DICTIONARY"]


def test_allele_marker_dictionary_round_trips():
    assert _snapshot()["allele_markers"] == _legacy()["ALLELE_VISUAL_MARKER_DICTIONARY"]


def test_reviewed_marker_aliases_round_trip():
    assert _snapshot()["aliases"] == _legacy()["REVIEWED_MARKER_ALIASES"]


def test_balancer_metadata_round_trips():
    assert _snapshot()["balancers"] == _legacy()["BALANCER_METADATA"]


def test_balancer_aliases_markers_and_known_symbols_round_trip():
    snapshot = _snapshot()
    legacy = _legacy()
    assert snapshot["balancer_aliases"] == legacy["BALANCER_ALIASES"]
    assert snapshot["balancer_markers"] == legacy["BALANCER_MARKERS"]
    assert snapshot["known_balancer_symbols"] == set(legacy["KNOWN_BALANCER_SYMBOLS"])


def test_probe_symbols_cover_every_critical_marker_including_sp():
    probes = set(_snapshot()["probe_symbols"])
    assert probes == set(_legacy()["CRITICAL_MARKERS"])
    assert "Sp" in probes
    assert len(probes) == 46


def test_stability_scores_and_notes_round_trip():
    stability = _snapshot()["stability"]
    legacy = _legacy()
    assert {label: entry["score"] for label, entry in stability.items()} == \
        legacy["MARKER_STABILITY_SCORES"]
    for label, notes in legacy["MARKER_NOTES"].items():
        assert stability[label]["notes"] == notes


def test_image_aliases_round_trip_except_the_epistasis_key():
    legacy = dict(_legacy()["PHENOTYPE_IMAGE_ALIASES"])
    residual = legacy.pop("epistasis:w_mini_white_rescue")
    assert residual, "the epistasis key is expected to exist and stay hardcoded"
    assert _snapshot()["image_aliases"] == legacy


def test_construct_marker_overrides_round_trip():
    construct_markers = _snapshot()["construct_markers"]
    assert set(construct_markers) == {"w", "y", "v"}
    assert construct_markers["w"]["overrides"]["phenotype_key"] == "mini_white"
    assert construct_markers["y"]["overrides"]["display_label"] == "y+"
    assert construct_markers["v"]["overrides"]["body_part"] == "eye"


def test_shipped_catalog_has_no_invalid_definitions():
    assert _snapshot()["invalid_definitions"] == []


def test_every_shipped_definition_is_marked_shipped():
    definitions = _snapshot()["definitions"].values()
    assert definitions
    assert {d["origin"] for d in definitions} == {"shipped"}
