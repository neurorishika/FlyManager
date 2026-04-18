import gzip
import io
import json
from datetime import datetime
from pathlib import Path

import pytest
from flask import Flask

from flymanager.app.services.flybase import (get_flybase_reference_status,
                                             refresh_flybase_reference_data)
from flymanager.app.services.stock_standardization import (
    review_stock_standardization, search_flybase_standardization_candidates)
from flymanager.utils.phenotypes.compute import compute_marker_phenotype
from flymanager.utils.phenotypes.construct_markers import \
    extract_construct_markers
from flymanager.utils.phenotypes.data.downloads import (
    download_flybase_bundle, parse_flybase_bulkdata_html)
from flymanager.utils.phenotypes.data.examiner import (
    analyze_marker_coverage, audit_bloomington_csv,
    audit_live_stock_standardization, audit_stock_csv_standardization,
    generate_visible_marker_inventory, render_stock_standardization_markdown,
    render_visible_marker_inventory_markdown, summarize_tsv_file)
from flymanager.utils.phenotypes.flybase_pipeline import \
    clear_flybase_phenotype_cache
from flymanager.utils.phenotypes.parser import (parse_gene_package,
                                                tokenize_gene_package)
from flymanager.utils.phenotypes.predictor import (
    annotate_offspring_predictions, predict_individual_phenotype,
    predict_stock_phenotype, summarize_parent_phenotypes)
from flymanager.utils.phenotypes.resolver import resolve_package_markers
from flymanager.utils.phenotypes.visual_markers import (BALANCER_MARKERS,
                                                        get_balancer_metadata)


def _write_runtime_flybase_fixture(tmp_path):
    (tmp_path / "genotype_phenotype_data_fb_fixture.tsv").write_text(
        "genotype_symbols\tgenotype_FBids\tphenotype_name\tphenotype_id\tqualifier_names\tqualifier_ids\treference\n"
        "foo[1]\tFBal0000001\tvisible\tFBcv:0000354\trecessive|wing\tFBcv:0000298|FBbt:00005106\tFBrf0000001\n"
        "foo[1]\tFBal0000001\tsterile\tFBcv:0009999\tadult|recessive\tFBcv:0000001|FBcv:0000298\tFBrf0000004\n",
        encoding="utf-8",
    )
    (tmp_path / "stocks_FB_fixture.tsv").write_text(
        "FBgn\tFB_genotype\n"
        "FBgn0000001\tfoo[1]; P{UAS-GFP.U}; +; +\n",
        encoding="utf-8",
    )
    (tmp_path / "transgenic_construct_descriptions_fb_fixture.tsv").write_text(
        "\t".join(
            [
                "Allele symbol",
                "Allele ID",
                "Transgenic Construct (symbol)",
                "Transgenic Construct ID",
                "Transgenic Product class (term)",
                "Transgenic Product class ID",
                "Regulatory region (symbol)",
                "Regulatory region (id)",
                "Encoded product/tool (symbol)",
                "Encoded product/tool (id)",
                "Tagged with (symbol)",
                "Tagged with (id)",
                "Also carries (symbol)",
                "Also carries (id)",
                "Description (text)",
                "References",
                "Stocks (number)",
            ]
        )
        + "\n"
        + "\t".join(
            [
                "Avic\\GFP[UAS.cUa]",
                "FBal0117927",
                "P{UAS-GFP.U}",
                "FBtp0012966",
                "wild_type",
                "SO:0000817",
                "UAS",
                "FBto0000180",
                "GFP",
                "FBto0000031",
                "",
                "",
                "",
                "",
                "UAS regulatory sequences drive expression of GFP.",
                "FBrf0000002",
                "4",
            ]
        )
        + "\n",
        encoding="utf-8",
    )
    (tmp_path / "split_system_combinations_fb_fixture.tsv").write_text(
        "Symbol\tComponent_Alleles\tStocks\tReferences\n"
        "Split-GFP\tPartA|PartB\tFBst0000001:foo[1]; P{UAS-GFP.U}; +; +\tFBrf0000003\n",
        encoding="utf-8",
    )
    (tmp_path / "dmel_classical_and_insertion_allele_descriptions_fb_fixture.tsv").write_text(
        "Allele symbol\tAllele ID\tDescription\nfoo[1]\tFBal0000001\tFixture allele\n",
        encoding="utf-8",
    )


def test_resolve_package_markers_uses_cached_flybase_allele_evidence(monkeypatch, tmp_path):
    _write_runtime_flybase_fixture(tmp_path)
    monkeypatch.setenv("FLYMANAGER_FLYBASE_DATA_DIR", str(tmp_path))
    monkeypatch.setenv("FLYMANAGER_FLYBASE_CACHE_PATH", str(tmp_path / "cache.json"))
    clear_flybase_phenotype_cache()

    resolved = resolve_package_markers("foo[1]")

    assert resolved["unresolved_tokens"] == []
    assert [marker["display_label"] for marker in resolved["markers"]] == ["foo"]
    assert resolved["markers"][0]["source"] == "flybase_allele_evidence"


def test_predict_individual_phenotype_surfaces_construct_and_split_system_annotations(monkeypatch, tmp_path):
    _write_runtime_flybase_fixture(tmp_path)
    monkeypatch.setenv("FLYMANAGER_FLYBASE_DATA_DIR", str(tmp_path))
    monkeypatch.setenv("FLYMANAGER_FLYBASE_CACHE_PATH", str(tmp_path / "cache.json"))
    clear_flybase_phenotype_cache()

    prediction = predict_individual_phenotype("foo[1]; P{UAS-GFP.U}; +; +", "female")

    assert prediction["summary"] == "foo"
    assert prediction["construct_annotation_labels"] == ["GFP reporter"]
    assert prediction["split_system_labels"] == ["Split-GFP"]
    assert any("split-system combinations" in warning for warning in prediction["warnings"])


def test_predict_individual_phenotype_surfaces_flybase_consequences_and_provenance(monkeypatch, tmp_path):
    _write_runtime_flybase_fixture(tmp_path)
    monkeypatch.setenv("FLYMANAGER_FLYBASE_DATA_DIR", str(tmp_path))
    monkeypatch.setenv("FLYMANAGER_FLYBASE_CACHE_PATH", str(tmp_path / "cache.json"))
    clear_flybase_phenotype_cache()

    prediction = predict_individual_phenotype("foo[1]; +; +; +", "female")

    assert prediction["fertility_status"] == "reduced_or_sterile"
    assert prediction["sterile_alleles"] == ["foo"]
    assert prediction["stage_specific_effects"] == ["foo: adult"]
    assert prediction["provenance_summary"]["primary_basis"] == "inference_only"
    assert any("inference-only FlyBase evidence" in warning for warning in prediction["warnings"])


def test_tokenize_gene_package_respects_brackets_and_commas():
    tokens = tokenize_gene_package("TM3, Sb[1] Ser[1]")

    assert tokens == ["TM3", "Sb[1]", "Ser[1]"]


def test_parse_gene_package_classifies_constructs_balancers_and_alleles():
    parsed = parse_gene_package("TM3, Sb[1] P{w[+mC]=Orco-GAL4.W}11.17")

    assert parsed["balancers"][0]["symbol"] == "TM3"
    assert parsed["classical_alleles"][0]["gene_stem"] == "Sb"
    assert parsed["constructs"][0]["markers"][0]["gene_stem"] == "w"


def test_parse_gene_package_normalizes_balancer_aliases_and_trailing_punctuation():
    parsed = parse_gene_package("FM7h w[*]:: |P-cytotype|")

    assert parsed["balancers"][0]["symbol"] == "FM7h"
    assert parsed["classical_alleles"][0]["token"] == "w[*]"
    assert parsed["annotations"] == ["|P-cytotype|"]


def test_parse_gene_package_prefers_longest_matching_balancer_symbol_inside_inversion_tokens():
    parsed = parse_gene_package("In(1)FM7c")

    assert parsed["balancers"][0]["symbol"] == "FM7c"


