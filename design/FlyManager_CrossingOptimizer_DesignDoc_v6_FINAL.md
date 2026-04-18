# Design Document: Automated Crossing Scheme Optimizer for FlyManager

**Author:** Rishika Mohanta / Claude  
**Date:** April 2026  
**Status:** Draft v6 (planned architecture; implementation snapshot updated 2026-04-17)  
**Repository:** `neurorishika/FlyManager`

---

## 1. Executive Summary

This document specifies an **Automated Crossing Scheme Optimizer** for FlyManager. Given a target genotype, the system computes optimal multi-generation crossing schemes from available stocks, minimizing generations, maximizing yield, and guaranteeing phenotypic identifiability at every step.

### 1.1 External Data Sources

1. **FlyBase Precomputed Bulk Downloads** — canonical stock catalog, allele-to-gene bridge, phenotype enrichment, construct metadata, and gene-position support
2. **Drosophila Phenotype Ontology (DPO)** — formal phenotype classification (~200 terms)
3. **BDSC Nomenclature Grammar** — formal genotype string parsing rules
4. **BDSC Core Balancer Definitions** — balancer inversions, breakpoints, and markers

### 1.2 Planned System Architecture (7 Subsystems)

1. **Phenotype Knowledge Base** — three-source hybrid: manual visual marker dictionary + FlyBase bulk data + construct marker parsing
2. **Phenotype Computation Engine** — computes visible phenotype of any genotype
3. **Genetic Constraint Engine** — Drosophila-specific rules: balancer coverage, interchromosomal effects, marker stability, male recombination, chromosome 4, impossible targets
4. **Cross Simulator** — extends `cross_genotypes()` with phenotype-aware output
5. **Search Engine** — template-based planning with exhaustive forward validation (CSP deferred to Phase 2)
6. **Interactive Optimizer UI** — phenotype preview, scheme customization, multi-target batching
7. **Data Pipeline** — ingestion, monthly updates, validation

### 1.3 Current Repository Status Snapshot (2026-04-17)

The repository does **not** yet contain the full crossing optimizer described in this document. What exists today is a substantial phenotype-analysis foundation plus FlyBase sync/reporting infrastructure.

| Area | Status | Implemented Now | Not Yet Implemented |
| ----- | ----- | ----- | ----- |
| Phase 0 data examination | Complete | Release-aware FlyBase discovery/download, local TSV examination, examination report generation, visible-marker inventory audit, balancer scraping/reporting, unresolved-token curation, and admin sync/status reporting | No blocking Phase 0 gap remains |
| Manual marker knowledge base | Implemented | Curated `visual_markers.py`, allele-specific overrides, reviewed aliases, curated balancer metadata/marker sets | LLM fallback, ontology-backed normalization |
| Grammar parsing | Implemented | `parser.py` tokenization and parsing for constructs, balancers, classical alleles, aberrations, annotations | None of the major Phase 1 parser goals are obviously blocked |
| Reference-data ingestion | Implemented | Normalized FlyBase TSV ingestion, balancer ingestion, refresh-lifecycle integration, and manifest-backed status reporting are present | Runtime callers do not yet uniformly require Mongo-backed reference lookups |
| Marker resolution | Implemented | Manual dictionary + construct markers + FlyBase-derived cached evidence merged in `resolver.py`, with Mongo-backed cache preference when a db handle is available | LLM fallback, ontology-backed normalization |
| Phenotype prediction | Implemented for Phase 2 scope | `compute_marker_phenotype()`, FlyBase consequence annotations, formal epistasis rules, sex-aware stock prediction, cross parent/offspring phenotype summaries, provenance-aware confidence/warning model | Deeper constraint-level viability logic still belongs to later phases |
| UI integration | Implemented for Phase 2 scope | Cached phenotype preview surfaces in stock view, cross view, explorer summaries, admin backfill controls, and a standalone stock-scoped phenotype sandbox | Dedicated optimizer blueprint and scheme-planning UI |
| Constraint/simulator/search stack | Partial | `flymanager/utils/constraints/` is implemented, and `flymanager/utils/crossing/simulator.py` now provides viability-aware simulation, sibling identifiability scoring, reciprocal evaluation, and cache/UI-facing summaries | Optimizer templates, scoring, search, and batch planning remain absent |

The most important practical takeaway is that FlyManager now has a usable phenotype preview stack, a shipped constraint layer, and a Phase 4-style simulator surfaced in the cross UI, but it still does not have automated crossing-scheme search or the dedicated optimizer workflow.

### 1.4 Critical Design Decision: Three-Source Hybrid Phenotype Architecture

