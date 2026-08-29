import pytest

from flymanager.utils.phenotypes import marker_catalog


@pytest.fixture(autouse=True)
def _reset_catalog():
    marker_catalog.reset_catalog()
    yield
    marker_catalog.reset_catalog()


def test_get_catalog_compiles_the_shipped_file_without_mongo():
    snapshot = marker_catalog.get_catalog()
    assert snapshot["gene_markers"]["Cy"]["display_label"] == "Cy"
    assert snapshot["signature"]


def test_get_catalog_is_memoized():
    assert marker_catalog.get_catalog() is marker_catalog.get_catalog()


def test_set_catalog_replaces_the_snapshot():
    replacement = marker_catalog.compile_catalog(
        {"catalogVersion": 9, "definitions": []}, [])
    marker_catalog.set_catalog(replacement)
    assert marker_catalog.get_catalog()["catalogVersion"] == 9


def test_reset_catalog_forces_a_recompile():
    marker_catalog.set_catalog(marker_catalog.compile_catalog(
        {"catalogVersion": 9, "definitions": []}, []))
    marker_catalog.reset_catalog()
    assert marker_catalog.get_catalog()["catalogVersion"] == 1
