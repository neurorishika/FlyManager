from flymanager.utils.constraints._shared import (iter_genotype_packages,
                                                  normalize_genotype,
                                                  normalize_target_sex)


def evaluate_interchromosomal_risk(genotype=None, *, balancers=None, target_sex="female"):
    normalized = None
    balancer_records = []

    if genotype is not None:
        normalized = normalize_genotype(genotype)
        sex = normalize_target_sex(target_sex)
        for package_info in iter_genotype_packages(normalized, sex):
            for balancer in package_info["parsed"].get("balancers", []):
                balancer_records.append(
                    {
                        "symbol": balancer.get("symbol"),
                        "chromosome": package_info["chromosome"],
                    }
                )

    for balancer in balancers or []:
        if isinstance(balancer, dict):
            symbol = str(balancer.get("symbol") or balancer.get("balancer_symbol") or "").strip()
            chromosome = balancer.get("chromosome")
        else:
            symbol = str(balancer or "").strip()
            chromosome = None
        if not symbol:
            continue
        balancer_records.append({"symbol": symbol, "chromosome": chromosome})

    balancer_count = len(balancer_records)
    unique_symbols = []
    for record in balancer_records:
        if record["symbol"] not in unique_symbols:
            unique_symbols.append(record["symbol"])

    if balancer_count <= 1:
        risk_score = 0.0
    elif balancer_count == 2:
        risk_score = 0.35
    elif balancer_count == 3:
        risk_score = 0.7
    else:
        risk_score = 0.9

    chromosome_counts = {}
    for record in balancer_records:
        chromosome = record.get("chromosome")
        chromosome_counts[chromosome] = chromosome_counts.get(chromosome, 0) + 1
    if any(count > 1 for count in chromosome_counts.values() if count is not None):
        risk_score = min(1.0, risk_score + 0.1)

    if risk_score >= 0.8:
        risk_label = "severe"
    elif risk_score >= 0.55:
        risk_label = "high"
    elif risk_score >= 0.25:
        risk_label = "moderate"
    else:
        risk_label = "low"

    warnings = []
    if balancer_count >= 2:
        warnings.append(
            "Multiple balancers can trigger interchromosomal effects and increase recombination risk near and beyond balancer breakpoints."
        )
    if balancer_count >= 3:
        warnings.append(
            "Three or more simultaneous balancers are treated as a high-risk stock configuration for segregation and stock breakdown."
        )
    if any(count > 1 for count in chromosome_counts.values() if count is not None):
        warnings.append(
            "More than one balancer appears on the same chromosome class, which is treated as a severe maintenance risk."
        )

    return {
        "normalized_genotype": normalized,
        "balancer_count": balancer_count,
        "balancer_symbols": unique_symbols,
        "risk_score": round(risk_score, 2),
        "risk_label": risk_label,
        "warnings": warnings,
    }