"""The FlyBase evidence memo must not outlive the sources it was built from.

`get_flybase_phenotype_cache` returned its in-process memo unconditionally,
and the disk-load path checked only `cache_version`, never `source_signature`
-- despite its docstring promising staleness would degrade to empty results.

That is the one failure direction `pipelineSignature` exists to prevent. Web
and worker share /app/data. After an admin FlyBase refresh the worker rewrites
the sources and the evidence index, but a long-lived gunicorn process keeps
serving predictions computed from the OLD memo while
`build_stock_phenotype_cache` stamps `compute_flybase_pipeline_signature()`,
which stats the NEW files. Every stock saved through the web from then on
carries stale-evidence output labelled current, and strict readers and
backfills accept it forever. Only a container restart cleared it.
"""
import json

import pytest

from flymanager.utils.phenotypes import flybase_pipeline as fp


@pytest.fixture(autouse=True)
def _clear_memo():
    fp.clear_flybase_phenotype_cache()
    yield
    fp.clear_flybase_phenotype_cache()


def _write_cache(tmp_path, signature, marker="original"):
    payload = {
        "cache_version": fp.PHENOTYPE_EVIDENCE_CACHE_VERSION,
        "source_signature": signature,
        "available": True,
        "marker": marker,
        "alleles": {}, "genes": {},
    }
    path = tmp_path / "phenotype_evidence.json"
    path.write_text(json.dumps(payload), encoding="utf-8")
    return path


def test_a_memo_built_from_stale_sources_is_not_served(tmp_path, monkeypatch):
    path = _write_cache(tmp_path, "signature-A")
    monkeypatch.setattr(fp, "compute_flybase_pipeline_signature",
                        lambda data_dir=None: "signature-A")
    first = fp.get_flybase_phenotype_cache(data_dir=tmp_path, cache_path=path)
    assert first.get("marker") == "original"

    # The worker rebuilds the sources: the signature moves, the file on disk is
    # rewritten, and the memo this process holds is now evidence from before.
    _write_cache(tmp_path, "signature-B", marker="rebuilt")
    monkeypatch.setattr(fp, "compute_flybase_pipeline_signature",
                        lambda data_dir=None: "signature-B")
    second = fp.get_flybase_phenotype_cache(data_dir=tmp_path, cache_path=path)
    assert second.get("marker") == "rebuilt", "served a memo built from old sources"


def test_a_stale_disk_cache_is_still_served_rather_than_rebuilt(tmp_path, monkeypatch):
    """Deliberately NOT invalidated, unlike the memo.

    A lookup must never trigger the multi-minute source parse, so an index
    that is behind its sources degrades gracefully instead of emptying out --
    pinned by test_flybase_evidence_cache_never_rebuilds_on_lookup. Only the
    in-process memo is invalidated, which is what makes a running web process
    pick up the file an admin refresh just rewrote.
    """
    path = _write_cache(tmp_path, "signature-OLD")
    monkeypatch.setattr(fp, "compute_flybase_pipeline_signature",
                        lambda data_dir=None: "signature-NEW")
    payload = fp.get_flybase_phenotype_cache(data_dir=tmp_path, cache_path=path)
    assert payload.get("marker") == "original"


def test_a_current_cache_is_still_served_and_still_memoized(tmp_path, monkeypatch):
    path = _write_cache(tmp_path, "signature-A")
    monkeypatch.setattr(fp, "compute_flybase_pipeline_signature",
                        lambda data_dir=None: "signature-A")
    assert fp.get_flybase_phenotype_cache(data_dir=tmp_path, cache_path=path)["marker"] == "original"

    # Deleting the file must not change the answer while the signature holds --
    # otherwise this becomes a disk read on every prediction.
    path.unlink()
    assert fp.get_flybase_phenotype_cache(data_dir=tmp_path, cache_path=path)["marker"] == "original"
