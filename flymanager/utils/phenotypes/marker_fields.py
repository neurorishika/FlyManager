"""Field specs that let the marker catalog UI be a form instead of a JSON editor.

A marker definition is stored as a small envelope of JSON sections, and the
catalog pages originally exposed those sections verbatim as textareas. That is
fine for someone who has read the store, and unusable for the person who
actually knows what ``Sb`` looks like under a scope. The specs below describe,
per kind, the handful of fields that kind really uses, so both the templates
and the form parser work from one description instead of drifting apart.

Only fields listed here are managed by the form: `form_to_document` rebuilds a
section from the spec but carries over any other key the section already had,
so a value added through the admin JSON editor survives an ordinary save.
"""

# A field is (path, label, type, help, options). `path` is the dotted location
# inside the definition document; the form input uses the same name.
_DOMINANCE = ("dominant", "recessive")
_ALIAS_TYPES = ("allele_token", "construct_token")


def _field(path, label, type_="text", help_="", options=(), placeholder="",
           separator=","):
    # `separator` only matters for list fields. Free text (a balancer's notes)
    # is split on newlines alone, because a note like "breaks down at 25C, use
    # fresh" would otherwise silently become two notes on the next save.
    return {"path": path, "name": path, "label": label, "type": type_,
            "help": help_, "options": tuple(options), "placeholder": placeholder,
            "separator": separator}


_APPEARANCE = (
    _field("payload.display_label", "Shows up as",
           help_="How this marker is written in a predicted phenotype."),
    _field("payload.effect", "What you see",
           help_="Plain description of the visible effect, e.g. 'short stubby bristles'."),
    _field("payload.body_part", "Where to look",
           placeholder="eye, wing, bristle, body …"),
    _field("payload.chromosome", "Chromosome", "int",
           help_="1 (X), 2, 3 or 4. Leave blank if it varies."),
    _field("payload.dominance", "Dominance", "select", options=_DOMINANCE,
           help_="Whether one copy is enough to see the phenotype."),
    _field("payload.scoring_confidence", "Scoring confidence", "float",
           help_="0 to 1 — how reliably this can be scored by eye."),
    _field("payload.phenotype_key", "Internal key",
           help_="Identifier used to group this marker across stocks. "
                 "Leave blank to use the marker name."),
)

_PROVENANCE = (
    _field("provenance.geneName", "Gene name"),
    _field("provenance.flybaseId", "FlyBase ID", placeholder="FBgn…"),
    _field("provenance.referenceUrl", "Reference link", "url"),
    _field("provenance.source", "Where this came from",
           help_="A paper, a stock centre sheet, or your own observation."),
)

_IMAGING = (
    _field("imaging.aliases", "Image search names", "list",
           help_="Other spellings that should find the same reference photos, "
                 "comma separated."),
)

MARKER_FIELD_SPECS = {
    "gene_marker": {
        "title": "Gene marker",
        "blurb": "A gene symbol that FlyManager recognises anywhere in a genotype.",
        "groups": (
            ("Recognised as", (
                _field("match.symbol", "Gene symbol", help_="The exact symbol to match, e.g. Sb."),
            )),
            ("Appearance", _APPEARANCE + (
                _field("payload.homozygous_lethal", "Homozygous lethal", "bool",
                       help_="Two copies of this are not viable."),
            )),
            ("Reference images", _IMAGING),
            ("Where this came from", _PROVENANCE),
        ),
    },
    "allele_marker": {
        "title": "Allele marker",
        "blurb": "One specific allele, e.g. Bl[1], rather than the gene as a whole.",
        "groups": (
            ("Recognised as", (
                _field("match.token", "Full token", help_="How the allele is written, e.g. Bl[1]."),
                _field("match.geneStem", "Gene stem", help_="The gene part, e.g. Bl."),
                _field("match.alleleSpec", "Allele", help_="The part in brackets, e.g. 1."),
            )),
            ("Appearance", (_field("payload.gene_stem", "Gene stem"),) + _APPEARANCE),
            ("Reference images", _IMAGING),
            ("Where this came from", _PROVENANCE),
        ),
    },
    "alias": {
        "title": "Alias",
        "blurb": "A nickname that should be read as something else before matching.",
        "groups": (
            ("Recognised as", (
                _field("match.token", "Written as", help_="The nickname seen in a genotype."),
            )),
            ("Means", (
                _field("payload.value", "Read it as",
                       help_="The canonical token this stands for."),
                _field("payload.alias_type", "Alias type", "select", options=_ALIAS_TYPES),
            )),
            ("Where this came from", _PROVENANCE),
        ),
    },
    "balancer": {
        "title": "Balancer",
        "blurb": "A balancer chromosome and the markers it carries by default.",
        "groups": (
            ("Recognised as", (
                _field("match.symbol", "Balancer name", help_="e.g. TM3."),
                _field("match.aliases", "Other names", "list",
                       help_="Comma separated alternative spellings."),
            )),
            ("Details", (
                _field("payload.family", "Family", help_="e.g. TM3, CyO."),
                _field("payload.chromosome", "Chromosome", "int"),
                _field("payload.default_markers", "Markers it carries", "list",
                       help_="Comma separated, e.g. Sb, Ser."),
                _field("payload.notes", "Notes", "list", separator="\n",
                       help_="One note per line."),
                _field("sorting.preferenceBonus", "Preference", "float",
                       help_="How strongly to prefer this balancer when the "
                             "solver picks one, from 0 to 0.25. Leave blank "
                             "for no preference."),
            )),
            ("Reference images", _IMAGING),
            ("Where this came from", _PROVENANCE),
        ),
    },
    "construct_marker": {
        "title": "Construct marker",
        "blurb": "A rescue or transgene marker such as w+, matched by gene stem "
                 "and allele prefix.",
        "groups": (
            ("Recognised as", (
                _field("match.geneStem", "Gene stem", help_="e.g. w."),
                _field("match.allelePrefix", "Allele prefix", help_="e.g. +."),
            )),
            ("Appearance", (
                _field("payload.overrides.display_label", "Shows up as"),
                _field("payload.overrides.effect", "What you see"),
                _field("payload.overrides.body_part", "Where to look"),
                _field("payload.overrides.dominance", "Dominance", "select",
                       options=_DOMINANCE),
                _field("payload.overrides.phenotype_key", "Internal key"),
            )),
            ("Reference images", _IMAGING),
            ("Where this came from", _PROVENANCE),
        ),
    },
}


