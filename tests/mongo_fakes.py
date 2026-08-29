"""Shared in-memory Mongo test doubles.

Started as the inline FakeCollection/FakeDatabase in test_bulk_operations.py;
extracted here and extended (find_one_and_update, delete_many, distinct,
sort/skip/limit cursor chaining, a small aggregate() covering the stages this
codebase's aggregation pipelines use) so later tests don't redefine it.
"""
import copy
import re
from types import SimpleNamespace

from pymongo import ReturnDocument


def _matches_operator_clause(actual, clause, *, field_present=True):
    for operator, operand in clause.items():
        if operator == "$exists":
            if field_present != operand:
                return False
        elif operator == "$in":
            if actual not in operand:
                return False
        elif operator == "$nin":
            if actual in operand:
                return False
        elif operator == "$ne":
            if actual == operand:
                return False
        elif operator == "$regex":
            flags = re.IGNORECASE if "i" in str(clause.get("$options", "")) else 0
            if actual is None or not re.search(operand, str(actual), flags):
                return False
        elif operator == "$options":
            continue  # consumed by the $regex branch above
        else:
            raise NotImplementedError(f"Unsupported query operator: {operator}")
    return True


def _matches(record, query):
    query = query or {}
    if "$and" in query and not all(_matches(record, clause) for clause in query["$and"]):
        return False
    if "$or" in query and not any(_matches(record, clause) for clause in query["$or"]):
        return False
    for key, value in query.items():
        if key in ("$or", "$and"):
            continue
        if isinstance(value, dict) and any(k.startswith("$") for k in value):
            if not _matches_operator_clause(
                record.get(key), value, field_present=key in record
            ):
                return False
        elif record.get(key) != value:
            return False
    return True


def _apply_set(record, updates):
    """Apply a $set document, honouring dotted paths like "Cache.field"."""
    for field, value in (updates or {}).items():
        if "." not in field:
            record[field] = value
            continue
        head, _, tail = field.partition(".")
        target = record.setdefault(head, {})
        if not isinstance(target, dict):
            target = {}
            record[head] = target
        _apply_set(target, {tail: value})


def _apply_inc(record, increments):
    """Apply an $inc document; a missing field increments from 0, like real Mongo."""
    for field, amount in (increments or {}).items():
        if "." not in field:
            record[field] = (record.get(field) or 0) + amount
            continue
        head, _, tail = field.partition(".")
        target = record.setdefault(head, {})
        if not isinstance(target, dict):
            target = {}
            record[head] = target
        _apply_inc(target, {tail: amount})


def _apply_update(record, update):
    """Apply the subset of update operators this fake supports."""
    _apply_set(record, update.get("$set", {}))
    _apply_inc(record, update.get("$inc", {}))


class FakeCursor:
    def __init__(self, records, projection=None):
        self._records = list(records)
        self._projection = projection
        self._sort_spec = None
        self._skip_count = 0
        self._limit_count = None

    def sort(self, key_or_list, direction=None):
        if direction is not None:
            self._sort_spec = [(key_or_list, direction)]
        else:
            self._sort_spec = list(key_or_list)
        return self

    def skip(self, count):
        self._skip_count = count
        return self

    def limit(self, count):
        self._limit_count = count
        return self

    def _materialize(self):
        records = list(self._records)
        if self._sort_spec:
            for field, direction in reversed(self._sort_spec):
                records.sort(key=lambda r: r.get(field), reverse=(direction == -1))
        if self._skip_count:
            records = records[self._skip_count:]
        if self._limit_count is not None:
            records = records[: self._limit_count]
        if self._projection:
            records = [
                {k: v for k, v in record.items() if k in self._projection}
                for record in records
            ]
        return [dict(record) for record in records]

    def __iter__(self):
        return iter(self._materialize())

    def __list__(self):
        return self._materialize()


def _apply_convert(value, spec):
    try:
        if spec.get("to") == "double":
            return float(value)
        return value
    except (TypeError, ValueError):
        return spec.get("onError", spec.get("onNull"))


