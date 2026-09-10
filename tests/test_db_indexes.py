import flymanager.app  # noqa: F401

from flymanager.utils.mongo.db import ensure_mongo_indexes


class RecordingCollection:
    def __init__(self, name, sink):
        self.name = name
        self._sink = sink

    def create_index(self, keys, **kwargs):
        self._sink.append((self.name, keys, kwargs.get("name")))


class RecordingDatabase:
    def __init__(self):
        self.calls = []

    def __getitem__(self, name):
        return RecordingCollection(name, self.calls)


def test_ensure_mongo_indexes_covers_filtered_fields():
    db = RecordingDatabase()
    ensure_mongo_indexes(db)

    index_specs = {(collection, tuple(keys)) for collection, keys, _ in db.calls}

    assert ("stocks", (("User", 1), ("Status", 1), ("TrayID", 1))) in index_specs
    assert ("stocks", (("User", 1), ("TrayID", 1))) in index_specs
    assert ("stocks", (("AssignedTo", 1), ("Status", 1), ("TrayID", 1))) in index_specs
    assert ("crosses", (("User", 1), ("Status", 1), ("TrayID", 1))) in index_specs
    assert ("crosses", (("User", 1), ("TrayID", 1))) in index_specs
    assert ("crosses", (("AssignedTo", 1), ("Status", 1), ("TrayID", 1))) in index_specs
    assert ("users", (("Username", 1),)) in index_specs
    assert ("users", (("ReportsTo", 1),)) in index_specs
    assert ("activity", (("user", 1), ("timestamp", -1))) in index_specs
    assert ("trays", (("User", 1), ("TrayID", 1))) in index_specs
    assert ("password_reset_tokens", (("TokenHash", 1),)) in index_specs

    names = [name for _, _, name in db.calls]
    assert len(names) == len(set(names)), "index names must be unique"
