import pytest

from flymanager.utils.converter import replace_collections_with_plan


class FakeCollection:
    def __init__(self, database, name):
        self.database = database
        self.name = name

    def insert_many(self, records):
        if self.database.fail_on_insert == self.name:
            raise RuntimeError(f"insert failed for {self.name}")
        self.database.data[self.name] = [dict(record) for record in records]

    def drop(self):
        self.database.data.pop(self.name, None)

    def rename(self, new_name):
        if self.database.fail_on_rename == self.name:
            raise RuntimeError(f"rename failed for {self.name}")
        self.database.data[new_name] = self.database.data.pop(self.name, [])


class FakeDatabase:
    def __init__(self, initial_data=None, fail_on_insert=None, fail_on_rename=None):
        self.data = {name: [dict(item) for item in records] for name, records in (initial_data or {}).items()}
        self.fail_on_insert = fail_on_insert
        self.fail_on_rename = fail_on_rename

    def __getitem__(self, name):
        if name not in self.data:
            self.data[name] = []
        return FakeCollection(self, name)

    def list_collection_names(self):
        return list(self.data.keys())

    def create_collection(self, name):
        self.data.setdefault(name, [])


def test_replace_collections_with_plan_swaps_target_collections_only():
    database = FakeDatabase(
        {
            "settings": [{"theme": "old"}],
            "users": [{"Username": "admin"}],
        }
    )

    replace_collections_with_plan(
        {
            "settings": [{"theme": "new"}],
            "stocks": [],
        },
        database,
    )

    assert database.data["settings"] == [{"theme": "new"}]
    assert database.data["stocks"] == []
    assert database.data["users"] == [{"Username": "admin"}]
    assert all(not name.startswith("__import_") for name in database.data)


def test_replace_collections_with_plan_rolls_back_on_swap_failure():
    database = FakeDatabase(
        {"settings": [{"theme": "old"}]},
        fail_on_rename="__import_tmp__settings",
    )

    with pytest.raises(RuntimeError, match="rename failed"):
        replace_collections_with_plan({"settings": [{"theme": "new"}]}, database)

    assert database.data["settings"] == [{"theme": "old"}]
    assert all(not name.startswith("__import_") for name in database.data)