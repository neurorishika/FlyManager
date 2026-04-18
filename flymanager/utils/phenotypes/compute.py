from collections import defaultdict

from flymanager.utils.genetics import qc_genotype
from flymanager.utils.phenotypes.epistasis import (
    apply_epistasis_rules, classify_mini_white_intensity,
    format_mini_white_intensity)
from flymanager.utils.phenotypes.flybase_pipeline import (
    lookup_flybase_allele_consequences, lookup_split_system_annotations)
from flymanager.utils.phenotypes.parser import parse_gene_package
from flymanager.utils.phenotypes.resolver import resolve_package_markers


def _chromosome_copies(chromosome_text, chromosome_index, sex):
    field = (chromosome_text or "").strip()
    if chromosome_index == 0:
        if sex == "male":
            return [field or "+"]
        if "/" in field:
            return [part.strip() or "+" for part in field.split("/")]
        return [field or "+", field or "+"]

    if "/" in field:
        return [part.strip() or "+" for part in field.split("/")]
    return [field or "+", field or "+"]


def compute_marker_phenotype(genotype, sex):
    if sex not in {"male", "female"}:
        raise ValueError("Sex must be either 'male' or 'female'")

    qc_passed, normalized = qc_genotype(genotype)
    if not qc_passed:
        raise ValueError(normalized)

    chromosome_fields = [field.strip() for field in normalized.split(";")]
    chromosome_copy_counts = {}
    marker_candidates = []
    unresolved_tokens = []
    mini_white_copy_count = 0
    construct_annotations = []
    allele_copy_inventory = {}

    for chromosome_index, chromosome_text in enumerate(chromosome_fields):
        copies = _chromosome_copies(chromosome_text, chromosome_index, sex)
        chromosome_copy_counts[chromosome_index] = len(copies)
        for copy_index, package in enumerate(copies):
            parsed_package = parse_gene_package(package)
            for allele in parsed_package.get("classical_alleles", []):
                token = allele.get("token")
                if not token:
                    continue
                key = (token, chromosome_index)
                allele_record = allele_copy_inventory.setdefault(
                    key,
                    {
                        "token": token,
                        "gene_stem": allele.get("gene_stem"),
                        "allele_spec": allele.get("allele_spec"),
                        "chromosome_index": chromosome_index,
                        "copy_indexes": set(),
                    },
                )
                allele_record["copy_indexes"].add(copy_index)
            resolved = resolve_package_markers(package)
            unresolved_tokens.extend(resolved["unresolved_tokens"])
            for annotation in resolved.get("construct_annotations", []):
                hydrated_annotation = dict(annotation)
                hydrated_annotation["chromosome_index"] = chromosome_index
                hydrated_annotation["copy_index"] = copy_index
                construct_annotations.append(hydrated_annotation)
            for marker in resolved["markers"]:
                candidate = dict(marker)
                candidate["chromosome_index"] = chromosome_index
                candidate["copy_index"] = copy_index
                marker_candidates.append(candidate)
                if candidate.get("mini_white"):
                    mini_white_copy_count += 1

    grouped_markers = defaultdict(
        lambda: {
            "copies": set(),
            "marker": None,
        }
    )

    for candidate in marker_candidates:
        key = (
            candidate.get("phenotype_key"),
            candidate.get("chromosome_index"),
        )
        grouped_markers[key]["marker"] = candidate
        grouped_markers[key]["copies"].add(candidate["copy_index"])

    expressed_markers = []
    homozygous_lethal_markers = []
    for grouped in grouped_markers.values():
        marker = dict(grouped["marker"])
        chromosome_index = marker["chromosome_index"]
        total_copies = chromosome_copy_counts[chromosome_index]
        mutated_copy_count = len(grouped["copies"])
        dominance = marker.get("dominance", "dominant")
        if marker.get("homozygous_lethal") and mutated_copy_count >= total_copies:
            homozygous_lethal_markers.append(marker)
        is_expressed = dominance != "recessive" or mutated_copy_count >= total_copies
        if is_expressed:
            marker["expressed_copy_count"] = mutated_copy_count
            marker["chromosome_copy_count"] = total_copies
            expressed_markers.append(marker)

    epistasis_report = apply_epistasis_rules(
        expressed_markers,
        mini_white_copy_count=mini_white_copy_count,
    )
    expressed_markers = epistasis_report["markers"]

    expressed_markers.sort(
        key=lambda marker: (
            marker.get("chromosome_index", 99),
            marker.get("display_label", marker.get("gene_stem", "")),
        )
    )
    deduplicated_construct_annotations = []
    seen_construct_annotations = set()
    for annotation in construct_annotations:
        key = (
            annotation.get("category"),
            annotation.get("label"),
            annotation.get("construct_symbol"),
        )
        if key in seen_construct_annotations:
            continue
        seen_construct_annotations.add(key)
        deduplicated_construct_annotations.append(annotation)

    split_system_annotations = lookup_split_system_annotations(normalized)
    consequence_annotations = []
    stage_specific_effects = []
    lethal_alleles = set()
    sterile_alleles = set()

    for allele_record in allele_copy_inventory.values():
        consequence = lookup_flybase_allele_consequences(allele_record["token"])
        if consequence is None:
            continue

        total_copies = chromosome_copy_counts[allele_record["chromosome_index"]]
        mutated_copy_count = len(allele_record["copy_indexes"])
        dominance_terms = set(consequence.get("dominance_terms") or [])
        if "dominant" in dominance_terms or "semi-dominant" in dominance_terms or "codominant" in dominance_terms:
            active = mutated_copy_count >= 1
        elif "recessive" in dominance_terms:
            active = mutated_copy_count >= total_copies
        else:
            active = mutated_copy_count >= total_copies
        if not active:
            continue

        display_label = consequence.get("gene_stem") or allele_record["token"]
        hydrated_consequence = {
            **consequence,
            "display_label": display_label,
            "chromosome_index": allele_record["chromosome_index"],
            "expressed_copy_count": mutated_copy_count,
            "chromosome_copy_count": total_copies,
        }
        consequence_annotations.append(hydrated_consequence)
        if "lethality" in consequence.get("categories", []):
            lethal_alleles.add(display_label)
        if "sterility" in consequence.get("categories", []):
            sterile_alleles.add(display_label)
        for stage_term in consequence.get("stage_terms", []):
            stage_specific_effects.append(f"{display_label}: {stage_term}")

    for marker in homozygous_lethal_markers:
        display_label = marker.get("display_label", marker.get("gene_stem", "?"))
        lethal_alleles.add(display_label)
        consequence_annotations.append(
            {
                "token": marker.get("allele_token") or display_label,
                "gene_stem": marker.get("gene_stem"),
                "display_label": display_label,
                "categories": ["lethality"],
                "stage_terms": ["adult"],
                "context_terms": [],
                "dominance_terms": ["recessive"],
                "conditional": False,
                "source": marker.get("source", "manual_dictionary"),
            }
        )

    viability_status = "likely_inviable" if lethal_alleles else "likely_viable"
    fertility_status = "reduced_or_sterile" if sterile_alleles else "likely_fertile"
    marker_summary = ", ".join(
        marker.get("display_label", marker.get("gene_stem", "?"))
        for marker in expressed_markers
    )

    return {
        "normalized_genotype": normalized,
        "sex": sex,
        "expressed_markers": expressed_markers,
        "all_marker_candidates": marker_candidates,
        "unresolved_tokens": sorted(set(unresolved_tokens)),
        "mini_white_copy_count": mini_white_copy_count,
        "mini_white_intensity": classify_mini_white_intensity(mini_white_copy_count),
        "mini_white_intensity_label": format_mini_white_intensity(mini_white_copy_count),
        "construct_annotations": deduplicated_construct_annotations,
        "split_system_annotations": split_system_annotations,
        "epistasis_events": epistasis_report["events"],
        "suppressed_markers": epistasis_report["suppressed_markers"],
        "consequence_annotations": consequence_annotations,
        "stage_specific_effects": sorted(set(stage_specific_effects)),
        "viability_status": viability_status,
        "fertility_status": fertility_status,
        "lethal_alleles": sorted(lethal_alleles),
        "sterile_alleles": sorted(sterile_alleles),
        "summary": marker_summary or "No marker phenotype predicted",
    }
