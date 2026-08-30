import hashlib
import json
from pathlib import Path

SEED = Path("data/markers/images")


def test_seed_is_complete_small_and_consistent():
    records = json.loads((SEED / "index.json").read_text())
    assert len(records) == 254
    assert len({r["imageId"] for r in records}) == len(records)
    assert sum(r["bytes"] for r in records) < 10_000_000
    for record in records:
        data = (SEED / record["file"]).read_bytes()
        assert hashlib.sha256(data).hexdigest() == record["sha256"]
        assert len(data) == record["bytes"]