def test_parse_gene_package_supports_parenthesized_gene_symbols():
    parsed = parse_gene_package("l(2)me[1]")

    assert parsed["classical_alleles"] == [
        {
            "token": "l(2)me[1]",
            "gene_stem": "l(2)me",
            "allele_spec": "1",
        }
    ]


def test_parse_gene_package_includes_rich_balancer_metadata():
    parsed = parse_gene_package("TM3, Sb[1]")
    metadata = parsed["balancers"][0]["metadata"]

    assert metadata["family"] == "TM3"
    assert metadata["chromosome"] == 3
    assert metadata["default_markers"] == ["p", "Ubx", "e"]
    assert any("Sb[1] and Ser[1]" in note for note in metadata["notes"])


def test_get_balancer_metadata_resolves_aliases_and_notes():
    fm6_metadata = get_balancer_metadata("FM6")
    alias_metadata = get_balancer_metadata("FM6B")
    tm3_metadata = get_balancer_metadata("TM3")

    assert alias_metadata == fm6_metadata
    assert "FM6B" in fm6_metadata["aliases"]
    assert tm3_metadata["family"] == "TM3"
    assert any("resolved separately" in note for note in tm3_metadata["notes"])


def test_extract_construct_markers_finds_mini_white():
    markers = extract_construct_markers("P{w[+mC]=Orco-GAL4.W}11.17")

    assert len(markers) == 1
    assert markers[0]["display_label"] == "mini-white"
    assert markers[0]["mini_white"] is True


def test_resolve_package_markers_combines_balancer_and_manual_dictionary_hits():
    resolved = resolve_package_markers("TM3, Sb[1] Ser[1]")
    labels = {marker["display_label"] for marker in resolved["markers"]}
    balancer_markers = [marker for marker in resolved["markers"] if marker.get("source") == "balancer_marker"]

    assert {"p", "Ubx", "e", "Sb", "Ser"}.issubset(labels)
    assert {marker["balancer_family"] for marker in balancer_markers} == {"TM3"}
    assert {marker["balancer_chromosome"] for marker in balancer_markers} == {3}
    assert any("resolved separately" in note for marker in balancer_markers for note in marker["balancer_notes"])
    assert resolved["unresolved_tokens"] == []


@pytest.mark.parametrize(
    ("balancer_symbol", "expected_markers"),
    sorted(BALANCER_MARKERS.items()),
)
def test_resolve_package_markers_matches_balancer_marker_table(balancer_symbol, expected_markers):
    resolved = resolve_package_markers(balancer_symbol)

    assert [marker["display_label"] for marker in resolved["markers"]] == expected_markers
    assert resolved["unresolved_tokens"] == []


def test_resolve_package_markers_resolves_bare_manual_marker_token():
    resolved = resolve_package_markers("w")

    assert resolved["unresolved_tokens"] == []
    assert [marker["display_label"] for marker in resolved["markers"]] == ["w"]


def test_resolve_package_markers_prefers_allele_specific_visible_marker_override():
    resolved = resolve_package_markers("wg[Sp-1]")

    assert resolved["unresolved_tokens"] == []
    assert [marker["display_label"] for marker in resolved["markers"]] == ["Sp"]
    assert resolved["markers"][0]["allele_specific"] is True


def test_resolve_package_markers_resolves_reviewed_bare_aliases_to_alleles():
    resolved = resolve_package_markers("Gla Sco")

    assert resolved["unresolved_tokens"] == []
    assert [marker["display_label"] for marker in resolved["markers"]] == ["Gla", "Sco"]
    assert {marker.get("allele_token") for marker in resolved["markers"]} == {"wg[Gla-1]", "sna[Sco]"}


def test_resolve_package_markers_leaves_bare_sp_unresolved():
    resolved = resolve_package_markers("Sp")

    assert resolved["markers"] == []
    assert resolved["unresolved_tokens"] == ["Sp"]


def test_resolve_package_markers_does_not_overcall_non_override_wg_alleles():
    resolved = resolve_package_markers("wg[1]")

    assert resolved["markers"] == []
    assert resolved["unresolved_tokens"] == ["wg[1]"]


def test_resolve_package_markers_reports_unknown_classical_alleles_as_unresolved():
    resolved = resolve_package_markers("mystery[1]")

    assert resolved["markers"] == []
    assert resolved["unresolved_tokens"] == ["mystery[1]"]


def test_resolve_package_markers_resolves_additional_curated_visible_alleles():
    resolved = resolve_package_markers("amos[Roi-1] PPO1[Bc] l(2)me[1] wa")

    assert resolved["unresolved_tokens"] == []
    assert [marker["display_label"] for marker in resolved["markers"]] == ["Roi", "Bc", "me", "wa"]
    assert {marker.get("allele_token") for marker in resolved["markers"] if marker.get("allele_specific")} == {
        "amos[Roi-1]",
        "PPO1[Bc]",
        "l(2)me[1]",
    }


def test_compute_marker_phenotype_handles_x_linked_recessive_expression_rules():
    male_summary = compute_marker_phenotype("w[1118]; +; +; +", "male")
    female_summary = compute_marker_phenotype("+/w[1118]; +; +; +", "female")

    assert any(marker["display_label"] == "w" for marker in male_summary["expressed_markers"])
    assert all(marker["display_label"] != "w" for marker in female_summary["expressed_markers"])


def test_compute_marker_phenotype_tracks_construct_markers_and_dominant_balancers():
    summary = compute_marker_phenotype("; CyO/P{w[+mC]=Orco-GAL4.W}11.17; Sb[1]/+; +", "female")
    labels = {marker["display_label"] for marker in summary["expressed_markers"]}

    assert "Cy" in labels
    assert "Sb" in labels
    assert summary["mini_white_copy_count"] == 1


def test_compute_marker_phenotype_expresses_allele_specific_dominant_markers():
    summary = compute_marker_phenotype("+; wg[Sp-1]/+; +; +", "female")

    assert [marker["display_label"] for marker in summary["expressed_markers"]] == ["Sp"]


def test_compute_marker_phenotype_applies_cn_bw_epistasis():
    summary = compute_marker_phenotype("+; cn[1] bw[1]; +; +", "female")

    assert [marker["display_label"] for marker in summary["expressed_markers"]] == ["cn+bw"]
    assert summary["epistasis_events"][0]["rule"] == "cn_bw_white_eyes"


def test_compute_marker_phenotype_rewrites_white_plus_mini_white_to_single_rescue_marker():
    summary = compute_marker_phenotype(
        "w[1118]; P{w[+mC]=Orco-GAL4.W}11.17/+; +; +",
        "female",
    )

    assert [marker["display_label"] for marker in summary["expressed_markers"]] == ["mini-white pale orange"]
    assert summary["epistasis_events"][0]["rule"] == "w_mini_white_rescue"
    assert {marker["display_label"] for marker in summary["suppressed_markers"]} == {"w", "mini-white"}
    assert summary["mini_white_copy_count"] == 1
    assert summary["mini_white_intensity"] == "pale_orange"


def test_predict_individual_phenotype_adds_confidence_and_warnings():
    prediction = predict_individual_phenotype(
        "; CyO/P{w[+mC]=Orco-GAL4.W}11.17; Sb[1]/+; +",
        "female",
    )

    assert prediction["summary"] == "Cy, mini-white, Sb"
    assert prediction["confidence_label"] in {"high", "medium"}
    assert prediction["warnings"] == []


def test_predict_individual_phenotype_reports_mini_white_rescue_as_single_eye_state():
    prediction = predict_individual_phenotype(
        "w[1118]; P{w[+mC]=Orco-GAL4.W}11.17/+; +; +",
        "female",
    )

    assert prediction["summary"] == "mini-white pale orange"
    assert prediction["marker_labels"] == ["mini-white pale orange"]
    assert any(
        "mini-white rescue eye color is estimated from copy count" in warning
        for warning in prediction["warnings"]
    )


