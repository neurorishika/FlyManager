# Design Document: Automated Crossing Scheme Optimizer for FlyManager

**Author:** Rishika Mohanta / Claude  
**Date:** April 2026  
**Status:** Draft v6 (final)  
**Repository:** `neurorishika/FlyManager`

---

## 1. Executive Summary

This document specifies an **Automated Crossing Scheme Optimizer** for FlyManager. Given a target genotype, the system computes optimal multi-generation crossing schemes from available stocks, minimizing generations, maximizing yield, and guaranteeing phenotypic identifiability at every step.

### 1.1 External Data Sources

1. **FlyBase Precomputed Bulk Downloads** — canonical stock catalog, allele-to-gene bridge, phenotype enrichment, construct metadata, and gene-position support
2. **Drosophila Phenotype Ontology (DPO)** — formal phenotype classification (~200 terms)
3. **BDSC Nomenclature Grammar** — formal genotype string parsing rules
4. **BDSC Core Balancer Definitions** — balancer inversions, breakpoints, and markers

### 1.2 System Architecture (7 Subsystems)

1. **Phenotype Knowledge Base** — three-source hybrid: manual visual marker dictionary + FlyBase bulk data + construct marker parsing
2. **Phenotype Computation Engine** — computes visible phenotype of any genotype
3. **Genetic Constraint Engine** — Drosophila-specific rules: balancer coverage, interchromosomal effects, marker stability, male recombination, chromosome 4, impossible targets
4. **Cross Simulator** — extends `cross_genotypes()` with phenotype-aware output
5. **Search Engine** — template-based planning with exhaustive forward validation (CSP deferred to Phase 2)
6. **Interactive Optimizer UI** — phenotype preview, scheme customization, multi-target batching
7. **Data Pipeline** — ingestion, monthly updates, validation

### 1.3 Critical Design Decision: Three-Source Hybrid Phenotype Architecture

| Purpose | Primary Source | Why |
|---------|---------------|-----|
| **Visual marker descriptions** (what Cy/Sb/w look like) | Manual dictionary (~50-100 entries) | The examined FlyBase bundle has many `visible` phenotype rows, but not concise microscope-facing descriptions for common sorting markers |
| **Lethality, sterility, conditional phenotypes** | FlyBase `genotype_phenotype_data` bulk TSV | Large literature-curated enrichment source with phenotype classes, qualifiers, and references |
| **Transgene selectable markers** (w+mC, y+, v+) | Construct parsing + FlyBase `transgenic_construct_descriptions` | Construct names still encode selectable markers directly, and FlyBase adds encoded-product/regulatory-region metadata |
| **Stock sourcing across repositories** | FlyBase `stocks` TSV | Canonical stock catalog spanning Bloomington, Vienna, Kyoto, NIG-Fly, KDRC, FlyORF, NDSSC, and others |
| **Allele-to-gene bridge** | FlyBase `fbal_to_fbgn` TSV | Direct FBal → FBgn / gene-symbol bridge for joining stock, phenotype, and allele metadata |
| **Balancer position support** | FlyBase `gene_map_table` TSV | Direct gene-symbol → recombination / cytological / sequence position mapping |

These sources are NOT interchangeable. The manual dictionary is still the **guaranteed backbone** for identifiability. FlyBase is now confirmed as the best canonical enrichment and stock-source layer. Construct parsing remains necessary because FlyManager still starts from genotype strings, not normalized FlyBase entities.

---

## 2. Existing Codebase Context

### 2.1 Repository Structure

```
FlyManager/
├── flymanager/
│   ├── app/                     # Flask app, routes, templates, static, services
│   ├── utils/
│   │   ├── genetics.py          # cross_genotypes(), qc_genotype(), get_genetic_components()
│   │   ├── mongo/               # All MongoDB CRUD
│   │   ├── converter.py, labels.py, scanner.py
├── data/bloomington.csv
├── tests/
```

### 2.2 Key Components

- **Genotype format:** `"X; chr2; chr3; chr4"`, normalized by `qc_genotype()`. **Must not change.**
- **Cross simulator:** `cross_genotypes(male, female)` → `[genotype, sex, probability]`. Treats each chromosome arm as **atomic** (no intra-arm recombination).
- **BDSC integration:** Monthly CSV → `genesX/2nd/3rd/4th` collections.
- **Stack:** Python 3.9-3.12, Flask, PyMongo, NumPy, Pandas.

