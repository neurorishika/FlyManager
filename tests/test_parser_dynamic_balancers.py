import pytest

from flymanager.utils.phenotypes import marker_catalog
from flymanager.utils.phenotypes.parser import parse_gene_package


@pytest.fixture(autouse=True)
def _reset_catalog():
    marker_catalog.reset_catalog()
    yield
    marker_catalog.reset_catalog()


def _install_extra_balancer():
    """Add a balancer to the catalog *after* the parser module was imported."""
    shipped = marker_catalog.load_shipped_catalog()
    overlay = [{
        "Key": "ZZ7",
        "kind": "balancer",
        "match": {"symbol": "ZZ7", "aliases": ["ZZ7b"]},
        "payload": {"family": "ZZ7", "chromosome": 3,
                    "default_markers": ["Sb"], "notes": []},
        "sorting": {}, "audit": {}, "imaging": {}, "expression": {},
        "provenance": {"source": "user"}, "origin": "user",
    }]
    marker_catalog.set_catalog(marker_catalog.compile_catalog(shipped, overlay))


def test_shipped_balancer_is_still_recognised():
    parsed = parse_gene_package("CyO")
    assert [b["symbol"] for b in parsed["balancers"]] == ["CyO"]
    assert parsed["balancers"][0]["default_markers"] == ["Cy", "pr", "cn"]


def test_shipped_balancer_alias_normalises():
    parsed = parse_gene_package("Binsn")
    assert parsed["balancers"][0]["symbol"] == "Binsc"


def test_user_balancer_added_after_import_is_recognised():
    assert parse_gene_package("ZZ7")["balancers"] == []
    _install_extra_balancer()
    parsed = parse_gene_package("ZZ7")
    assert [b["symbol"] for b in parsed["balancers"]] == ["ZZ7"]
    assert parsed["balancers"][0]["default_markers"] == ["Sb"]


def test_user_balancer_alias_added_after_import_normalises():
    _install_extra_balancer()
    assert parse_gene_package("ZZ7b")["balancers"][0]["symbol"] == "ZZ7"


def test_inversion_token_matches_longest_symbol_first():
    _install_extra_balancer()
    parsed = parse_gene_package("In(2LR)SM6a")
    assert parsed["balancers"][0]["symbol"] == "SM6a"