def test_predict_stock_phenotype_flags_projection_limitations_for_heterozygous_x():
    prediction = predict_stock_phenotype("+/w[1118]; +; +; +")

    assert prediction["best_guess_summary"] == "No marker phenotype predicted"
    assert prediction["projection_limited"] is True
    assert prediction["male_summary"] is None
    assert any(
        "Male phenotype cannot be projected uniquely" in warning
        for warning in prediction["warnings"]
    )


def test_predict_stock_phenotype_reports_shared_markers_when_both_sexes_match():
    prediction = predict_stock_phenotype("w[1118]; CyO/+; +; +")

    assert prediction["best_guess_summary"] == "w, Cy"
    assert prediction["female_summary"] == "w, Cy"
    assert prediction["male_summary"] == "w, Cy"
    assert prediction["shared_marker_labels"] == ["w", "Cy"]


def test_summarize_parent_phenotypes_combines_male_and_female_predictions():
    parent_summary = summarize_parent_phenotypes(
        "w[1118]; CyO/+; +; +",
        "+; +; Sb[1]/+; +",
    )

    assert parent_summary["male"]["summary"] == "w, Cy"
    assert parent_summary["female"]["summary"] == "Sb"
    assert parent_summary["summary"] == "Male w, Cy / Female Sb"


def test_annotate_offspring_predictions_enriches_probability_rows():
    annotated = annotate_offspring_predictions(
        [
            ["w[1118]; CyO/+; +; +", "male", 0.25],
            ["+; +; Sb[1]/+; +", "female", 0.75],
        ]
    )

    assert annotated[0]["phenotype_summary"] == "w, Cy"
    assert annotated[0]["probability_percent"] == 25.0
    assert annotated[1]["phenotype_summary"] == "Sb"


def test_annotate_offspring_predictions_flags_confusable_sibling_classes():
    annotated = annotate_offspring_predictions(
        [
            ["w[1118]; CyO/+; +; +", "female", 0.25],
            ["w[1118]; CyO/+; +; +", "female", 0.25],
            ["+; +; Sb[1]/+; +", "female", 0.5],
        ]
    )

    assert annotated[0]["identifiability"]["identifiable"] is False
    assert annotated[0]["identifiability"]["confusable_genotypes"] == ["w[1118]; CyO/+; +; +"]
    assert annotated[2]["identifiability"]["identifiable"] is True


def test_generate_visible_marker_inventory_intersects_visible_rows_with_bloomington_usage(tmp_path):
    phenotype_path = tmp_path / "genotype_phenotype_data.tsv"
    phenotype_path.write_text(
        "#comment\n"
        "genotype_symbols\tgenotype_FBids\tphenotype_name\tphenotype_id\tqualifier_names\tqualifier_ids\treference\n"
        "v[1]\tFBal0000001\tvisible\tFBcv:0000354\trecessive\tFBcv:0000298\tFBrf0000001\n"
        "v[1]\tFBal0000001\tvisible\tFBcv:0000354\trecessive|wing\tFBcv:0000298|FBbt:00005106\tFBrf0000002\n"
        "pr[1]\tFBal0000002\tvisible\tFBcv:0000354\twith genotype\tFBcv:9999999\tFBrf0000003\n",
        encoding="utf-8",
    )

    csv_path = tmp_path / "bloomington.csv"
    csv_path.write_text(
        'Stk #,Ch # all,Genotype\n1,1,"v[1]"\n2,2,"pr[1]"\n3,1,"v[1]"\n',
        encoding="utf-8",
    )

    report = generate_visible_marker_inventory(
        phenotype_path,
        csv_path,
        high_priority_threshold=2,
    )

    assert report["summary"]["visible_rows"] == 3
    assert report["summary"]["unconditional_visible_rows"] == 2
    assert report["summary"]["compatible_visible_gene_stems"] == 1
    assert report["top_gene_stems"][0]["gene_stem"] == "v"
    assert report["top_gene_stems"][0]["compatible_gene_occurrences"] == 2
    assert report["top_gene_stems"][0]["curation_bucket"] == "high"
    assert report["top_alleles"][0]["token"] == "v[1]"
    assert report["body_part_ids"][0][0] == "FBBT:00005106"


def test_render_visible_marker_inventory_markdown_includes_curation_queue(tmp_path):
    phenotype_path = tmp_path / "genotype_phenotype_data.tsv"
    phenotype_path.write_text(
        "genotype_symbols\tgenotype_FBids\tphenotype_name\tphenotype_id\tqualifier_names\tqualifier_ids\treference\n"
        "v[1]\tFBal0000001\tvisible\tFBcv:0000354\trecessive\tFBcv:0000298\tFBrf0000001\n",
        encoding="utf-8",
    )
    csv_path = tmp_path / "bloomington.csv"
    csv_path.write_text(
        'Stk #,Ch # all,Genotype\n1,1,"v[1]"\n',
        encoding="utf-8",
    )

    report = generate_visible_marker_inventory(
        phenotype_path,
        csv_path,
        high_priority_threshold=1,
    )
    markdown = render_visible_marker_inventory_markdown(report)

    assert "# Visible Marker Inventory" in markdown
    assert "## Curation Queue" in markdown
    assert "v: compatible=1" in markdown


def test_predict_stock_phenotype_recognizes_new_v_marker_and_v_plus_construct_support():
    stock_prediction = predict_stock_phenotype("v[1]; +; +; +")
    construct_prediction = predict_individual_phenotype(
        "P{v[+t1.8]=rescue}; +; +; +",
        "female",
    )

    assert stock_prediction["best_guess_summary"] == "v"
    assert construct_prediction["summary"] == "v+"


def test_predict_stock_phenotype_surfaces_projection_annotations(monkeypatch, tmp_path):
    _write_runtime_flybase_fixture(tmp_path)
    monkeypatch.setenv("FLYMANAGER_FLYBASE_DATA_DIR", str(tmp_path))
    monkeypatch.setenv("FLYMANAGER_FLYBASE_CACHE_PATH", str(tmp_path / "cache.json"))
    clear_flybase_phenotype_cache()

    prediction = predict_stock_phenotype("foo[1]; P{UAS-GFP.U}; +; +")

    assert prediction["female_construct_annotation_labels"] == ["GFP reporter"]
    assert prediction["male_construct_annotation_labels"] == ["GFP reporter"]
    assert prediction["female_split_system_labels"] == ["Split-GFP"]
    assert prediction["male_split_system_labels"] == ["Split-GFP"]


def test_summarize_tsv_file_skips_comment_rows(tmp_path):
    file_path = tmp_path / "genotype_phenotype_data_current.tsv"
    file_path.write_text(
        "# comment\ncol1\tcol2\nvalue1\tvalue2\n",
        encoding="utf-8",
    )

    summary = summarize_tsv_file(file_path)

    assert summary["row_count"] == 1
    assert summary["columns"] == ["col1", "col2"]


def test_summarize_tsv_file_reads_gzipped_tsv(tmp_path):
    file_path = tmp_path / "genotype_phenotype_data_fb_2026_01.tsv.gz"
    with gzip.open(file_path, "wt", encoding="utf-8") as handle:
        handle.write("# comment\ncol1\tcol2\nvalue1\tvalue2\n")

    summary = summarize_tsv_file(file_path)

    assert summary["exists"] is True
    assert summary["row_count"] == 1
    assert summary["columns"] == ["col1", "col2"]


def test_summarize_tsv_file_uses_explicit_hash_header_after_metadata(tmp_path):
    file_path = tmp_path / "stocks_FB2026_01.tsv.gz"
    with gzip.open(file_path, "wt", encoding="utf-8") as handle:
        handle.write(
            "## report metadata\n"
            "#col1\tcol2\tcol3\n"
            "value1\tvalue2\tvalue3\n"
        )

    summary = summarize_tsv_file(file_path)

    assert summary["row_count"] == 1
    assert summary["columns"] == ["col1", "col2", "col3"]