### 2.3 Cross Simulator Limitation

`cross_genotypes()` cannot model intra-arm recombination. Consequence: **the optimizer cannot plan schemes requiring separation of linked alleles on the same arm.** If a target requires a cis combination not in any stock, the optimizer reports "requires recombination — not supported in Phase 1" rather than producing an incorrect scheme. Phase 2 adds female recombination modeling.

### 2.4 Genotype Matching Semantics

Two matching levels:

- **Phenotypic equivalence** (for planning): `w[*]` ≡ `w[1118]` — same phenotype. Gene stem matching.
- **Stock identity** (for sourcing): `w[*]` ≠ `w[1118]` — exact match after `qc_genotype()`.

The optimizer uses phenotypic equivalence for identifiability and stock identity for sourcing.

### 2.5 Known Scope Exclusions

Not supported: Y chromosome, compound chromosomes, translocations, deficiencies/duplications, temperature-sensitive modeling, genetic background effects, intra-arm recombination (Phase 1).

---

## 3. External Data Sources

### 3.1 FlyBase Bulk Downloads

```bash
# Use release-specific links from the FlyBase bulk-data page or the FlyManager downloader.
wget https://s3ftp.flybase.org/releases/FB2026_01/precomputed_files/alleles/genotype_phenotype_data_fb_2026_01.tsv.gz
wget https://s3ftp.flybase.org/releases/FB2026_01/precomputed_files/alleles/fbal_to_fbgn_fb_2026_01.tsv.gz
wget https://s3ftp.flybase.org/releases/FB2026_01/precomputed_files/alleles/dmel_classical_and_insertion_allele_descriptions_fb_2026_01.tsv.gz
wget https://s3ftp.flybase.org/releases/FB2026_01/precomputed_files/stocks/stocks_FB2026_01.tsv.gz
wget https://s3ftp.flybase.org/releases/FB2026_01/precomputed_files/transposons/transgenic_construct_descriptions_fb_2026_01.tsv.gz
wget https://s3ftp.flybase.org/releases/FB2026_01/precomputed_files/alleles/split_system_combinations_fb_2026_01.tsv.gz
wget https://s3ftp.flybase.org/releases/FB2026_01/precomputed_files/genes/gene_map_table_fb_2026_01.tsv.gz
```

Observed value of the examined FB2026_01 files:

- `stocks`: foundational. Best source for canonical stock sourcing and repository breadth.
- `genotype_phenotype_data`: foundational. Best source for phenotype enrichment, viability, sterility, and qualifier logic.
- `fbal_to_fbgn`: foundational. Core allele→gene join table.
- `dmel_classical_and_insertion_allele_descriptions`: foundational. Strong allele class / insertion / stock-count enrichment source.
- `transgenic_construct_descriptions`: foundational. Strong construct metadata source with encoded products, regulatory regions, and stock counts.
- `gene_map_table`: foundational for constraints. Best currently examined source for recombination and cytological position support.
- `split_system_combinations`: secondary. Useful for split-GAL4 and modern driver-combination handling, but not core to the general genetics pipeline.

### 3.2 DPO Ontology

