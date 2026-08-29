import json
import re
import urllib.request
from collections import Counter
from copy import deepcopy
from html.parser import HTMLParser
from pathlib import Path

from flymanager.utils.constraints._shared import normalize_chromosome_label
from flymanager.utils.phenotypes.data.downloads import DEFAULT_TIMEOUT_SECONDS
from flymanager.utils.phenotypes.parser import parse_gene_package

BALANCER_DEFS_URL = "https://bdsc.indiana.edu/stocks/balancers/balancer_defs.html"
BALANCER_INTRO_URL = "https://bdsc.indiana.edu/stocks/balancers/balancer_intro.html"
REQUEST_HEADERS = {
    "User-Agent": "FlyManager/0.1 (BDSC balancer ingestion)",
}
CYTOLOGY_RE = re.compile(r"\b\d{1,2}[A-F](?:\d{1,2})?(?:-\d{1,2}[A-F](?:\d{1,2})?)?\b")
CHROMOSOME_RE = re.compile(r"\b([1234X])\b", re.IGNORECASE)


def _normalize_text(value):
    return re.sub(r"\s+", " ", str(value or "")).strip()


def _split_marker_tokens(markers_text):
    tokens = []
    for token in re.split(r"\s*[,;/|]\s*", str(markers_text or "")):
        normalized = _normalize_text(token)
        if normalized:
            tokens.append(normalized)
    return tokens


def _deduplicate(items):
    deduplicated = []
    seen = set()
    for item in items:
        normalized = json.dumps(item, sort_keys=True) if isinstance(item, (dict, list)) else str(item)
        if normalized in seen:
            continue
        seen.add(normalized)
        deduplicated.append(item)
    return deduplicated


def _pick_field(row_map, candidates):
    for candidate in candidates:
        for key, value in row_map.items():
            if candidate in key:
                normalized = _normalize_text(value)
                if normalized:
                    return normalized
    return ""


def _normalize_chromosome(value):
    normalized_value = _normalize_text(value).upper()
    if normalized_value in {"X", "1", "2", "3", "4"}:
        return normalized_value

    match = CHROMOSOME_RE.search(normalized_value)
    if not match:
        return ""
    return match.group(1).upper()


def _extract_breakpoint_regions(*texts):
    breakpoints = []
    for text in texts:
        breakpoints.extend(CYTOLOGY_RE.findall(str(text or "")))
    return _deduplicate(breakpoints)


def _extract_marker_tokens(genotype_text, markers_text):
    explicit_tokens = _split_marker_tokens(markers_text)
    if explicit_tokens:
        return _deduplicate([token for token in explicit_tokens if token])

    tokens = []
    parsed_genotype = _normalize_text(genotype_text)
    if parsed_genotype:
        try:
            parsed = parse_gene_package(parsed_genotype)
        except Exception:
            parsed = None
        if parsed is not None:
            for allele in parsed.get("classical_alleles", []):
                if allele.get("token"):
                    tokens.append(allele["token"])
            for unresolved in parsed.get("unresolved", []):
                normalized = _normalize_text(unresolved)
                if normalized and normalized != parsed_genotype:
                    tokens.append(normalized)

    return _deduplicate([token for token in tokens if token])


class _HTMLTableExtractor(HTMLParser):
    def __init__(self):
        super().__init__()
        self.tables = []
        self._last_heading = ""
        self._current_table = None
        self._current_row = None
        self._current_cell = None
        self._capture_heading = False
        self._heading_text = []
        self._capture_cell = False
        self._cell_text = []
        self._capture_caption = False
        self._caption_text = []

    def handle_starttag(self, tag, attrs):
        del attrs
        if tag in {"h1", "h2", "h3", "h4"}:
            self._capture_heading = True
            self._heading_text = []
            return

        if tag == "table":
            self._current_table = {
                "heading": self._last_heading,
                "caption": "",
                "rows": [],
            }
            return

        if tag == "caption" and self._current_table is not None:
            self._capture_caption = True
            self._caption_text = []
            return

        if tag == "tr" and self._current_table is not None:
            self._current_row = []
            return

        if tag in {"th", "td"} and self._current_row is not None:
            self._capture_cell = True
            self._cell_text = []

    def handle_data(self, data):
        if self._capture_heading:
            self._heading_text.append(data)
        if self._capture_caption:
            self._caption_text.append(data)
        if self._capture_cell:
            self._cell_text.append(data)

    def handle_endtag(self, tag):
        if tag in {"h1", "h2", "h3", "h4"} and self._capture_heading:
            self._capture_heading = False
            self._last_heading = _normalize_text("".join(self._heading_text))
            self._heading_text = []
            return

        if tag == "caption" and self._capture_caption:
            self._capture_caption = False
            if self._current_table is not None:
                self._current_table["caption"] = _normalize_text("".join(self._caption_text))
            self._caption_text = []
            return

        if tag in {"th", "td"} and self._capture_cell:
            self._capture_cell = False
            if self._current_row is not None:
                self._current_row.append(_normalize_text("".join(self._cell_text)))
            self._cell_text = []
            return

        if tag == "tr" and self._current_row is not None:
            if any(_normalize_text(cell) for cell in self._current_row):
                self.append_row(self._current_table, self._current_row)
            self._current_row = None
            return

        if tag == "table" and self._current_table is not None:
            if self._current_table["rows"]:
                self.tables.append(self._current_table)
            self._current_table = None

    def append_row(self, table, row):
        if table is not None:
            table["rows"].append(row)