def test_analyze_marker_coverage_counts_visible_and_unconditional_rows(tmp_path):
    file_path = tmp_path / "genotype_phenotype_data_current.tsv"
    file_path.write_text(
        "# comment\nallele\tphenotype\tqualifier\nCy[1]\tvisible wing phenotype\tFBbt:0000001\nCy[1]\tvisible wing phenotype\twith genotype\n",
        encoding="utf-8",
    )

    coverage, qualifier_examples = analyze_marker_coverage(file_path, ["Cy"])

    assert coverage["Cy"]["total_rows"] == 2
    assert coverage["Cy"]["visible_rows"] == 2
    assert coverage["Cy"]["anatomy_rows"] == 1
    assert coverage["Cy"]["unconditional_rows"] == 1
    assert len(qualifier_examples) == 2


def test_analyze_marker_coverage_reads_gzipped_input(tmp_path):
    file_path = tmp_path / "genotype_phenotype_data_fb_2026_01.tsv.gz"
    with gzip.open(file_path, "wt", encoding="utf-8") as handle:
        handle.write(
            "# comment\nallele\tphenotype\tqualifier\nCy[1]\tvisible wing phenotype\tFBbt:0000001\n"
        )

    coverage, qualifier_examples = analyze_marker_coverage(file_path, ["Cy"])

    assert coverage["Cy"]["total_rows"] == 1
    assert coverage["Cy"]["visible_rows"] == 1
    assert len(qualifier_examples) == 1


def test_analyze_marker_coverage_does_not_match_single_letter_markers_in_flybase_ids(tmp_path):
    file_path = tmp_path / "genotype_phenotype_data_fb_2026_01.tsv.gz"
    with gzip.open(file_path, "wt", encoding="utf-8") as handle:
        handle.write(
            "## metadata\n"
            "#genotype_symbols\tgenotype_FBids\tphenotype_name\tphenotype_id\tqualifier_names\tqualifier_ids\treference\n"
            "w[1118]\tFBal0000001\tvisible\tFBcv:0000354\t\t\tFBrf0000001\n"
        )

    coverage, _ = analyze_marker_coverage(file_path, ["f", "w"])

    assert coverage["f"]["total_rows"] == 0
    assert coverage["w"]["total_rows"] == 1


def test_resolve_package_markers_keeps_ambiguous_flybase_alias_unresolved(monkeypatch, tmp_path):
    (tmp_path / "genotype_phenotype_data_fb_fixture.tsv").write_text(
        "genotype_symbols\tgenotype_FBids\tphenotype_name\tphenotype_id\tqualifier_names\tqualifier_ids\treference\n"
        "foo[Zip]\tFBal0000001\tvisible\tFBcv:0000354\tdominant|wing\tFBcv:0000299|FBbt:00005106\tFBrf0000001\n"
        "bar[Zip]\tFBal0000002\tvisible\tFBcv:0000354\tdominant|eye\tFBcv:0000299|FBbt:00004508\tFBrf0000002\n",
        encoding="utf-8",
    )
    (tmp_path / "stocks_FB_fixture.tsv").write_text(
        "FBgn\tFB_genotype\n"
        "FBgn0000001\tfoo[Zip]; +; +; +\n"
        "FBgn0000002\tbar[Zip]; +; +; +\n",
        encoding="utf-8",
    )
    monkeypatch.setenv("FLYMANAGER_FLYBASE_DATA_DIR", str(tmp_path))
    monkeypatch.setenv("FLYMANAGER_FLYBASE_CACHE_PATH", str(tmp_path / "cache.json"))
    clear_flybase_phenotype_cache()

    resolved = resolve_package_markers("Zip")

    assert resolved["markers"] == []
    assert resolved["unresolved_tokens"] == ["Zip"]


def test_resolve_package_markers_resolves_additional_reviewed_bare_aliases():
    gla_resolved = resolve_package_markers("Gla")
    mio_resolved = resolve_package_markers("Mio")

    assert [marker["display_label"] for marker in gla_resolved["markers"]] == ["Gla"]
    assert gla_resolved["unresolved_tokens"] == []
    assert [marker["display_label"] for marker in mio_resolved["markers"]] == ["Mio"]
    assert mio_resolved["unresolved_tokens"] == []


def test_resolve_package_markers_treats_reviewed_construct_alias_as_standardized_construct():
    resolved = resolve_package_markers("OrCo-LexA")

    assert resolved["unresolved_tokens"] == []
    assert resolved["markers"] == []


def test_resolve_package_markers_uses_expanded_gene_level_marker_dictionary_for_classical_alleles():
    cases = {
        "Adc[b-1]": "Adc",
        "ca[1]": "ca",
        "Bar[1]": "Bar",
        "N[1]": "N",
        "hry[1]": "hry",
        "red[1]": "red",
        "speck[1]": "speck",
        "nub[1]": "nub",
    }

    for token, label in cases.items():
        resolved = resolve_package_markers(token)
        assert resolved["unresolved_tokens"] == []
        assert [marker["display_label"] for marker in resolved["markers"]] == [label]


def test_resolve_package_markers_prefers_allele_specific_entries_for_curated_bl_and_sr_alleles():
    cases = {
        "Bl[1]": "macrochaetae",
        "sr[1]": "flightless",
    }

    for token, effect_fragment in cases.items():
        resolved = resolve_package_markers(token)
        assert resolved["unresolved_tokens"] == []
        assert len(resolved["markers"]) == 1
        assert resolved["markers"][0]["allele_specific"] is True
        assert effect_fragment in resolved["markers"][0]["effect"]


def test_audit_bloomington_csv_reports_unresolved_tokens_and_markers(tmp_path):
    csv_path = tmp_path / "bloomington.csv"
    csv_path.write_text(
        "Stk #,Ch # all,Genotype\n1,2,TM3, Sb[1] Ser[1]\n2,2,P{w[+mC]=Orco-GAL4.W}11.17\n",
        encoding="utf-8",
    )

    # The genotype field contains commas, so rewrite with proper quoting.
    csv_path.write_text(
        'Stk #,Ch # all,Genotype\n1,2,"TM3, Sb[1] Ser[1]"\n2,2,"P{w[+mC]=Orco-GAL4.W}11.17"\n',
        encoding="utf-8",
    )

    audit = audit_bloomington_csv(csv_path)
    marker_labels = {label for label, _count in audit["top_markers"]}

    assert audit["total_packages"] == 2
    assert {"p", "Ubx", "e", "Sb", "Ser", "mini-white"}.issubset(marker_labels)
    assert audit["sample_resolutions"]


def test_audit_bloomington_csv_splits_ambiguous_and_unknown_unresolved_tokens(monkeypatch, tmp_path):
    (tmp_path / "genotype_phenotype_data_fb_fixture.tsv").write_text(
        "genotype_symbols\tgenotype_FBids\tphenotype_name\tphenotype_id\tqualifier_names\tqualifier_ids\treference\n"
        "foo[Zip]\tFBal0000001\tvisible\tFBcv:0000354\tdominant|wing\tFBcv:0000299|FBbt:00005106\tFBrf0000001\n"
        "bar[Zip]\tFBal0000002\tvisible\tFBcv:0000354\tdominant|eye\tFBcv:0000299|FBbt:00004508\tFBrf0000002\n",
        encoding="utf-8",
    )
    (tmp_path / "stocks_FB_fixture.tsv").write_text(
        "FBgn\tFB_genotype\n"
        "FBgn0000001\tfoo[Zip]; +; +; +\n"
        "FBgn0000002\tbar[Zip]; +; +; +\n",
        encoding="utf-8",
    )
    monkeypatch.setenv("FLYMANAGER_FLYBASE_DATA_DIR", str(tmp_path))
    monkeypatch.setenv("FLYMANAGER_FLYBASE_CACHE_PATH", str(tmp_path / "cache.json"))
    clear_flybase_phenotype_cache()

    csv_path = tmp_path / "bloomington.csv"
    csv_path.write_text(
        'Stk #,Ch # all,Genotype\n1,2,"Zip"\n2,2,"Mystery"\n',
        encoding="utf-8",
    )

    audit = audit_bloomington_csv(csv_path, data_dir=tmp_path)

    assert audit["top_ambiguous_unresolved_tokens"] == [("Zip", 1)]
    assert audit["top_unknown_unresolved_tokens"] == [("Mystery", 1)]


