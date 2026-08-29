import json

import pytest

from flymanager.utils.phenotypes.marker_catalog import (
    compile_catalog, compute_marker_catalog_signature, load_shipped_catalog,
    validate_definition)

GENE_ROW = {
    "Key": "Cy",
    "kind": "gene_marker",
    "match": {"symbol": "Cy"},
    "payload": {
        "body_part": "wing",
        "effect": "curly wings",
        "dominance": "dominant",
        "display_label": "Cy",
        "phenotype_key": "Cy",
        "chromosome": 2,
        "scoring_confidence": 0.95,
    },
    "sorting": {"stabilityScore": 0.95, "notes": ["Curly is reliable."]},
    "audit": {"isProbeMarker": True, "probeSymbol": ""},
    "imaging": {"aliases": ["cy", "cyo"], "images": []},
    "expression": {},
    "provenance": {"source": "manual_dictionary"},
    "origin": "shipped",
}

ALLELE_ROW = {
    "Key": "wg[Sp-1]",
    "kind": "allele_marker",
    "match": {"token": "wg[Sp-1]", "geneStem": "wg", "alleleSpec": "Sp-1"},
    "payload": {
        "gene_stem": "wg",
        "body_part": "wing",
        "effect": "spade-shaped notched wings",
        "dominance": "dominant",
        "display_label": "Sp",
        "phenotype_key": "wg_Sp",
        "chromosome": 2,
        "scoring_confidence": 0.78,
    },
    "sorting": {},
    "audit": {"isProbeMarker": True, "probeSymbol": "Sp"},
    "imaging": {"aliases": ["sp"], "images": []},
    "expression": {},
    "provenance": {"geneName": "wingless", "flybaseId": "FBgn0284084",
                   "source": "manual_dictionary"},
    "origin": "shipped",
}

ALIAS_ROW = {
    "Key": "Gla",
    "kind": "alias",
    "match": {"token": "Gla"},
    "payload": {"alias_type": "allele_token", "value": "wg[Gla-1]"},
    "sorting": {}, "audit": {}, "imaging": {}, "expression": {},
    "provenance": {}, "origin": "shipped",
}

BALANCER_ROW = {
    "Key": "CyO",
    "kind": "balancer",
    "match": {"symbol": "CyO", "aliases": []},
    "payload": {"family": "CyO", "chromosome": 2,
                "default_markers": ["Cy"], "notes": []},
    "sorting": {}, "audit": {}, "imaging": {}, "expression": {},
    "provenance": {"source": "bdsc_cheat_sheet"}, "origin": "shipped",
}

ALIASED_BALANCER_ROW = {
    "Key": "Binsc",
    "kind": "balancer",
    "match": {"symbol": "Binsc", "aliases": ["Binsn"]},
    "payload": {"family": "Binsc", "chromosome": 1,
                "default_markers": ["sc"], "notes": []},
    "sorting": {}, "audit": {}, "imaging": {}, "expression": {},
    "provenance": {"source": "bdsc_cheat_sheet"}, "origin": "shipped",
}

CONSTRUCT_ROW = {
    "Key": "construct:w+",
    "kind": "construct_marker",
    "match": {"geneStem": "w", "allelePrefix": "+"},
    "payload": {"overrides": {
        "dominance": "dominant",
        "display_label": "mini-white",
        "effect": "pigmented eyes from construct marker",
        "phenotype_key": "mini_white",
        "mini_white": True,
    }},
    "sorting": {"stabilityScore": 0.58, "notes": ["dosage-sensitive"]},
    "audit": {}, "imaging": {"aliases": ["miniwhite", "w+"], "images": []},
    "expression": {}, "provenance": {}, "origin": "shipped",
}


def _shipped(definitions, version=1):
    return {"catalogVersion": version, "definitions": list(definitions)}


