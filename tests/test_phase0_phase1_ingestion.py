import json
from pathlib import Path

from flymanager.utils.phenotypes.data.balancer_ingest import (
    ingest_balancer_definitions, parse_bdsc_balancer_definitions_html,
    parse_bdsc_balancer_intro_html, render_balancer_report_markdown)
from flymanager.utils.phenotypes.data.flybase_ingest import (
    audit_live_stock_resolution, ingest_flybase_bundle,
    render_live_stock_resolution_markdown)
from flymanager.utils.phenotypes.data.manage import build_argument_parser
from flymanager.utils.phenotypes.flybase_pipeline import (
    build_flybase_phenotype_cache, clear_flybase_phenotype_cache)


class FakeCollection:
    def __init__(self):
        self.documents = []
        self.indexes = []

    def delete_many(self, _query):
        self.documents = []

    def insert_many(self, docs):
        self.documents.extend(dict(doc) for doc in docs)

    def update_one(self, query, update, upsert=False):
        for document in self.documents:
            if self._matches(document, query):
                document.update(update.get("$set", {}))
                return
        if upsert:
            new_document = dict(query)
            new_document.update(update.get("$set", {}))
            self.documents.append(new_document)

    def create_index(self, keys, name=None):
        self.indexes.append({"keys": keys, "name": name})

    def find(self, query=None, _projection=None):
        query = query or {}
        return [
            dict(document)
            for document in self.documents
            if self._matches(document, query)
        ]

    def find_one(self, query):
        for document in self.documents:
            if self._matches(document, query):
                return dict(document)
        return None

    def count_documents(self, query):
        return len(self.find(query))

    def _matches(self, document, query):
        for key, value in query.items():
            document_value = document.get(key)
            if isinstance(document_value, list):
                if value not in document_value:
                    return False
                continue
            if document_value != value:
                return False
        return True


class FakeDatabase(dict):
    def __getitem__(self, name):
        if name not in self:
            self[name] = FakeCollection()
        return dict.__getitem__(self, name)


def _write_ingest_fixture(tmp_path):
    (tmp_path / "genotype_phenotype_data_fb_fixture.tsv").write_text(
        "genotype_symbols\tgenotype_FBids\tphenotype_name\tphenotype_id\tqualifier_names\tqualifier_ids\treference\n"
        "foo[1]\tFBal0000001\tvisible\tFBcv:0000354\trecessive|wing\tFBcv:0000298|FBbt:00005106\tFBrf0000001\n",
        encoding="utf-8",
    )
    (tmp_path / "fbal_to_fbgn_fb_fixture.tsv").write_text(
        "#\tAlleleID\tAlleleSymbol\tGeneID\tGeneSymbol\n"
        "FBal0000001\tfoo[1]\tFBgn0000001\tfoo\n",
        encoding="utf-8",
    )
    (tmp_path / "stocks_FB_fixture.tsv").write_text(
        "FBst\tcollection_short_name\tstock_type_cv\tspecies\tFB_genotype\tdescription\tstock_number\n"
        "FBst0000001\tBloomington\tliving stock ; FBsv:0000002\tDmel\tfoo[1]; +; +; +\tFixture stock\t1\n",
        encoding="utf-8",
    )
    (tmp_path / "dmel_classical_and_insertion_allele_descriptions_fb_fixture.tsv").write_text(
        "#Allele (symbol)\tAllele (id)\tGene (symbol)\tGene (id)\tAllele Class (term)\tAllele Class (id)\tInsertion (symbol)\tInsertion (id)\tInserted element type (term)\tInserted element type (id)\tRegulatory region (symbol)\tRegulatory region (id)\tEncoded product/tool (symbol)\tEncoded product/tool (id)\tTagged with (symbol)\tTagged with (id)\tAlso carries (symbol)\tAlso carries (id)\tDescription (text)\tDescription (supporting reference)\tStocks (number)\n"
        "foo[1]\tFBal0000001\tfoo\tFBgn0000001\tclassical\tSO:0001218\t\t\t\t\t\t\t\t\t\t\t\t\tFixture allele\tFBrf0000001\t2\n",
        encoding="utf-8",
    )
    (tmp_path / "transgenic_construct_descriptions_fb_fixture.tsv").write_text(
        "#Component Allele (symbol)\tComponent Allele (id)\tTransgenic Construct (symbol)\tTransgenic Construct (id)\tTransgenic Product class (term)\tTransgenic Product class (id)\tRegulatory region (symbol)\tRegulatory region (id)\tEncoded product/tool (symbol)\tEncoded product/tool (id)\tTagged with (symbol)\tTagged with (id)\tAlso carries (symbol)\tAlso carries (id)\tDescription (text)\tDescription (supporting reference)\tStocks (number)\n"
        "foo[GFP]\tFBal0000002\tP{foo-GFP}\tFBtp0000001\treporter\tSO:0000804\tfoo\tFBgn0000001\tGFP\tFBgn0014447\t\t\t\t\tFixture construct\tFBrf0000002\t1\n",
        encoding="utf-8",
    )
    (tmp_path / "split_system_combinations_fb_fixture.tsv").write_text(
        "Symbol\tComponent_Alleles\tStocks\tReferences\n"
        "Split-GFP\tfoo[1]|bar[1]\tFBst0000001:foo[1]; +; +; +\tFBrf0000003\n",
        encoding="utf-8",
    )
    (tmp_path / "gene_map_table_fb_fixture.tsv").write_text(
        "##organism_abbreviation\tcurrent_symbol\tprimary_FBid\trecombination_loc\tcytogenetic_loc\tsequence_loc\n"
        "Dmel\tfoo\tFBgn0000001\t2-[10]\t20A-20A\t2R:100..200\n",
        encoding="utf-8",
    )


