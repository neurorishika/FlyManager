import pytest

from flymanager.utils.phenotypes import marker_catalog
from flymanager.utils.phenotypes.marker_rebuild import (build_affected_query,
                                                        derive_affected_tokens)


@pytest.fixture(autouse=True)
def _reset_catalog():
    marker_catalog.reset_catalog()
    yield
    marker_catalog.reset_catalog()


def _snapshot():
    return marker_catalog.get_catalog()


def test_editing_a_balancer_marker_closes_over_every_referencing_balancer():
    tokens = derive_affected_tokens(_snapshot(), ["Cy"])
    assert {"Cy", "CyO", "SM1", "SM5", "SM6a", "SM6b"} <= tokens


def test_the_closure_includes_balancer_aliases():
    tokens = derive_affected_tokens(_snapshot(), ["sc"])
    assert "Binsc" in tokens
    assert "Binsn" in tokens, "an alias of a referencing balancer must be swept too"


def test_editing_an_allele_marker_includes_aliases_pointing_at_it():
    tokens = derive_affected_tokens(_snapshot(), ["wg[Gla-1]"])
    assert "wg[Gla-1]" in tokens
    assert "Gla" in tokens


def test_editing_an_alias_includes_its_target():
    tokens = derive_affected_tokens(_snapshot(), ["Gla"])
    assert {"Gla", "wg[Gla-1]"} <= tokens


def test_editing_a_balancer_includes_its_symbol_and_aliases_but_not_its_markers():
    tokens = derive_affected_tokens(_snapshot(), ["Binsc"])
    assert {"Binsc", "Binsn"} <= tokens
    assert "sc" not in tokens, "a balancer edit does not change the sc marker itself"


def test_an_unknown_key_still_yields_its_own_token():
    assert derive_affected_tokens(_snapshot(), ["nosuchmarker"]) == {"nosuchmarker"}


def test_a_removed_balancer_alias_is_swept_via_the_previous_document():
    """Post-edit the alias is gone, so only the pre-edit document knows a
    genotype saying 'Binsn' is now affected."""
    previous = {"Key": "Binsc", "kind": "balancer",
                "match": {"symbol": "Binsc", "aliases": ["Binsn"]}}
    tokens = derive_affected_tokens(_snapshot(), ["Binsc"], previous_documents=[previous])
    assert {"Binsc", "Binsn"} <= tokens


def test_a_removed_alias_row_is_swept_via_its_previous_target():
    previous = {"Key": "Gla", "kind": "alias", "match": {"token": "Gla"},
                "payload": {"alias_type": "allele_token", "value": "wg[Gla-1]"}}
    tokens = derive_affected_tokens(_snapshot(), ["Gla"], previous_documents=[previous])
    assert {"Gla", "wg[Gla-1]"} <= tokens


def test_a_construct_marker_edit_is_not_scopable():
    assert derive_affected_tokens(_snapshot(), ["construct:w+"]) is None


def test_deleting_a_shipped_override_is_not_scopable():
    assert derive_affected_tokens(_snapshot(), ["Cy"], deleted_override_keys=["Cy"]) is None


def test_no_keys_yields_an_empty_scope_not_a_full_rebuild():
    assert derive_affected_tokens(_snapshot(), []) == set()


def test_build_affected_query_escapes_regex_metacharacters():
    query = build_affected_query({"wg[Gla-1]"}, genotype_fields=("Genotype",))
    pattern = query["$or"][0]["Genotype"]["$regex"]
    assert r"\[" in pattern and r"\]" in pattern


def test_build_affected_query_is_case_insensitive():
    """Genotypes reach markers through the lowercase-normalised FlyBase alias
    index, so 'sco' must match a sweep of 'Sco'."""
    query = build_affected_query({"Sco"}, genotype_fields=("Genotype",))
    assert query["$or"][0]["Genotype"]["$options"] == "i"


def test_build_affected_query_covers_every_field():
    query = build_affected_query({"Cy"}, genotype_fields=("MaleGenotype", "FemaleGenotype"))
    assert {clause_field for clause in query["$or"] for clause_field in clause} == \
        {"MaleGenotype", "FemaleGenotype"}


def test_build_affected_query_with_no_tokens_matches_nothing():
    assert build_affected_query(set(), genotype_fields=("Genotype",)) is None


def test_a_flybase_alias_spelling_is_swept(monkeypatch):
    """resolver.py resolves a bare allele spec through the FlyBase alias
    index straight into a catalog row, so editing that row must sweep the
    bare spelling. Missing it would leave those records stale forever once
    the rebuild stamps them as current."""
    monkeypatch.setattr(
        "flymanager.utils.phenotypes.flybase_pipeline.get_flybase_phenotype_cache",
        lambda *args, **kwargs: {"marker_alias_index": {"bc": {"canonical_token": "PPO1[Bc]"}}},
    )
    tokens = derive_affected_tokens(_snapshot(), ["PPO1[Bc]"])
    assert {"PPO1[Bc]", "bc"} <= tokens


def test_an_unreadable_flybase_cache_does_not_break_scoping(monkeypatch):
    """The marker write path must not fail because reference data is absent."""
    def boom(*args, **kwargs):
        raise RuntimeError("evidence cache unavailable")

    monkeypatch.setattr(
        "flymanager.utils.phenotypes.flybase_pipeline.get_flybase_phenotype_cache", boom)
    assert derive_affected_tokens(_snapshot(), ["Cy"]) >= {"Cy", "CyO"}