class _HTMLTextCollector(HTMLParser):
    def __init__(self):
        super().__init__()
        self.lines = []
        self._buffer = []

    def handle_starttag(self, tag, attrs):
        del attrs
        if tag == "br":
            self._flush()

    def handle_data(self, data):
        self._buffer.append(data)

    def handle_endtag(self, tag):
        if tag in {"p", "li", "div", "h1", "h2", "h3", "h4", "tr"}:
            self._flush()

    def _flush(self):
        text = _normalize_text("".join(self._buffer))
        self._buffer = []
        if text:
            self.lines.append(text)


def _fetch_text(url, timeout=DEFAULT_TIMEOUT_SECONDS):
    request = urllib.request.Request(url, headers=REQUEST_HEADERS)
    with urllib.request.urlopen(request, timeout=timeout) as response:
        return response.read().decode("utf-8", errors="replace")


def _iter_balancer_tables(html):
    parser = _HTMLTableExtractor()
    parser.feed(html)
    for table in parser.tables:
        if not table.get("rows"):
            continue
        yield table


def parse_bdsc_balancer_definitions_html(html, source_url=BALANCER_DEFS_URL):
    balancers = []

    for table in _iter_balancer_tables(html):
        rows = table["rows"]
        if len(rows) < 2:
            continue

        header_row = rows[0]
        normalized_headers = [_normalize_text(cell).lower() for cell in header_row]
        header_text = " ".join(normalized_headers)
        if "chrom" not in header_text and "balancer" not in header_text and "symbol" not in header_text:
            continue

        for row in rows[1:]:
            if len(row) != len(header_row):
                if len(row) < 2:
                    continue
                padded_row = list(row) + [""] * (len(header_row) - len(row))
                row = padded_row[:len(header_row)]

            row_map = {
                normalized_headers[index]: row[index]
                for index in range(len(header_row))
            }
            symbol = _pick_field(row_map, ["balancer", "symbol", "name"])
            chromosome = _normalize_chromosome(_pick_field(row_map, ["chrom"]))
            genotype = _pick_field(row_map, ["genotype"]) or _pick_field(row_map, ["description"]) or ""
            markers_text = _pick_field(row_map, ["marker"]) or _pick_field(row_map, ["dominant"]) or ""
            breakpoint_text = _pick_field(row_map, ["breakpoint", "inversion"]) or ""
            notes = _pick_field(row_map, ["comment", "note", "remark"]) or ""

            if not symbol:
                symbol = _normalize_text(row[0])
            if not chromosome:
                chromosome = _normalize_chromosome(" ".join(row))
            if not breakpoint_text:
                breakpoint_text = "; ".join(_extract_breakpoint_regions(*row))

            if not symbol:
                continue

            balancer = {
                "symbol": symbol,
                "chromosome": chromosome,
                "genotype": genotype,
                "markers_text": markers_text,
                "marker_tokens": _extract_marker_tokens(genotype, markers_text),
                "breakpoint_text": breakpoint_text,
                "breakpoint_regions": _extract_breakpoint_regions(genotype, markers_text, breakpoint_text, notes),
                "notes": notes,
                "source_table_heading": table.get("heading", ""),
                "source_table_caption": table.get("caption", ""),
                "source_url": source_url,
            }
            balancers.append(balancer)

    chromosome_counts = Counter(
        balancer["chromosome"] or "unknown"
        for balancer in balancers
    )
    return {
        "source_url": source_url,
        "balancers": _deduplicate(balancers),
        "summary": {
            "total_balancers": len(_deduplicate(balancers)),
            "chromosome_counts": dict(chromosome_counts),
        },
    }