def test_parse_bdsc_balancer_definitions_html_extracts_balancers():
    html = """
    <html><body>
      <h2>Common Balancers</h2>
      <table>
        <tr>
          <th>Balancer Symbol</th><th>Chromosome</th><th>Genotype</th><th>Dominant Markers</th><th>Breakpoints</th><th>Notes</th>
        </tr>
        <tr>
          <td>FM7c</td><td>X</td><td>In(1)FM7c, y[1] sc[*] sn[*] B[1]</td><td>y, sc, sn, B</td><td>3C-20A</td><td>X chromosome balancer</td>
        </tr>
        <tr>
          <td>TM3</td><td>3</td><td>TM3, Sb[1] Ser[1]</td><td>Sb, Ser</td><td>61A-65D</td><td>Third chromosome balancer</td>
        </tr>
      </table>
    </body></html>
    """
    intro_html = """
    <html><body>
      <p>Choose balancers whose breakpoints span the mutation of interest.</p>
      <p>Interchromosomal effects can alter segregation and fertility in multi-balancer stocks.</p>
    </body></html>
    """

    definitions = parse_bdsc_balancer_definitions_html(html)
    intro = parse_bdsc_balancer_intro_html(intro_html)
    report = {"definitions": definitions, "intro": intro}
    markdown = render_balancer_report_markdown(report)

    assert definitions["summary"]["total_balancers"] == 2
    assert definitions["balancers"][0]["symbol"] == "FM7c"
    assert definitions["balancers"][0]["chromosome"] == "X"
    assert definitions["balancers"][0]["marker_tokens"] == ["y", "sc", "sn", "B"]
    assert definitions["balancers"][1]["breakpoint_regions"] == ["61A-65D"]
    assert intro["selection_rules"] == ["Choose balancers whose breakpoints span the mutation of interest."]
    assert intro["interchromosomal_notes"] == ["Interchromosomal effects can alter segregation and fertility in multi-balancer stocks."]
    assert "# BDSC Balancer Reference Report" in markdown
    assert "### TM3" in markdown