def test_audit_stock_csv_standardization_separates_safe_ambiguous_unknown_and_unmodeled(tmp_path):
    cache_path = tmp_path / "cache.json"
    cache_path.write_text(
        json.dumps(
            {
                "marker_alias_index": {
                    "zip": {"canonical_token": "foo[Zip]"},
                },
                "ambiguous_marker_aliases": {
                    "sp": ["wg[Sp-1]", "other[Sp]"],
                },
            }
        ),
        encoding="utf-8",
    )

    csv_path = tmp_path / "stocks.csv"
    csv_path.write_text(
        "UniqueID,Name,Genotype\n"
        'UID1,Reviewed,"w[1118]; Gla/+; +; +"\n'
        'UID2,CacheAlias,"+; Zip/+; +; +"\n'
        'UID3,Ambiguous,"+; Sp/+; +; +"\n'
        'UID4,Unknown,"+; Mystery/+; +; +"\n'
        'UID5,StandardButUnmodeled,"+; wg[1]/+; +; +"\n',
        encoding="utf-8",
    )

    report = audit_stock_csv_standardization(csv_path, cache_path=cache_path)

    assert report["safe_replacement_total"] == 2
    assert report["ambiguous_shorthand_total"] == 1
    assert report["unknown_token_total"] == 1
    assert report["unmodeled_standard_allele_total"] == 1
    assert report["safe_replacements"] == [
        {
            "token": "Gla",
            "count": 1,
            "samples": ["UID1: w[1118]; Gla/+; +; +"],
            "replacement": "wg[Gla-1]",
        },
        {
            "token": "Zip",
            "count": 1,
            "samples": ["UID2: +; Zip/+; +; +"],
            "replacement": "foo[Zip]",
        },
    ]
    assert report["ambiguous_shorthand"] == [
        {
            "token": "Sp",
            "count": 1,
            "samples": ["UID3: +; Sp/+; +; +"],
            "candidates": ["wg[Sp-1]", "other[Sp]"],
        }
    ]
    assert report["unknown_tokens"] == [
        {
            "token": "Mystery",
            "count": 1,
            "samples": ["UID4: +; Mystery/+; +; +"],
        }
    ]
    assert report["unmodeled_standard_alleles"] == [
        {
            "token": "wg[1]",
            "count": 1,
            "samples": ["UID5: +; wg[1]/+; +; +"],
        }
    ]


def test_render_stock_standardization_markdown_includes_sections(tmp_path):
    cache_path = tmp_path / "cache.json"
    cache_path.write_text(json.dumps({"marker_alias_index": {}, "ambiguous_marker_aliases": {}}), encoding="utf-8")
    csv_path = tmp_path / "stocks.csv"
    csv_path.write_text(
        "UniqueID,Genotype\n"
        'UID1,"w[1118]; Gla/+; +; +"\n',
        encoding="utf-8",
    )

    report = audit_stock_csv_standardization(csv_path, cache_path=cache_path)
    markdown = render_stock_standardization_markdown(report)

    assert "# Stock Token Standardization Audit" in markdown
    assert "## Safe Replacements" in markdown
    assert "Gla: replace with wg[Gla-1]" in markdown


class _FakeStandardizationCollection:
    def __init__(self, rows):
        self._rows = [dict(row) for row in rows]

    def find(self, _query=None, _projection=None):
        return [dict(row) for row in self._rows]


class _FakeStandardizationDatabase(dict):
    def __getitem__(self, name):
        return dict.__getitem__(self, name)


def test_audit_live_stock_standardization_reports_construct_aliases_and_ambiguous_tokens(tmp_path):
    cache_path = tmp_path / "cache.json"
    cache_path.write_text(
        json.dumps(
            {
                "marker_alias_index": {},
                "ambiguous_marker_aliases": {
                    "sp": ["wg[Sp-1]", "other[Sp]"],
                },
            }
        ),
        encoding="utf-8",
    )

    db = _FakeStandardizationDatabase(
        {
            "stocks": _FakeStandardizationCollection(
                [
                    {
                        "UniqueID": "UID1",
                        "Name": "Orco-LexA(3rd)",
                        "Genotype": "w; CyO/Sp; OrCo-LexA/TM3; ",
                    }
                ]
            )
        }
    )

    report = audit_live_stock_standardization(db, cache_path=cache_path)

    assert report["source_kind"] == "mongo"
    assert report["collection"] == "stocks"
    assert report["safe_replacements"] == [
        {
            "token": "OrCo-LexA",
            "count": 1,
            "samples": ["UID1: w; CyO/Sp; OrCo-LexA/TM3;"],
            "replacement": "P{Orco-LexA-VP16}unspecified",
        }
    ]
    assert report["ambiguous_shorthand"] == [
        {
            "token": "Sp",
            "count": 1,
            "samples": ["UID1: w; CyO/Sp; OrCo-LexA/TM3;"],
            "candidates": ["wg[Sp-1]", "other[Sp]"],
        }
    ]


def test_search_flybase_standardization_candidates_reads_allele_descriptions_and_constructs(monkeypatch, tmp_path):
    (tmp_path / "dmel_classical_and_insertion_allele_descriptions_fb_fixture.tsv").write_text(
        "\t".join(
            [
                "Allele (symbol)",
                "Allele (id)",
                "Gene (symbol)",
                "Gene (id)",
                "Allele Class (term)",
                "Allele Class (id)",
                "Insertion (symbol)",
                "Insertion (id)",
                "Unused 1",
                "Unused 2",
                "Regulatory region (symbol)",
                "Regulatory region (id)",
                "Encoded product/tool (symbol)",
                "Encoded product/tool (id)",
                "Unused 3",
                "Unused 4",
                "Unused 5",
                "Unused 6",
                "Description (text)",
                "Description (supporting reference)",
                "Stocks (number)",
            ]
        )
        + "\n"
        + "\t".join(
            [
                "P{Orco-LexA-VP16}unspecified",
                "FBal9990001",
                "Orco",
                "FBgn0000001",
                "transgenic_insertion",
                "SO:0001218",
                "P{Orco-LexA-VP16}",
                "FBti9990001",
                "",
                "",
                "Orco",
                "FBto0000001",
                "LexA-VP16",
                "FBto0000002",
                "",
                "",
                "",
                "",
                "Orco LexA driver insertion used for olfactory neuron targeting.",
                "FBrf9990001",
                "0",
            ]
        )
        + "\n",
        encoding="utf-8",
    )
    (tmp_path / "transgenic_construct_descriptions_fb_fixture.tsv").write_text(
        "\t".join(
            [
                "Component Allele (symbol)",
                "Component Allele (id)",
                "Transgenic Construct (symbol)",
                "Transgenic Construct (id)",
                "Transgenic Product class (term)",
                "Transgenic Product class (id)",
                "Regulatory region (symbol)",
                "Regulatory region (id)",
                "Encoded product/tool (symbol)",
                "Encoded product/tool (id)",
                "Tagged with (symbol)",
                "Tagged with (id)",
                "Also carries (symbol)",
                "Also carries (id)",
                "Description (text)",
                "Description (supporting reference)",
                "Stocks (number)",
            ]
        )
        + "\n"
        + "\t".join(
            [
                "P{Orco-LexA-VP16}unspecified",
                "FBal9990001",
                "P{Orco-LexA-VP16}",
                "FBtp9990001",
                "driver",
                "SO:0000800",
                "Orco",
                "FBto0000001",
                "LexA-VP16",
                "FBto0000002",
                "",
                "",
                "",
                "",
                "Orco regulatory sequences drive LexA-VP16.",
                "FBrf9990001",
                "0",
            ]
        )
        + "\n",
        encoding="utf-8",
    )
    monkeypatch.setenv("FLYMANAGER_FLYBASE_DATA_DIR", str(tmp_path))
    monkeypatch.setenv("FLYMANAGER_FLYBASE_CACHE_PATH", str(tmp_path / "cache.json"))
    clear_flybase_phenotype_cache()

    matches = search_flybase_standardization_candidates("OrCo-LexA", data_dir=tmp_path)

    assert matches
    assert matches[0]["canonical_token"] == "P{Orco-LexA-VP16}unspecified"
    assert matches[0]["candidate_kind"] == "construct"
    assert matches[0]["insertion_symbol"] == "P{Orco-LexA-VP16}"


