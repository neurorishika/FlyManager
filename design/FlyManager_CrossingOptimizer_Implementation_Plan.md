# FlyManager Crossing Scheme Optimizer — Implementation Plan for Claude Code

**Project Owner:** Rishika Mohanta (neurorishika)  
**Repository:** <https://github.com/neurorishika/FlyManager>  
**Design Document:** See `FlyManager_CrossingOptimizer_DesignDoc_v6_FINAL.md` (co-located with this file)  
**Date:** April 2026

---

## 0. What This Project Is

FlyManager is a Flask/MongoDB app for managing *Drosophila melanogaster* stocks and crosses. We are adding an **Automated Crossing Scheme Optimizer** that takes a target fly genotype and finds the best multi-generation crossing scheme to build it from available stocks.

The core challenge: at every step of a crossing scheme, you have to sort the correct progeny from siblings by looking at them under a microscope. The system must predict what every progeny class *looks like* and verify the desired class is visually distinguishable.

**Before writing any optimizer code, we need to build and validate the phenotype prediction pipeline.** Most of this plan is about verifying data sources and building that pipeline. The actual search algorithm comes last.

## 0.1 Current Status Snapshot (2026-04-17)

The repository has already implemented a meaningful subset of the phenotype foundation, but it has **not** implemented the crossing optimizer itself.

| Phase | Status | Current Repo State |
| ----- | ----- | ----- |
| Phase 0 | Complete | FlyBase release discovery/download, local examination report generation, visible-marker inventory audit, unresolved-token curation, balancer reference scraping/reporting, live stock-resolution auditing, and admin sync health/status reporting are present |
| Phase 1 | Complete | Manual marker dictionary, parser, construct-marker extraction, reviewed aliases, curated balancer metadata, Mongo-backed FlyBase ingestion, balancer ingestion, ingestion CLI commands, refresh-lifecycle integration, and the hybrid Mongo-plus-JSON cache architecture are present |
| Phase 2 | Complete and shipped | Stock/cross phenotype previews, parent/offspring phenotype cache generation, cache refresh/backfill, FlyBase consequence annotations, provenance-aware scoring, sibling confusability analysis, and a standalone phenotype sandbox are implemented |
| Phase 3 | Complete | `flymanager/utils/constraints/` now provides balancer selection, interchromosomal-risk scoring, marker-stability assessment, target validation, yield heuristics, and regression tests |
| Phase 4 | Substantial partial implementation | `flymanager/utils/crossing/` now ships a phenotype-aware simulator wrapper, viability-aware pruning, sibling identifiability scoring, reciprocal-direction evaluation, simulator summaries, regression tests, and cross-view/cache integration; remaining gaps are broader historical-cross validation and optimizer/search consumption |
| Phase 5 | Not started | No optimizer/search/templates/scoring modules |
| Phase 6 | Not started | No optimizer blueprint, dedicated optimizer templates, or batch planner UI |

This document now serves two purposes:

1. Preserve the target architecture.
2. Record where the current repo diverges from that target so future work can resume from the real baseline.

## 0.2 Phase-by-Phase Implementation Checklist

Use this as the execution checklist for closing the remaining gaps. Checked items are already in the repository in some form. Unchecked items are still missing or only partially covered.

### Phase 0 Checklist: Data Examination and Source Validation

- [x] Discover the latest FlyBase release and download the required bulk-data bundle
- [x] Generate and maintain `data/flybase/EXAMINATION_REPORT.md`
- [x] Audit visible-marker coverage in FlyBase phenotype data
- [x] Audit compatible Bloomington stock rows against the phenotype pipeline
- [x] Generate unresolved-token curation output for Bloomington-package parsing
- [x] Expose FlyBase sync health and report status in admin workflows
- [x] Expose ingestion, balancer-refresh, and phenotype-cache provenance in admin/status surfaces
- [x] Scrape and normalize BDSC balancer definition pages into a structured dataset
- [x] Capture balancer breakpoint and marker-stability facts from external sources instead of only curated code constants
- [x] Write a persistent Phase 0 summary that explicitly compares external-data assumptions against live Mongo usage patterns

### Phase 1 Checklist: Ingestion and Knowledge Base

- [x] Maintain a curated manual visual-marker dictionary in `visual_markers.py`
- [x] Support allele-specific marker overrides and reviewed bare-token aliases
- [x] Parse genotype packages into constructs, classical alleles, balancers, aberrations, and unresolved tokens
- [x] Extract construct markers such as mini-white, `y+`, and `v+`
- [x] Build a local FlyBase evidence cache for allele evidence, aliases, construct annotations, and split-system annotations
- [x] Resolve phenotype evidence by merging curated markers, construct parsing, reviewed aliases, and FlyBase cache lookups
- [x] Create a normalized Mongo-backed ingestion pipeline for FlyBase TSVs (`flybase_ingest.py`)
- [x] Create a balancer-ingestion module (`balancer_ingest.py`) instead of relying only on curated metadata in code
- [x] Materialize queryable Mongo collections for FlyBase phenotype rows, allele-gene mappings, stocks, constructs, and gene-map data
- [x] Add ingestion-oriented CLI commands such as `ingest-flybase`, `ingest-balancers`, and `test-allele`
- [x] Add coverage validation that reports source-by-source resolution quality across the real stock collection
- [x] Integrate FlyBase and balancer ingestion into the refresh lifecycle so reference data refreshes together
- [x] Decide and document the hybrid architecture: Mongo-ingested reference collections are authoritative, while the JSON evidence cache remains the runtime fast path with file fallback
- [x] Prefer Mongo-backed cache regeneration when a database handle is already available

### Phase 2 Checklist: Phenotype Computation and Preview