def iter_fields(kind):
    """Every field of a kind, flattened out of its groups."""
    spec = MARKER_FIELD_SPECS.get(kind)
    if not spec:
        return []
    return [field for _title, fields in spec["groups"] for field in fields]


def _split(path):
    return path.split(".")


def _get(document, parts):
    node = document
    for part in parts:
        if not isinstance(node, dict) or part not in node:
            return None
        node = node[part]
    return node


def _set(document, parts, value):
    node = document
    for part in parts[:-1]:
        node = node.setdefault(part, {})
    node[parts[-1]] = value


def _drop(document, parts):
    node = document
    for part in parts[:-1]:
        node = node.get(part)
        if not isinstance(node, dict):
            return
    node.pop(parts[-1], None)


def field_value(document, path, separator=", "):
    """The current value of a field, rendered for an HTML input."""
    value = _get(document or {}, _split(path))
    if value is None:
        return ""
    if isinstance(value, bool):
        return value
    if isinstance(value, (list, tuple)):
        return separator.join(str(item) for item in value)
    return str(value)


def _parse_list(raw, separator=","):
    items = []
    if separator != "\n":
        raw = raw.replace("\n", separator)
    for chunk in raw.split(separator):
        chunk = chunk.strip()
        if chunk:
            items.append(chunk)
    return items


def _coerce(field, form):
    """Return (present, value) for one field given the submitted form."""
    name = field["name"]
    if field["type"] == "bool":
        if name in form:
            return True, True
        if f"{name}__present" in form:
            return True, False
        return False, None
    if name not in form:
        return False, None
    raw = (form.get(name) or "").strip()
    if field["type"] == "list":
        return True, _parse_list(raw, field.get("separator", ","))
    if not raw:
        return False, None
    if field["type"] == "int":
        try:
            return True, int(raw)
        except ValueError:
            raise ValueError(f"{field['label']} must be a whole number.")
    if field["type"] == "float":
        try:
            return True, float(raw)
        except ValueError:
            raise ValueError(f"{field['label']} must be a number.")
    return True, raw


def form_to_document(kind, form, existing=None):
    """Rebuild the envelope sections a kind's form covers.

    Sections the spec does not mention are absent from the result: the route's
    merge fills them from the stored document, which is what keeps a save from
    resetting anything the user never saw.
    """
    fields = iter_fields(kind)
    sections = {}
    for field in fields:
        section = _split(field["path"])[0]
        if section not in sections:
            source = (existing or {}).get(section)
            sections[section] = dict(source) if isinstance(source, dict) else {}

    for field in fields:
        parts = _split(field["path"])
        present, value = _coerce(field, form)
        target = sections[parts[0]]
        if present:
            _set(target, parts[1:], value)
        else:
            _drop(target, parts[1:])

    # Nested containers emptied by clearing every field they held (construct
    # overrides) should not linger as an empty dict pretending to be data.
    for section in sections.values():
        for key in [k for k, v in section.items() if v == {}]:
            section.pop(key)
    return sections