Used for structured lethality/sterility/phenotypic class annotation. NOT the primary source for visual descriptions (that's the manual dictionary).

### 3.3 BDSC Nomenclature + Balancer Definitions + Breakpoints

Formal grammar for parsing. Scrapeable balancer data including inversion breakpoints and marker stability.

---

## 4. Subsystem 1: Phenotype Knowledge Base

### 4.1 BDSC Grammar Parser

Parses chromosome arm content into constructs, classical alleles, balancers, aberrations, unresolved tokens.

### 4.2 Manual Visual Marker Dictionary (PRIMARY for identifiability)

~50-100 manually curated Python dictionary entries. Version-controlled. Covers every allele used in everyday fly sorting: Cy, Sb, Tb, Hu, B, w, y, e, cn, bw, st, vg, dp, Ser, D, Sp, Bl, sn, f, L, ey, Ubx, MKRS, Sco, etc. Each entry specifies: body_part, effect description, dominance, scoring_confidence, chromosome, homozygous_lethal, reversion notes.

### 4.3 Construct Marker Extraction (PRIMARY for transgenes)

Regex extraction of w+mC, y+, v+ from construct names following transposon grammar.

### 4.4 Mini-White Dosage Model

Tracks copy count. Warns when sorting relies on dosage differences (confidence ~0.4) vs presence/absence (confidence ~0.95).

### 4.5 FlyBase Data (ENRICHMENT)

Primary use after examining the bundle: phenotype enrichment, stock normalization support, allele/construct metadata, and gene-position support.

- `genotype_phenotype_data`: phenotype classes such as `visible`, `lethal`, `viable`, `female sterile`, plus qualifiers like `recessive`, `dominant`, stage, sex, and clone context.
- `fbal_to_fbgn`: normalize allele IDs to genes.
- `allele_descriptions`: capture allele class, insertion/tool context, descriptions, and stock counts.
- `construct_descriptions`: capture encoded product/tool, regulatory region, tags, and stock counts.
- `gene_map_table`: support position-aware balancer logic.

Important limitation: the FlyBase phenotype table is not a substitute for a manual visual marker dictionary when the question is what a fly looks like under a stereoscope.

### 4.6 BDSC↔FlyBase Name Bridge

Bridge strategy after Phase 0:

- Stock-level bridge: source collection + stock number → FlyBase `stocks` row.
- Allele/gene bridge: parsed genotype tokens → `fbal_to_fbgn` → FBgn / gene symbol.
- Enrichment joins: gene / allele context → phenotype, allele-description, and construct-description tables.

The stocks TSV is a strong stock-catalog bridge, but not a direct stock# → FBal lookup table. Allele-level normalization still requires genotype parsing plus FlyBase joins.

### 4.7 LLM Fallback

Claude API for unresolved alleles. Low confidence (0.3-0.5). Cached in MongoDB.

### 4.8 MongoDB Collections and Indexes

Collections: `flybase_phenotypes`, `flybase_allele_genes`, `flybase_stock_alleles`, `flybase_constructs`, `flybase_split_combinations`, `balancer_definitions`, `epistasis_rules`, `crossing_scheme_cache`, `sub_scheme_library`.

Required indexes on: `allele_symbol`, `gene_symbol`, `gene_id`, `stock_number`, `collection_short_name`, `FBst`, `symbol` (balancers).

---

## 5. Subsystem 2: Phenotype Computation Engine

Three-source computation: for each token, check manual dictionary first (guaranteed coverage for common markers), then construct parsing, then FlyBase enrichment (phenotype classes, qualifiers, lethality/sterility, allele context, construct metadata). Apply dominance, epistasis, mini-white dosage, and full-genotype interactions.

---

## 6. Subsystem 3: Genetic Constraint Engine

### 6.1 Balancer Selection by Cytological Position

Match allele position against balancer inversion breakpoints. Prefer balancers with distal breakpoints covering the mutation. Use FlyBase gene → cytological map position.

### 6.2 Interchromosomal Effect

Penalize 3+ simultaneous balancers. Track per-step, multiply risk by generation count.

### 6.3 Marker Stability

Quantitative scores: Cy(0.95) > Hu(0.85) > Sp/Sb(0.80) > Ser(0.75) > D(0.65) > Tb(0.50) > B(0.40). Warn when sole marker has stability <0.6.

### 6.4 No Male Recombination

Males transmit arms intact — exploit for cis-preservation. Annotate each cross step.

### 6.5 Chromosome 4

No recombination. Use ey[D] or ci[D] as balancers.

### 6.6 Impossible Target Detection

Detect homozygous lethal/sterile targets. Suggest balanced alternatives.

### 6.7 Recombination-Required Detection

If target needs cis combination not in any stock and no assembly path exists, report gracefully.

### 6.8 Full-Genotype Identifiability

Chromosome decomposition is a planning heuristic. Validation uses full genotype across all chromosomes.

### 6.9 Expected Progeny Yield

~100 progeny/vial. Warn when <3 expected correct. Recommend parallel vials.

---

## 7. Subsystem 4: Cross Simulator

Wraps `cross_genotypes()` with phenotype computation, identifiability checking, cross direction evaluation (tries both, picks better), practical feasibility, yield estimation.

---

## 8. Subsystem 5: Search Engine

### 8.1 Phase 1 Approach: Template + Forward Search

**CSP deferred to Phase 2.** Phase 1 uses:

1. **Template matching** — 4 formalized templates (balancer introduction, chromosome combination, homozygosing, transgene stacking) with parameterized step skeletons
2. **Stock enumeration** — for each template, enumerate valid instantiations from available stocks
3. **Forward simulation** — simulate every cross, check identifiability at every step, check all constraints
4. **Scoring** — multi-dimensional with justified defaults: w_gen=1.0, w_confidence=0.5, w_yield=0.3, w_stability=0.3, w_interchrom=0.2, w_vials=0.1, w_virgin=0.1 (user-tunable)

### 8.2 Balancer Look-Ahead

When choosing a balancer, check marker contrast at ALL subsequent steps.

### 8.3 Target Preprocessing

Validate viability, detect recombination requirements, determine maintenance strategy, identify background requirements.

### 8.4 Multi-Target Batch (MVP)

Sequential optimization with shared intermediate detection. Full Steiner-tree optimization is Phase 2.

---

## 9. Subsystem 6: Interactive UI

Phenotype Preview (standalone, ships Phase 2). Interactive optimizer with user preferences. Multi-target Gantt timeline.

---

## 10. Data Pipeline

### 10.1 Phase 0: Critical Data Examination

Answer these questions BEFORE writing code:
1. Do Cy[1], Sb[1], Tb[1], w[1] have "visible" annotations in `genotype_phenotype_data`?
2. Does stocks TSV bridge BDSC stock# → FlyBase allele IDs?
3. Are FBbt anatomy terms in the phenotype file or separate?
4. What's the qualifier format?

Observed Phase 0 answers from FB2026_01:

- Common markers do have many `visible` rows in `genotype_phenotype_data`.
- The useful signal is phenotype class + qualifier enrichment, not direct visual descriptions.
- The `stocks` table is highly useful for stock sourcing, but not sufficient alone for allele normalization.
- The current examined phenotype file does not provide the hoped-for `FBbt` anatomy coverage for the critical-marker question.

Conclusion: manual dictionary remains primary for visible marker descriptions; FlyBase is the canonical enrichment and stock-source layer.

### 10.2 Network

Downloads can run outside the container or through the FlyManager release-aware downloader. Files live in `data/flybase/`. CLI ingestion and examination operate on local files and can read `.tsv.gz` directly.

---

## 11. Implementation Plan

| Phase | Weeks | Deliverable |
|-------|-------|-------------|
| 0: Data Examination | 1 | Confirmed/revised architecture |
| 1: Ingestion + Manual Dictionary | 1-3 | All data in MongoDB. Dictionary complete. |
| 2: Phenotype + Preview | 3-5 | **Phenotype Preview UI live** |
| 3: Genetic Constraints | 5-7 | All constraints computable |
| 4: Cross Simulator | 7-9 | Enhanced simulator with identifiability |
| 5: Search Engine | 9-13 | Optimizer working from CLI |
| 6: UI + Integration | 13-16 | Full feature in web UI |
| Future | — | CSP solver, recombination, batch optimization |

---

## 12. File Summary

```
flymanager/utils/phenotypes/        # Phenotype computation
├── parser.py, visual_markers.py, construct_markers.py, resolver.py
├── compute.py, identifiability.py, genotype_matching.py
├── epistasis.py, split_gal4.py, llm_fallback.py

flymanager/utils/constraints/       # Genetic rules
├── balancer_selection.py, interchromosomal.py, marker_stability.py
├── target_validation.py, yield_estimator.py

flymanager/utils/phenotypes/data/   # Ingestion pipeline
├── flybase_ingest.py, balancer_ingest.py, downloads.py, manage.py

flymanager/utils/crossing/          # Search engine
├── simulator.py, optimizer.py, templates.py, scorer.py
├── decomposer.py, memoizer.py, models.py, cache.py, batch.py

flymanager/app/routes/optimizer.py
flymanager/app/templates/optimizer/*.html
data/flybase/ (gitignored)
tests/ (18 test files)
```

---

## 13. Dependencies

```toml
cachetools = "^5.3"
pronto = "^2.5"
beautifulsoup4 = "^4.12"
lxml = "^5.1"
anthropic = "^0.40"   # optional
```

---

## 14. References

1-18: FlyBase downloads/wiki, DPO, FBcv, BDSC nomenclature/balancers/breakpoints, CrossPlan, Roote & Prokop, Osumi-Sutherland DPO paper, Silicheva mini-white, Miller et al. balancer papers (2016/2018/2020), Hentges & Justice "Joy of balancers", FlyBase Allele Report wiki.