def test_review_stock_standardization_combines_reviewed_aliases_ambiguity_and_fuzzy_flybase_candidates(monkeypatch, tmp_path):
    (tmp_path / "dmel_classical_and_insertion_allele_descriptions_fb_fixture.tsv").write_text(
        "\t".join(
            [
                "Allele (symbol)",
                "Allele (id)",
                "Gene (symbol)",
                "Gene (id)",
                "Allele Class (term)",
                "Allele Class (id)",
                "Insertion (symbol)",
                "Insertion (id)",
                "Unused 1",
                "Unused 2",
                "Regulatory region (symbol)",
                "Regulatory region (id)",
                "Encoded product/tool (symbol)",
                "Encoded product/tool (id)",
                "Unused 3",
                "Unused 4",
                "Unused 5",
                "Unused 6",
                "Description (text)",
                "Description (supporting reference)",
                "Stocks (number)",
            ]
        )
        + "\n"
        + "\t".join(
            [
                "P{Orco-LexA-VP16}unspecified",
                "FBal9990001",
                "Orco",
                "FBgn0000001",
                "transgenic_insertion",
                "SO:0001218",
                "P{Orco-LexA-VP16}",
                "FBti9990001",
                "",
                "",
                "Orco",
                "FBto0000001",
                "LexA-VP16",
                "FBto0000002",
                "",
                "",
                "",
                "",
                "Orco LexA driver insertion used for olfactory neuron targeting.",
                "FBrf9990001",
                "0",
            ]
        )
        + "\n",
        encoding="utf-8",
    )
    monkeypatch.setenv("FLYMANAGER_FLYBASE_DATA_DIR", str(tmp_path))
    monkeypatch.setenv("FLYMANAGER_FLYBASE_CACHE_PATH", str(tmp_path / "cache.json"))
    clear_flybase_phenotype_cache(cache_path=tmp_path / "cache.json")
    monkeypatch.setattr(
        "flymanager.app.services.stock_standardization.get_flybase_phenotype_cache",
        lambda *args, **kwargs: {
            "marker_alias_index": {},
            "ambiguous_marker_aliases": {"sp": ["wg[Sp-1]", "other[Sp]"]},
            "construct_annotations": {},
            "allele_markers": {},
            "split_systems": {},
            "allele_consequences": {},
        },
    )

    review = review_stock_standardization(
        "w; CyO/Sp; OrCo-LexA/TM3;",
        data_dir=tmp_path,
        cache_path=tmp_path / "cache.json",
    )

    assert review["issue_count"] == 2
    orco_issue = next(issue for issue in review["issues"] if issue["token"] == "OrCo-LexA")
    sp_issue = next(issue for issue in review["issues"] if issue["token"] == "Sp")
    assert orco_issue["recommended_replacement"] == "P{Orco-LexA-VP16}unspecified"
    assert orco_issue["recommended_source"] == "reviewed_alias"
    assert orco_issue["fuzzy_candidates"][0]["canonical_token"] == "P{Orco-LexA-VP16}unspecified"
    assert orco_issue["fuzzy_candidates"][0]["candidate_kind"] in {"reviewed_alias", "construct"}
    assert sp_issue["ambiguous_candidates"] == ["wg[Sp-1]", "other[Sp]"]


def test_search_flybase_standardization_candidates_ignores_short_partial_matches(monkeypatch):
    monkeypatch.setattr(
        "flymanager.app.services.stock_standardization._candidate_index",
        lambda **kwargs: (
            {
                "canonical_token": "P{Orco-LexA-VP16}unspecified",
                "candidate_kind": "construct",
                "display_label": "P{Orco-LexA-VP16}unspecified",
                "description": "Orco LexA driver insertion.",
                "flybase_id": "FBal9990001",
                "gene_symbol": "Orco",
                "insertion_symbol": "P{Orco-LexA-VP16}",
                "regulatory_region_symbol": "Orco",
                "encoded_product_symbol": "LexA-VP16",
                "stocks_number": 0,
                "source": "FlyBase allele description",
                "_search_terms": ("P{Orco-LexA-VP16}", "Orco", "LexA-VP16"),
            },
            {
                "canonical_token": "e[1]",
                "candidate_kind": "allele",
                "display_label": "e[1]",
                "description": "Unrelated ebony allele.",
                "flybase_id": "FBal0000001",
                "gene_symbol": "e",
                "insertion_symbol": "",
                "regulatory_region_symbol": "",
                "encoded_product_symbol": "",
                "stocks_number": 0,
                "source": "FlyBase allele description",
                "_search_terms": ("e",),
            },
        ),
    )

    matches = search_flybase_standardization_candidates("OrCo-LexA")

    assert matches[0]["canonical_token"] == "P{Orco-LexA-VP16}unspecified"
    assert all(match["canonical_token"] != "e[1]" for match in matches)


def test_review_stock_standardization_injects_reviewed_alias_candidate_when_missing_from_flybase(monkeypatch):
    monkeypatch.setattr(
        "flymanager.app.services.stock_standardization.get_flybase_phenotype_cache",
        lambda *args, **kwargs: {
            "marker_alias_index": {},
            "ambiguous_marker_aliases": {},
            "construct_annotations": {},
            "allele_markers": {},
            "split_systems": {},
            "allele_consequences": {},
        },
    )
    monkeypatch.setattr(
        "flymanager.app.services.stock_standardization.search_flybase_standardization_candidates",
        lambda *args, **kwargs: [
            {
                "canonical_token": "e[1]",
                "candidate_kind": "allele",
                "display_label": "e[1]",
                "description": "Unrelated ebony allele.",
                "flybase_id": "FBal0000001",
                "gene_symbol": "e",
                "insertion_symbol": "",
                "regulatory_region_symbol": "",
                "encoded_product_symbol": "",
                "stocks_number": 0,
                "source": "FlyBase allele description",
                "match_score": 60,
                "match_basis": "e",
            }
        ],
    )

    review = review_stock_standardization("w; CyO/+; OrCo-LexA/TM3;")

    orco_issue = next(issue for issue in review["issues"] if issue["token"] == "OrCo-LexA")
    assert orco_issue["fuzzy_candidates"][0]["canonical_token"] == "P{Orco-LexA-VP16}unspecified"
    assert orco_issue["fuzzy_candidates"][0]["candidate_kind"] == "reviewed_alias"
    assert orco_issue["fuzzy_candidates"][0]["source"] == "reviewed alias"