| Purpose | Primary Source | Why |
| ----- | ----- | ----- |
| **Visual marker descriptions** (what Cy/Sb/w look like) | Manual dictionary (~50-100 entries) | The examined FlyBase bundle has many `visible` phenotype rows, but not concise microscope-facing descriptions for common sorting markers |
| **Lethality, sterility, conditional phenotypes** | FlyBase `genotype_phenotype_data` bulk TSV | Large literature-curated enrichment source with phenotype classes, qualifiers, and references |
| **Transgene selectable markers** (w+mC, y+, v+) | Construct parsing + FlyBase `transgenic_construct_descriptions` | Construct names still encode selectable markers directly, and FlyBase adds encoded-product/regulatory-region metadata |
| **Stock sourcing across repositories** | FlyBase `stocks` TSV | Canonical stock catalog spanning Bloomington, Vienna, Kyoto, NIG-Fly, KDRC, FlyORF, NDSSC, and others |
| **Allele-to-gene bridge** | FlyBase `fbal_to_fbgn` TSV | Direct FBal → FBgn / gene-symbol bridge for joining stock, phenotype, and allele metadata |
| **Balancer position support** | FlyBase `gene_map_table` TSV | Direct gene-symbol → recombination / cytological / sequence position mapping |

These sources are NOT interchangeable. The manual dictionary is still the **guaranteed backbone** for identifiability. FlyBase is now confirmed as the best canonical enrichment and stock-source layer. Construct parsing remains necessary because FlyManager still starts from genotype strings, not normalized FlyBase entities.

Current repository decision: Mongo-ingested FlyBase and balancer collections are the authoritative refresh/reference layer, while `PHENOTYPE_EVIDENCE_CACHE.json` remains the runtime fast path and is rebuilt from Mongo when a database handle is available, with raw-file fallback retained for offline and bootstrap flows.

---

## 2. Existing Codebase Context

### 2.1 Repository Structure

```text
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

### 2.6 Current Optimizer-Adjacent Footprint In Repo

As of 2026-04-17, the main optimizer-adjacent files already present are:

- `flymanager/utils/phenotypes/parser.py` — gene-package tokenizer/parser
- `flymanager/utils/phenotypes/visual_markers.py` — curated visual markers, reviewed aliases, curated balancer metadata
- `flymanager/utils/phenotypes/construct_markers.py` — construct marker extraction, including mini-white/y+/v+
- `flymanager/utils/phenotypes/resolver.py` — three-source marker resolution using manual curation + constructs + FlyBase evidence cache
- `flymanager/utils/phenotypes/compute.py` — expressed-marker computation for a genotype/sex pair
- `flymanager/utils/phenotypes/predictor.py` — stock/cross phenotype summaries, offspring annotation, cache builders
- `flymanager/utils/phenotypes/identifiability.py` — sibling-aware sortability/confusability analysis and selection-instruction generation
- `flymanager/utils/phenotypes/flybase_pipeline.py` — local FlyBase evidence cache builder/lookup layer
- `flymanager/utils/phenotypes/data/downloads.py` and `examiner.py` — release-aware download discovery and Phase 0 reporting
- `flymanager/utils/phenotypes/data/flybase_ingest.py` — normalized FlyBase TSV ingestion into Mongo-backed reference collections
- `flymanager/utils/phenotypes/data/balancer_ingest.py` — BDSC balancer scraping, report generation, and balancer collection ingestion
- `flymanager/utils/phenotypes/backfill.py` — bulk phenotype cache regeneration for stocks and crosses
- `flymanager/utils/constraints/` — balancer selection, interchromosomal risk, marker stability, target validation, and yield heuristics
- `flymanager/utils/crossing/simulator.py` — viability-aware phenotype simulation, sibling identifiability annotation, reciprocal-direction scoring, and compact simulator summaries
- `flymanager/app/services/flybase.py` — monthly FlyBase sync/report/backfill orchestration

Notably absent are `flymanager/utils/crossing/optimizer.py`, `templates.py`, `scorer.py`, `decomposer.py`, `memoizer.py`, `models.py`, `cache.py`, `batch.py`, `flymanager/app/routes/optimizer.py`, and the dedicated optimizer templates described later in this document.

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

Implementation status as of 2026-04-17: partially shipped. `flymanager/utils/crossing/simulator.py` now wraps `cross_genotypes()` with phenotype prediction, target validation, yield heuristics, viability-aware pruning, sibling identifiability annotations, simulator summary generation, and reciprocal-direction evaluation. These outputs are persisted into the cross phenotype cache and rendered in the cross detail UI as prune reasons, viable-pool percentages, best-sortable-class summaries, and reciprocal direction guidance. Remaining work is broader validation against real historical crosses and using these outputs inside the future optimizer/search package.

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

The standalone optimizer UI described here is still planned work. The current shipped UI surface is narrower:

- Stock view includes a cached "Phenotype Preview" section with best-guess phenotype, sex-specific projections, construct annotations, split-system annotations, warnings, and manual cache refresh.
- Cross view includes cached male/female parent phenotype previews, phenotype-annotated predicted offspring rows, simulator summary metrics, prune reasons, and reciprocal-direction guidance derived from the new simulator/cache payload.
- Explorer and admin surfaces expose phenotype summaries, FlyBase sync health, and phenotype-cache backfill controls.

There is currently no dedicated optimizer blueprint, no scheme builder UI, no phenotype-preview JSON endpoint under an optimizer route, and no multi-target planning interface.

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

Downloads can run outside the container or through the FlyManager release-aware downloader. Files live in `data/flybase/`. CLI examination operates on local files and can read `.tsv.gz` directly.

Current implementation note: the repository now uses a hybrid model. `flybase_ingest.py` and `balancer_ingest.py` populate Mongo-backed reference collections during the FlyBase refresh workflow, and `flybase_pipeline.py` keeps `PHENOTYPE_EVIDENCE_CACHE.json` as the runtime fast layer that can rebuild from Mongo or raw files.

---

## 11. Implementation Plan

| Phase | Original Deliverable | Status as of 2026-04-17 | Notes |
| ----- | ----- | ----- | ----- |
| 0: Data Examination | Confirmed/revised architecture | Complete | Download discovery, local examination, report generation, marker inventory tooling, balancer scraping, and status reporting exist |
| 1: Ingestion + Manual Dictionary | All data in MongoDB. Dictionary complete. | Complete | Manual dictionary, Mongo ingestion collections, balancer scraper/ingest, CLI commands, and the hybrid cache/runtime pipeline exist |
| 2: Phenotype + Preview | **Phenotype Preview shipped** | Complete for checklist scope | Stock/cross phenotype previews, cache refresh/backfill, consequence annotations, provenance-aware scoring, sibling confusability, and a standalone phenotype sandbox are live |
| 3: Genetic Constraints | All constraints computable | Complete | `flymanager/utils/constraints/` now ships balancer selection, interchromosomal-risk scoring, marker-stability assessment, target validation, yield heuristics, and regression tests |
| 4: Cross Simulator | Enhanced simulator with identifiability | Substantial partial implementation | `flymanager/utils/crossing/simulator.py` now provides viability-aware simulation, prune reasons, sibling identifiability scoring, reciprocal-direction evaluation, and compact summaries that are surfaced in the cross cache and cross detail UI |
| 5: Search Engine | Optimizer working from CLI | Not started | No `crossing/optimizer.py`, templates, scoring, or scheme search |
| 6: UI + Integration | Full feature in web UI | Not started | No optimizer routes/templates beyond phenotype preview surfaces |
| Future | CSP solver, recombination, batch optimization | Not started | Still future work |

Recommended next steps from the current baseline:

1. Add real historical-cross regression coverage so the shipped simulator is validated against known lab selection outcomes rather than only synthetic fixtures.
2. Build the missing Phase 5 search modules: `templates.py`, `optimizer.py`, `scorer.py`, and the support models/cache layers, on top of `simulate_cross()` and `evaluate_cross_directions()`.
3. Create the dedicated optimizer blueprint/UI so simulator rationale can drive scheme explanations, not just the existing cross detail page.

---

## 12. File Summary

Current repository state:

```text
flymanager/utils/phenotypes/
├── __init__.py
├── backfill.py
├── compute.py
├── construct_markers.py
├── identifiability.py
├── flybase_pipeline.py
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

