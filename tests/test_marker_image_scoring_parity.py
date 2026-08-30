import json
from pathlib import Path

import pytest

from flymanager.utils.phenotypes import image_catalog
from flymanager.utils.phenotypes.image_library import select_phenotype_reference_images


@pytest.fixture(autouse=True)
def _restore_snapshot():
    yield
    image_catalog.set_image_catalog_for_testing(
        {"entries": [], "by_marker_key": {}, "revision": -1})


def test_baseline_fixture_is_not_vacuous():
    """A parity test over an all-empty baseline proves nothing.

    The fixture was captured at 697ab49 against the filesystem scorer, with
    stubs taken from the resolved marker payloads themselves so gene_stem
    and allele_token are present.
    """
    baseline = json.loads(Path("tests/fixtures/image_scoring_baseline.json").read_text())
    with_matches = [record for record in baseline if record["matches"]]
    assert len(baseline) >= 100
    assert len(with_matches) >= 25, (
        f"only {len(with_matches)} baseline records match; the parity guard "
        "is weaker than it looks")


def test_image_matching_reproduces_captured_filesystem_baseline():
    seed = json.loads(Path("data/markers/images/index.json").read_text())
    image_catalog.set_image_catalog_for_testing(image_catalog.compile_image_catalog(
        [{**record, "storageId": f"seed-{record['imageId']}"} for record in seed]))
    paths = {record["imageId"]: record["display"]["sourcePath"] for record in seed}
    baseline = json.loads(Path("tests/fixtures/image_scoring_baseline.json").read_text())
    mismatches = []
    for record in baseline:
        actual = select_phenotype_reference_images([record["marker"]])
        got = [paths[match["image_id"]] for match in actual]
        expected = [match["relative_path"] for match in record["matches"]]
        if got != expected:
            mismatches.append((record["marker"]["key"], expected, got))
    assert not mismatches, mismatches[:5]
