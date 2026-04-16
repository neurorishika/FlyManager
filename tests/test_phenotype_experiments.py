import gzip
import io

from flymanager.utils.phenotypes.compute import compute_marker_phenotype
from flymanager.utils.phenotypes.construct_markers import \
    extract_construct_markers
from flymanager.utils.phenotypes.data.downloads import (
    download_flybase_bundle, parse_flybase_bulkdata_html)
from flymanager.utils.phenotypes.data.examiner import (analyze_marker_coverage,
                                                       audit_bloomington_csv,
                                                       summarize_tsv_file)
from flymanager.utils.phenotypes.parser import (parse_gene_package,
                                                tokenize_gene_package)
from flymanager.utils.phenotypes.resolver import resolve_package_markers


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

    assert parsed["balancers"][0]["symbol"] == "FM7"
    assert parsed["classical_alleles"][0]["token"] == "w[*]"
    assert parsed["annotations"] == ["|P-cytotype|"]


def test_extract_construct_markers_finds_mini_white():
    markers = extract_construct_markers("P{w[+mC]=Orco-GAL4.W}11.17")

    assert len(markers) == 1
    assert markers[0]["display_label"] == "mini-white"
    assert markers[0]["mini_white"] is True


def test_resolve_package_markers_combines_balancer_and_manual_dictionary_hits():
    resolved = resolve_package_markers("TM3, Sb[1] Ser[1]")
    labels = {marker["display_label"] for marker in resolved["markers"]}

    assert {"Sb", "Ser"}.issubset(labels)
    assert resolved["unresolved_tokens"] == []


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

    assert audit["total_packages"] == 2
    assert audit["top_markers"][0][0] in {"Sb", "Ser", "mini-white"}
    assert audit["sample_resolutions"]


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