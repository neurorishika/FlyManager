"""Tests for the "compute once, persist, refresh only on explicit request" rule.

Phenotype/evidence caches must never be silently recomputed on an ordinary
read (explorer/view pages). They're computed at create/edit time and only
ever refreshed again via an explicit user action.
"""
import json

from flymanager.utils.phenotypes import flybase_pipeline
from flymanager.utils.phenotypes.predictor import (
    PHENOTYPE_CACHE_VERSION,
    get_cached_cross_phenotype,
    get_cached_stock_phenotype,
)


def _stock(genotype="w[1118]", version=PHENOTYPE_CACHE_VERSION, signature="sig-current"):
    return {
        "Genotype": genotype,
        "PhenotypeCache": {
            "version": version,
            "pipelineSignature": signature,
            "genotype": genotype,
            "prediction": {"best_guess_summary": "White eyes", "confidence_label": "high"},
        },
    }


def _cross(male="w[1118]", female="w[1118]/w[1118]", version=PHENOTYPE_CACHE_VERSION, signature="sig-current"):
    return {
        "MaleGenotype": male,
        "FemaleGenotype": female,
        "PhenotypeCache": {
            "version": version,
            "pipelineSignature": signature,
            "maleGenotype": male,
            "femaleGenotype": female,
            "parentPhenotypes": {"summary": "White eyes"},
            "predictedOffspring": [],
        },
    }


def test_strict_lookup_rejects_stale_version(monkeypatch):
    monkeypatch.setattr(
        "flymanager.utils.phenotypes.predictor.compute_flybase_pipeline_signature",
        lambda: "sig-current",
    )
    stock = _stock(version=PHENOTYPE_CACHE_VERSION - 1)
    assert get_cached_stock_phenotype(stock, strict=True) is None


def test_strict_lookup_rejects_stale_signature(monkeypatch):
    monkeypatch.setattr(
        "flymanager.utils.phenotypes.predictor.compute_flybase_pipeline_signature",
        lambda: "sig-new",
    )
    stock = _stock(signature="sig-old")
    assert get_cached_stock_phenotype(stock, strict=True) is None


def test_lenient_lookup_serves_stale_cache_instead_of_recomputing(monkeypatch):
    monkeypatch.setattr(
        "flymanager.utils.phenotypes.predictor.compute_flybase_pipeline_signature",
        lambda: "sig-new",
    )
    stock = _stock(version=PHENOTYPE_CACHE_VERSION - 1, signature="sig-old")
    cache = get_cached_stock_phenotype(stock, strict=False)
    assert cache is not None
    assert cache["prediction"]["best_guess_summary"] == "White eyes"


def test_lenient_lookup_still_rejects_genotype_mismatch(monkeypatch):
    monkeypatch.setattr(
        "flymanager.utils.phenotypes.predictor.compute_flybase_pipeline_signature",
        lambda: "sig-current",
    )
    stock = _stock(genotype="w[1118]")
    stock["Genotype"] = "y[1] w[1118]"  # genotype edited after cache was built
    assert get_cached_stock_phenotype(stock, strict=False) is None


def test_lenient_lookup_missing_cache_returns_none(monkeypatch):
    monkeypatch.setattr(
        "flymanager.utils.phenotypes.predictor.compute_flybase_pipeline_signature",
        lambda: "sig-current",
    )
    stock = {"Genotype": "w[1118]", "PhenotypeCache": None}
    assert get_cached_stock_phenotype(stock, strict=False) is None


def test_cross_lenient_lookup_serves_stale_cache(monkeypatch):
    monkeypatch.setattr(
        "flymanager.utils.phenotypes.predictor.compute_flybase_pipeline_signature",
        lambda: "sig-new",
    )
    cross = _cross(version=PHENOTYPE_CACHE_VERSION - 1, signature="sig-old")
    cache = get_cached_cross_phenotype(cross, strict=False)
    assert cache is not None
    assert cache["parentPhenotypes"]["summary"] == "White eyes"


def test_cross_strict_lookup_rejects_stale_cache(monkeypatch):
    monkeypatch.setattr(
        "flymanager.utils.phenotypes.predictor.compute_flybase_pipeline_signature",
        lambda: "sig-new",
    )
    cross = _cross(version=PHENOTYPE_CACHE_VERSION - 1, signature="sig-old")
    assert get_cached_cross_phenotype(cross, strict=True) is None


def test_flybase_evidence_cache_never_rebuilds_on_lookup(tmp_path, monkeypatch):
    """A signature mismatch must degrade to empty results, not trigger a rebuild."""
    data_dir = tmp_path / "flybase"
    data_dir.mkdir()

    build_calls = []
    monkeypatch.setattr(
        flybase_pipeline,
        "build_flybase_phenotype_cache",
        lambda **kwargs: build_calls.append(kwargs) or {},
    )

    # No cache built yet at all.
    cache = flybase_pipeline.get_flybase_phenotype_cache(data_dir=data_dir)
    assert cache["available"] is False
    assert cache["allele_markers"] == {}
    assert build_calls == []

    # A stale on-disk cache (signature no longer matches source files) must
    # still be served as-is, not rebuilt.
    cache_path = data_dir / flybase_pipeline.DEFAULT_CACHE_FILENAME
    cache_path.write_text(
        json.dumps(
            {
                "cache_version": flybase_pipeline.PHENOTYPE_EVIDENCE_CACHE_VERSION,
                "generated_at": "2020-01-01T00:00:00Z",
                "source_signature": "stale-signature",
                "source_kind": "files",
                "allele_markers": {"w[1118]": {"display_label": "w"}},
            }
        ),
        encoding="utf-8",
    )
    flybase_pipeline.clear_flybase_phenotype_cache()
    cache = flybase_pipeline.get_flybase_phenotype_cache(data_dir=data_dir)
    assert cache["allele_markers"]["w[1118]"]["display_label"] == "w"
    assert build_calls == []


def test_flybase_evidence_cache_status_reports_missing(tmp_path):
    data_dir = tmp_path / "flybase"
    data_dir.mkdir()
    flybase_pipeline.clear_flybase_phenotype_cache()
    status = flybase_pipeline.flybase_phenotype_cache_status(data_dir=data_dir)
    assert status["built"] is False
    assert status["stale"] is True


def test_flybase_evidence_cache_status_reports_stale_signature(tmp_path):
    data_dir = tmp_path / "flybase"
    data_dir.mkdir()
    cache_path = data_dir / flybase_pipeline.DEFAULT_CACHE_FILENAME
    cache_path.write_text(
        json.dumps(
            {
                "cache_version": flybase_pipeline.PHENOTYPE_EVIDENCE_CACHE_VERSION,
                "generated_at": "2020-01-01T00:00:00Z",
                "source_signature": "outdated-signature",
                "source_kind": "files",
                "allele_markers": {},
            }
        ),
        encoding="utf-8",
    )
    flybase_pipeline.clear_flybase_phenotype_cache()
    status = flybase_pipeline.flybase_phenotype_cache_status(data_dir=data_dir)
    assert status["built"] is True
    assert status["stale"] is True