def test_parse_flybase_bulkdata_html_extracts_release_specific_downloads():
    html = """
    <html>
      <body>
                <p>Archive release FB2015_02</p>
        <h1>FlyBase: Current Release FB2026_01</h1>
        <a href="https://s3ftp.flybase.org/releases/FB2026_01/precomputed_files/alleles/genotype_phenotype_data_fb_2026_01.tsv.gz">genotype_phenotype_data_fb_2026_01.tsv.gz</a>
        <a href="https://s3ftp.flybase.org/releases/FB2026_01/precomputed_files/alleles/fbal_to_fbgn_fb_2026_01.tsv.gz">fbal_to_fbgn_fb_2026_01.tsv.gz</a>
        <a href="https://s3ftp.flybase.org/releases/FB2026_01/precomputed_files/alleles/dmel_classical_and_insertion_allele_descriptions_fb_2026_01.tsv.gz">dmel_classical_and_insertion_allele_descriptions_fb_2026_01.tsv.gz</a>
        <a href="https://s3ftp.flybase.org/releases/FB2026_01/precomputed_files/stocks/stocks_FB2026_01.tsv.gz">stocks_FB2026_01.tsv.gz</a>
        <a href="https://s3ftp.flybase.org/releases/FB2026_01/precomputed_files/transposons/transgenic_construct_descriptions_fb_2026_01.tsv.gz">transgenic_construct_descriptions_fb_2026_01.tsv.gz</a>
        <a href="https://s3ftp.flybase.org/releases/FB2026_01/precomputed_files/alleles/split_system_combinations_fb_2026_01.tsv.gz">split_system_combinations_fb_2026_01.tsv.gz</a>
        <a href="https://s3ftp.flybase.org/releases/FB2026_01/precomputed_files/genes/gene_map_table_fb_2026_01.tsv.gz">gene_map_table_fb_2026_01.tsv.gz</a>
      </body>
    </html>
    """

    manifest = parse_flybase_bulkdata_html(html)

    assert manifest["release"] == "FB2026_01"
    assert manifest["downloads"]["stocks"]["filename"] == "stocks_FB2026_01.tsv.gz"
    assert manifest["downloads"]["dpo"]["filename"] == "dpo.obo"
    assert manifest["missing"] == {}


def test_download_flybase_bundle_downloads_requested_files(tmp_path, monkeypatch):
    bundle = {
        "release": "FB2026_01",
        "source_url": "https://flybase.org/downloads/bulkdata",
        "missing": {},
        "downloads": {
            "genotype_phenotype_data": {
                "filename": "genotype_phenotype_data_fb_2026_01.tsv.gz",
                "url": "https://example.org/genotype_phenotype_data_fb_2026_01.tsv.gz",
            },
            "dpo": {
                "filename": "dpo.obo",
                "url": "https://example.org/dpo.obo",
            },
        },
    }

    class _FakeResponse(io.BytesIO):
        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, tb):
            self.close()

    def fake_urlopen(request, timeout=60):
        return _FakeResponse(f"payload:{request.full_url}".encode("utf-8"))

    monkeypatch.setattr("urllib.request.urlopen", fake_urlopen)

    report = download_flybase_bundle(
        tmp_path,
        bundle=bundle,
        required_keys=["genotype_phenotype_data", "dpo"],
    )

    genotype_path = tmp_path / "genotype_phenotype_data_fb_2026_01.tsv.gz"
    dpo_path = tmp_path / "dpo.obo"

    assert report["release"] == "FB2026_01"
    assert [item["status"] for item in report["results"]] == ["downloaded", "downloaded"]
    assert genotype_path.read_text(encoding="utf-8") == "payload:https://example.org/genotype_phenotype_data_fb_2026_01.tsv.gz"
    assert dpo_path.read_text(encoding="utf-8") == "payload:https://example.org/dpo.obo"


def test_refresh_flybase_reference_data_downloads_release_bundle_and_refreshes_gene_metadata(tmp_path, monkeypatch):
    app = Flask(__name__)
    data_dir = tmp_path / "flybase"
    manifest_path = data_dir / "LATEST_DOWNLOAD_MANIFEST.json"
    examination_path = data_dir / "EXAMINATION_REPORT.md"

    bundle = {
        "release": "FB2026_01",
        "source_url": "https://flybase.org/downloads/bulkdata",
        "missing": {},
        "downloads": {
            "stocks": {
                "filename": "stocks_FB2026_01.tsv.gz",
                "url": "https://example.org/stocks_FB2026_01.tsv.gz",
            }
        },
    }
    download_report = {
        "release": "FB2026_01",
        "source_url": bundle["source_url"],
        "data_dir": str(data_dir),
        "missing": {},
        "results": [
            {
                "key": "stocks",
                "status": "downloaded",
                "path": str(data_dir / "stocks_FB2026_01.tsv.gz"),
                "url": "https://example.org/stocks_FB2026_01.tsv.gz",
                "size_bytes": 42,
            }
        ],
    }

    captured = {}

    def fake_download_flybase_bundle(target_dir, overwrite=False, timeout=60, bundle=None, required_keys=None):
        Path(target_dir).mkdir(parents=True, exist_ok=True)
        (Path(target_dir) / "stocks_FB2026_01.tsv.gz").write_text("stocks", encoding="utf-8")
        captured["download"] = {
            "target_dir": str(target_dir),
            "overwrite": overwrite,
            "timeout": timeout,
            "bundle": bundle,
        }
        return download_report

    monkeypatch.setattr(
        "flymanager.app.services.flybase.discover_latest_flybase_downloads",
        lambda timeout=60: bundle,
    )
    monkeypatch.setattr(
        "flymanager.app.services.flybase.download_flybase_bundle",
        fake_download_flybase_bundle,
    )
    monkeypatch.setattr(
        "flymanager.app.services.flybase.examine_flybase_directory",
        lambda target_dir: {"data_dir": str(target_dir), "files": {}},
    )
    monkeypatch.setattr(
        "flymanager.app.services.flybase.render_flybase_examination_markdown",
        lambda examination: "# FlyBase Examination Report\n",
    )

    def fake_update_gene_metadata_from_flybase(app, stocks_file_path=None, timestamp=None):
        captured["gene_refresh"] = {
            "stocks_file_path": str(stocks_file_path),
            "timestamp": timestamp,
        }
        return {"supported_rows": 7}

    monkeypatch.setattr(
        "flymanager.app.services.flybase.update_gene_metadata_from_flybase",
        fake_update_gene_metadata_from_flybase,
    )
    monkeypatch.setattr(
        "flymanager.app.services.flybase.audit_bloomington_csv",
        lambda *args, **kwargs: {},
    )
    monkeypatch.setattr(
        "flymanager.app.services.flybase.render_unresolved_token_curation_markdown",
        lambda report: "# Unresolved Tokens\n",
    )
    monkeypatch.setattr(
        "flymanager.app.services.flybase.discover_bdsc_balancers",
        lambda timeout=60: {"definitions": {"summary": {"total_balancers": 0}}},
    )
    monkeypatch.setattr(
        "flymanager.app.services.flybase.write_balancer_report_files",
        lambda *args, **kwargs: {
            "json_path": str(data_dir / "BALANCER_DEFINITIONS.json"),
            "markdown_path": str(data_dir / "BALANCER_REPORT.md"),
        },
    )
    monkeypatch.setattr(
        "flymanager.app.services.flybase.ingest_balancer_definitions",
        lambda db, report: {"inserted": 0},
    )
    monkeypatch.setattr(
        "flymanager.app.services.flybase.ingest_flybase_bundle",
        lambda target_dir, db: {"collections": {}},
    )
    monkeypatch.setattr(
        "flymanager.app.services.flybase.build_flybase_phenotype_cache",
        lambda **kwargs: {
            "cache_path": str(data_dir / "PHENOTYPE_EVIDENCE_CACHE.json"),
            "rebuilt": True,
            "source_kind": "mongo_ingest",
            "summary": {},
        },
    )
    monkeypatch.setattr(
        "flymanager.app.services.flybase.backfill_stock_phenotype_cache",
        lambda *args, **kwargs: {"scanned": 0, "updated": 0, "skipped_valid": 0},
    )
    monkeypatch.setattr(
        "flymanager.app.services.flybase.backfill_cross_phenotype_cache",
        lambda *args, **kwargs: {"scanned": 0, "updated": 0, "skipped_valid": 0},
    )

    report = refresh_flybase_reference_data(
        app,
        data_dir=data_dir,
        manifest_path=manifest_path,
        examination_output_path=examination_path,
    )

    assert report["release"] == "FB2026_01"
    assert report["gene_summary"] == {"supported_rows": 7}
    assert captured["download"]["overwrite"] is True
    assert captured["download"]["bundle"] == bundle
    assert captured["gene_refresh"]["stocks_file_path"] == str(data_dir / "stocks_FB2026_01.tsv.gz")
    assert json.loads(manifest_path.read_text(encoding="utf-8"))["release"] == "FB2026_01"
    assert examination_path.read_text(encoding="utf-8") == "# FlyBase Examination Report\n"


