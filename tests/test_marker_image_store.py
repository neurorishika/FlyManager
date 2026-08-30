import pytest

from flymanager.utils.phenotypes.image_store import (GridFSImageStore,
                                                     ImageNotFound,
                                                     MemoryImageStore)

BUCKET = "marker_images_test"


@pytest.fixture
def gridfs_store():
    """The production backend, exercised against the real Mongo.

    FakeDatabase cannot emulate GridFS, so without this every storage test
    runs against a dict and a broken GridFSImageStore ships green.
    """
    from flymanager.app import db

    store = GridFSImageStore(db, bucket_name=BUCKET)
    created = []
    original_put = store.put

    def tracking_put(data, *, content_type):
        storage_id = original_put(data, content_type=content_type)
        created.append(storage_id)
        return storage_id

    store.put = tracking_put
    yield store
    for storage_id in created:
        store.delete(storage_id)


def test_memory_store_round_trip_and_delete():
    store = MemoryImageStore()
    first = store.put(b"same", content_type="image/webp")
    second = store.put(b"same", content_type="image/webp")
    assert first != second
    assert store.open(first).read() == b"same"
    store.delete(first)
    assert not store.exists(first)
    with pytest.raises(ImageNotFound):
        store.open(first)


def test_gridfs_store_round_trips_real_bytes(gridfs_store):
    payload = b"RIFF\x00\x00\x00\x00WEBPfake-bytes"
    storage_id = gridfs_store.put(payload, content_type="image/webp")

    assert isinstance(storage_id, str)
    assert gridfs_store.exists(storage_id)
    assert gridfs_store.open(storage_id).read() == payload


def test_gridfs_store_does_not_deduplicate_identical_bytes(gridfs_store):
    """Dedup is the catalog's job, keyed on sha256; the store stays dumb."""
    first = gridfs_store.put(b"same", content_type="image/webp")
    second = gridfs_store.put(b"same", content_type="image/webp")

    assert first != second
    assert gridfs_store.open(first).read() == gridfs_store.open(second).read()


def test_gridfs_store_delete_is_idempotent(gridfs_store):
    storage_id = gridfs_store.put(b"bytes", content_type="image/webp")
    gridfs_store.delete(storage_id)

    assert not gridfs_store.exists(storage_id)
    gridfs_store.delete(storage_id)  # must not raise on a second delete
    with pytest.raises(ImageNotFound):
        gridfs_store.open(storage_id)


def test_gridfs_store_reports_missing_and_malformed_ids(gridfs_store):
    # A well-formed ObjectId that was never stored, and a string that is not
    # an ObjectId at all -- both must be "missing", never a raw pymongo error.
    assert not gridfs_store.exists("000000000000000000000000")
    assert not gridfs_store.exists("not-an-object-id")
    with pytest.raises(ImageNotFound):
        gridfs_store.open("not-an-object-id")


def test_gridfs_store_survives_a_large_payload(gridfs_store):
    """Bigger than GridFS's 255 KB chunk size, so chunking is exercised."""
    payload = bytes(range(256)) * 4000  # ~1 MB
    storage_id = gridfs_store.put(payload, content_type="image/webp")

    assert gridfs_store.open(storage_id).read() == payload
