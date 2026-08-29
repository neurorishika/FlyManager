"""Every marker consumer must read the live snapshot, not an import-time copy."""
import pytest

from flymanager.utils.constraints.balancer_selection import \
    _candidate_documents
from flymanager.utils.phenotypes import marker_catalog
from flymanager.utils.phenotypes.resolver import resolve_package_markers
from tests.mongo_fakes import FakeDatabase


@pytest.fixture(autouse=True)
def _reset_catalog():
    marker_catalog.reset_catalog()
    yield
    marker_catalog.reset_catalog()


def _install_user_balancer_and_marker():
    shipped = marker_catalog.load_shipped_catalog()
    overlay = [
        {
            "Key": "zz",
            "kind": "gene_marker",
            "match": {"symbol": "zz"},
            "payload": {"body_part": "wing", "effect": "zigzag wings",
                        "dominance": "dominant", "display_label": "zz",
                        "phenotype_key": "zz", "chromosome": 3,
                        "scoring_confidence": 0.8},
            "sorting": {}, "audit": {}, "imaging": {}, "expression": {},
            "provenance": {"source": "user"}, "origin": "user",
        },
        {
            "Key": "ZZ7",
            "kind": "balancer",
            "match": {"symbol": "ZZ7", "aliases": []},
            "payload": {"family": "ZZ7", "chromosome": 3,
                        "default_markers": ["zz"], "notes": []},
            "sorting": {}, "audit": {}, "imaging": {}, "expression": {},
            "provenance": {"source": "user"}, "origin": "user",
        },
    ]
    marker_catalog.set_catalog(marker_catalog.compile_catalog(shipped, overlay))


def test_resolver_resolves_a_user_balancers_markers():
    _install_user_balancer_and_marker()
    resolved = resolve_package_markers("ZZ7")
    labels = [marker["display_label"] for marker in resolved["markers"]]
    assert labels == ["zz"]
    assert resolved["markers"][0]["balancer_symbol"] == "ZZ7"


def test_balancer_selection_sees_a_user_balancer():
    _install_user_balancer_and_marker()
    candidates = _candidate_documents(3, FakeDatabase({"balancer_definitions": []}))
    assert "ZZ7" in {candidate.get("symbol") for candidate in candidates}


def test_standardization_stops_flagging_a_token_once_it_is_a_marker():
    from flymanager.app.services.stock_standardization import \
        summarize_genotype_standardization

    before = summarize_genotype_standardization("zz")
    assert "zz" in before["topTokens"]

    _install_user_balancer_and_marker()
    after = summarize_genotype_standardization("zz")
    assert "zz" not in after["topTokens"]
