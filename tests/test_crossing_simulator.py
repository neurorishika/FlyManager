import pytest

from flymanager.utils.crossing import (cross_genotypes_with_phenotypes,
                                       evaluate_cross_directions,
                                       simulate_cross)
from flymanager.utils.phenotypes import annotate_offspring_predictions
from flymanager.utils.phenotypes.identifiability import check_identifiability


def test_check_identifiability_warns_when_mini_white_is_only_discriminator():
    progeny = annotate_offspring_predictions(
        [
            ["w[1118]; P{w[+mC]=Orco-GAL4.W}11.17/+; +; +", "female", 0.5],
            ["w[1118]; +; +; +", "female", 0.5],
        ]
    )

    report = check_identifiability(progeny, [0])
    result = report["results"][0]

    assert result["identifiable"] is True
    assert result["depends_on_low_confidence_sorting"] is True
    assert result["distinguishing_features"] == ["mini-white pale orange"]
    assert any(
        "mini-white dosage is the sole predicted discriminator" in warning
        for warning in result["warnings"]
    )


def test_cross_genotypes_with_phenotypes_enriches_rows_with_validation_and_viability():
    rows = cross_genotypes_with_phenotypes("; CyO/+; +; +", "; CyO/+; +; +")

    assert rows
    assert all("phenotype" in row for row in rows)
    assert all("validation" in row for row in rows)
    assert any(row["is_viable"] is False for row in rows)
    assert any(row["identifiability"]["label"] for row in rows)


def test_simulate_cross_prunes_inviable_classes_and_renormalizes_viable_fraction():
    simulation = simulate_cross("; CyO/+; +; +", "; CyO/+; +; +")

    assert simulation["summary"]["pruned_class_count"] == 2
    assert simulation["summary"]["viable_fraction"] == pytest.approx(0.75)
    assert sorted(
        row["viable_probability"] for row in simulation["viable_offspring"]
    ) == pytest.approx([1 / 6, 1 / 6, 1 / 3, 1 / 3], rel=1e-3)
    assert any(
        "impossible maintenance target" in reason or "impossible balancer target" in reason
        for row in simulation["pruned_offspring"]
        for reason in row["prune_reasons"]
    )


def test_evaluate_cross_directions_prefers_informative_x_linked_direction():
    evaluation = evaluate_cross_directions("w[1118]; +; +; +", "; +; +; +")

    assert evaluation["recommended_direction"] == "reverse"
    assert evaluation["recommended_cross"] == {
        "male": "; +; +; +",
        "female": "w[1118]; +; +; +",
    }
    assert (
        evaluation["reverse"]["summary"]["estimated_sortable_targets_per_vial"]
        > evaluation["forward"]["summary"]["estimated_sortable_targets_per_vial"]
    )
    assert any("Reverse order" in line for line in evaluation["rationale"])