def test_ingest_balancer_definitions_persists_records():
    report = {
        "definitions": {
            "source_url": "https://example.org/balancers",
            "balancers": [
                {
                    "symbol": "TM3",
                    "chromosome": "3",
                    "genotype": "TM3, Sb[1] Ser[1]",
                    "marker_tokens": ["Sb", "Ser"],
                    "markers_text": "Sb, Ser",
                    "breakpoint_text": "61A-65D",
                    "breakpoint_regions": ["61A-65D"],
                    "notes": "Third chromosome balancer",
                    "source_table_heading": "Common Balancers",
                    "source_table_caption": "",
                    "source_url": "https://example.org/balancers",
                }
            ],
        }
    }
    db = FakeDatabase()

    result = ingest_balancer_definitions(db, report)

    assert result["inserted"] == 1
    assert db["balancer_definitions"].documents[0]["symbol"] == "TM3"
    assert {index["keys"] for index in db["balancer_definitions"].indexes} == {"symbol", "chromosome"}


def test_ingest_flybase_bundle_populates_expected_collections(tmp_path):
    _write_ingest_fixture(tmp_path)
    db = FakeDatabase()

    report = ingest_flybase_bundle(tmp_path, db)

    assert report["collections"]["flybase_phenotypes"]["inserted"] == 1
    assert report["collections"]["flybase_allele_genes"]["inserted"] == 1
    assert report["collections"]["flybase_stock_alleles"]["inserted"] == 1
    assert report["collections"]["flybase_constructs"]["inserted"] == 1
    assert report["collections"]["flybase_gene_map"]["inserted"] == 1
    phenotype_doc = db["flybase_phenotypes"].documents[0]
    assert phenotype_doc["allele_tokens"] == ["foo[1]"]
    assert phenotype_doc["gene_symbols"] == ["foo"]
    assert phenotype_doc["is_unconditional_visible"] is True
    assert phenotype_doc["dominance_terms"] == ["recessive"]
    assert phenotype_doc["body_part_ids"] == ["FBBT:00005106"]
    assert db["flybase_stock_alleles"].documents[0]["FBst"] == "FBst0000001"


def test_audit_live_stock_resolution_reports_unresolved_tokens():
    db = FakeDatabase()
    db["stocks"].documents = [
        {"UniqueID": "UID1", "Name": "Known", "Genotype": "TM3, Sb[1]"},
        {"UniqueID": "UID2", "Name": "Unknown", "Genotype": "Mystery"},
    ]

    report = audit_live_stock_resolution(db)
    markdown = render_live_stock_resolution_markdown(report)

    assert report["summary"]["records_scanned"] == 2
    assert report["summary"]["records_with_unresolved_tokens"] == 1
    assert report["top_unresolved_tokens"] == [("Mystery", 1)]
    assert report["top_balancers"] == [("TM3", 1)]
    assert "# Live Stock Resolution Report" in markdown
    assert "Mystery: 1" in markdown


def test_build_flybase_phenotype_cache_can_use_ingested_mongo_collections(tmp_path):
    _write_ingest_fixture(tmp_path)
    db = FakeDatabase()
    ingest_flybase_bundle(tmp_path, db)
    cache_path = tmp_path / "PHENOTYPE_EVIDENCE_CACHE.json"

    clear_flybase_phenotype_cache(cache_path)
    report = build_flybase_phenotype_cache(
        data_dir=tmp_path,
        cache_path=cache_path,
        force=True,
        db=db,
    )

    payload = json.loads(cache_path.read_text(encoding="utf-8"))
    assert report["rebuilt"] is True
    assert payload["source_kind"] == "mongo_ingest"
    assert payload["allele_markers"]["foo[1]"]["gene_stem"] == "foo"
    assert payload["construct_annotations"]["P{foo-GFP}"]["category"] == "reporter"
    assert payload["summary"]["cached_constructs"] >= 1


def test_manage_parser_includes_new_phase_zero_and_one_commands():
    parser = build_argument_parser()

    ingest_flybase_args = parser.parse_args(["ingest-flybase"])
    ingest_balancers_args = parser.parse_args(["ingest-balancers"])
    allele_args = parser.parse_args(["test-allele", "foo[1]"])
    live_audit_args = parser.parse_args(["audit-live-resolution"])

    assert ingest_flybase_args.command == "ingest-flybase"
    assert ingest_balancers_args.command == "ingest-balancers"
    assert allele_args.command == "test-allele"
    assert allele_args.token == "foo[1]"
    assert live_audit_args.command == "audit-live-resolution"