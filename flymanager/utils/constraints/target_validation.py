from flymanager.utils.constraints._shared import (
    flatten_available_stock_genotypes, iter_genotype_packages,
    normalize_genotype, normalize_target_sex)
from flymanager.utils.constraints.interchromosomal import \
    evaluate_interchromosomal_risk
from flymanager.utils.phenotypes.compute import compute_marker_phenotype


def _is_homozygous_balancer_issue(genotype, target_sex):
    issues = []
    normalized = normalize_genotype(genotype)
    sex = normalize_target_sex(target_sex)
    chromosome_fields = [field.strip() for field in normalized.split(";")]

    if sex == "male" and "/" in chromosome_fields[0]:
        issues.append("Male targets cannot carry two X haplotypes in the genotype format.")

    for package_info in iter_genotype_packages(normalized, sex):
        balancers = package_info["parsed"].get("balancers", [])
        if not balancers:
            continue

        chromosome_index = package_info["chromosome_index"]
        chromosome_text = chromosome_fields[chromosome_index]
        if chromosome_index == 0 and sex == "male":
            continue
        if "/" not in chromosome_text:
            issues.append(
                f"Chromosome {package_info['chromosome']} is homozygous or fixed for balancer {balancers[0]['symbol']}, which is treated as an impossible maintenance target."
            )
            continue

        left_package, right_package = [part.strip() or "+" for part in chromosome_text.split("/")]
        if left_package == right_package:
            issues.append(
                f"Chromosome {package_info['chromosome']} is homozygous for {balancers[0]['symbol']}, which is treated as an impossible balancer target."
            )

    return issues


def _linked_target_packages(genotype, target_sex):
    linked = []
    for package_info in iter_genotype_packages(genotype, target_sex):
        parsed = package_info["parsed"]
        elements = []
        elements.extend(allele["token"] for allele in parsed.get("classical_alleles", []))
        elements.extend(construct["full"] for construct in parsed.get("constructs", []))
        elements.extend(parsed.get("unresolved", []))
        if len(elements) > 1:
            linked.append(
                {
                    "chromosome": package_info["chromosome"],
                    "package": package_info["package"],
                    "elements": elements,
                }
            )
    return linked


def _suggest_balancer_heterozygote(genotype, target_sex):
    normalized = normalize_genotype(genotype)
    sex = normalize_target_sex(target_sex)
    chromosome_fields = [field.strip() for field in normalized.split(";")]
    changed = False

    for package_info in iter_genotype_packages(normalized, sex):
        parsed = package_info["parsed"]
        if not parsed.get("balancers"):
            continue
        chromosome_index = package_info["chromosome_index"]
        chromosome_text = chromosome_fields[chromosome_index]
        if "/" not in chromosome_text:
            chromosome_fields[chromosome_index] = f"+/{chromosome_text}"
            changed = True
        else:
            left_package, right_package = [part.strip() or "+" for part in chromosome_text.split("/")]
            if left_package == right_package:
                chromosome_fields[chromosome_index] = f"+/{left_package}"
                changed = True

    if not changed:
        return None
    return normalize_genotype("; ".join(chromosome_fields))


def validate_target(target_genotype, target_sex, db=None, available_stock_genotypes=None):
    sex = normalize_target_sex(target_sex)
    try:
        normalized = normalize_genotype(target_genotype)
    except ValueError as exc:
        return {
            "normalized_genotype": target_genotype,
            "viable": False,
            "fertile": False,
            "maintainable": False,
            "issues": [str(exc)],
            "impossible_reasons": [str(exc)],
            "unsupported_reasons": [],
            "suggested_alternative": None,
            "maintenance_strategy": "Invalid genotype string.",
            "requires_recombination": False,
            "background_requirements": [],
            "interchromosomal_risk": evaluate_interchromosomal_risk(balancers=[]),
        }

    phenotype = compute_marker_phenotype(normalized, sex)
    impossible_reasons = _is_homozygous_balancer_issue(normalized, sex)
    unsupported_reasons = []
    issues = []

    if phenotype["viability_status"] != "likely_viable":
        impossible_reasons.append(
            "Phenotype computation predicts inviability for this target genotype."
        )
    if sex == "male":
        issues.append("Male endpoints are treated as non-self-propagating stock targets.")

    available_packages = flatten_available_stock_genotypes(available_stock_genotypes)
    linked_packages = _linked_target_packages(normalized, sex)
    requires_recombination = False
    for linked_package in linked_packages:
        if linked_package["package"] in available_packages:
            continue
        requires_recombination = True
        message = (
            f"Chromosome {linked_package['chromosome']} contains a linked cis package "
            f"({', '.join(linked_package['elements'])}) that is not present in the supplied source stocks."
        )
        if linked_package["chromosome"] == 4:
            impossible_reasons.append(message + " Chromosome 4 is treated as non-recombining.")
        else:
            unsupported_reasons.append(
                message + " This implies recombination-aware assembly, which Phase 3 treats conservatively and expects to happen through female meiosis."
            )

    interchromosomal_risk = evaluate_interchromosomal_risk(normalized, target_sex=sex)
    if interchromosomal_risk["risk_score"] >= 0.55:
        issues.extend(interchromosomal_risk["warnings"])

    background_requirements = []
    if phenotype["mini_white_copy_count"]:
        expressed_labels = {marker.get("display_label") for marker in phenotype["expressed_markers"]}
        if "w" not in expressed_labels:
            background_requirements.append(
                "A white-eyed background is recommended if mini-white constructs need to be sorted visually."
            )

    viable = not impossible_reasons
    fertile = phenotype["fertility_status"] == "likely_fertile"
    maintainable = viable and fertile and sex == "female"

    if impossible_reasons:
        maintenance_strategy = "Maintain an alternative balanced heterozygote instead of the impossible endpoint."
    elif not maintainable and sex == "male":
        maintenance_strategy = "Maintain the target through sibling females or a balanced parent stock because the male endpoint does not self-propagate."
    elif interchromosomal_risk["balancer_count"]:
        maintenance_strategy = "Maintain as a heterozygous balanced stock and minimize simultaneous balancers where possible."
    else:
        maintenance_strategy = "Target is compatible with routine self-propagating maintenance."

    suggested_alternative = _suggest_balancer_heterozygote(normalized, sex)

    issues.extend(
        message
        for message in (
            "Reduced fertility or sterility is predicted for this target genotype."
            if phenotype["fertility_status"] != "likely_fertile"
            else None,
        )
        if message is not None
    )

    return {
        "normalized_genotype": normalized,
        "viable": viable,
        "fertile": fertile,
        "maintainable": maintainable,
        "issues": impossible_reasons + unsupported_reasons + issues,
        "impossible_reasons": impossible_reasons,
        "unsupported_reasons": unsupported_reasons,
        "suggested_alternative": suggested_alternative,
        "maintenance_strategy": maintenance_strategy,
        "requires_recombination": requires_recombination,
        "background_requirements": background_requirements,
        "interchromosomal_risk": interchromosomal_risk,
    }