def test_gene_marker_payload_merges_provenance_and_omits_absent_fields():
    snapshot = compile_catalog(_shipped([GENE_ROW]), [])
    assert snapshot["gene_markers"]["Cy"] == {
        "body_part": "wing",
        "effect": "curly wings",
        "dominance": "dominant",
        "display_label": "Cy",
        "phenotype_key": "Cy",
        "chromosome": 2,
        "scoring_confidence": 0.95,
        "source": "manual_dictionary",
    }
    assert "gene_name" not in snapshot["gene_markers"]["Cy"]


def test_allele_marker_is_indexed_by_token_with_provenance():
    snapshot = compile_catalog(_shipped([ALLELE_ROW]), [])
    marker = snapshot["allele_markers"]["wg[Sp-1]"]
    assert marker["gene_stem"] == "wg"
    assert marker["gene_name"] == "wingless"
    assert marker["flybase_id"] == "FBgn0284084"
    assert "reference_url" not in marker


def test_alias_indexes_forward_and_by_target():
    snapshot = compile_catalog(_shipped([ALIAS_ROW]), [])
    assert snapshot["aliases"]["Gla"] == {"alias_type": "allele_token",
                                          "value": "wg[Gla-1]"}
    assert snapshot["aliases_by_target"]["wg[Gla-1]"] == {"Gla"}


def test_balancer_metadata_shape_markers_and_reverse_index():
    snapshot = compile_catalog(_shipped([GENE_ROW, BALANCER_ROW]), [])
    assert snapshot["balancers"]["CyO"] == {
        "symbol": "CyO", "family": "CyO", "chromosome": 2,
        "default_markers": ["Cy"], "notes": [], "source": "bdsc_cheat_sheet",
    }
    assert snapshot["balancer_markers"]["CyO"] == ["Cy"]
    assert snapshot["known_balancer_symbols"] == {"CyO"}
    assert snapshot["balancers_referencing"]["Cy"] == {"CyO"}


def test_balancer_aliases_are_indexed_and_listed_on_the_canonical_row():
    snapshot = compile_catalog(_shipped([ALIASED_BALANCER_ROW]), [])
    assert snapshot["balancer_aliases"] == {"Binsn": "Binsc"}
    assert snapshot["balancers"]["Binsc"]["aliases"] == ["Binsn"]
    assert snapshot["known_balancer_symbols"] == {"Binsc", "Binsn"}


def test_precomputed_hot_path_indexes():
    snapshot = compile_catalog(_shipped([GENE_ROW, ALLELE_ROW, ALIASED_BALANCER_ROW]), [])
    assert snapshot["gene_marker_symbols"] == frozenset({"Cy"})
    assert snapshot["allele_marker_tokens"] == frozenset({"wg[Sp-1]"})
    assert snapshot["balancer_match_order"] == ("Binsc", "Binsn")


def test_balancer_without_aliases_has_no_aliases_key():
    snapshot = compile_catalog(_shipped([BALANCER_ROW]), [])
    assert "aliases" not in snapshot["balancers"]["CyO"]


def test_construct_marker_is_indexed_by_gene_stem():
    snapshot = compile_catalog(_shipped([CONSTRUCT_ROW]), [])
    assert snapshot["construct_markers"]["w"] == {
        "key": "construct:w+",
        "allele_prefix": "+",
        "overrides": CONSTRUCT_ROW["payload"]["overrides"],
    }


def test_stability_is_keyed_by_display_label_including_constructs():
    snapshot = compile_catalog(_shipped([GENE_ROW, CONSTRUCT_ROW]), [])
    assert snapshot["stability"]["Cy"] == {"score": 0.95,
                                           "notes": ["Curly is reliable."]}
    assert snapshot["stability"]["mini-white"]["score"] == 0.58


def test_image_aliases_are_keyed_by_phenotype_key_only():
    snapshot = compile_catalog(_shipped([GENE_ROW, CONSTRUCT_ROW]), [])
    assert snapshot["image_aliases"]["Cy"] == ["cy", "cyo"]
    assert snapshot["image_aliases"]["mini_white"] == ["miniwhite", "w+"]
    assert "construct:w+" not in snapshot["image_aliases"]


