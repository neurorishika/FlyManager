import contextlib
import re
import shutil
import urllib.request
from html.parser import HTMLParser
from pathlib import Path
from urllib.parse import urljoin, urlparse

BULKDATA_URL = "https://flybase.org/downloads/bulkdata"
DPO_URL = "http://purl.obolibrary.org/obo/dpo.obo"
DEFAULT_TIMEOUT_SECONDS = 60
REQUEST_HEADERS = {
    "User-Agent": "FlyManager/0.1 (FlyBase bulk data sync)",
}
REQUIRED_FLYBASE_DOWNLOADS = {
    "genotype_phenotype_data": "genotype_phenotype_data",
    "fbal_to_fbgn": "fbal_to_fbgn",
    "allele_descriptions": "dmel_classical_and_insertion_allele_descriptions",
    "stocks": "stocks",
    "construct_descriptions": "transgenic_construct_descriptions",
    "split_system_combinations": "split_system_combinations",
    "gene_map_table": "gene_map_table",
}


class _AnchorCollector(HTMLParser):
    def __init__(self, base_url):
        super().__init__()
        self._base_url = base_url
        self._current_href = None
        self._current_text = []
        self.links = []

    def handle_starttag(self, tag, attrs):
        if tag != "a":
            return
        href = dict(attrs).get("href")
        if not href:
            return
        self._current_href = urljoin(self._base_url, href)
        self._current_text = []

    def handle_data(self, data):
        if self._current_href is not None:
            self._current_text.append(data)

    def handle_endtag(self, tag):
        if tag != "a" or self._current_href is None:
            return
        self.links.append(
            {
                "text": "".join(self._current_text).strip(),
                "href": self._current_href,
            }
        )
        self._current_href = None
        self._current_text = []


def _fetch_text(url, timeout=DEFAULT_TIMEOUT_SECONDS):
    request = urllib.request.Request(url, headers=REQUEST_HEADERS)
    with urllib.request.urlopen(request, timeout=timeout) as response:
        return response.read().decode("utf-8", errors="replace")


def _extract_release_id(html):
    matches = re.findall(r"FB\d{4}_\d{2}", html)
    if not matches:
        raise ValueError("Could not determine current FlyBase release from bulk-data page")

    def release_sort_key(release_id):
        year, release_number = release_id.removeprefix("FB").split("_")
        return int(year), int(release_number)

    return max(set(matches), key=release_sort_key)


def _extract_links(html, base_url):
    parser = _AnchorCollector(base_url)
    parser.feed(html)
    seen = set()
    deduplicated = []
    for link in parser.links:
        href = link["href"]
        if href in seen:
            continue
        seen.add(href)
        deduplicated.append(link)
    return deduplicated


def _choose_download_link(links, prefix):
    candidates = []
    for link in links:
        href = link["href"]
        if "s3ftp.flybase.org" not in href:
            continue
        filename = Path(urlparse(href).path).name
        if prefix not in filename:
            continue
        if not filename.endswith(".tsv.gz"):
            continue
        candidates.append(
            {
                "url": href,
                "filename": filename,
                "text": link["text"],
            }
        )

    if not candidates:
        return None

    candidates.sort(
        key=lambda candidate: (
            not candidate["filename"].startswith(prefix),
            len(candidate["filename"]),
            candidate["filename"],
        )
    )
    return candidates[0]


def parse_flybase_bulkdata_html(html, download_page_url=BULKDATA_URL, required_downloads=None):
    required_downloads = required_downloads or REQUIRED_FLYBASE_DOWNLOADS
    release = _extract_release_id(html)
    links = _extract_links(html, download_page_url)

    downloads = {}
    missing = {}
    for key, prefix in required_downloads.items():
        chosen = _choose_download_link(links, prefix)
        if chosen is None:
            missing[key] = prefix
            continue
        downloads[key] = {
            "prefix": prefix,
            "filename": chosen["filename"],
            "url": chosen["url"],
            "text": chosen["text"],
        }

    downloads["dpo"] = {
        "prefix": "dpo",
        "filename": "dpo.obo",
        "url": DPO_URL,
        "text": "Drosophila Phenotype Ontology",
    }

    return {
        "release": release,
        "source_url": download_page_url,
        "downloads": downloads,
        "missing": missing,
    }


def discover_latest_flybase_downloads(download_page_url=BULKDATA_URL, timeout=DEFAULT_TIMEOUT_SECONDS):
    html = _fetch_text(download_page_url, timeout=timeout)
    return parse_flybase_bulkdata_html(html, download_page_url=download_page_url)


def _download_file(url, target_path, timeout=DEFAULT_TIMEOUT_SECONDS):
    target_path = Path(target_path)
    temp_path = target_path.parent / f"{target_path.name}.part"
    request = urllib.request.Request(url, headers=REQUEST_HEADERS)
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response, temp_path.open("wb") as handle:
            shutil.copyfileobj(response, handle)
        temp_path.replace(target_path)
    except Exception:
        with contextlib.suppress(FileNotFoundError):
            temp_path.unlink()
        raise


def download_flybase_bundle(
    data_dir,
    overwrite=False,
    dry_run=False,
    timeout=DEFAULT_TIMEOUT_SECONDS,
    required_keys=None,
    bundle=None,
):
    data_dir = Path(data_dir)
    data_dir.mkdir(parents=True, exist_ok=True)
    bundle = bundle or discover_latest_flybase_downloads(timeout=timeout)

    selected_keys = required_keys or list(bundle["downloads"].keys())
    results = []

    for key in selected_keys:
        download = bundle["downloads"].get(key)
        if download is None:
            results.append({"key": key, "status": "missing"})
            continue

        target_path = data_dir / download["filename"]
        if target_path.exists() and not overwrite:
            results.append(
                {
                    "key": key,
                    "status": "skipped",
                    "path": str(target_path),
                    "url": download["url"],
                    "size_bytes": target_path.stat().st_size,
                }
            )
            continue

        if dry_run:
            results.append(
                {
                    "key": key,
                    "status": "would_download",
                    "path": str(target_path),
                    "url": download["url"],
                }
            )
            continue

        _download_file(download["url"], target_path, timeout=timeout)
        results.append(
            {
                "key": key,
                "status": "downloaded",
                "path": str(target_path),
                "url": download["url"],
                "size_bytes": target_path.stat().st_size,
            }
        )

    return {
        "release": bundle["release"],
        "source_url": bundle["source_url"],
        "data_dir": str(data_dir),
        "missing": bundle["missing"],
        "results": results,
    }