def test_get_flybase_reference_status_reads_last_sync_manifest(tmp_path, monkeypatch):
    app = Flask(__name__)
    data_dir = tmp_path / "flybase"
    manifest_path = data_dir / "LATEST_DOWNLOAD_MANIFEST.json"
    examination_path = data_dir / "EXAMINATION_REPORT.md"
    curation_path = data_dir / "UNRESOLVED_TOKEN_REPORT.md"
    data_dir.mkdir(parents=True, exist_ok=True)

    class FixedDatetime(datetime):
        @classmethod
        def now(cls):
            return cls(2026, 4, 17, 12, 0, 0)

    monkeypatch.setattr("flymanager.app.services.flybase.datetime", FixedDatetime)

    manifest_path.write_text(
        json.dumps(
            {
                "release": "FB2026_01",
                "synced_at": "2026-04-17 11:15:00",
                "results": [
                    {"status": "downloaded"},
                    {"status": "skipped"},
                    {"status": "downloaded"},
                ],
                "gene_summary": {"supported_rows": 19},
                "flybase_ingestion_report": {
                    "collections": {
                        "flybase_phenotypes": {"inserted": 7},
                        "flybase_stock_alleles": {"inserted": 5},
                    }
                },
                "balancer_ingestion_report": {
                    "files": {
                        "json_path": str(data_dir / "BALANCER_DEFINITIONS.json"),
                        "markdown_path": str(data_dir / "BALANCER_REPORT.md"),
                    },
                    "db": {"inserted": 6},
                },
                "phenotype_cache_report": {
                    "source_kind": "mongo_ingest",
                    "summary": {
                        "cached_alleles": 3,
                        "cached_constructs": 2,
                        "cached_split_genotypes": 1,
                    },
                },
            }
        ),
        encoding="utf-8",
    )
    examination_path.write_text("report", encoding="utf-8")

    status = get_flybase_reference_status(
        app,
        data_dir=data_dir,
        manifest_path=manifest_path,
        examination_output_path=examination_path,
    )

    assert status == {
        "has_sync": True,
        "release": "FB2026_01",
        "synced_at": "2026-04-17 11:15:00",
        "last_attempt_at": "2026-04-17 11:15:00",
        "last_attempt_status": "",
        "last_error": "",
        "downloaded_count": 2,
        "skipped_count": 1,
        "total_files": 3,
        "supported_rows": 19,
        "days_since_sync": 0,
        "manifest_path": str(manifest_path),
        "examination_output_path": str(examination_path),
        "examination_exists": True,
        "curation_output_path": str(curation_path),
        "curation_report_exists": False,
        "unresolved_token_report": {},
        "flybase_ingestion_report": {
            "collections": {
                "flybase_phenotypes": {"inserted": 7},
                "flybase_stock_alleles": {"inserted": 5},
            }
        },
        "flybase_ingestion_collections": {
            "flybase_phenotypes": 7,
            "flybase_stock_alleles": 5,
        },
        "flybase_ingested_collection_count": 2,
        "flybase_ingested_documents": 12,
        "balancer_ingestion_report": {
            "files": {
                "json_path": str(data_dir / "BALANCER_DEFINITIONS.json"),
                "markdown_path": str(data_dir / "BALANCER_REPORT.md"),
            },
            "db": {"inserted": 6},
        },
        "balancer_count": 6,
        "balancer_json_path": str(data_dir / "BALANCER_DEFINITIONS.json"),
        "balancer_json_exists": False,
        "balancer_markdown_path": str(data_dir / "BALANCER_REPORT.md"),
        "balancer_markdown_exists": False,
        "phenotype_cache_report": {
            "source_kind": "mongo_ingest",
            "summary": {
                "cached_alleles": 3,
                "cached_constructs": 2,
                "cached_split_genotypes": 1,
            },
        },
        "phenotype_cache_source_kind": "mongo_ingest",
        "phenotype_cache_source_label": "Mongo ingest",
        "phenotype_cache_summary": {
            "cached_alleles": 3,
            "cached_constructs": 2,
            "cached_split_genotypes": 1,
        },
        "status_state": "healthy",
        "status_label": "Healthy",
        "status_tone": "success",
        "status_message": "FlyBase reference data is current based on the latest successful sync from today.",
        "status_detail": "Release FB2026_01 synced at 2026-04-17 11:15:00.",
    }


def test_get_flybase_reference_status_marks_stale_syncs(tmp_path, monkeypatch):
    app = Flask(__name__)
    data_dir = tmp_path / "flybase"
    manifest_path = data_dir / "LATEST_DOWNLOAD_MANIFEST.json"
    examination_path = data_dir / "EXAMINATION_REPORT.md"
    data_dir.mkdir(parents=True, exist_ok=True)

    class FixedDatetime(datetime):
        @classmethod
        def now(cls):
            return cls(2026, 4, 17, 12, 0, 0)

    monkeypatch.setattr("flymanager.app.services.flybase.datetime", FixedDatetime)

    manifest_path.write_text(
        json.dumps(
            {
                "release": "FB2026_01",
                "synced_at": "2026-02-01 10:00:00",
                "results": [{"status": "downloaded"}],
                "gene_summary": {"supported_rows": 19},
            }
        ),
        encoding="utf-8",
    )
    examination_path.write_text("report", encoding="utf-8")

    status = get_flybase_reference_status(
        app,
        data_dir=data_dir,
        manifest_path=manifest_path,
        examination_output_path=examination_path,
    )

    assert status["status_state"] == "stale"
    assert status["status_label"] == "Stale"
    assert status["status_tone"] == "warning"
    assert status["days_since_sync"] == 75
    assert status["status_message"] == "The latest successful FlyBase sync is 75 days ago."


def test_get_flybase_reference_status_marks_failed_attempts(tmp_path, monkeypatch):
    app = Flask(__name__)
    data_dir = tmp_path / "flybase"
    manifest_path = data_dir / "LATEST_DOWNLOAD_MANIFEST.json"
    examination_path = data_dir / "EXAMINATION_REPORT.md"
    data_dir.mkdir(parents=True, exist_ok=True)

    class FixedDatetime(datetime):
        @classmethod
        def now(cls):
            return cls(2026, 4, 17, 12, 0, 0)

    monkeypatch.setattr("flymanager.app.services.flybase.datetime", FixedDatetime)

    manifest_path.write_text(
        json.dumps(
            {
                "release": "FB2026_01",
                "synced_at": "2026-04-01 10:00:00",
                "last_attempt_at": "2026-04-17 11:30:00",
                "last_attempt_status": "failed",
                "last_error": "network timeout",
                "results": [{"status": "downloaded"}],
                "gene_summary": {"supported_rows": 19},
            }
        ),
        encoding="utf-8",
    )
    examination_path.write_text("report", encoding="utf-8")

    status = get_flybase_reference_status(
        app,
        data_dir=data_dir,
        manifest_path=manifest_path,
        examination_output_path=examination_path,
    )

    assert status["status_state"] == "failed"
    assert status["status_label"] == "Failed"
    assert status["status_tone"] == "danger"
    assert status["last_error"] == "network timeout"
    assert status["status_message"] == "The last FlyBase refresh attempt failed at 2026-04-17 11:30:00."
    assert status["status_detail"] == "Last successful sync: 2026-04-01 10:00:00 (16 days ago)."