def test_probe_symbols_use_probe_symbol_override_and_are_sorted():
    snapshot = compile_catalog(_shipped([GENE_ROW, ALLELE_ROW]), [])
    assert snapshot["probe_symbols"] == ["Cy", "Sp"]


def test_overlay_document_overrides_shipped_by_key():
    override = dict(GENE_ROW, origin="user",
                    payload=dict(GENE_ROW["payload"], effect="edited"))
    snapshot = compile_catalog(_shipped([GENE_ROW]), [override])
    assert snapshot["gene_markers"]["Cy"]["effect"] == "edited"
    assert snapshot["definitions"]["Cy"]["origin"] == "user"


def test_overlay_document_can_add_a_new_key():
    new_row = dict(GENE_ROW, Key="Zz", origin="user",
                   match={"symbol": "Zz"},
                   payload=dict(GENE_ROW["payload"], display_label="Zz",
                                phenotype_key="Zz"))
    snapshot = compile_catalog(_shipped([GENE_ROW]), [new_row])
    assert set(snapshot["gene_markers"]) == {"Cy", "Zz"}


def test_invalid_overlay_document_is_skipped_and_reported():
    snapshot = compile_catalog(_shipped([GENE_ROW]),
                               [{"Key": "", "kind": "gene_marker"}])
    assert snapshot["gene_markers"]["Cy"]["effect"] == "curly wings"
    assert len(snapshot["invalid_definitions"]) == 1
    assert "Key is required" in snapshot["invalid_definitions"][0]["errors"]


def test_validate_definition_rejects_unknown_kind_and_bad_alias():
    assert "kind must be one of" in " ".join(
        validate_definition({"Key": "x", "kind": "nope"}))
    assert "alias payload.value is required" in validate_definition(
        {"Key": "x", "kind": "alias", "payload": {}})


def test_signature_is_stable_and_content_sensitive():
    first = compile_catalog(_shipped([GENE_ROW, BALANCER_ROW]), [])
    second = compile_catalog(_shipped([BALANCER_ROW, GENE_ROW]), [])
    edited = compile_catalog(
        _shipped([dict(GENE_ROW, payload=dict(GENE_ROW["payload"], effect="x")),
                  BALANCER_ROW]), [])
    assert first["signature"] == second["signature"]
    assert first["signature"] != edited["signature"]
    assert len(first["signature"]) == 32


def test_signature_ignores_audit_trail_origin_and_override_marker():
    plain = compile_catalog(_shipped([GENE_ROW]), [])
    touched = compile_catalog(_shipped([dict(
        GENE_ROW,
        CreatedBy="alice",
        UpdatedAt="2026-01-01T00:00:00Z",
        origin="curated",
        overridesShipped={"key": "Cy", "shippedVersion": 1},
    )]), [])
    assert plain["signature"] == touched["signature"]


def test_signature_tracks_catalog_version():
    assert (compile_catalog(_shipped([GENE_ROW], version=1), [])["signature"]
            != compile_catalog(_shipped([GENE_ROW], version=2), [])["signature"])


def test_load_shipped_catalog_reads_a_file(tmp_path):
    path = tmp_path / "catalog.json"
    path.write_text(json.dumps(_shipped([GENE_ROW])), encoding="utf-8")
    loaded = load_shipped_catalog(path)
    assert loaded["catalogVersion"] == 1
    assert loaded["definitions"][0]["Key"] == "Cy"


def test_load_shipped_catalog_raises_on_malformed_json(tmp_path):
    path = tmp_path / "catalog.json"
    path.write_text("{not json", encoding="utf-8")
    with pytest.raises(ValueError):
        load_shipped_catalog(path)


def test_load_shipped_catalog_raises_when_definitions_missing(tmp_path):
    path = tmp_path / "catalog.json"
    path.write_text(json.dumps({"catalogVersion": 1}), encoding="utf-8")
    with pytest.raises(ValueError):
        load_shipped_catalog(path)