def parse_bdsc_balancer_intro_html(html, source_url=BALANCER_INTRO_URL):
    parser = _HTMLTextCollector()
    parser.feed(html)

    lines = _deduplicate(parser.lines)
    breakpoint_guidance = []
    interchromosomal_notes = []
    selection_rules = []

    for line in lines:
        lower_line = line.lower()
        if "breakpoint" in lower_line or "inversion" in lower_line:
            breakpoint_guidance.append(line)
        if "interchromosomal" in lower_line:
            interchromosomal_notes.append(line)
        if any(keyword in lower_line for keyword in ("choose", "use ", "recommended", "best")):
            selection_rules.append(line)

    return {
        "source_url": source_url,
        "breakpoint_guidance": breakpoint_guidance,
        "interchromosomal_notes": interchromosomal_notes,
        "selection_rules": selection_rules,
        "summary": {
            "line_count": len(lines),
            "breakpoint_guidance": len(breakpoint_guidance),
            "interchromosomal_notes": len(interchromosomal_notes),
            "selection_rules": len(selection_rules),
        },
    }


def discover_bdsc_balancers(timeout=DEFAULT_TIMEOUT_SECONDS):
    defs_html = _fetch_text(BALANCER_DEFS_URL, timeout=timeout)
    intro_html = _fetch_text(BALANCER_INTRO_URL, timeout=timeout)
    definitions = parse_bdsc_balancer_definitions_html(defs_html)
    intro = parse_bdsc_balancer_intro_html(intro_html)
    return {
        "generated_from": {
            "definitions_url": BALANCER_DEFS_URL,
            "intro_url": BALANCER_INTRO_URL,
        },
        "definitions": definitions,
        "intro": intro,
    }


def render_balancer_report_markdown(report):
    definitions = report["definitions"]
    intro = report["intro"]
    lines = [
        "# BDSC Balancer Reference Report",
        "",
        f"- Definitions URL: {definitions['source_url']}",
        f"- Intro URL: {intro['source_url']}",
        f"- Total balancers parsed: {definitions['summary']['total_balancers']}",
        "",
        "## Chromosome Counts",
        "",
    ]

    for chromosome, count in sorted(definitions["summary"]["chromosome_counts"].items()):
        lines.append(f"- {chromosome}: {count}")

    lines.extend(["", "## Balancer Definitions", ""])
    for balancer in definitions["balancers"]:
        lines.append(f"### {balancer['symbol']}")
        lines.append("")
        lines.append(f"- Chromosome: {balancer['chromosome'] or 'unknown'}")
        lines.append(f"- Genotype: {balancer['genotype'] or 'n/a'}")
        lines.append(f"- Marker tokens: {', '.join(balancer['marker_tokens']) or 'n/a'}")
        lines.append(f"- Breakpoint text: {balancer['breakpoint_text'] or 'n/a'}")
        if balancer["notes"]:
            lines.append(f"- Notes: {balancer['notes']}")
        lines.append("")

    lines.extend(["## Selection Rules", ""])
    if intro["selection_rules"]:
        lines.extend(f"- {line}" for line in intro["selection_rules"])
    else:
        lines.append("- None captured")

    lines.extend(["", "## Breakpoint Guidance", ""])
    if intro["breakpoint_guidance"]:
        lines.extend(f"- {line}" for line in intro["breakpoint_guidance"])
    else:
        lines.append("- None captured")

    lines.extend(["", "## Interchromosomal Effect Notes", ""])
    if intro["interchromosomal_notes"]:
        lines.extend(f"- {line}" for line in intro["interchromosomal_notes"])
    else:
        lines.append("- None captured")

    return "\n".join(lines).rstrip() + "\n"


