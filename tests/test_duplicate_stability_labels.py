"""Two definitions sharing a display label collide in the stability indexes.

Those indexes are keyed by display label, not by Key, so the last definition
indexed wins by dict order and the other's stability score or contextual cap
silently does nothing. Changing the key would change resolution, so the
collision is not fixed here -- it is reported.
"""
import pytest

from flymanager.utils.phenotypes import marker_catalog


@pytest.fixture(autouse=True)
def _reset():
    marker_catalog.reset_catalog()
    yield
    marker_catalog.reset_catalog()


def _definition(key, label, sorting):
    return {
        "Key": key, "kind": "gene_marker", "match": {"symbol": key},
        "payload": {"display_label": label, "effect": "x",
                    "dominance": "dominant", "body_part": "wing",
                    "phenotype_key": key, "chromosome": 2},
        "sorting": sorting,
    }


def _compile(overlay):
    snapshot = marker_catalog.compile_catalog(
        marker_catalog.load_shipped_catalog(), overlay)
    marker_catalog.set_catalog(snapshot)
    return snapshot


def test_the_shipped_catalog_has_no_duplicate_labels():
    assert marker_catalog.get_catalog()["duplicate_stability_labels"] == {}


def test_two_definitions_claiming_one_label_are_reported():
    snapshot = _compile([
        _definition("zza", "Dup", {"stabilityScore": 0.9}),
        _definition("zzb", "Dup", {"stabilityScore": 0.1}),
    ])
    assert snapshot["duplicate_stability_labels"] == {"Dup": ["zza", "zzb"]}


def test_a_contextual_rule_counts_as_a_claim_too():
    """A definition with a cap and no base score writes nothing to the flat
    stability table, but still collides on the contextual index."""
    snapshot = _compile([
        _definition("zzc", "Dup", {"stabilityScore": 0.9}),
        _definition("zzd", "Dup", {"contextualStability": [
            {"whenBalancer": ["CyO"], "maxScore": 0.2}]}),
    ])
    assert snapshot["duplicate_stability_labels"] == {"Dup": ["zzc", "zzd"]}


def test_a_definition_with_no_sorting_does_not_claim_a_label():
    snapshot = _compile([
        _definition("zze", "Dup", {"stabilityScore": 0.9}),
        _definition("zzf", "Dup", {}),
    ])
    assert snapshot["duplicate_stability_labels"] == {}


def test_distinct_labels_are_not_reported():
    snapshot = _compile([
        _definition("zzg", "One", {"stabilityScore": 0.9}),
        _definition("zzh", "Two", {"stabilityScore": 0.1}),
    ])
    assert snapshot["duplicate_stability_labels"] == {}


def test_the_scratch_key_does_not_leak_into_the_snapshot():
    assert "_stability_label_claims" not in marker_catalog.get_catalog()
