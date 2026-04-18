from flymanager.utils.constraints import (assess_marker_stability,
                                          estimate_target_yield,
                                          evaluate_interchromosomal_risk,
                                          select_optimal_balancer,
                                          validate_target)


class FakeCollection:
    def __init__(self, documents=None):
        self.documents = list(documents or [])
        self.indexes = []

    def find(self, query=None):
        query = query or {}
        return [
            dict(document)
            for document in self.documents
            if self._matches(document, query)
        ]

    def find_one(self, query):
        for document in self.find(query):
            return dict(document)
        return None

    def create_index(self, keys, name=None):
        self.indexes.append({"keys": keys, "name": name})

    def _matches(self, document, query):
        for key, value in query.items():
            if document.get(key) != value:
                return False
        return True


class FakeDatabase(dict):
    def __getitem__(self, name):
        if name not in self:
            self[name] = FakeCollection()
        return dict.__getitem__(self, name)


def test_assess_marker_stability_penalizes_tm6b_tubby_and_keeps_cy_high():
    cy = assess_marker_stability("Cy")
    tubby = assess_marker_stability("Tb", balancer_symbol="TM6B")
    bar = assess_marker_stability("B")

    assert cy["score"] == 0.95
    assert tubby["score"] == 0.35
    assert bar["score"] == 0.4
    assert cy["score"] > bar["score"] > tubby["score"]


def test_evaluate_interchromosomal_risk_flags_three_balancers_as_high_risk():
    report = evaluate_interchromosomal_risk(
        "; CyO/+; TM3/+; TM6B/+",
        target_sex="female",
    )

    assert report["balancer_count"] == 3
    assert report["risk_label"] == "high"
    assert any("Multiple balancers" in warning for warning in report["warnings"])


def test_select_optimal_balancer_prefers_candidate_with_closer_distal_breakpoint():
    db = FakeDatabase()
    db["flybase_gene_map"] = FakeCollection(
        [
            {
                "current_symbol": "foo",
                "cytogenetic_loc": "55C",
                "recombination_loc": "2-55",
            }
        ]
    )
    db["balancer_definitions"] = FakeCollection(
        [
            {
                "symbol": "CyO",
                "chromosome": "2",
                "breakpoint_regions": ["22D", "58B"],
                "marker_tokens": ["Cy", "cn", "pr"],
            },
            {
                "symbol": "SM6a",
                "chromosome": "2",
                "breakpoint_regions": ["50C", "56D"],
                "marker_tokens": ["al", "Cy", "cn", "speck"],
            },
        ]
    )

    selection = select_optimal_balancer("foo", 2, db)

    assert selection["selected_balancer"]["symbol"] == "SM6a"
    assert selection["used_fallback"] is False
    assert selection["candidates"][0]["breakpoint_score"] >= selection["candidates"][1]["breakpoint_score"]


def test_select_optimal_balancer_falls_back_to_default_when_gene_position_is_missing():
    selection = select_optimal_balancer("unknown_gene", 2, FakeDatabase())

    assert selection["selected_balancer"]["symbol"] == "CyO"
    assert selection["used_fallback"] is True


def test_validate_target_rejects_homozygous_balancer_targets():
    report = validate_target("; CyO; +; +", "female")

    assert report["viable"] is False
    assert report["maintainable"] is False
    assert report["suggested_alternative"] == "; +/CyO; +; +"
    assert any("impossible balancer target" in issue or "fixed for balancer" in issue for issue in report["issues"])


def test_validate_target_flags_linked_cis_targets_as_recombination_dependent_when_not_in_sources():
    report = validate_target(
        "w[1118]; P{A}attP40 P{B}attP40/CyO; +; +",
        "female",
        available_stock_genotypes=["w[1118]; P{A}attP40/CyO; +; +"],
    )

    assert report["requires_recombination"] is True
    assert report["unsupported_reasons"]
    assert any("female meiosis" in issue for issue in report["unsupported_reasons"])


def test_validate_target_marks_male_endpoint_as_non_self_propagating():
    report = validate_target("w[1118]; +; +; +", "male")

    assert report["viable"] is True
    assert report["maintainable"] is False
    assert "male endpoint" in report["maintenance_strategy"]


def test_estimate_target_yield_penalizes_recombination_and_multi_balancer_complexity():
    simple = estimate_target_yield("w[1118]; CyO/+; +; +", "female")
    complex_target = estimate_target_yield(
        "w[1118]; P{A}attP40 P{B}attP40/CyO; TM3/+; +",
        "female",
        available_stock_genotypes=["w[1118]; P{A}attP40/CyO; +; +"],
    )

    assert simple["yield_label"] in {"moderate", "high"}
    assert complex_target["yield_label"] == "low"
    assert complex_target["expected_targets_per_vial"] < simple["expected_targets_per_vial"]
    assert complex_target["recommended_parallel_vials"] >= simple["recommended_parallel_vials"]