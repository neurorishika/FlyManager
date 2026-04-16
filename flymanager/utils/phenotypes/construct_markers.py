import re

from flymanager.utils.phenotypes.visual_markers import get_visual_marker

CONSTRUCT_MARKER_RE = re.compile(r"(?P<stem>[wyv])\[(?P<allele>\+[^\]]*)\]")
CONSTRUCT_PREFIX_RE = re.compile(r"^(?P<kind>[A-Za-z0-9]+)\{")


def extract_construct_marker_symbols(construct_token):
    markers = []
    seen = set()
    for match in CONSTRUCT_MARKER_RE.finditer(construct_token or ""):
        symbol = match.group("stem")
        allele = match.group("allele")
        key = (symbol, allele)
        if key in seen:
            continue
        seen.add(key)
        markers.append({"gene_stem": symbol, "allele_spec": allele})
    return markers


def extract_construct_markers(construct_token):
    construct_markers = []
    construct_type_match = CONSTRUCT_PREFIX_RE.match(construct_token or "")
    construct_type = construct_type_match.group("kind") if construct_type_match else "construct"

    for marker in extract_construct_marker_symbols(construct_token):
        stem = marker["gene_stem"]
        allele_spec = marker["allele_spec"]
        base_marker = get_visual_marker(stem)
        if base_marker is None:
            continue

        if stem == "w":
            base_marker.update(
                {
                    "dominance": "dominant",
                    "display_label": "mini-white",
                    "effect": "pigmented eyes from construct marker",
                    "phenotype_key": "mini_white",
                    "mini_white": True,
                }
            )
        elif stem == "y":
            base_marker.update(
                {
                    "dominance": "dominant",
                    "display_label": "y+",
                    "effect": "yellow rescue marker",
                    "phenotype_key": "y_plus",
                }
            )
        elif stem == "v":
            base_marker.update(
                {
                    "body_part": "eye",
                    "dominance": "dominant",
                    "display_label": "v+",
                    "effect": "vermilion rescue marker",
                    "phenotype_key": "v_plus",
                }
            )

        base_marker.update(
            {
                "allele_spec": allele_spec,
                "construct_token": construct_token,
                "construct_type": construct_type,
                "source": "construct_marker",
            }
        )
        construct_markers.append(base_marker)

    return construct_markers