- [x] Compute visible marker summaries for a genotype/sex pair
- [x] Predict stock-level phenotype summaries across male and female projections
- [x] Annotate cross offspring predictions with phenotype summaries and confidence labels
- [x] Cache phenotype summaries on stock and cross records
- [x] Expose phenotype previews in stock view, cross view, and explorer surfaces
- [x] Add refresh and bulk-backfill actions for phenotype caches
- [x] Surface construct-annotation and split-system notes in phenotype output
- [x] Add viability, lethality, sterility, and stage-aware phenotype consequences from FlyBase phenotype classes and qualifiers
- [x] Add a formal epistasis engine for masking and interaction rules such as `cn` + `bw`
- [x] Add full-genotype identifiability analysis for answering whether the desired class is uniquely sortable from siblings
- [x] Add confusable-class detection across sibling progeny rather than reporting each class independently
- [x] Add a standalone phenotype-preview API or sandbox UI for arbitrary genotype testing outside stock/cross pages
- [x] Add provenance-aware scoring that distinguishes high-confidence microscope markers from low-confidence inference-only signals

### Phase 3 Checklist: Genetic Constraint Engine

- [x] Create `flymanager/utils/constraints/`
- [x] Implement balancer selection by chromosome and gene position
- [x] Implement interchromosomal-effect and multi-balancer risk checks
- [x] Implement marker-stability scoring for candidate sorting markers
- [x] Implement impossible-target and unsupported-target validation
- [x] Implement yield estimation and vial-requirement heuristics
- [x] Add tests that validate constraint outputs against known Drosophila genetics rules

### Phase 4 Checklist: Enhanced Simulator and Identifiability

- [x] Wrap `cross_genotypes()` with phenotype-aware simulation outputs
- [x] Add viability-aware pruning of impossible or non-surviving offspring classes
- [x] Add direction-aware cross evaluation when reciprocal crosses behave differently
- [x] Implement identifiability scoring for each offspring class relative to siblings
- [x] Detect when a planned step depends on non-unique or low-confidence sorting
- [ ] Add regression tests based on real historical crosses and expected sortable classes

### Phase 5 Checklist: Optimizer and Search

- [x] Create `flymanager/utils/crossing/`
- [ ] Implement formal crossing templates for common balancing and stock-building patterns
- [ ] Implement target decomposition into chromosome-level subgoals
- [ ] Implement candidate-scheme generation and pruning
- [ ] Implement multi-factor scoring for generations, confidence, yield, and stock cost
- [ ] Implement memoization and reusable sub-scheme caching
- [ ] Add gold-standard optimizer tests against hand-designed schemes

### Phase 6 Checklist: UI and Workflow Integration

- [ ] Create `flymanager/app/routes/optimizer.py`
- [ ] Create dedicated optimizer templates under `flymanager/app/templates/optimizer/`
- [ ] Add target-genotype entry, validation, and dry-run preview flows
- [ ] Add interactive per-step phenotype and identifiability explanations in the UI
- [ ] Add scheme editing and re-scoring after user adjustments
- [ ] Add persistence for saved schemes and cached optimizer results
- [ ] Add batch-planning support for multi-target projects
- [ ] Run end-to-end user testing with real lab workflows before declaring the optimizer production-ready

---

## 1. Repository Setup

```bash
git clone https://github.com/neurorishika/FlyManager.git
cd FlyManager
```

Key files to understand first:
- `flymanager/utils/genetics.py` — `cross_genotypes()`, `qc_genotype()`, `get_genetic_components()`. Read these thoroughly. They are the foundation.
- `flymanager/app/services/bloomington.py` — existing BDSC CSV integration
- `flymanager/app/settings.py` — stock/cross property definitions
- `data/bloomington.csv` — full BDSC catalog (~90k rows)

The genotype format is `"X_content; chr2_content; chr3_content; chr4_content"` with `/` for heterozygous arms. `qc_genotype()` normalizes it. **This format must not change.**

---

## 2. Phase 0: Data Examination (DO THIS FIRST)

**Goal:** Download external data sources, examine them, and verify our design assumptions before writing any ingestion code.

**Implementation status as of 2026-04-17:** Implemented as a hybrid local-file plus Mongo workflow. The repo already contains `flymanager/utils/phenotypes/data/downloads.py`, `examiner.py`, `balancer_ingest.py`, and `manage.py`, plus `data/flybase/EXAMINATION_REPORT.md`, monthly refresh hooks, and status surfaces that expose ingestion and cache provenance.

### 2.1 Download FlyBase Bulk Data

These files must be downloaded outside any restricted network. Place them in `data/flybase/`.

Use the FlyBase bulk-data page as the authoritative source for the current release links:

- `https://flybase.org/downloads/bulkdata`

FlyBase now publishes release-specific filenames such as `genotype_phenotype_data_fb_2026_01.tsv.gz`, not just older `*_current.tsv.gz` names. As of release `FB2026_01`, the direct links are:

```bash
mkdir -p data/flybase
cd data/flybase

# Core files
wget https://s3ftp.flybase.org/releases/FB2026_01/precomputed_files/alleles/genotype_phenotype_data_fb_2026_01.tsv.gz
wget https://s3ftp.flybase.org/releases/FB2026_01/precomputed_files/alleles/fbal_to_fbgn_fb_2026_01.tsv.gz
wget https://s3ftp.flybase.org/releases/FB2026_01/precomputed_files/alleles/dmel_classical_and_insertion_allele_descriptions_fb_2026_01.tsv.gz
wget https://s3ftp.flybase.org/releases/FB2026_01/precomputed_files/stocks/stocks_FB2026_01.tsv.gz
wget https://s3ftp.flybase.org/releases/FB2026_01/precomputed_files/transposons/transgenic_construct_descriptions_fb_2026_01.tsv.gz
wget https://s3ftp.flybase.org/releases/FB2026_01/precomputed_files/alleles/split_system_combinations_fb_2026_01.tsv.gz

# Cytological map (for balancer selection)
wget https://s3ftp.flybase.org/releases/FB2026_01/precomputed_files/genes/gene_map_table_fb_2026_01.tsv.gz

# DPO ontology
wget http://purl.obolibrary.org/obo/dpo.obo

# Decompress all if desired. The new Phase 0 examiner can read .tsv.gz directly.
gunzip *.gz
```

If a later FlyBase release is current by the time you run this, swap `FB2026_01` for the current release identifier shown on the bulk-data page and use the corresponding release-specific filenames.

### 2.2 Questions to Answer

Write a Python script that examines each file and answers these questions. Save the answers in `data/flybase/EXAMINATION_REPORT.md`.