class FakeCollection:
    def __init__(self, database, name):
        self.database = database
        self.name = name

    @property
    def _records(self):
        return self.database.data.setdefault(self.name, [])

    def find_one(self, query=None, projection=None):
        for record in self._records:
            if _matches(record, query or {}):
                doc = dict(record)
                if projection:
                    doc = {k: v for k, v in doc.items() if k in projection}
                return doc
        return None

    def find(self, query=None, projection=None):
        return FakeCursor(
            (r for r in self._records if _matches(r, query or {})),
            projection=projection,
        )

    def count_documents(self, query=None):
        return sum(1 for r in self._records if _matches(r, query or {}))

    def distinct(self, field, query=None):
        # Matches the real driver: returns raw distinct values verbatim,
        # including None/"" - callers are responsible for filtering those
        # out, same as they must against a real MongoDB deployment.
        values = {
            record.get(field)
            for record in self._records
            if _matches(record, query or {})
        }
        return list(values)

    def insert_one(self, document):
        self._records.append(dict(document))
        return SimpleNamespace(inserted_id=len(self._records))

    def insert_many(self, documents):
        documents = list(documents)
        for document in documents:
            self._records.append(dict(document))
        return SimpleNamespace(inserted_ids=list(range(len(documents))))

    def update_one(self, query, update, upsert=False):
        for record in self._records:
            if _matches(record, query):
                _apply_set(record, update.get("$set", {}))
                return SimpleNamespace(matched_count=1, modified_count=1, upserted_id=None)
        if upsert:
            # Insert a new document with the update
            new_doc = dict(query)
            _apply_set(new_doc, update.get("$set", {}))
            self._records.append(new_doc)
            return SimpleNamespace(matched_count=0, modified_count=0, upserted_id=len(self._records))
        return SimpleNamespace(matched_count=0, modified_count=0, upserted_id=None)

    def find_one_and_update(self, query, update, return_document="AFTER", upsert=False):
        # `return_document` may be the literal string "AFTER" or the real
        # pymongo.ReturnDocument.AFTER enum member; a plain `!= "AFTER"`
        # string comparison never matches the enum form, so check both.
        return_after = return_document in ("AFTER", ReturnDocument.AFTER)
        for record in self._records:
            if _matches(record, query):
                if not return_after:
                    before = dict(record)
                    _apply_update(record, update)
                    return before
                _apply_update(record, update)
                return dict(record)
        if upsert:
            new_doc = dict(query)
            _apply_update(new_doc, update)
            self._records.append(new_doc)
            return dict(new_doc) if return_after else None
        return None

    def delete_one(self, query):
        for index, record in enumerate(self._records):
            if _matches(record, query):
                del self._records[index]
                return SimpleNamespace(deleted_count=1)
        return SimpleNamespace(deleted_count=0)

    def delete_many(self, query):
        keep, removed = [], 0
        for record in self._records:
            if _matches(record, query):
                removed += 1
            else:
                keep.append(record)
        self._records[:] = keep
        return SimpleNamespace(deleted_count=removed)

    def bulk_write(self, operations, ordered=True):
        modified = 0
        for operation in operations:
            query = operation._filter
            update = operation._doc
            for record in self._records:
                if _matches(record, query):
                    _apply_set(record, update.get("$set", {}))
                    modified += 1
                    break
        return SimpleNamespace(modified_count=modified)

    def create_index(self, *args, **kwargs):
        return "-".join(str(a) for a in args)

    def aggregate(self, pipeline, **kwargs):
        # kwargs (e.g. allowDiskUse) are accepted for signature compatibility
        # with pymongo's real Collection.aggregate but have no effect here.
        records = [dict(r) for r in self._records]
        for stage in pipeline:
            if "$match" in stage:
                query = stage["$match"]
                records = [r for r in records if _matches(r, query)]
            elif "$addFields" in stage:
                for field, spec in stage["$addFields"].items():
                    convert_spec = spec.get("$convert") if isinstance(spec, dict) else None
                    for record in records:
                        if convert_spec:
                            source_field = convert_spec["input"].lstrip("$")
                            record[field] = _apply_convert(
                                record.get(source_field), convert_spec
                            )
            elif "$sort" in stage:
                for field, direction in reversed(list(stage["$sort"].items())):
                    records.sort(key=lambda r: r.get(field), reverse=(direction == -1))
            elif "$skip" in stage:
                records = records[stage["$skip"]:]
            elif "$limit" in stage:
                records = records[: stage["$limit"]]
            else:
                raise NotImplementedError(f"Unsupported aggregation stage: {stage}")
        return records


class FakeDatabase:
    def __init__(self, initial_data=None):
        self.data = {
            name: [dict(item) for item in records]
            for name, records in (initial_data or {}).items()
        }

    def __getitem__(self, name):
        self.data.setdefault(name, [])
        return FakeCollection(self, name)

    def __getattr__(self, name):
        # Mirror pymongo.Database, which supports both db["name"] and db.name.
        if name.startswith("_"):
            raise AttributeError(name)
        return self[name]