flymanager/app/services/
├── flybase.py

flymanager/app/routes/
├── cross.py         # cross phenotype cache refresh + simulator summary/reciprocal-evaluation wiring
├── settings.py      # admin FlyBase refresh + phenotype cache backfill controls
├── stock.py         # stock phenotype cache refresh + stock phenotype preview wiring

flymanager/app/templates/
├── cross/view_cross.html
├── home.html
├── settings/admin.html
├── stock/phenotype_preview.html
├── stock/view_stock.html
```

Still planned but not present:

```text
flymanager/utils/crossing/optimizer.py
flymanager/utils/crossing/templates.py
flymanager/utils/crossing/scorer.py
flymanager/utils/crossing/decomposer.py
flymanager/utils/crossing/memoizer.py
flymanager/utils/crossing/models.py
flymanager/utils/crossing/cache.py
flymanager/utils/crossing/batch.py
flymanager/app/routes/optimizer.py
flymanager/app/templates/optimizer/*.html
```

---

## 13. Dependencies

```toml
# Planned for later optimizer phases; not present in pyproject.toml as of 2026-04-17
cachetools = "^5.3"
pronto = "^2.5"
beautifulsoup4 = "^4.12"
lxml = "^5.1"
anthropic = "^0.40"   # optional
```

Current implementation relies on the standard library for FlyBase download discovery/parsing (`urllib`, `html.parser`) and does not currently depend on BeautifulSoup, lxml, pronto, cachetools, or Anthropic.

---

## 14. References

1-18: FlyBase downloads/wiki, DPO, FBcv, BDSC nomenclature/balancers/breakpoints, CrossPlan, Roote & Prokop, Osumi-Sutherland DPO paper, Silicheva mini-white, Miller et al. balancer papers (2016/2018/2020), Hentges & Justice "Joy of balancers", FlyBase Allele Report wiki.