**Question 1 — genotype_phenotype_data columns:**
```python
import csv
with open('data/flybase/genotype_phenotype_data_*.tsv') as f:
    reader = csv.reader(f, delimiter='\t')
    # Skip comment lines (start with #)
    for row in reader:
        if not row[0].startswith('#'):
            print("COLUMNS:", row)  # First non-comment row is headers
            break
    # Print 20 sample data rows
    for i, row in enumerate(reader):
        if i < 20: print(row)
```

Record: column names, column count, and how genotype/phenotype/qualifier fields are structured.

**Question 2 — Common marker coverage:**
```python
# Search for these specific alleles in the phenotype data
CRITICAL_MARKERS = ['Cy', 'Sb', 'Tb', 'Hu', 'B', 'w', 'y', 'e', 'cn', 'bw', 
                     'st', 'vg', 'Ser', 'D', 'Sp', 'sn', 'f', 'ey']

# For each marker, count:
# - Total rows mentioning it
# - Rows with "visible" in the phenotype class
# - Rows with anatomy (FBbt) terms
# - Rows that are UNCONDITIONAL (no "with" clause)
```

This is the most critical question. If common markers have good "visible" coverage with anatomy terms, FlyBase can be a primary source for visual phenotype structured data. If coverage is sparse, the manual dictionary is essential.

Observed result from FB2026_01: common markers do have many `visible` rows, but the useful signal is phenotype-class enrichment rather than direct microscope-facing descriptions. The manual dictionary is still required.

**Question 3 — Qualifier format:**
```python
# Find all rows with "visible" phenotype class
# Print 20 examples showing the full qualifier syntax
# Look for: pipe-delimited qualifiers, "with" clauses, anatomy terms
```

**Question 4 — Stocks bridge:**
```python
# Examine stocks_*.tsv
# Does it contain BDSC stock numbers?
# Does it contain FlyBase allele IDs (FBal)?
# Can we map: BDSC stock# → FBal → FBgn → phenotype data?
# Print 10 sample rows
```

Observed result from FB2026_01: the stocks table is a strong stock-catalog bridge with `FBst`, collection name, species, genotype text, description, and stock number. It is not a direct stock# → FBal lookup table, so allele-level bridging still depends on genotype parsing plus `fbal_to_fbgn`.

**Question 5 — File sizes and row counts:**
```python
import os
for f in os.listdir('data/flybase'):
    if f.endswith('.tsv'):
        with open(f'data/flybase/{f}') as fh:
            lines = sum(1 for _ in fh)
        size = os.path.getsize(f'data/flybase/{f}')
        print(f"{f}: {lines} rows, {size/1e6:.1f} MB")
```

**Question 6 — Construct descriptions:**
```python
# Examine transgenic_construct_descriptions
# Do entries contain marker gene lists (w+mC, y+, etc.)?
# Print 10 sample rows
```

**Question 7 — Gene map table:**
```python
# Examine gene_map_table
# Does it map gene symbols → cytological positions?
# This is needed for balancer selection by position
# Print 10 sample rows
```

### 2.3 Examine BDSC Balancer Definitions

Fetch and examine the BDSC balancer pages:

```python
import requests
from bs4 import BeautifulSoup

# Balancer definitions
url = "https://bdsc.indiana.edu/stocks/balancers/balancer_defs.html"
resp = requests.get(url)
soup = BeautifulSoup(resp.text, 'html.parser')
# Extract: balancer symbol, chromosome, markers, inversion breakpoints
# Print structured summary

# Balancer intro (contains breakpoint selection rules)
url2 = "https://bdsc.indiana.edu/stocks/balancers/balancer_intro.html"
# Extract: selection rules, interchromosomal effect warnings
```

**Current implementation note:** this BDSC balancer scraping step is now implemented in `flymanager/utils/phenotypes/data/balancer_ingest.py`. The repo can write structured balancer JSON/Markdown reports, persist normalized balancer definitions into MongoDB, and still keep curated balancer shorthand in `flymanager/utils/phenotypes/visual_markers.py` as a runtime fallback.

### 2.4 Examine User's Existing Cross Data

The user's BDSC digest is at: (provided as uploaded file — examine the gene packages)

Also examine the user's actual cross comments in their FlyManager MongoDB for phenotype selection instructions. If you have access to the database, query:
```python
# Look for cross entries with selection instructions in comments
db.crosses.find({"Comments": {"$regex": "SELECTION|phenotypic marker|Curly|Stubble"}})
```

### 2.5 Phase 0 Deliverable

Write `data/flybase/EXAMINATION_REPORT.md` containing:
- Column structures for each file
- Coverage statistics for critical markers
- Qualifier format examples
- Stocks bridge confirmation (yes/no)
- File size/row count table
- BDSC balancer definition structure
- Explicit assessment: which design assumptions are confirmed, which need revision
- Per-file value assessment: foundational, secondary, or optional for the genetics pipeline

### 2.6 Go/No-Go Decision

After Phase 0, decide the phenotype source architecture:

| If... | Then... |
|-------|---------|
| Common markers have many `visible` rows but poor direct visual-description coverage | Keep the manual dictionary as the primary visual source; use FlyBase as phenotype enrichment |
| `genotype_phenotype_data` has broad phenotype classes and qualifiers | Use it for viability, sterility, dominance, stage/context, and genotype-level enrichment |
| Stocks TSV is broad across repositories but not a direct allele bridge | Use it as the canonical stock-source layer, not the sole allele-normalization layer |
| `fbal_to_fbgn` is available | Use it as the core allele→gene bridge |
| Gene map table has cytological positions | Enable balancer selection by position |
| No cytological position data | Skip position-based balancer selection; use simpler per-chromosome matching |

Current decision from the examined FB2026_01 bundle:

- Foundational: `stocks`, `genotype_phenotype_data`, `fbal_to_fbgn`, `dmel_classical_and_insertion_allele_descriptions`, `transgenic_construct_descriptions`, `gene_map_table`
- Secondary: `split_system_combinations`
- Manual dictionary remains primary for sort-by-eye marker descriptions
- Mongo-ingested FlyBase and balancer collections are the authoritative reference-data layer refreshed by the monthly/admin sync workflow
- `PHENOTYPE_EVIDENCE_CACHE.json` remains the runtime fast path and is rebuilt from Mongo-ingested collections when a database handle is available, with raw-file fallback retained for offline tooling

