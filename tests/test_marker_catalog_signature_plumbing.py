import pytest

from flymanager.app.services.stock_standardization import (
    STANDARDIZATION_CACHE_VERSION, build_cross_standardization_cache,
    build_stock_standardization_cache, get_cached_cross_standardization,
    get_cached_stock_standardization)
from flymanager.utils.phenotypes import marker_catalog
from flymanager.utils.phenotypes.predictor import (
    PHENOTYPE_CACHE_VERSION, build_cross_phenotype_cache,
    build_stock_phenotype_cache, get_cached_cross_phenotype,
    get_cached_stock_phenotype)

GENOTYPE = "w[1118]; CyO/Sp"


@pytest.fixture(autouse=True)
def _reset_catalog():
    marker_catalog.reset_catalog()
    marker_catalog.reset_refresh_state()
    yield
    marker_catalog.reset_catalog()
    marker_catalog.reset_refresh_state()


def _mutate_catalog():
    """Change one marker's effect text, which must move the signature."""
    shipped = marker_catalog.load_shipped_catalog()
    row = next(d for d in shipped["definitions"] if d["Key"] == "Cy")
    marker_catalog.set_catalog(marker_catalog.compile_catalog(
        shipped,
        [dict(row, origin="user",
              payload=dict(row["payload"], effect="edited for the test"))],
    ))


def test_cache_versions_were_bumped():
    assert PHENOTYPE_CACHE_VERSION == 3
    assert STANDARDIZATION_CACHE_VERSION >= 2


def test_stock_phenotype_cache_carries_the_catalog_signature():
    cache = build_stock_phenotype_cache(GENOTYPE)
    assert cache["markerCatalogSignature"] == marker_catalog.get_catalog()["signature"]


def test_cross_phenotype_cache_carries_the_catalog_signature():
    cache = build_cross_phenotype_cache(GENOTYPE, GENOTYPE)
    assert cache["markerCatalogSignature"] == marker_catalog.get_catalog()["signature"]


def test_strict_read_rejects_a_stale_catalog_signature():
    record = {"Genotype": GENOTYPE, "PhenotypeCache": build_stock_phenotype_cache(GENOTYPE)}
    assert get_cached_stock_phenotype(record, strict=True) is not None
    _mutate_catalog()
    assert get_cached_stock_phenotype(record, strict=True) is None


def test_non_strict_read_still_serves_a_stale_catalog_signature():
    record = {"Genotype": GENOTYPE, "PhenotypeCache": build_stock_phenotype_cache(GENOTYPE)}
    _mutate_catalog()
    assert get_cached_stock_phenotype(record, strict=False) is not None


def test_strict_cross_read_rejects_a_stale_catalog_signature():
    record = {
        "MaleGenotype": GENOTYPE,
        "FemaleGenotype": GENOTYPE,
        "PhenotypeCache": build_cross_phenotype_cache(GENOTYPE, GENOTYPE),
    }
    assert get_cached_cross_phenotype(record, strict=True) is not None
    _mutate_catalog()
    assert get_cached_cross_phenotype(record, strict=True) is None


def test_a_cache_written_before_this_change_has_no_signature_and_is_tolerated():
    """Absent signature means 'written before signatures existed'; the version
    bump is what forces those to recompute, exactly as pipelineSignature does."""
    cache = build_stock_phenotype_cache(GENOTYPE)
    cache.pop("markerCatalogSignature")
    record = {"Genotype": GENOTYPE, "PhenotypeCache": cache}
    assert get_cached_stock_phenotype(record, strict=True) is not None


def test_standardization_caches_carry_and_check_the_signature():
    stock_record = {"Genotype": GENOTYPE,
                    "StandardizationCache": build_stock_standardization_cache(GENOTYPE)}
    cross_record = {
        "MaleGenotype": GENOTYPE,
        "FemaleGenotype": GENOTYPE,
        "StandardizationCache": build_cross_standardization_cache(GENOTYPE, GENOTYPE),
    }
    assert stock_record["StandardizationCache"]["markerCatalogSignature"] == \
        marker_catalog.get_catalog()["signature"]
    assert get_cached_stock_standardization(stock_record, strict=True) is not None
    assert get_cached_cross_standardization(cross_record, strict=True) is not None

    _mutate_catalog()
    assert get_cached_stock_standardization(stock_record, strict=True) is None
    assert get_cached_cross_standardization(cross_record, strict=True) is None
    assert get_cached_stock_standardization(stock_record, strict=False) is not None


def test_the_worker_envelope_refreshes_the_catalog_before_running(monkeypatch):
    """A worker that skipped this would recompute every cache against the
    shipped-only marker set and stamp a signature the web process rejects."""
    from flymanager.app.jobs import tasks

    calls = []
    monkeypatch.setattr(
        "flymanager.utils.phenotypes.marker_catalog.refresh_catalog",
        lambda db, **kwargs: calls.append("refreshed"))
    monkeypatch.setattr(tasks, "mark_job_running", lambda *a, **k: calls.append("running"))
    monkeypatch.setattr(tasks, "mark_job_succeeded", lambda *a, **k: None)

    tasks._run("job-key", lambda app, db: {"ok": True})

    assert calls[0] == "refreshed", "the refresh must happen before the job body"