def _catalog_row_from_balancer(document):
    symbol = str(document.get("symbol") or "").strip()
    payload = {
        "family": document.get("family") or symbol,
        # normalize_chromosome_label is what _candidate_documents compares
        # against ("X" -> 1, "2" -> 2, ...). The real BDSC parser
        # (_normalize_chromosome above) emits strings ("X", "1".."4"), never
        # ints; storing the raw string here made every ingested balancer
        # silently invisible to scoring ("2" != 2), and re-ingesting a
        # shipped symbol would overwrite its working int-typed row with a
        # broken string-typed one.
        "chromosome": normalize_chromosome_label(document.get("chromosome")),
        "default_markers": list(document.get("default_markers") or []),
        "notes": list(document.get("notes") or []),
        # Carried so balancer_selection keeps breakpoint-aware scoring once it
        # stops reading the balancer_definitions staging collection.
        "breakpoint_regions": list(document.get("breakpoint_regions") or []),
        "breakpoint_text": document.get("breakpoint_text", ""),
    }
    return {
        "Key": symbol,
        "kind": "balancer",
        "match": {"symbol": symbol, "aliases": list(document.get("aliases") or [])},
        "payload": payload,
        "sorting": {}, "audit": {}, "imaging": {"aliases": [], "images": []},
        "expression": {},
        "provenance": {"source": "bdsc_ingest"},
        "origin": "curated",
        "CuratedBy": "bdsc_ingest",
    }


def _bump_marker_catalog_revision(db):
    """Bump markerCatalogRevision directly against ``db["settings"]``.

    This deliberately does not import flymanager.utils.mongo.marker_definitions
    (or anything else under flymanager.utils.mongo): that package has a hard
    circular import on flymanager.app (utils.mongo -> utils.mongo.crosses ->
    app.settings -> app/__init__ -> utils.mongo again), which only resolves
    because every real caller happens to import flymanager.app first. This
    module's ingest_balancer_definitions is also called from manage.py, a
    standalone CLI script that never imports flymanager.app, so pulling in
    flymanager.utils.mongo here would crash that path. marker_catalog.py's own
    read_catalog_revision sidesteps the same cycle the same way, by touching
    db["settings"] directly instead of going through the mongo package.
    """
    from flymanager.utils.phenotypes.marker_catalog import \
        MARKER_CATALOG_REVISION_KEY

    settings = db["settings"].find_one({}) or {}
    revision = int(settings.get(MARKER_CATALOG_REVISION_KEY) or 0) + 1
    db["settings"].update_one({}, {"$set": {MARKER_CATALOG_REVISION_KEY: revision}}, upsert=True)
    return revision


def _upsert_catalog_balancers(db, balancers):
    """Mirror ingested balancers into marker_definitions.

    A row a user has taken ownership of (origin "user") is left alone: the
    ingest is a reference-data refresh, not an authority to overwrite someone's
    deliberate override.

    A row whose chromosome does not normalize (an unparseable scrape, or a
    genuinely off-catalog chromosome label) is skipped outright rather than
    written with payload.chromosome == None: candidate matching would never
    select it either way (None never equals the int normalize_chromosome_label
    produces for a real chromosome), but writing it would still silently
    clobber a previously-good row for the same symbol -- e.g. a bad re-scrape
    of an already-working shipped balancer -- with one that can never be
    selected. Leaving the existing row (or writing nothing for a new symbol)
    is strictly safer than persisting known-broken data.
    """
    collection = db["marker_definitions"]
    written = 0
    for document in balancers:
        row = _catalog_row_from_balancer(document)
        if not row["Key"] or row["payload"]["chromosome"] is None:
            continue
        existing = collection.find_one({"Key": row["Key"]})
        if existing is not None and existing.get("origin") == "user":
            continue
        collection.update_one({"Key": row["Key"]}, {"$set": row}, upsert=True)
        written += 1

    if written:
        _bump_marker_catalog_revision(db)
    return written


def ingest_balancer_definitions(db, report, collection_name="balancer_definitions"):
    collection = db[collection_name]
    balancers = [deepcopy(document) for document in report["definitions"]["balancers"]]
    collection.delete_many({})
    if balancers:
        collection.insert_many(balancers)
    collection.create_index("symbol")
    collection.create_index("chromosome")
    return {
        "collection": collection_name,
        "inserted": len(balancers),
        "source_url": report["definitions"]["source_url"],
        "catalog_rows": _upsert_catalog_balancers(db, balancers),
    }


def write_balancer_report_files(report, *, json_path, markdown_path):
    json_path = Path(json_path)
    markdown_path = Path(markdown_path)
    json_path.parent.mkdir(parents=True, exist_ok=True)
    markdown_path.parent.mkdir(parents=True, exist_ok=True)
    json_path.write_text(json.dumps(report, indent=2, sort_keys=True), encoding="utf-8")
    markdown_path.write_text(render_balancer_report_markdown(report), encoding="utf-8")
    return {
        "json_path": str(json_path),
        "markdown_path": str(markdown_path),
    }