---

## 3. Phase 1: Data Ingestion + Manual Dictionary

**Goal:** Get all phenotype data into a queryable form.

**Implementation status as of 2026-04-17:** Complete for the planned Phase 1 scope. The manual dictionary, allele-specific overrides, reviewed aliases, construct-marker extraction, parser, resolver, Mongo-backed FlyBase ingestion layer (`flybase_ingest.py`), balancer ingestion/reporting (`balancer_ingest.py`), materialized reference collections, and management CLI commands now exist. The architecture decision is also made: Mongo-ingested reference collections are authoritative, while the JSON evidence cache remains the runtime fast layer.

### 3.1 Manual Visual Marker Dictionary

Create `flymanager/utils/phenotypes/visual_markers.py`. This is a Python dictionary of ~50-100 alleles that produce visible phenotypes at the stereomicroscope. Each entry needs:

```python
VISUAL_MARKER_DICTIONARY = {
    "Cy": {
        "body_part": "wing",
        "effect": "curly wings (upturned at tips)", 
        "dominance": "dominant",
        "scoring_confidence": 0.99,
        "homozygous_lethal": True,
        "chromosome": 2,
        "notes": "Most reliable wing marker. On CyO balancer.",
    },
    # ... ~50-100 entries
}
```

Sources for curating this dictionary:
- BDSC nomenclature page examples
- Roote & Prokop G3 2013 supplementary material (has marker descriptions)
- FlyBase allele reports for specific alleles
- CGS Lab phenotype catalog (https://cgslab.com/phenotypes/)
- Wikipedia Drosophila melanogaster page (has a concise marker list)

**Test:** For every allele in the dictionary, verify the gene stem appears in the BDSC data (bloomington.csv gene package strings).

### 3.2 FlyBase Ingestion Pipeline

Create `flymanager/utils/phenotypes/data/flybase_ingest.py`:

Current implementation note: this Mongo ingestion pipeline now exists in `flymanager/utils/phenotypes/data/flybase_ingest.py` and is wired into the monthly/admin refresh flow. `flymanager/utils/phenotypes/flybase_pipeline.py` remains the runtime cache layer and can rebuild its JSON evidence cache from Mongo-ingested collections when a database handle is available, or directly from files as a fallback.

```python
def ingest_genotype_phenotype_data(filepath: str, db) -> dict:
    """
    Parse genotype_phenotype_data TSV.
    
    For each row:
    1. Extract genotype symbols, genotype FlyBase IDs, phenotype class, qualifier names/IDs, and reference
    2. Parse phenotype class + qualifiers into dominance / stage / context buckets where possible
    3. Parse conditional wording such as "with genotype" if present
    4. Preserve raw row context because many rows are genotype-level rather than simple single-allele facts
    5. Insert into flybase_phenotypes collection
    
    Return: {total_rows, inserted, skipped, errors, coverage_stats}
    """
    pass

def ingest_fbal_to_fbgn(filepath: str, db) -> dict:
    """Parse allele-to-gene mapping. Insert into flybase_allele_genes."""
    pass

def ingest_stocks(filepath: str, db) -> dict:
    """Parse stocks TSV. Insert into flybase_stock_alleles as the canonical multi-repository stock catalog."""
    pass

def ingest_allele_descriptions(filepath: str, db) -> dict:
    """Parse allele descriptions for class, insertion/tool context, free-text description, and stock counts."""
    pass

def ingest_construct_descriptions(filepath: str, db) -> dict:
    """Parse construct descriptions for encoded products, regulatory regions, tags, and stock counts."""
    pass

def ingest_gene_map_table(filepath: str, db) -> dict:
    """Parse gene symbol to recombination / cytological / sequence positions for balancer and linkage logic."""
    pass
```

**Create indexes after ingestion:**
```python
db.flybase_phenotypes.create_index("allele_symbol")
db.flybase_phenotypes.create_index("gene_symbol")
db.flybase_phenotypes.create_index([("gene_id", 1), ("is_unconditional", 1)])
db.flybase_allele_genes.create_index("allele_symbol")
db.flybase_allele_genes.create_index("gene_symbol")
db.flybase_stock_alleles.create_index("stock_number")
db.flybase_stock_alleles.create_index([("collection_short_name", 1), ("stock_number", 1)])
db.flybase_stock_alleles.create_index("FBst")
db.flybase_gene_map.create_index("current_symbol")
db.balancer_definitions.create_index("symbol")
```

### 3.3 Balancer Definitions Ingestion

Create `flymanager/utils/phenotypes/data/balancer_ingest.py`:

Scrape BDSC balancer_defs.html and balancer_intro.html. For each balancer, store:
- Symbol, chromosome, full genotype
- Dominant markers with their visual phenotypes (cross-reference with VISUAL_MARKER_DICTIONARY)
- Inversion breakpoints (if available from BDSC or Miller et al. papers)
- Recommended stocks
- Marker stability assessment

Current implementation note: the scraper and persisted balancer dataset now exist in `balancer_ingest.py` and `balancer_definitions`, while `visual_markers.py` still carries curated balancer shorthand for fast runtime resolution and fallback behavior.

### 3.4 Management CLI

Create `flymanager/utils/phenotypes/data/manage.py`:

```python
# Commands:
# python manage.py examine          — Phase 0 examination
# python manage.py ingest-flybase   — ingest all FlyBase TSVs
# python manage.py ingest-balancers — scrape + ingest BDSC balancer data
# python manage.py audit            — coverage statistics
# python manage.py test-allele Cy   — look up allele across all sources
# python manage.py test-genotype "w[1118]; P{...}/CyO; +; +"
```

Current implementation note: `manage.py` now ships `examine`, `audit-bloomington`, `marker-inventory`, `discover-flybase`, `download-flybase`, `ingest-flybase`, `ingest-balancers`, `test-allele`, `audit-live-resolution`, and `test-genotype`.

### 3.5 Phase 1 Validation

Run these checks before proceeding:

```python
# 1. Dictionary coverage: can we resolve every allele in the user's actual stocks?
#    Parse all genotypes in the user's stock collection.
#    For each allele token, check: is it in VISUAL_MARKER_DICTIONARY, or resolvable via FlyBase, or a construct?
#    Report: % resolved, % unresolved, list of unresolved tokens.

# 2. FlyBase coverage: for each entry in VISUAL_MARKER_DICTIONARY,
#    does FlyBase have a matching phenotype row, allele-description row,
#    construct row, or stock-linked context?
#    Report which source is useful for each marker.

# 3. Balancer coverage: does balancer_definitions contain CyO, TM3, TM6B, FM7a, FM7c, SM6a?
#    Does each have at least one dominant marker?

# 4. Stock bridge: pick 10 random BDSC stock numbers from bloomington.csv.
#    Can we look them up in flybase_stock_alleles and recover the FlyBase stock row?
#    Then test whether genotype parsing + allele/gene joins recover useful phenotype and allele metadata.
```

---

## 4. Phase 2: Phenotype Computation + Preview

**Goal:** Given any genotype string, predict its visible phenotype. Ship as a standalone UI feature.

**Implementation status as of 2026-04-17:** Partially implemented and already exposed in the main app, but not as a standalone optimizer blueprint. The current repo ships `compute_marker_phenotype()` in `compute.py`, higher-level stock/cross prediction helpers in `predictor.py`, phenotype cache builders, cache validation helpers, stock-view phenotype preview UI, cross-view parent/offspring previews, explorer summaries, and admin/home backfill actions.

### 4.1 BDSC Grammar Parser

Create `flymanager/utils/phenotypes/parser.py`:

```python
def parse_gene_package(package_str: str) -> dict:
    """
    Parse a chromosome arm content string into structured components.
    
    Rules (from BDSC nomenclature):
    - Constructs: P{...}, PBac{...}, Mi{...}, TI{...}, M{...}
      Format: ends{marker_genes=construct_name}insertion_id
    - Balancers: core_symbol, variant_markers (e.g., "TM3, Sb[1]")
    - Classical alleles: gene[allele] (e.g., "w[1118]", "Sb[1]")
    - Aberrations: Type(Chromosome)Id (e.g., "Df(2R)Exel6069")
    - Alleles separated by spaces
    - Aberrations separated from alleles by comma+space
    
    Returns:
    {
        "constructs": [{"full": str, "markers": [str], "type": str}],
        "classical_alleles": [{"token": str, "gene_stem": str, "allele_spec": str}],
        "balancers": [{"symbol": str, "variant_markers": [str]}],
        "aberrations": [str],
        "unresolved": [str],
    }
    """
```

**Test exhaustively:**
```python
# Parse every unique gene package in bloomington.csv
# Report: total packages, % with constructs, % with classical alleles, 
#         % with balancers, % with unresolved tokens
# Examine every unresolved token — are they real parsing failures or edge cases?

# Critical test cases from actual BDSC genotypes:
test_cases = [
    "P{w[+mC]=Orco-GAL4.W}11.17",                    # Simple construct
    "TM3, Sb[1] Ser[1]",                               # Balancer with markers
    "betaTub60D[2] Kr[If-1]",                           # Multiple classical alleles
    "Df(2R)Exel7131, P{w[+mC]=XP-U}Exel7131",         # Aberration + construct
    "In(1)FM7, sn[X2] v[Of] B[1]",                     # Balancer with full notation
    "TM6B, Tb[1] Hu[1]",                                # TM6B variant
    "P{w[+mW.hs]=GawB}Orco[GAL4-W] P{w[+mC]=UAS-GCaMP6f}attP40",  # Two constructs
    "+",                                                 # Wild-type
]
```

### 4.2 Three-Source Phenotype Resolver

Create `flymanager/utils/phenotypes/resolver.py`:

```python
def resolve_allele_phenotype(token: str, chromosome: int, db) -> dict | None:
    """
    Resolution pipeline (in priority order):
    
    1. Is it a known balancer? → look up balancer_definitions → return all dominant markers
    2. Extract gene stem. Is stem in VISUAL_MARKER_DICTIONARY? → return visual phenotype
    3. Is it a construct? → extract marker genes → return construct marker effects
    4. Look up gene stem / allele token in flybase_allele_genes, allele descriptions,
       and construct descriptions → recover gene, tool, insertion, and stock context
    5. Query flybase_phenotypes for enrichment annotations and references
    6. If still unresolved → return None (phenotypically silent for our purposes)
    
    Returns: {body_part, effect, dominance, scoring_confidence, source, ...} or None
    """
```

### 4.3 Phenotype Computation

Create `flymanager/utils/phenotypes/compute.py`:

```python
def compute_phenotype(genotype: str, sex: str, db) -> dict:
    """
    Full phenotype computation:
    1. Parse genotype into components via get_genetic_components()
    2. For each chromosome arm, parse gene package, resolve each token
    3. Collect all phenotype contributions
    4. Apply dominance (dominant > semi-dominant > recessive)
    5. Apply epistasis rules (e.g., cn + bw = white eyes)
    6. Count mini-white copies across all arms
    7. Check viability (homozygous lethals)
    8. Check fertility
    9. Generate human-readable summary
    
    Returns:
    {
        "visible_phenotypes": [{"body_part", "effect", "source_allele", "dominance", "confidence"}],
        "mini_white_copies": int,
        "mini_white_intensity": str,  # "white"/"pale_orange"/"orange"/"dark_orange"/"red"
        "is_viable": bool,
        "is_fertile": bool,
        "lethal_alleles": [str],
        "phenotype_summary": str,     # "Cy, w, Sb"
        "sex_specific_notes": [str],
        "warnings": [str],
    }
    """
```

Current implementation note: the closest shipped equivalent is split across two layers:

- `compute_marker_phenotype(genotype, sex)` returns expressed markers, unresolved tokens, mini-white copy counts, construct annotations, split-system annotations, and a short marker summary.
- `predict_individual_phenotype()` and `predict_stock_phenotype()` add confidence labels, sex-aware stock projections, warnings, and cache-ready payloads.

The currently shipped code now includes FlyBase-derived viability/fertility consequences, lethal and sterile allele annotations, and the formal epistasis layer used by the higher-level prediction helpers. The remaining gap is that the standalone preview path is still narrower than the later optimizer-target model, and the richer viability-aware pruning/search behavior lives in the Phase 4 simulator rather than in a single monolithic `compute_phenotype()` API.

### 4.4 Phenotype Preview UI

Create a Flask route and template that lets users type a genotype and see its predicted phenotype:

```python
# flymanager/app/routes/optimizer.py
@bp.route('/crossing_optimizer/preview_phenotype', methods=['POST'])
@login_required
def preview_phenotype():
    genotype = request.json.get("genotype")
    sex = request.json.get("sex", "female")
    qc_ok, normalized = qc_genotype(genotype)
    if not qc_ok:
        return jsonify({"error": normalized}), 400
    phenotype = compute_phenotype(normalized, sex, db)
    return jsonify(phenotype)
```

Current implementation note: this exact optimizer blueprint route does not exist. Instead, FlyManager now exposes a standalone phenotype sandbox at `GET/POST /stock/phenotype_preview`, and phenotype preview is also embedded into existing stock and cross record views, with manual refresh endpoints:

- `POST /stock/view/<unique_id>/refresh_phenotype`
- `POST /cross/view_cross/<unique_id>/refresh_phenotype`

These routes rebuild cached phenotype payloads and render them inside the normal stock/cross UI.

### 4.5 Phase 2 Validation

This is the most important validation step. Run `compute_phenotype()` on the user's actual stocks and crosses:

```python
# 1. Parse every genotype in the user's stock collection
#    Run compute_phenotype() on each
#    Report: any crashes? Any stocks where viability prediction seems wrong?

# 2. For crosses with selection instructions in Comments:
#    Extract the mentioned markers from Comments
#    Run compute_phenotype() on the parent genotypes
#    Check: does our phenotype prediction include the markers the user actually used?
#    This is ground-truth validation.

# 3. Run compute_phenotype() on 20 well-known BDSC stock genotypes 
#    (pick stocks with clear marker descriptions in BDSC comments)
#    Manually verify predictions are correct.

# 4. Test edge cases:
#    - Genotype with 3 mini-white constructs (dosage model)
#    - Genotype with cn + bw on same chromosome (epistasis → white eyes)
#    - Male with X-linked recessive (should be visible)
#    - Female with X-linked recessive (should NOT be visible)
#    - Genotype with TM6B, Tb[1] (should warn about Tb reversion)
```

**This phase should ship as a standalone feature.** Users can start getting value from phenotype predictions before the optimizer exists.

---

## 5. Phase 3: Genetic Constraint Engine

**Goal:** Build the Drosophila-specific biological rules that the optimizer needs.

**Implementation status as of 2026-04-17:** Complete for the Phase 3 baseline. The repo now ships `flymanager/utils/constraints/` with `balancer_selection.py`, `interchromosomal.py`, `marker_stability.py`, `target_validation.py`, and `yield_estimator.py`, plus regression tests that cover conservative Drosophila-specific validation and risk rules.

### 5.1 Balancer Selection by Cytological Position

Create `flymanager/utils/constraints/balancer_selection.py`:

```python
def select_optimal_balancer(
    allele_gene_symbol: str,
    chromosome: int,
    db,
    existing_balancers: list[str] = [],
    excluded_balancers: list[str] = [],
) -> dict:
    """
    1. Look up cytological position of allele's gene (from gene_map_table or FlyBase)
    2. For each candidate balancer on this chromosome:
       a. Check: does its inversion coverage bracket the gene's position?
       b. Score: coverage quality, marker stability, interchromosomal effect
    3. Return best balancer with score and warnings
    
    If cytological position data unavailable, fall back to simple per-chromosome 
    default selection (CyO for chr2, TM3 for chr3).
    """
```

### 5.2 Target Validation

Create `flymanager/utils/constraints/target_validation.py`:

```python
def validate_target(target_genotype: str, target_sex: str, db) -> dict:
    """
    Pre-search validation:
    1. Is the target genotype syntactically valid? (qc_genotype)
    2. Is it biologically viable? (no homozygous lethals without balancer)
    3. Is it fertile? (no homozygous sterile if needs self-propagation)
    4. Is it maintainable? (can this stock self-propagate?)
    5. Does it require recombination? (alleles in cis not found in any stock)
    6. What background is required? (w- for mini-white tracking?)
    
    Returns: {viable, fertile, maintainable, issues[], suggested_alternative, 
              maintenance_strategy, requires_recombination, background_requirements}
    """
```

### 5.3 Other Constraints

- `interchromosomal.py` — count simultaneous balancers, compute risk score
- `marker_stability.py` — MARKER_STABILITY dictionary, stability assessment
- `yield_estimator.py` — expected correct progeny per vial, parallel vial recommendations

### 5.4 Phase 3 Validation

```python
# 1. Balancer selection: for 10 well-known genes at different cytological positions,
#    verify the selected balancer makes biological sense

# 2. Target validation: test these specific cases:
#    - "Cy/Cy; +; +; +" → should detect homozygous lethal
#    - "w[1118]; P{...}/CyO; P{...}/TM3, Sb[1]; +" → should be viable, balanced
#    - "w[1118]; P{A}attP40 P{B}attP40/CyO; +; +" → requires recombination (two cis insertions at attP40)

# 3. Marker stability: verify Tb scores lower than Cy, B scores lowest
```

---

## 6. Phase 4: Enhanced Cross Simulator

**Goal:** Wrap `cross_genotypes()` to produce phenotype-annotated progeny tables with identifiability checking.

**Implementation status as of 2026-04-17:** Substantial partial implementation. The repo now ships `flymanager/utils/crossing/simulator.py` with `simulate_cross()`, `cross_genotypes_with_phenotypes()`, and `evaluate_cross_directions()`, plus `flymanager/utils/phenotypes/identifiability.py` for sibling-aware sortability analysis. Cross phenotype caches now persist simulator summaries and reciprocal-direction evaluations, and the cross detail view surfaces prune reasons, viable-pool percentages, low-confidence sorting cues, and reciprocal direction guidance. Remaining work is mainly validation depth and wiring these outputs into the future optimizer/search layer.

### 6.1 Enhanced Simulator

Create `flymanager/utils/crossing/simulator.py`:

```python
def cross_genotypes_with_phenotypes(male: str, female: str, db) -> list[dict]:
    """
    1. Call existing cross_genotypes(male, female)
    2. For each progeny class, call compute_phenotype()
    3. Filter out inviable classes (homozygous lethal)
    4. Recalculate probabilities among viable classes
    5. Return enriched progeny table
    """
```

Current implementation note: this wrapper now exists via `simulate_cross()` and `cross_genotypes_with_phenotypes()` in `flymanager/utils/crossing/simulator.py`. The returned rows include phenotype payloads, target-validation output, yield estimates, viability flags, prune reasons, viable-only renormalized probabilities, and sibling identifiability annotations.

### 6.2 Identifiability Checker

Create `flymanager/utils/phenotypes/identifiability.py`:

```python
def check_identifiability(progeny_classes: list[dict], target_indices: list[int]) -> dict:
    """
    Can the target class be distinguished from ALL other viable classes?
    
    Compare phenotype vectors pairwise. For each pair (target, other):
    - Find body parts where they differ
    - Assess confidence of each difference
    - Flag: mini-white dosage as sole discriminator (low confidence)
    - Flag: Tb as sole discriminator (reversion risk)
    
    CRITICAL: This must use FULL-GENOTYPE phenotypes, not per-chromosome.
    Eye color depends on X-linked w status AND autosomal markers simultaneously.
    
    Returns: {identifiable, distinguishing_markers, confidence, 
              selection_instructions, warnings, confusable_classes}
    """
```

Current implementation note: `flymanager/utils/phenotypes/identifiability.py` now implements this sibling-aware comparison layer. It flags confusable sibling classes, exclusion-only sorting, mini-white-only and Tb-only discriminator cases, and low-confidence sorting dependence, and produces explicit selection instructions for each viable progeny class.

### 6.3 Cross Direction Evaluation

```python
def evaluate_cross_directions(parent_a: str, parent_b: str, db) -> dict:
    """
    Try both directions (A♂ × B♀ and B♂ × A♀).
    Compare identifiability and yield for each.
    Recommend the better direction with rationale.
    Consider: X-linked markers, virgin collection ease (balanced female = easier).
    """
```

Current implementation note: this reciprocal-evaluation layer now exists in `flymanager/utils/crossing/simulator.py`. It compares forward and reverse summaries, emits a recommended direction plus rationale/operational notes, and the compact summary is now cached and rendered in the cross detail UI.

### 6.4 Phase 4 Validation

```python
# 1. Run cross_genotypes_with_phenotypes() on 5 real crosses from the user's history
#    Compare progeny phenotype predictions against user's actual selection instructions

# 2. Test identifiability on known scenarios:
#    - w[1118]/Y; P{w+}/CyO; +/+ × w[1118]; +; +/+ 
#      → Can we distinguish P{w+}/CyO (orange eyes, curly) from +/+ (white eyes, straight)?
#    - Test case where mini-white dosage is the ONLY difference → should warn

# 3. Test cross direction: X-linked marker cross
#    - w[1118] female × w[+] male → F1 females are w[+]/w[1118] (red eyes)
#    - w[+] female × w[1118] male → F1 females are w[+]/w[1118] (red eyes)
#    - But F1 MALES differ: first cross gives w[+]/Y, second gives w[1118]/Y
```

---

## 7. Phase 5: Search Engine

**Goal:** Given a target genotype + available stocks, find optimal crossing schemes.

**Implementation status as of 2026-04-17:** Not started beyond the Phase 4 simulator foundation. `flymanager/utils/crossing/` exists today, but it currently contains only the simulator layer and exports; template generation, optimizer search, scoring, decomposition, memoization, cache, and batch planning modules are still absent.

### 7.1 Template Definitions

Create `flymanager/utils/crossing/templates.py` with 4 formalized templates:

1. **Balancer Introduction** — get an allele onto a balancer (2 generations)
2. **Chromosome Combination** — combine balanced alleles from different chromosomes (3 generations)
3. **Homozygosing** — make a balanced allele homozygous (1 generation, if viable)
4. **Transgene Stacking** — combine multiple constructs (2 generations)

Each template is a dataclass with:
- `applicable_when` — function checking if template fits the target
- `required_inputs` — what stocks/alleles are needed
- `steps` — parameterized step skeletons
- `instantiate(stocks, db)` — fills in the template with specific stocks
- `validate(db)` — runs forward simulation and identifiability checking

### 7.2 Optimizer Pipeline

Create `flymanager/utils/crossing/optimizer.py`:

```python
def find_optimal_schemes(
    target_genotype: str,
    target_sex: str,
    available_stocks: list[dict],
    db,
    max_generations: int = 8,
    user_preferences: dict = None,  # excluded_balancers, fluorescence_scope, etc.
) -> list[CrossingScheme]:
    """
    1. Preprocess target (validate, determine maintenance strategy, check recombination needs)
    2. Decompose target into per-chromosome requirements
    3. Match against templates — find all applicable templates
    4. For each applicable template:
       a. Enumerate valid stock combinations (bounded search)
       b. Instantiate template with each combination
       c. Forward simulate: cross_genotypes_with_phenotypes() at each step
       d. Check identifiability at each step (full-genotype)
       e. Check all constraints (balancer position, interchromosomal, stability, yield)
       f. Score the scheme
    5. Rank all valid schemes by score
    6. Return top-k
    """
```

### 7.3 Scoring

Create `flymanager/utils/crossing/scorer.py`:

Default weights (user-tunable):
- `w_generations = 1.0` (each costs ~2 weeks)
- `w_confidence = 0.5` (identifiability reliability)
- `w_yield = 0.3` (higher yield = fewer vials)
- `w_stability = 0.3` (marker reversion risk)
- `w_interchrom = 0.2` (multi-balancer risk)
- `w_vials = 0.1` (practical convenience)
- `w_virgin = 0.1` (virgin collection ease)

### 7.4 Phase 5 Validation

Test against gold standard schemes:

```python
# 1. Simple balancer introduction:
#    Target: "w[1118]; P{w[+mC]=Orco-GAL4.W}11.17/CyO; +; +"
#    Available: w[1118] stock + BDSC Orco-GAL4 stock + CyO balancer stock
#    Expected: 2-generation scheme

# 2. Two-chromosome assembly:
#    Target: "w[1118]; P{...}/CyO; P{...}/TM3, Sb[1]; +"
#    Available: stock with chr2 insertion + stock with chr3 insertion + double balancer stock
#    Expected: 3-generation scheme

# 3. Impossible target:
#    Target: "w[1118]; CyO; +; +" (CyO homozygous → lethal)
#    Expected: optimizer rejects with suggestion "maintain as +/CyO"

# 4. Compare optimizer output against user's ACTUAL crossing history
#    for the Orco Coactivation Master Line
```

---

## 8. Phase 6: UI + Integration

**Implementation status as of 2026-04-17:** Not started beyond phenotype preview surfaces inside existing stock/cross/admin pages.

- Flask Blueprint with routes for preview, compute, simulate, batch
- Templates: index, preview, results, scheme_detail, batch, simulate, preferences
- SocketIO for progress on long computations
- "Start This Scheme" → auto-creates cross in Cross Explorer
- Multi-target batch: sequential with shared intermediate detection (MVP)

---

## 9. Key Technical Decisions (Refer to Design Doc)

- **Genotype format must not change** — all new code produces/consumes `qc_genotype()` format
- **No intra-arm recombination in Phase 1** — detect and report rather than plan incorrectly
- **Template search, not CSP** — CSP solver deferred to Phase 2 extension
- **Phenotypic equivalence vs stock identity** — two levels of genotype matching
- **Mini-white is NOT binary** — count copies, warn on dosage-based sorting
- **Marker stability varies** — Cy(0.95) > Sb(0.80) > Tb(0.50) > B(0.40)
- **Interchromosomal effect** — penalize 3+ simultaneous balancers
- **No male recombination** — exploit for cis-preservation
- **Chromosome 4** — no recombination, simple balancing with ey[D]

---

## 10. Dependencies to Add

```toml
# pyproject.toml
cachetools = "^5.3"          # LRU caching for allele resolution
pronto = "^2.5"              # OBO/OWL ontology parser (for DPO)
beautifulsoup4 = "^4.12"     # BDSC page scraping
lxml = "^5.1"                # HTML parser for BeautifulSoup
```

Implementation status as of 2026-04-17: these dependencies are still planned and are not listed in the repository `pyproject.toml`. The current FlyBase downloader/examiner uses only standard-library networking and HTML parsing.

---

## 11. File Tree

Already present in the repository:

```text
flymanager/utils/phenotypes/
├── __init__.py
├── backfill.py
├── compute.py
├── construct_markers.py
├── epistasis.py
├── flybase_pipeline.py
├── identifiability.py
├── parser.py
├── predictor.py
├── resolver.py
├── visual_markers.py

flymanager/utils/constraints/
├── __init__.py
├── _shared.py
├── balancer_selection.py
├── interchromosomal.py
├── marker_stability.py
├── target_validation.py
├── yield_estimator.py

flymanager/utils/crossing/
├── __init__.py
├── simulator.py

flymanager/utils/phenotypes/data/
├── balancer_ingest.py
├── downloads.py
├── examiner.py
├── flybase_ingest.py
├── manage.py
```

Still planned to create:

```text
flymanager/utils/phenotypes/
├── genotype_matching.py    # Phenotypic equivalence + stock identity
├── split_gal4.py           # AD/DBD awareness

flymanager/utils/phenotypes/data/
├── __init__.py             # optional package marker only

flymanager/utils/crossing/
├── optimizer.py            # find_optimal_schemes()
├── templates.py            # Formalized crossing templates
├── scorer.py               # Multi-dimensional scoring
├── decomposer.py           # Target decomposition
├── memoizer.py             # Sub-scheme cache
├── models.py               # Dataclasses
├── cache.py                # MongoDB caching
├── batch.py                # Multi-target (MVP: sequential)

flymanager/app/routes/optimizer.py
flymanager/app/templates/optimizer/*.html

data/flybase/               # User-downloaded, gitignored
tests/                      # 18+ test files
```text

---

## 12. Order of Operations (Summary)

```text
Phase 0              → Already largely in repo: discover/download data, examine, write EXAMINATION_REPORT.md
                        REMAINING: optional deeper source-quality audits only when a new FlyBase release changes assumptions
Phase 1              → Already in repo: dictionary, parser, resolver, Mongo ingestion, balancer ingest, and hybrid cache/runtime plumbing
                        REMAINING: no blocking Phase 1 architecture work
Phase 2              → Complete in repo: shipped phenotype previews, FlyBase consequence annotations, sibling confusability analysis, and standalone phenotype sandbox
                        REMAINING: no blocking Phase 2 checklist work
Phase 3 (Weeks 5-7)  → Complete in repo: balancer selection, target validation, risk scoring, and yield heuristics
                        REMAINING: keep these validators stable as optimizer/search pruning inputs
Phase 4 (Weeks 7-9)  → Substantially implemented in repo: enhanced simulator, identifiability checker, reciprocal evaluation, and cross-view/cache integration
                        REMAINING: validate against real crosses with known selection criteria and broaden regression coverage beyond synthetic cases
Phase 5 (Weeks 9-13) → Templates, optimizer, scoring
                        VALIDATE: gold standard schemes, comparison to user's history
Phase 6 (Weeks 13-16)→ UI, integration, batch mode
                        VALIDATE: end-to-end user testing
```

**Updated sequencing note:** Phase 0 through Phase 3 are complete and the first substantial slice of Phase 4 is now shipped. The next real implementation boundary is finishing Phase 4 validation and then building Phase 5 search/optimizer modules on top of the new simulator outputs.

### 12.1 Recommended Next Steps

1. Add a real historical-cross validation corpus so the Phase 4 simulator can be checked against actual lab selection outcomes rather than only synthetic regression fixtures.
2. Start the Phase 5 search package with `templates.py`, `optimizer.py`, and `scorer.py`, reusing `simulate_cross()`, `evaluate_cross_directions()`, and the Phase 3 validators as pruning/scoring inputs.
3. Create the dedicated optimizer blueprint and UI flows so the new simulator rationale can be reused outside the existing cross detail page.
