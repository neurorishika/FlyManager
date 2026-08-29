import pytest

from flymanager.utils.phenotypes import marker_catalog, visual_markers


@pytest.fixture(autouse=True)
def _reset_catalog():
    marker_catalog.reset_catalog()
    yield
    marker_catalog.reset_catalog()


def test_get_visual_marker_returns_a_gene_marker_with_gene_stem():
    marker = visual_markers.get_visual_marker("Cy")
    assert marker["display_label"] == "Cy"
    assert marker["gene_stem"] == "Cy"


def test_get_visual_marker_prefers_an_allele_token():
    marker = visual_markers.get_visual_marker("wg", allele_spec="Sp-1")
    assert marker["display_label"] == "Sp"
    assert marker["allele_specific"] is True
    assert marker["allele_token"] == "wg[Sp-1]"


def test_get_visual_marker_accepts_an_explicit_token():
    marker = visual_markers.get_visual_marker("wg", token="wg[Gla-1]")
    assert marker["display_label"] == "Gla"


def test_get_visual_marker_returns_none_for_an_unknown_symbol():
    assert visual_markers.get_visual_marker("definitely-not-a-marker") is None


def test_get_visual_marker_returns_a_copy():
    first = visual_markers.get_visual_marker("Cy")
    first["display_label"] = "mutated"
    assert visual_markers.get_visual_marker("Cy")["display_label"] == "Cy"


def test_get_balancer_metadata_resolves_aliases_and_copies():
    assert visual_markers.get_balancer_metadata("Binsn")["symbol"] == "Binsc"
    assert visual_markers.get_balancer_metadata("CyO")["default_markers"] == \
        ["Cy", "pr", "cn"]
    assert visual_markers.get_balancer_metadata("nope") is None
    metadata = visual_markers.get_balancer_metadata("CyO")
    metadata["default_markers"].append("bogus")
    assert "bogus" not in visual_markers.get_balancer_metadata("CyO")["default_markers"]


def test_get_reviewed_marker_alias_matches_the_legacy_shape():
    assert visual_markers.get_reviewed_marker_alias("Gla") == {
        "alias_type": "allele_token", "value": "wg[Gla-1]"}
    assert visual_markers.get_reviewed_marker_alias("  Sco ")["value"] == "sna[Sco]"
    assert visual_markers.get_reviewed_marker_alias("nope") is None


def test_collection_accessors_expose_the_snapshot():
    assert "Cy" in visual_markers.get_gene_marker_symbols()
    assert "wg[Sp-1]" in visual_markers.get_allele_marker_tokens()
    assert "Binsn" in visual_markers.get_balancer_aliases()
    assert "CyO" in visual_markers.get_known_balancer_symbols()
    assert visual_markers.get_balancer_markers()["CyO"] == ["Cy", "pr", "cn"]
    assert "Sp" in visual_markers.get_probe_marker_symbols()
    assert len(visual_markers.get_probe_marker_symbols()) == 46


def test_accessors_follow_a_replaced_snapshot():
    edited = marker_catalog.load_shipped_catalog()
    overlay = [dict(
        next(d for d in edited["definitions"] if d["Key"] == "Cy"),
        origin="user",
        payload={"body_part": "wing", "effect": "edited effect",
                 "dominance": "dominant", "display_label": "Cy",
                 "phenotype_key": "Cy", "chromosome": 2,
                 "scoring_confidence": 0.95},
    )]
    marker_catalog.set_catalog(marker_catalog.compile_catalog(edited, overlay))
    assert visual_markers.get_visual_marker("Cy")["effect"] == "edited effect"


def test_hot_path_accessors_return_immutable_values():
    """These deliberately do not copy, so they must be immutable — otherwise a
    caller can corrupt the shared process-wide snapshot for everyone."""
    assert isinstance(visual_markers.get_gene_marker_symbols(), frozenset)
    assert isinstance(visual_markers.get_allele_marker_tokens(), frozenset)
    assert isinstance(visual_markers.get_known_balancer_symbols(), frozenset)
    assert isinstance(visual_markers.get_balancer_match_order(), tuple)


def test_legacy_constant_shims_are_gone():
    """The shims froze marker data at import; nothing may depend on them again."""
    for name in (
        "CRITICAL_MARKERS",
        "VISUAL_MARKER_DICTIONARY",
        "ALLELE_VISUAL_MARKER_DICTIONARY",
        "REVIEWED_MARKER_ALIASES",
        "BALANCER_METADATA",
        "BALANCER_ALIASES",
        "BALANCER_MARKERS",
        "KNOWN_BALANCER_SYMBOLS",
    ):
        assert not hasattr(visual_markers, name), f"{name} should have been deleted"
