import pytest

from flymanager.utils.phenotypes.image_store import ImageNotFound, MemoryImageStore


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
