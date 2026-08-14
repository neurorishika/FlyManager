# MongoDB Efficiency Fixes Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Status note (added post-write, before execution began):** a concurrent session committed `a782aab` ("Reduce MongoDB round trips and push explorer filtering into queries") on this same branch before this plan's tasks were dispatched. It is a *different, mostly non-overlapping* piece of work: it added an optional `projection` kwarg to `get_accessible_documents`/`get_accessible_stocks`/`get_accessible_crosses` (unused by any of this plan's target routes so far — Task 12 can build on it instead of re-adding it), moved `bulk_remove_from_tray` onto the background job queue, and introduced a new `flymanager/utils/materialized_cache.py` caching layer (unrelated to this plan). It did **not** touch `flymanager/utils/mongo/db.py` (indexes), `flip_owned_document`, `flymanager/utils/mongo/trays.py`, `assign_tray_route`, the permanent-delete routes, `get_direct_reports`, or `flymanager/app/routes/stock.py`/`cross.py` (the explorer routes still fetch-all-then-Python-filter exactly as audited). One retarget: Task 11 below still applies, but the per-document `update_one` loop it targets now lives in `backfill_materialized_cache` in `flymanager/utils/materialized_cache.py` (`flymanager/utils/phenotypes/backfill.py` now delegates to it) rather than in `_backfill_collection` in `backfill.py` — apply Task 11's fix there instead. All 12 tasks below are otherwise unaffected and still need to be executed.

**Goal:** Eliminate the verified MongoDB inefficiencies in FlyManager — missing indexes, N+1 query loops, redundant round trips, and full-collection-then-Python-filter explorer views — with root-cause fixes rather than caching band-aids.

**Architecture:** No new services or infra. Each fix stays inside the existing `flymanager/utils/mongo/*` and `flymanager/app/routes/*` modules: add compound indexes that match real query shapes, replace per-item loops with `$in` reads + `bulk_write`/`delete_many`, collapse read-update-read sequences into `find_one_and_update`, and — for the two explorer list views — push deterministic filtering, projection, and pagination into the Mongo query while explicitly preserving the existing fuzzy-search behavior as an in-app post-filter (verified: fuzzy matching cannot be pushed into a Mongo query without changing results).

**Tech Stack:** Flask, PyMongo, pytest (existing `FakeCollection`/`FakeDatabase` in-memory test-double pattern from `tests/test_bulk_operations.py`, extended into a shared module).

**Spec:** No separate spec document exists. This plan's "Audit Summary" section below is the verified spec — every fix listed was confirmed against the current code (not just the original audit) before being written down; two audit claims that did not hold up were dropped (see "Findings that were NOT included").

## Audit Summary (verified)

Confirmed real, in scope for this plan:
1. `ensure_mongo_indexes` ([flymanager/utils/mongo/db.py:51](flymanager/utils/mongo/db.py#L51)) is missing indexes for fields actually filtered on: `stocks`/`crosses` `TrayID`+`Status`, `users.Username`, `users.ReportsTo`, `activity.user`+`timestamp`, `trays.User`+`TrayID`, `password_reset_tokens.TokenHash`.
2. `uid_exists` ([db.py:96](flymanager/utils/mongo/db.py#L96)) fetches full documents just to test existence.
3. `flip_owned_document` ([flymanager/utils/mongo_records.py:389](flymanager/utils/mongo_records.py#L389)) does find → update → find-again, a wasted third round trip.
4. `can_assign_to_user`/`get_direct_reports` ([flymanager/utils/mongo/access.py:159](flymanager/utils/mongo/access.py#L159), [:193](flymanager/utils/mongo/access.py#L193)) scans the entire `users` collection on every single assignee check.
5. `get_accessible_trays` ([flymanager/utils/mongo/trays.py:129](flymanager/utils/mongo/trays.py#L129)) issues one `find_one` per accessible stock/cross to backfill trays.
6. `get_tray_occupancy` ([trays.py:301](flymanager/utils/mongo/trays.py#L301)) issues 2 `find_one` calls per cross to resolve parent stock names.
7. `tray_management` ([flymanager/app/routes/tray.py:26](flymanager/app/routes/tray.py#L26)) calls `get_tray_occupancy` once per tray in a Python loop.
8. `delete_stock_permanently`/`delete_cross_permanently` ([flymanager/app/routes/stock.py:1921](flymanager/app/routes/stock.py#L1921), [flymanager/app/routes/cross.py:974](flymanager/app/routes/cross.py#L974)) loop per-uid doing `find_one` + a separate delete call + a separate activity insert.
9. `assign_tray_route` ([flymanager/app/routes/tray.py:58](flymanager/app/routes/tray.py#L58)) loops `update_document_assignment` once per stock/cross in the tray instead of batching.
10. `_backfill_collection` ([flymanager/utils/phenotypes/backfill.py:42](flymanager/utils/phenotypes/backfill.py#L42)) issues one `update_one` per matching document instead of `bulk_write`.
11. `stock_explorer`/`stock_explorer_selection` ([flymanager/app/routes/stock.py:567](flymanager/app/routes/stock.py#L567), [:681](flymanager/app/routes/stock.py#L681)) and `cross_explorer`/`cross_explorer_selection` ([flymanager/app/routes/cross.py:244](flymanager/app/routes/cross.py#L244)) fetch every accessible document (full fields, no projection) and filter/sort/paginate entirely in Python, on every page view and every filter change.

**Findings that were NOT included, and why:**
- *"Bulk flip/status-change routes are unwired N+1 loops"* — **false on inspection**. `bulk_flip_route`/`bulk_status_change_route` ([flymanager/app/routes/flip.py:260](flymanager/app/routes/flip.py#L260), [:326](flymanager/app/routes/flip.py#L326)) already enqueue RQ background jobs (`task_bulk_flip`/`task_bulk_status_change` in [flymanager/app/jobs/tasks.py:62](flymanager/app/jobs/tasks.py#L62)) that call the batched `bulk_flip_records`/`bulk_change_status_records` in `flymanager/utils/mongo/bulk_operations.py`. Already fixed; no task needed.
- *`autopopulate_series_replicate_ids` full scan* — **false on inspection**. Its query `db["stocks"].find({"User": username}, {"SeriesID": 1})` ([flymanager/app/routes/stock.py:1656](flymanager/app/routes/stock.py#L1656)) is already served as an index prefix by the existing `stocks_user_uid` index on `(User, UniqueID)`. No fix needed.
- *Dashboard `home()` and `standardization_reviewer()` full-fetch* — **real, but excluded from this plan**. Both routes ([flymanager/app/routes/main.py:301](flymanager/app/routes/main.py#L301), [:996](flymanager/app/routes/main.py#L996)) compute derived statistics (status distributions, overdue/due-today buckets, naming-issue detection) that read most fields of every accessible document — there is no projection that safely shrinks the fetch without risking missing a field the computation needs, and pushing the derived stats into an aggregation pipeline is a materially different, higher-risk rewrite than the rest of this plan. Flagging as a candidate for a follow-up plan rather than guessing at a fix here.

## Global Constraints

- Every fix must be behavior-preserving: same JSON/HTML responses, same fuzzy-search semantics, same activity-log entries, same `ModificationLog` text — only the number of Mongo round trips changes.
- No new dependencies. Use `pymongo.UpdateOne`, `ReturnDocument`, `bulk_write`, `delete_many`, `.distinct()` — all already available via the existing `pymongo` dependency.
- Every task's Mongo-facing change must ship with a test using the `FakeDatabase`/`FakeCollection` test double (Task 1), run against both the old and new code path where an equivalence claim is made.
- Do not add caching layers, feature flags, or config toggles — these are direct query/index fixes, not architecture changes requiring a rollout mechanism.

---

## Task 1: Shared Mongo test doubles

**Files:**
- Create: `tests/mongo_fakes.py`
- Test: `tests/test_mongo_fakes.py`

**Interfaces:**
- Produces: `FakeCollection` (methods: `find_one`, `find` returning a chainable `FakeCursor` supporting `.sort()`, `.skip()`, `.limit()`; `insert_one`, `insert_many`, `update_one`, `find_one_and_update`, `delete_one`, `delete_many`, `bulk_write`, `distinct`, `count_documents`, `create_index` (no-op), `aggregate` (supports `$match`, `$addFields` with `$convert`, `$sort`, `$skip`, `$limit` stages — the only stages this plan's aggregation pipeline uses)) and `FakeDatabase` (dict-like collection access, `__getitem__`).
- Consumed by: every later task's tests.

This extends the existing inline `FakeCollection`/`FakeDatabase` pattern in `tests/test_bulk_operations.py` into a shared, reusable module — later tasks import from here instead of redefining test doubles. `tests/test_bulk_operations.py` itself is left untouched (its inline classes keep working; not worth the churn of migrating a passing test file as a side effect of this plan).

- [ ] **Step 1: Write the failing test for basic find/update/delete parity with the existing inline fake**

```python
# tests/test_mongo_fakes.py
import flymanager.app  # noqa: F401  (see test_bulk_operations.py for why)

from tests.mongo_fakes import FakeDatabase


def test_find_one_and_update_returns_after_document():
    db = FakeDatabase({"stocks": [{"UniqueID": "s1", "User": "alice", "Status": "Healthy"}]})
    result = db["stocks"].find_one_and_update(
        {"UniqueID": "s1", "User": "alice"},
        {"$set": {"Status": "Sick"}},
        return_document="AFTER",
    )
    assert result["Status"] == "Sick"
    assert db["stocks"].find_one({"UniqueID": "s1"})["Status"] == "Sick"


def test_delete_many_removes_matching_and_returns_count():
    db = FakeDatabase({
        "stocks": [
            {"UniqueID": "s1", "User": "alice", "Status": "No longer maintained"},
            {"UniqueID": "s2", "User": "alice", "Status": "Healthy"},
        ]
    })
    result = db["stocks"].delete_many({"UniqueID": {"$in": ["s1", "s2"]}, "Status": "No longer maintained"})
    assert result.deleted_count == 1
    assert [s["UniqueID"] for s in db["stocks"].find({})] == ["s2"]


def test_find_supports_sort_skip_limit_chaining():
    db = FakeDatabase({
        "stocks": [
            {"UniqueID": "s1", "User": "alice", "Name": "b"},
            {"UniqueID": "s2", "User": "alice", "Name": "a"},
            {"UniqueID": "s3", "User": "alice", "Name": "c"},
        ]
    })
    cursor = db["stocks"].find({"User": "alice"}).sort("Name", 1).skip(1).limit(1)
    assert [doc["UniqueID"] for doc in cursor] == ["s1"]


def test_distinct_returns_unique_non_null_values():
    db = FakeDatabase({
        "stocks": [
            {"UniqueID": "s1", "User": "alice", "TrayID": "T1"},
            {"UniqueID": "s2", "User": "alice", "TrayID": "T2"},
            {"UniqueID": "s3", "User": "alice", "TrayID": "T1"},
            {"UniqueID": "s4", "User": "alice", "TrayID": ""},
        ]
    })
    assert sorted(db["stocks"].distinct("TrayID", {"User": "alice"})) == ["T1", "T2"]


def test_aggregate_match_addfields_convert_sort_skip_limit():
    db = FakeDatabase({
        "stocks": [
            {"UniqueID": "s1", "User": "alice", "TrayPosition": "10"},
            {"UniqueID": "s2", "User": "alice", "TrayPosition": "2"},
            {"UniqueID": "s3", "User": "alice", "TrayPosition": "not-a-number"},
        ]
    })
    pipeline = [
        {"$match": {"User": "alice"}},
        {"$addFields": {"_sortPos": {"$convert": {"input": "$TrayPosition", "to": "double", "onError": 0, "onNull": 0}}}},
        {"$sort": {"_sortPos": 1}},
        {"$skip": 0},
        {"$limit": 2},
    ]
    result = list(db["stocks"].aggregate(pipeline))
    assert [doc["UniqueID"] for doc in result] == ["s3", "s2"]
```

- [ ] **Step 2: Run the test to verify it fails with an import error**

Run: `python -m pytest tests/test_mongo_fakes.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'tests.mongo_fakes'`

- [ ] **Step 3: Implement `tests/mongo_fakes.py`**

```python
# tests/mongo_fakes.py
"""Shared in-memory Mongo test doubles.

Started as the inline FakeCollection/FakeDatabase in test_bulk_operations.py;
extracted here and extended (find_one_and_update, delete_many, distinct,
sort/skip/limit cursor chaining, a small aggregate() covering the stages this
codebase's aggregation pipelines use) so later tests don't redefine it.
"""
import copy
from types import SimpleNamespace


def _matches(record, query):
    for key, value in (query or {}).items():
        if isinstance(value, dict) and "$in" in value:
            if record.get(key) not in value["$in"]:
                return False
        elif record.get(key) != value:
            return False
    return True


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
        values = {
            record.get(field)
            for record in self._records
            if _matches(record, query or {})
        }
        return sorted(v for v in values if v not in (None, ""))

    def insert_one(self, document):
        self._records.append(dict(document))
        return SimpleNamespace(inserted_id=len(self._records))

    def insert_many(self, documents):
        documents = list(documents)
        for document in documents:
            self._records.append(dict(document))
        return SimpleNamespace(inserted_ids=list(range(len(documents))))

    def update_one(self, query, update):
        for record in self._records:
            if _matches(record, query):
                record.update(update.get("$set", {}))
                return SimpleNamespace(matched_count=1, modified_count=1)
        return SimpleNamespace(matched_count=0, modified_count=0)

    def find_one_and_update(self, query, update, return_document="AFTER"):
        for record in self._records:
            if _matches(record, query):
                if return_document != "AFTER":
                    before = dict(record)
                    record.update(update.get("$set", {}))
                    return before
                record.update(update.get("$set", {}))
                return dict(record)
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
                    record.update(update.get("$set", {}))
                    modified += 1
                    break
        return SimpleNamespace(modified_count=modified)

    def create_index(self, *args, **kwargs):
        return "-".join(str(a) for a in args)

    def aggregate(self, pipeline):
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
```

- [ ] **Step 4: Run the test to verify it passes**

Run: `python -m pytest tests/test_mongo_fakes.py -v`
Expected: PASS (5 tests)

- [ ] **Step 5: Commit**

```bash
git add tests/mongo_fakes.py tests/test_mongo_fakes.py
git commit -m "test: add shared in-memory Mongo test doubles"
```

---

## Task 2: Add missing indexes

**Files:**
- Modify: `flymanager/utils/mongo/db.py:51-58`
- Test: `tests/test_db_indexes.py`

**Interfaces:**
- Consumes: `FakeDatabase` (Task 1) — extended to record `create_index` calls for assertion (already supported: `FakeCollection.create_index` returns a joined-name string; the test asserts on call arguments via a spy, not the fake).
- Produces: no new public interface — `ensure_mongo_indexes(db)` keeps its existing signature; later tasks (5, 12) assume these indexes exist as *the reason their query is fast*, not as a hard dependency their code calls.

**Design decision (verified):** only add indexes for fields that are actually filtered/sorted on in the current codebase (confirmed via grep across `flymanager/`), matching the `$or`-of-owner-scope shape every accessible-document query uses. Not adding uniqueness constraints (e.g. `users.Username`, `trays.User+TrayID`) — enforcing uniqueness is a data-integrity change that needs a dedup check against production data first; that's out of scope for a performance pass and is called out explicitly rather than silently skipped.

- [ ] **Step 1: Write the failing test**

```python
# tests/test_db_indexes.py
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
    assert ("stocks", (("AssignedTo", 1), ("Status", 1), ("TrayID", 1))) in index_specs
    assert ("crosses", (("User", 1), ("Status", 1), ("TrayID", 1))) in index_specs
    assert ("crosses", (("AssignedTo", 1), ("Status", 1), ("TrayID", 1))) in index_specs
    assert ("users", (("Username", 1),)) in index_specs
    assert ("users", (("ReportsTo", 1),)) in index_specs
    assert ("activity", (("user", 1), ("timestamp", -1))) in index_specs
    assert ("trays", (("User", 1), ("TrayID", 1))) in index_specs
    assert ("password_reset_tokens", (("TokenHash", 1),)) in index_specs

    names = [name for _, _, name in db.calls]
    assert len(names) == len(set(names)), "index names must be unique"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_db_indexes.py -v`
Expected: FAIL — missing index specs not found in `index_specs`

- [ ] **Step 3: Add the indexes**

```python
# flymanager/utils/mongo/db.py — replace ensure_mongo_indexes (lines 51-58)
def ensure_mongo_indexes(db):
    """Create the indexes used by stock/cross access, tray, and auth query patterns."""
    db["stocks"].create_index([("User", 1), ("UniqueID", 1)], name="stocks_user_uid")
    db["stocks"].create_index([("AssignedTo", 1), ("UniqueID", 1)], name="stocks_assigned_uid")
    db["stocks"].create_index([("User", 1), ("Status", 1), ("TrayID", 1)], name="stocks_user_status_tray")
    db["stocks"].create_index([("AssignedTo", 1), ("Status", 1), ("TrayID", 1)], name="stocks_assigned_status_tray")

    db["crosses"].create_index([("User", 1), ("UniqueID", 1)], name="crosses_user_uid")
    db["crosses"].create_index([("AssignedTo", 1), ("UniqueID", 1)], name="crosses_assigned_uid")
    db["crosses"].create_index([("User", 1), ("Status", 1), ("TrayID", 1)], name="crosses_user_status_tray")
    db["crosses"].create_index([("AssignedTo", 1), ("Status", 1), ("TrayID", 1)], name="crosses_assigned_status_tray")

    db["users"].create_index([("Username", 1)], name="users_username")
    db["users"].create_index([("ReportsTo", 1)], name="users_reports_to")

    db["activity"].create_index([("user", 1), ("timestamp", -1)], name="activity_user_timestamp")

    db["trays"].create_index([("User", 1), ("TrayID", 1)], name="trays_user_trayid")

    db["password_reset_tokens"].create_index([("TokenHash", 1)], name="password_reset_tokens_hash")

    db["operation_locks"].create_index("key", unique=True, name="operation_locks_key")
    db["operation_locks"].create_index("expires_at", expireAfterSeconds=0, name="operation_locks_expires_at")
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest tests/test_db_indexes.py -v`
Expected: PASS

- [ ] **Step 5: Apply the new indexes to the running database**

The app calls `ensure_mongo_indexes` at startup (verify via `grep -rn "ensure_mongo_indexes" flymanager/app/__init__.py`), so a normal deploy/restart creates the new indexes — `create_index` is idempotent for indexes that don't already exist under that name, and MongoDB builds them in the background by default, so no maintenance window is required. No migration script needed.

- [ ] **Step 6: Commit**

```bash
git add flymanager/utils/mongo/db.py tests/test_db_indexes.py
git commit -m "perf: add indexes for TrayID/Status/Username/ReportsTo/activity/token query patterns"
```

---

## Task 3: `uid_exists` existence-only projection

**Files:**
- Modify: `flymanager/utils/mongo/db.py:96-117`
- Test: `tests/test_db_uid_exists.py`

**Interfaces:**
- Consumes: `FakeDatabase` (Task 1).
- Produces: `uid_exists(uid, db)` — same signature and return type (`bool`), only the query changes.

- [ ] **Step 1: Write the failing test**

```python
# tests/test_db_uid_exists.py
import flymanager.app  # noqa: F401

from tests.mongo_fakes import FakeDatabase
from flymanager.utils.mongo.db import uid_exists


def test_uid_exists_true_for_stock():
    db = FakeDatabase({"stocks": [{"UniqueID": "s1", "User": "alice"}]})
    assert uid_exists("s1", db) is True


def test_uid_exists_true_for_cross():
    db = FakeDatabase({"crosses": [{"UniqueID": "c1", "User": "alice"}]})
    assert uid_exists("c1", db) is True


def test_uid_exists_false_when_absent():
    db = FakeDatabase({"stocks": [{"UniqueID": "s1", "User": "alice"}]})
    assert uid_exists("nope", db) is False


def test_uid_exists_only_requests_id_field(monkeypatch):
    db = FakeDatabase({"stocks": [{"UniqueID": "s1", "User": "alice"}]})
    seen_projections = []
    original_find_one = db["stocks"].__class__.find_one

    def spy(self, query=None, projection=None):
        seen_projections.append(projection)
        return original_find_one(self, query, projection)

    monkeypatch.setattr(db["stocks"].__class__, "find_one", spy)
    uid_exists("s1", db)
    assert all(projection == {"_id": 1} for projection in seen_projections)
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_db_uid_exists.py -v`
Expected: FAIL on `test_uid_exists_only_requests_id_field` — current code passes no projection

- [ ] **Step 3: Add the projection**

```python
# flymanager/utils/mongo/db.py — replace body of uid_exists (lines 110-117)
    stocks_collection = db["stocks"]
    crosses_collection = db["crosses"]

    stock = stocks_collection.find_one({"UniqueID": uid}, {"_id": 1})
    cross = crosses_collection.find_one({"UniqueID": uid}, {"_id": 1})

    return stock is not None or cross is not None
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest tests/test_db_uid_exists.py -v`
Expected: PASS (4 tests)

- [ ] **Step 5: Commit**

```bash
git add flymanager/utils/mongo/db.py tests/test_db_uid_exists.py
git commit -m "perf: project only _id in uid_exists existence check"
```

---

## Task 4: `flip_owned_document` — collapse read-update-read into one round trip

**Files:**
- Modify: `flymanager/utils/mongo_records.py:389-417`
- Test: `tests/test_mongo_records_flip.py`

**Interfaces:**
- Consumes: `FakeDatabase` (Task 1) — requires `find_one_and_update`, already added.
- Produces: `flip_owned_document(collection_name, user, uid, db, timestamp, new_status=None, added_comment=None, accept_datetime=False)` — same signature and return value (the post-update document, or `None` if not found).

- [ ] **Step 1: Write the failing test**

```python
# tests/test_mongo_records_flip.py
import flymanager.app  # noqa: F401

from tests.mongo_fakes import FakeDatabase
from flymanager.utils.mongo_records import flip_owned_document


def test_flip_owned_document_returns_none_when_missing():
    db = FakeDatabase({"stocks": []})
    assert flip_owned_document("stocks", "alice", "missing", db, "2026-02-01 09:00") is None


def test_flip_owned_document_returns_updated_document_with_new_flip_log():
    db = FakeDatabase({
        "stocks": [{
            "UniqueID": "s1", "User": "alice",
            "FlipLog": "V1, 2026-01-01 10:00",
            "CurrentlyAliveVials": "V1",
            "Status": "Healthy",
        }]
    })
    result = flip_owned_document("stocks", "alice", "s1", db, "2026-02-01 09:00")

    assert result["FlipLog"] == "V2, 2026-02-01 09:00; V1, 2026-01-01 10:00"
    assert result["CurrentlyAliveVials"] == "V1, V2"
    assert result["LastFlipDate"] == "2026-02-01 09:00"
    stored = db["stocks"].find_one({"UniqueID": "s1"})
    assert stored["FlipLog"] == result["FlipLog"]


def test_flip_owned_document_applies_new_status_and_comment():
    db = FakeDatabase({
        "stocks": [{
            "UniqueID": "s1", "User": "alice",
            "FlipLog": "V1, 2026-01-01 10:00",
            "CurrentlyAliveVials": "V1",
            "Status": "Healthy",
            "Comments": "",
        }]
    })
    result = flip_owned_document(
        "stocks", "alice", "s1", db, "2026-02-01 09:00",
        new_status="Sick", added_comment="looks unwell",
    )
    assert result["Status"] == "Sick"
    assert result["Comments"] == "looks unwell"


def test_flip_owned_document_only_calls_find_once():
    db = FakeDatabase({
        "stocks": [{
            "UniqueID": "s1", "User": "alice",
            "FlipLog": "V1, 2026-01-01 10:00",
            "CurrentlyAliveVials": "V1",
            "Status": "Healthy",
        }]
    })
    collection = db["stocks"]
    call_counts = {"find_one": 0, "update_one": 0, "find_one_and_update": 0}
    original_find_one = collection.__class__.find_one
    original_update_one = collection.__class__.update_one
    original_find_one_and_update = collection.__class__.find_one_and_update

    def counting_find_one(self, *args, **kwargs):
        call_counts["find_one"] += 1
        return original_find_one(self, *args, **kwargs)

    def counting_update_one(self, *args, **kwargs):
        call_counts["update_one"] += 1
        return original_update_one(self, *args, **kwargs)

    def counting_find_one_and_update(self, *args, **kwargs):
        call_counts["find_one_and_update"] += 1
        return original_find_one_and_update(self, *args, **kwargs)

    collection.__class__.find_one = counting_find_one
    collection.__class__.update_one = counting_update_one
    collection.__class__.find_one_and_update = counting_find_one_and_update
    try:
        flip_owned_document("stocks", "alice", "s1", db, "2026-02-01 09:00")
    finally:
        collection.__class__.find_one = original_find_one
        collection.__class__.update_one = original_update_one
        collection.__class__.find_one_and_update = original_find_one_and_update

    assert call_counts["find_one"] == 1  # only the initial existence read
    assert call_counts["update_one"] == 0
    assert call_counts["find_one_and_update"] == 1
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_mongo_records_flip.py -v`
Expected: FAIL on `test_flip_owned_document_only_calls_find_once` — current implementation calls `find_one` twice and `update_one` once

- [ ] **Step 3: Implement**

```python
# flymanager/utils/mongo_records.py — replace flip_owned_document body (lines 399-417)
from pymongo import ReturnDocument  # add to imports at top of file


def flip_owned_document(
    collection_name,
    user,
    uid,
    db,
    timestamp,
    new_status=None,
    added_comment=None,
    accept_datetime=False,
):
    collection = db[collection_name]
    current_document = collection.find_one({"UniqueID": uid, "User": user})
    if not current_document:
        return None

    normalized_timestamp = _normalize_flip_timestamp(
        timestamp, accept_datetime=accept_datetime
    )
    update_fields = _build_flip_update_fields(
        current_document,
        normalized_timestamp,
        new_status=new_status,
        added_comment=added_comment,
    )

    return collection.find_one_and_update(
        {"UniqueID": uid, "User": user},
        {"$set": update_fields},
        return_document=ReturnDocument.AFTER,
    )
```

`FakeCollection.find_one_and_update` (Task 1) accepts `return_document` as either the string `"AFTER"` or the real `pymongo.ReturnDocument.AFTER` enum member — compare on `!= "AFTER"` only where the fake is used directly; against the real driver, `ReturnDocument.AFTER` is what's passed, matching production usage.

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest tests/test_mongo_records_flip.py -v`
Expected: PASS (4 tests)

- [ ] **Step 5: Run the existing bulk-operations equivalence tests to confirm no regression**

Run: `python -m pytest tests/test_bulk_operations.py -v`
Expected: PASS — `bulk_flip_records` doesn't call `flip_owned_document` (it has its own in-memory computation, per `bulk_operations.py`), so this change shouldn't affect it, but it's a cheap check since both paths must keep producing identical documents.

- [ ] **Step 6: Commit**

```bash
git add flymanager/utils/mongo_records.py tests/test_mongo_records_flip.py
git commit -m "perf: collapse flip_owned_document into a single find_one_and_update"
```

---

## Task 5: Query direct reports instead of scanning all users

**Files:**
- Modify: `flymanager/utils/mongo/access.py:159-198`
- Test: `tests/test_access_direct_reports.py`

**Interfaces:**
- Consumes: `FakeDatabase` (Task 1).
- Produces: `get_direct_reports(user, db)` — same signature, same return shape (`list[str]` of usernames), now backed by a direct query instead of `get_user_profiles(db)` + Python filter. `get_user_profiles` itself is untouched (still needed elsewhere for full-roster listing).

- [ ] **Step 1: Write the failing test**

```python
# tests/test_access_direct_reports.py
import flymanager.app  # noqa: F401

from tests.mongo_fakes import FakeDatabase
from flymanager.utils.mongo.access import get_direct_reports, can_assign_to_user


def _users_db():
    return FakeDatabase({
        "users": [
            {"Username": "alice", "ReportsTo": ""},
            {"Username": "bob", "ReportsTo": "alice"},
            {"Username": "carol", "ReportsTo": "alice"},
            {"Username": "dave", "ReportsTo": "bob"},
        ]
    })


def test_get_direct_reports_returns_only_direct_reports():
    db = _users_db()
    assert sorted(get_direct_reports("alice", db)) == ["bob", "carol"]
    assert get_direct_reports("bob", db) == ["dave"]
    assert get_direct_reports("carol", db) == []


def test_get_direct_reports_does_not_scan_full_collection():
    db = _users_db()
    collection = db["users"]
    original_find = collection.__class__.find
    seen_queries = []

    def spy(self, query=None, projection=None):
        seen_queries.append(query or {})
        return original_find(self, query, projection)

    collection.__class__.find = spy
    try:
        get_direct_reports("alice", db)
    finally:
        collection.__class__.find = original_find

    assert seen_queries, "get_direct_reports must query the users collection"
    assert all(query.get("ReportsTo") == "alice" for query in seen_queries), (
        "must filter by ReportsTo server-side, not fetch {} and filter in Python"
    ).format(seen_queries)


def test_can_assign_to_user_allows_direct_report():
    db = _users_db()
    assert can_assign_to_user("alice", "bob", db) is True


def test_can_assign_to_user_denies_non_report():
    db = _users_db()
    assert can_assign_to_user("alice", "dave", db) is False
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_access_direct_reports.py -v`
Expected: FAIL on `test_get_direct_reports_does_not_scan_full_collection` — current implementation calls `db["users"].find({})`

- [ ] **Step 3: Implement**

```python
# flymanager/utils/mongo/access.py — replace get_direct_reports (lines 159-164)
def get_direct_reports(user, db):
    normalized_user = _normalize_username(user)
    return sorted(
        _normalize_username(profile.get("Username"))
        for profile in db["users"].find({"ReportsTo": normalized_user}, {"Username": 1})
    )
```

`can_assign_to_user` ([access.py:193-198](flymanager/utils/mongo/access.py#L193)) is unchanged — it already calls `get_direct_reports` once per check, so it inherits the fix automatically.

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest tests/test_access_direct_reports.py -v`
Expected: PASS (4 tests)

- [ ] **Step 5: Commit**

```bash
git add flymanager/utils/mongo/access.py tests/test_access_direct_reports.py
git commit -m "perf: query users.ReportsTo directly instead of scanning the full collection"
```

---

## Task 6: Batch the tray-backfill lookup in `get_accessible_trays`

**Files:**
- Modify: `flymanager/utils/mongo/trays.py:129-156`
- Test: `tests/test_trays_accessible.py`

**Interfaces:**
- Consumes: `FakeDatabase` (Task 1).
- Produces: `get_accessible_trays(user, db)` — same signature and return shape (`list[dict]` of annotated trays, deduped and sorted the same way).

- [ ] **Step 1: Write the failing test**

```python
# tests/test_trays_accessible.py
import flymanager.app  # noqa: F401

from tests.mongo_fakes import FakeDatabase
from flymanager.utils.mongo.trays import get_accessible_trays


def _db_with_shared_tray():
    return FakeDatabase({
        "trays": [
            {"UniqueID": "tray-bob-T1", "User": "bob", "TrayID": "T1", "Rows": 10, "Columns": 10},
        ],
        "stocks": [
            {"UniqueID": "s1", "User": "bob", "AssignedTo": "alice", "TrayID": "T1"},
        ],
        "crosses": [],
    })


def test_get_accessible_trays_backfills_shared_owner_tray():
    db = _db_with_shared_tray()
    trays = get_accessible_trays("alice", db)
    assert [t["UniqueID"] for t in trays] == ["tray-bob-T1"]


def test_get_accessible_trays_issues_one_batched_backfill_query():
    db = _db_with_shared_tray()
    collection = db["trays"]
    original_find = collection.__class__.find
    original_find_one = collection.__class__.find_one
    find_one_calls = []

    def spy_find_one(self, *args, **kwargs):
        find_one_calls.append((args, kwargs))
        return original_find_one(self, *args, **kwargs)

    collection.__class__.find_one = spy_find_one
    try:
        get_accessible_trays("alice", db)
    finally:
        collection.__class__.find_one = original_find_one

    assert len(find_one_calls) == 0, (
        "backfill must use one batched find(), not a find_one() per accessible record"
    )
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_trays_accessible.py -v`
Expected: FAIL on `test_get_accessible_trays_issues_one_batched_backfill_query` — current code calls `find_one` once per accessible stock/cross

- [ ] **Step 3: Implement**

```python
# flymanager/utils/mongo/trays.py — replace get_accessible_trays (lines 129-156)
def get_accessible_trays(user, db):
    from flymanager.utils.mongo.access import (get_accessible_crosses,
                                               get_accessible_stocks)

    trays_collection = db["trays"]
    trays = list(trays_collection.find({"User": user}))

    accessible_items = list(get_accessible_stocks(user, db)) + list(
        get_accessible_crosses(user, db)
    )
    backfill_pairs = {
        (_normalize_text(item.get("User")), _normalize_text(item.get("TrayID")))
        for item in accessible_items
        if _normalize_text(item.get("User")) and _normalize_text(item.get("TrayID"))
    }
    if backfill_pairs:
        backfill_query = {
            "$or": [
                {"User": owner, "TrayID": tray_id} for owner, tray_id in backfill_pairs
            ]
        }
        trays.extend(trays_collection.find(backfill_query))

    deduped_trays = _dedupe_trays(trays)
    deduped_trays.sort(
        key=lambda tray: (
            _normalize_text(tray.get("User")) != user,
            _normalize_text(tray.get("User")),
            _normalize_text(tray.get("TrayID")),
        )
    )
    return [annotate_tray_access(tray, user) for tray in deduped_trays]
```

`FakeCollection.find` (Task 1) already supports a plain `{"$or": [...]}` query via `_matches`? — check: `_matches` only understands `$in` and equality, not `$or`. Extend `_matches` in `tests/mongo_fakes.py` to support `$or` before this step, since this is the first task that needs it.

- [ ] **Step 3b: Extend the shared fake to support `$or` (prerequisite discovered while implementing this task)**

```python
# tests/mongo_fakes.py — replace _matches
def _matches(record, query):
    if "$or" in query:
        return any(_matches(record, clause) for clause in query["$or"])
    for key, value in (query or {}).items():
        if key == "$or":
            continue
        if isinstance(value, dict) and "$in" in value:
            if record.get(key) not in value["$in"]:
                return False
        elif record.get(key) != value:
            return False
    return True
```

Add a regression test for this to `tests/test_mongo_fakes.py`:

```python
def test_find_supports_or_query():
    db = FakeDatabase({
        "trays": [
            {"UniqueID": "t1", "User": "bob", "TrayID": "T1"},
            {"UniqueID": "t2", "User": "carol", "TrayID": "T2"},
            {"UniqueID": "t3", "User": "dave", "TrayID": "T3"},
        ]
    })
    result = list(db["trays"].find({"$or": [
        {"User": "bob", "TrayID": "T1"},
        {"User": "dave", "TrayID": "T3"},
    ]}))
    assert sorted(t["UniqueID"] for t in result) == ["t1", "t3"]
```

- [ ] **Step 4: Run tests to verify everything passes**

Run: `python -m pytest tests/test_mongo_fakes.py tests/test_trays_accessible.py -v`
Expected: PASS (7 tests)

- [ ] **Step 5: Commit**

```bash
git add tests/mongo_fakes.py tests/test_mongo_fakes.py flymanager/utils/mongo/trays.py tests/test_trays_accessible.py
git commit -m "perf: batch the tray-backfill lookup in get_accessible_trays into one query"
```

---

## Task 7: Batch male/female stock resolution in `get_tray_occupancy`

**Files:**
- Modify: `flymanager/utils/mongo/trays.py:301-415`
- Test: `tests/test_tray_occupancy.py`

**Interfaces:**
- Consumes: `FakeDatabase` (Task 1).
- Produces: `get_tray_occupancy(user, tray_id, db)` — same signature and return shape (occupancy dict keyed by position string).

- [ ] **Step 1: Write the failing test**

```python
# tests/test_tray_occupancy.py
import flymanager.app  # noqa: F401

from tests.mongo_fakes import FakeDatabase
from flymanager.utils.mongo.trays import get_tray_occupancy


def _stock(uid, position, **overrides):
    stock = {
        "UniqueID": uid, "User": "alice", "TrayID": "T1", "TrayPosition": position,
        "Name": uid, "Genotype": "w[1118]", "Status": "Healthy",
        "VialLifetime": "28", "FlipFrequency": "7",
    }
    stock.update(overrides)
    return stock


def _cross(uid, position, male_id, female_id, **overrides):
    cross = {
        "UniqueID": uid, "User": "alice", "TrayID": "T1", "TrayPosition": position,
        "Name": uid, "MaleGenotype": "w[1118]", "FemaleGenotype": "w[1118]",
        "Status": "Healthy", "VialLifetime": "14", "FlipFrequency": "7",
        "MaleUniqueID": male_id, "FemaleUniqueID": female_id,
    }
    cross.update(overrides)
    return cross


def test_get_tray_occupancy_resolves_cross_parent_ids():
    db = FakeDatabase({
        "stocks": [_stock("m1", "5"), _stock("f1", "6")],
        "crosses": [_cross("x1", "1", "m1", "f1")],
    })
    occupancy = get_tray_occupancy("alice", "T1", db)
    assert occupancy["1"]["male_stock_id"] == "m1"
    assert occupancy["1"]["female_stock_id"] == "f1"


def test_get_tray_occupancy_batches_parent_stock_lookup():
    db = FakeDatabase({
        "stocks": [_stock("m1", "5"), _stock("f1", "6"), _stock("m2", "7"), _stock("f2", "8")],
        "crosses": [
            _cross("x1", "1", "m1", "f1"),
            _cross("x2", "2", "m2", "f2"),
        ],
    })
    collection = db["stocks"]
    original_find_one = collection.__class__.find_one
    find_one_calls = []

    def spy(self, *args, **kwargs):
        find_one_calls.append((args, kwargs))
        return original_find_one(self, *args, **kwargs)

    collection.__class__.find_one = spy
    try:
        get_tray_occupancy("alice", "T1", db)
    finally:
        collection.__class__.find_one = original_find_one

    assert len(find_one_calls) == 0, (
        "parent stock names must be resolved with one batched $in query, not "
        "one find_one() per male/female per cross"
    )
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_tray_occupancy.py -v`
Expected: FAIL on `test_get_tray_occupancy_batches_parent_stock_lookup` — current code calls `find_one` twice per cross

- [ ] **Step 3: Implement**

```python
# flymanager/utils/mongo/trays.py — replace get_tray_occupancy (lines 301-415)
def get_tray_occupancy(user, tray_id, db):
    stocks_collection = db["stocks"]
    crosses_collection = db["crosses"]

    stocks = list(stocks_collection.find({"User": user, "TrayID": tray_id}))
    crosses = list(crosses_collection.find({"User": user, "TrayID": tray_id}))

    parent_ids = {
        parent_id
        for cross in crosses
        for parent_id in (cross.get("MaleUniqueID"), cross.get("FemaleUniqueID"))
        if parent_id
    }
    parent_stock_map = {}
    if parent_ids:
        parent_stock_map = {
            stock["UniqueID"]: stock
            for stock in stocks_collection.find(
                {"User": user, "UniqueID": {"$in": list(parent_ids)}}
            )
        }

    occupancy = {}

    for stock in stocks:
        position = stock.get("TrayPosition", "")
        if position and stock["Status"] != "No longer maintained":
            required_vials = calculate_required_vials(stock)
            position_int = int(float(position))
            row = ((position_int - 1) % 10) + 1
            column = ((position_int - 1) // 10) + 1

            position_str = str(position_int)
            occupancy[position_str] = {
                "type": "stock",
                "id": stock["UniqueID"],
                "name": stock["Name"],
                "genotype": stock["Genotype"],
                "status": stock["Status"],
                "required_vials": required_vials,
                "display_name": f"{stock['Name']} ({stock['UniqueID']})",
            }

            for i in range(1, required_vials):
                next_column = column + i
                blocked_pos = str(((next_column - 1) * 10) + row)
                occupancy[blocked_pos] = {
                    "type": "blocked",
                    "blocked_by": position_str,
                    "blocked_by_type": "stock",
                    "blocked_by_name": f"{stock['Name']} ({stock['UniqueID']})",
                }

    for cross in crosses:
        position = cross.get("TrayPosition", "")
        if position and cross["Status"] != "No longer maintained":
            required_vials = calculate_required_vials(cross)
            position_int = int(float(position))
            row = ((position_int - 1) % 10) + 1
            column = ((position_int - 1) // 10) + 1
            position_str = str(position_int)

            male_stock = parent_stock_map.get(cross.get("MaleUniqueID"))
            female_stock = parent_stock_map.get(cross.get("FemaleUniqueID"))

            male_id = male_stock["UniqueID"] if male_stock else "Unknown"
            female_id = female_stock["UniqueID"] if female_stock else "Unknown"

            occupancy[position_str] = {
                "type": "cross",
                "id": cross["UniqueID"],
                "name": cross["Name"],
                "male_genotype": cross["MaleGenotype"],
                "female_genotype": cross["FemaleGenotype"],
                "status": cross["Status"],
                "required_vials": required_vials,
                "male_stock_id": male_id,
                "female_stock_id": female_id,
                "display_name": f"{cross['Name']} ({cross['UniqueID']}, ♂:{male_id}, ♀:{female_id})",
            }

            for i in range(1, required_vials):
                next_column = column + i
                blocked_pos = str(((next_column - 1) * 10) + row)
                occupancy[blocked_pos] = {
                    "type": "blocked",
                    "blocked_by": position_str,
                    "blocked_by_type": "cross",
                    "blocked_by_name": f"{cross['Name']} ({cross['UniqueID']})",
                }

    return occupancy
```

Note: preserve the exact `♂`/`♀` (♂/♀) characters from the original file — copy them verbatim rather than retyping, since they're easy to mistranscribe.

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest tests/test_tray_occupancy.py -v`
Expected: PASS (2 tests)

- [ ] **Step 5: Manually verify the blocked-position and display-name logic is byte-identical to before**

Run: `python -m pytest tests/ -k occupancy -v`
Expected: PASS — add one more equivalence test comparing multi-vial blocking output against a hand-computed expected occupancy dict, to catch any off-by-one introduced while re-typing the loop body:

```python
def test_get_tray_occupancy_blocks_positions_for_multi_vial_stock():
    db = FakeDatabase({
        "stocks": [_stock("s1", "1", VialLifetime="28", FlipFrequency="7")],  # requires 4 vials -> ceil(28/7)
        "crosses": [],
    })
    occupancy = get_tray_occupancy("alice", "T1", db)
    assert occupancy["1"]["type"] == "stock"
    assert occupancy["11"]["type"] == "blocked" and occupancy["11"]["blocked_by"] == "1"
    assert occupancy["21"]["type"] == "blocked"
    assert occupancy["31"]["type"] == "blocked"
    assert "41" not in occupancy
```

- [ ] **Step 6: Commit**

```bash
git add flymanager/utils/mongo/trays.py tests/test_tray_occupancy.py
git commit -m "perf: batch cross parent-stock lookups in get_tray_occupancy"
```

---

## Task 8: Bulk occupancy for `tray_management`

**Files:**
- Modify: `flymanager/utils/mongo/trays.py` (add new function after `get_tray_occupancy`)
- Modify: `flymanager/app/routes/tray.py:26-55`
- Test: `tests/test_tray_occupancy_bulk.py`

**Interfaces:**
- Consumes: `get_tray_occupancy`'s per-tray computation logic (Task 7), refactored into a shared pure helper `_build_occupancy(stocks, crosses, parent_stock_map)` so both the single-tray and bulk paths compute occupancy identically.
- Produces: `get_tray_occupancies_bulk(trays, db)` in `trays.py` — takes a list of tray dicts (each with `User`/`TrayID`), returns `dict[tray_UniqueID, occupancy_dict]`, computed with exactly 3 Mongo queries total regardless of tray count (one `stocks` `$or` query, one `crosses` `$or` query, one `stocks` `$in` query for cross parents).

- [ ] **Step 1: Extract the pure computation helper (refactor `get_tray_occupancy` from Task 7, no behavior change)**

```python
# flymanager/utils/mongo/trays.py — add above get_tray_occupancy, then have
# get_tray_occupancy call it
def _build_occupancy(stocks, crosses, parent_stock_map):
    occupancy = {}

    for stock in stocks:
        position = stock.get("TrayPosition", "")
        if position and stock["Status"] != "No longer maintained":
            required_vials = calculate_required_vials(stock)
            position_int = int(float(position))
            row = ((position_int - 1) % 10) + 1
            column = ((position_int - 1) // 10) + 1

            position_str = str(position_int)
            occupancy[position_str] = {
                "type": "stock",
                "id": stock["UniqueID"],
                "name": stock["Name"],
                "genotype": stock["Genotype"],
                "status": stock["Status"],
                "required_vials": required_vials,
                "display_name": f"{stock['Name']} ({stock['UniqueID']})",
            }

            for i in range(1, required_vials):
                next_column = column + i
                blocked_pos = str(((next_column - 1) * 10) + row)
                occupancy[blocked_pos] = {
                    "type": "blocked",
                    "blocked_by": position_str,
                    "blocked_by_type": "stock",
                    "blocked_by_name": f"{stock['Name']} ({stock['UniqueID']})",
                }

    for cross in crosses:
        position = cross.get("TrayPosition", "")
        if position and cross["Status"] != "No longer maintained":
            required_vials = calculate_required_vials(cross)
            position_int = int(float(position))
            row = ((position_int - 1) % 10) + 1
            column = ((position_int - 1) // 10) + 1
            position_str = str(position_int)

            male_stock = parent_stock_map.get(cross.get("MaleUniqueID"))
            female_stock = parent_stock_map.get(cross.get("FemaleUniqueID"))
            male_id = male_stock["UniqueID"] if male_stock else "Unknown"
            female_id = female_stock["UniqueID"] if female_stock else "Unknown"

            occupancy[position_str] = {
                "type": "cross",
                "id": cross["UniqueID"],
                "name": cross["Name"],
                "male_genotype": cross["MaleGenotype"],
                "female_genotype": cross["FemaleGenotype"],
                "status": cross["Status"],
                "required_vials": required_vials,
                "male_stock_id": male_id,
                "female_stock_id": female_id,
                "display_name": f"{cross['Name']} ({cross['UniqueID']}, ♂:{male_id}, ♀:{female_id})",
            }

            for i in range(1, required_vials):
                next_column = column + i
                blocked_pos = str(((next_column - 1) * 10) + row)
                occupancy[blocked_pos] = {
                    "type": "blocked",
                    "blocked_by": position_str,
                    "blocked_by_type": "cross",
                    "blocked_by_name": f"{cross['Name']} ({cross['UniqueID']})",
                }

    return occupancy


def get_tray_occupancy(user, tray_id, db):
    stocks_collection = db["stocks"]
    crosses_collection = db["crosses"]

    stocks = list(stocks_collection.find({"User": user, "TrayID": tray_id}))
    crosses = list(crosses_collection.find({"User": user, "TrayID": tray_id}))

    parent_ids = {
        parent_id
        for cross in crosses
        for parent_id in (cross.get("MaleUniqueID"), cross.get("FemaleUniqueID"))
        if parent_id
    }
    parent_stock_map = {}
    if parent_ids:
        parent_stock_map = {
            stock["UniqueID"]: stock
            for stock in stocks_collection.find(
                {"User": user, "UniqueID": {"$in": list(parent_ids)}}
            )
        }

    return _build_occupancy(stocks, crosses, parent_stock_map)


def get_tray_occupancies_bulk(trays, db):
    """Compute occupancy for many trays in 3 queries total instead of ~2 per tray."""
    owner_tray_pairs = {
        (_normalize_text(tray.get("User")), _normalize_text(tray.get("TrayID")))
        for tray in trays
        if _normalize_text(tray.get("User")) and _normalize_text(tray.get("TrayID"))
    }
    if not owner_tray_pairs:
        return {}

    or_clauses = [{"User": owner, "TrayID": tray_id} for owner, tray_id in owner_tray_pairs]
    all_stocks = list(db["stocks"].find({"$or": or_clauses}))
    all_crosses = list(db["crosses"].find({"$or": or_clauses}))

    parent_ids_by_owner = {}
    for cross in all_crosses:
        owner = _normalize_text(cross.get("User"))
        for parent_id in (cross.get("MaleUniqueID"), cross.get("FemaleUniqueID")):
            if parent_id:
                parent_ids_by_owner.setdefault(owner, set()).add(parent_id)

    parent_stock_map_by_owner = {}
    for owner, parent_ids in parent_ids_by_owner.items():
        parent_stock_map_by_owner[owner] = {
            stock["UniqueID"]: stock
            for stock in db["stocks"].find(
                {"User": owner, "UniqueID": {"$in": list(parent_ids)}}
            )
        }

    stocks_by_pair = {}
    for stock in all_stocks:
        key = (_normalize_text(stock.get("User")), _normalize_text(stock.get("TrayID")))
        stocks_by_pair.setdefault(key, []).append(stock)

    crosses_by_pair = {}
    for cross in all_crosses:
        key = (_normalize_text(cross.get("User")), _normalize_text(cross.get("TrayID")))
        crosses_by_pair.setdefault(key, []).append(cross)

    occupancies_by_pair = {
        pair: _build_occupancy(
            stocks_by_pair.get(pair, []),
            crosses_by_pair.get(pair, []),
            parent_stock_map_by_owner.get(pair[0], {}),
        )
        for pair in owner_tray_pairs
    }

    result = {}
    for tray in trays:
        pair = (_normalize_text(tray.get("User")), _normalize_text(tray.get("TrayID")))
        result[tray["UniqueID"]] = occupancies_by_pair.get(pair, {})
    return result
```

- [ ] **Step 2: Write the failing test for the bulk function and the query-count guarantee**

```python
# tests/test_tray_occupancy_bulk.py
import flymanager.app  # noqa: F401

from tests.mongo_fakes import FakeDatabase
from flymanager.utils.mongo.trays import get_tray_occupancy, get_tray_occupancies_bulk


def _stock(uid, owner, tray_id, position, **overrides):
    stock = {
        "UniqueID": uid, "User": owner, "TrayID": tray_id, "TrayPosition": position,
        "Name": uid, "Genotype": "w[1118]", "Status": "Healthy",
        "VialLifetime": "28", "FlipFrequency": "7",
    }
    stock.update(overrides)
    return stock


def test_get_tray_occupancies_bulk_matches_per_tray_results():
    db = FakeDatabase({
        "stocks": [
            _stock("s1", "alice", "T1", "1"),
            _stock("s2", "alice", "T2", "1"),
            _stock("s3", "bob", "T1", "1"),
        ],
        "crosses": [],
    })
    trays = [
        {"UniqueID": "tray-a-1", "User": "alice", "TrayID": "T1"},
        {"UniqueID": "tray-a-2", "User": "alice", "TrayID": "T2"},
        {"UniqueID": "tray-b-1", "User": "bob", "TrayID": "T1"},
    ]

    bulk_result = get_tray_occupancies_bulk(trays, db)

    for tray in trays:
        expected = get_tray_occupancy(tray["User"], tray["TrayID"], db)
        assert bulk_result[tray["UniqueID"]] == expected


def test_get_tray_occupancies_bulk_issues_exactly_three_queries():
    db = FakeDatabase({
        "stocks": [_stock(f"s{i}", "alice", f"T{i}", "1") for i in range(5)],
        "crosses": [],
    })
    trays = [{"UniqueID": f"tray-{i}", "User": "alice", "TrayID": f"T{i}"} for i in range(5)]

    query_count = {"stocks": 0, "crosses": 0}
    for name in query_count:
        collection = db[name]
        original_find = collection.__class__.find

        def make_spy(counter_key, original):
            def spy(self, *args, **kwargs):
                query_count[counter_key] += 1
                return original(self, *args, **kwargs)
            return spy

        collection.__class__.find = make_spy(name, original_find)

    get_tray_occupancies_bulk(trays, db)

    assert query_count["stocks"] == 1  # no crosses -> no parent lookup needed
    assert query_count["crosses"] == 1


def test_get_tray_occupancies_bulk_empty_input_returns_empty_dict():
    db = FakeDatabase({})
    assert get_tray_occupancies_bulk([], db) == {}
```

- [ ] **Step 3: Run test to verify it fails**

Run: `python -m pytest tests/test_tray_occupancy_bulk.py -v`
Expected: FAIL — `get_tray_occupancies_bulk` not defined yet (implement Step 1's code now if not already applied)

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest tests/test_tray_occupancy_bulk.py tests/test_tray_occupancy.py -v`
Expected: PASS (6 tests)

- [ ] **Step 5: Wire `tray_management` to the bulk function**

```python
# flymanager/app/routes/tray.py — replace tray_management (lines 26-55)
from flymanager.utils.mongo import (OperationLockConflict, add_tray,
                                    calculate_required_vials, delete_tray,
                                    get_accessible_crosses,
                                    get_accessible_stock,
                                    get_accessible_stocks, get_accessible_tray,
                                    get_accessible_trays, get_direct_reports,
                                    get_tray, get_tray_occupancy,
                                    get_tray_occupancies_bulk,
                                    hold_operation_locks, move_item_to_tray,
                                    record_operation_lock_keys,
                                    update_document_assignment, update_tray,
                                    write_activity)

# ...

@bp.route('/trays')
@login_required
def tray_management():
    """
    Render the tray management page.
    """
    user = session.get('username')

    trays = get_accessible_trays(user, db)
    direct_reports = get_direct_reports(user, db)
    occupancies = get_tray_occupancies_bulk(trays, db)

    for tray in trays:
        occupancy = occupancies.get(tray["UniqueID"], {})
        tray["OccupiedStarts"] = len(
            [item for item in occupancy.values() if item.get("type") != "blocked"]
        )
        tray["BlockedCells"] = len(
            [item for item in occupancy.values() if item.get("type") == "blocked"]
        )

    return render_template(
        'tray/tray_management.html',
        trays=trays,
        direct_reports=direct_reports,
        page_title="Tray Management",
        username=user
    )
```

`get_tray_occupancies_bulk` needs to be exported from `flymanager/utils/mongo/__init__.py`'s re-export list alongside `get_tray_occupancy` — check that file's existing `from .trays import (...)` line and add it there.

- [ ] **Step 6: Manually verify in the running app**

Use the `run-flymanager` skill to start the app, log in, and open `/trays` with at least 2 trays that have items — confirm occupied/blocked counts match what's shown when opening each tray individually via `/tray/<tray_id>`.

- [ ] **Step 7: Commit**

```bash
git add flymanager/utils/mongo/trays.py flymanager/utils/mongo/__init__.py flymanager/app/routes/tray.py tests/test_tray_occupancy_bulk.py
git commit -m "perf: compute tray_management occupancy for all trays in 3 queries instead of ~2 per tray"
```

---

## Task 9: Batch permanent-delete routes

**Files:**
- Modify: `flymanager/app/routes/stock.py:1921-1982`
- Modify: `flymanager/app/routes/cross.py:974-1036`
- Test: `tests/test_delete_permanently.py`

**Interfaces:**
- Consumes: `FakeDatabase` (Task 1).
- Produces: no new function signatures — both routes keep their existing request/response contract (`{"success", "deleted", "skipped", "message"}`), only the internal query pattern changes to `$in` fetch + `delete_many` + `insert_many`.

This task modifies Flask routes directly rather than an isolated `utils` function, so the test drives the route logic extracted into a small testable helper rather than going through Flask's request/session machinery (matching how `bulk_operations.py` keeps Mongo logic separate from route/session concerns).

- [ ] **Step 1: Extract the batched delete logic into a testable helper in `mongo_records.py`**

```python
# flymanager/utils/mongo_records.py — add near delete_owned_document
def delete_owned_documents_if_status(collection_name, user, uids, db, *, required_status):
    """Delete every uid in `uids` owned by `user` whose Status matches
    `required_status`, in one batched read + one batched delete.

    Returns (deleted_uids, skipped_uids) — skipped covers both "not found /
    not owned" and "wrong status", matching the per-item route's behaviour
    of treating both as a skip rather than an error.
    """
    if not uids:
        return [], []

    collection = db[collection_name]
    candidates = {
        document["UniqueID"]: document
        for document in collection.find({"UniqueID": {"$in": uids}, "User": user})
    }

    deletable_uids = [
        uid for uid, document in candidates.items()
        if document.get("Status") == required_status
    ]
    skipped_uids = [uid for uid in uids if uid not in deletable_uids]

    if deletable_uids:
        collection.delete_many({"UniqueID": {"$in": deletable_uids}, "User": user})

    return deletable_uids, skipped_uids
```

- [ ] **Step 2: Write the failing test**

```python
# tests/test_delete_permanently.py
import flymanager.app  # noqa: F401

from tests.mongo_fakes import FakeDatabase
from flymanager.utils.mongo_records import delete_owned_documents_if_status


def _stocks_db():
    return FakeDatabase({
        "stocks": [
            {"UniqueID": "s1", "User": "alice", "Status": "No longer maintained"},
            {"UniqueID": "s2", "User": "alice", "Status": "Healthy"},
            {"UniqueID": "s3", "User": "alice", "Status": "No longer maintained"},
            {"UniqueID": "s4", "User": "bob", "Status": "No longer maintained"},
        ]
    })


def test_deletes_only_matching_status_and_owner():
    db = _stocks_db()
    deleted, skipped = delete_owned_documents_if_status(
        "stocks", "alice", ["s1", "s2", "s3", "s4", "missing"], db,
        required_status="No longer maintained",
    )
    assert sorted(deleted) == ["s1", "s3"]
    assert sorted(skipped) == ["missing", "s2", "s4"]
    remaining = {doc["UniqueID"] for doc in db["stocks"].find({})}
    assert remaining == {"s2", "s4"}


def test_empty_uids_returns_empty_without_querying():
    db = _stocks_db()
    deleted, skipped = delete_owned_documents_if_status(
        "stocks", "alice", [], db, required_status="No longer maintained",
    )
    assert deleted == [] and skipped == []


def test_issues_one_find_and_one_delete_many_regardless_of_count():
    db = _stocks_db()
    collection = db["stocks"]
    counts = {"find": 0, "delete_many": 0, "find_one": 0}
    for method in counts:
        original = getattr(collection.__class__, method)

        def make_spy(name, orig):
            def spy(self, *args, **kwargs):
                counts[name] += 1
                return orig(self, *args, **kwargs)
            return spy

        setattr(collection.__class__, method, make_spy(method, original))

    delete_owned_documents_if_status(
        "stocks", "alice", ["s1", "s2", "s3"], db, required_status="No longer maintained",
    )

    assert counts["find_one"] == 0
    assert counts["find"] == 1
    assert counts["delete_many"] == 1
```

- [ ] **Step 3: Run test to verify it fails**

Run: `python -m pytest tests/test_delete_permanently.py -v`
Expected: FAIL — `delete_owned_documents_if_status` not defined

- [ ] **Step 4: Run test to verify it passes (after Step 1's implementation)**

Run: `python -m pytest tests/test_delete_permanently.py -v`
Expected: PASS (3 tests)

- [ ] **Step 5: Wire `delete_stock_permanently` to the batched helper**

```python
# flymanager/app/routes/stock.py — replace the loop in delete_stock_permanently (lines 1951-1972)
    deleted_uids, skipped_uids = delete_owned_documents_if_status(
        "stocks", username, unique_ids, db, required_status="No longer maintained",
    )
    if deleted_uids:
        activity_documents = [
            {
                "user": username,
                "timestamp": datetime.datetime.now().strftime("%Y-%m-%d %H:%M"),
                "activity": f"Permanently deleted stock {uid}",
            }
            for uid in deleted_uids
        ]
        db["activity"].insert_many(activity_documents)

    deleted_count = len(deleted_uids)
    skipped_count = len(skipped_uids)
```

Check the top of `stock.py` for an existing `import datetime` (used elsewhere in the file, e.g. timestamp formatting) and reuse it — do not add a second import if one exists. Also add `delete_owned_documents_if_status` to the `flymanager.utils.mongo_records` import line at the top of `stock.py`.

- [ ] **Step 6: Mirror the same change in `delete_cross_permanently`**

```python
# flymanager/app/routes/cross.py — replace the loop in delete_cross_permanently (lines ~1007-1027)
    deleted_uids, skipped_uids = delete_owned_documents_if_status(
        "crosses", username, unique_ids, db, required_status="No longer maintained",
    )
    if deleted_uids:
        activity_documents = [
            {
                "user": username,
                "timestamp": datetime.datetime.now().strftime("%Y-%m-%d %H:%M"),
                "activity": f"Permanently deleted cross {uid}",
            }
            for uid in deleted_uids
        ]
        db["activity"].insert_many(activity_documents)

    deleted_count = len(deleted_uids)
    skipped_count = len(skipped_uids)
```

Same import notes as Step 5 apply to `cross.py`.

- [ ] **Step 7: Run the full route test suite for stock/cross deletion, if one exists**

Run: `grep -rln "delete_stock_permanently\|delete_cross_permanently\|delete_permanently" tests/`
If matching route-level tests exist, run them: `python -m pytest <matched files> -v` and confirm PASS. If none exist, this step is a no-op — route-level HTTP tests are out of scope for this plan (Task 1-11 focus on the Mongo layer directly, matching the existing test suite's convention of testing `utils/mongo` functions rather than routes).

- [ ] **Step 8: Commit**

```bash
git add flymanager/utils/mongo_records.py flymanager/app/routes/stock.py flymanager/app/routes/cross.py tests/test_delete_permanently.py
git commit -m "perf: batch permanent-delete routes into one find + one delete_many"
```

---

## Task 10: Batch `assign_tray_route`

**Files:**
- Modify: `flymanager/utils/mongo/access.py:201-240` (extract pure field-computation helper)
- Modify: `flymanager/app/routes/tray.py:58-111`
- Test: `tests/test_access_assignment.py`

**Interfaces:**
- Consumes: `FakeDatabase` (Task 1), `pymongo.UpdateOne` (already used in `bulk_operations.py`, same import pattern).
- Produces: `build_document_assignment_update_fields(current_document, owner, assignee)` in `access.py` — pure function, no I/O, returns the `$set` dict (or `None` if no change needed) so both the single-item and bulk paths share the exact same field-computation logic. `update_document_assignment` (existing, single-item) is refactored to call it but keeps its exact signature and behavior.

- [ ] **Step 1: Extract the pure field-computation helper**

```python
# flymanager/utils/mongo/access.py — replace update_document_assignment (lines 201-240)
def build_document_assignment_update_fields(current_document, owner, assignee):
    """Compute the $set fields for reassigning one document, or None if the
    assignment is already what was requested (no write needed).
    """
    normalized_assignee = _normalize_username(assignee)
    stored_assignee = ""
    if normalized_assignee and normalized_assignee != owner:
        stored_assignee = normalized_assignee

    if _normalize_username(current_document.get("AssignedTo")) == stored_assignee:
        return None

    timestamp = current_timestamp()
    if stored_assignee:
        assignment_detail = f"assigned to {stored_assignee}"
    else:
        assignment_detail = "returned to owner maintenance"

    modification_entry = f"{timestamp} : Assignment updated to {assignment_detail}"
    modification_log = current_document.get("ModificationLog", "")

    return {
        "AssignedTo": stored_assignee,
        "AssignmentUpdatedAt": timestamp,
        "AssignmentUpdatedBy": owner,
        "DataModifiedDate": timestamp,
        "ModificationLog": (
            f"{modification_entry}; {modification_log}"
            if modification_log
            else modification_entry
        ),
    }


def update_document_assignment(collection_name, owner, uid, assignee, db):
    collection = db[collection_name]
    current_document = collection.find_one({"UniqueID": uid, "User": owner})
    if not current_document:
        return False, "Record not found."

    if not can_assign_to_user(owner, assignee, db):
        return False, "Assignee must be one of your direct reports."

    update_fields = build_document_assignment_update_fields(current_document, owner, assignee)
    if update_fields is None:
        return True, None

    collection.update_one({"UniqueID": uid, "User": owner}, {"$set": update_fields})
    return True, None
```

- [ ] **Step 2: Add the batched multi-collection function**

```python
# flymanager/utils/mongo/access.py — add after update_document_assignment
def bulk_update_document_assignments(owner, assignee, targets, db):
    """Reassign many (collection_name, uid) targets to the same assignee.

    Mirrors update_document_assignment's per-item semantics (ownership check,
    can_assign_to_user check, identical ModificationLog text) but issues one
    find() + one bulk_write() per collection instead of one find_one() +
    one update_one() per target.

    `targets`: iterable of (collection_name, uid) tuples, all reassigned to
    the same `assignee` by the same `owner` (this is what assign_tray_route
    needs — every stock/cross in one tray moves to the same assignee).

    Returns (updated_count, error_message). error_message is set (and no
    writes happen) if `assignee` isn't a valid direct report.
    """
    if not can_assign_to_user(owner, assignee, db):
        return 0, "Assignee must be one of your direct reports."

    uids_by_collection = {}
    for collection_name, uid in targets:
        uids_by_collection.setdefault(collection_name, []).append(uid)

    updated_count = 0
    for collection_name, uids in uids_by_collection.items():
        collection = db[collection_name]
        documents = {
            document["UniqueID"]: document
            for document in collection.find({"UniqueID": {"$in": uids}, "User": owner})
        }
        operations = []
        for uid in uids:
            document = documents.get(uid)
            if not document:
                continue
            update_fields = build_document_assignment_update_fields(document, owner, assignee)
            updated_count += 1
            if update_fields is None:
                continue
            operations.append(
                UpdateOne({"UniqueID": uid, "User": owner}, {"$set": update_fields})
            )
        if operations:
            collection.bulk_write(operations, ordered=False)

    return updated_count, None
```

Add `from pymongo import UpdateOne` to the imports at the top of `access.py`.

- [ ] **Step 3: Write the failing test**

```python
# tests/test_access_assignment.py
import flymanager.app  # noqa: F401

from tests.mongo_fakes import FakeDatabase
from flymanager.utils.mongo.access import (
    update_document_assignment, bulk_update_document_assignments,
)


def _db_with_reports():
    return FakeDatabase({
        "users": [
            {"Username": "alice", "ReportsTo": ""},
            {"Username": "bob", "ReportsTo": "alice"},
        ],
        "stocks": [
            {"UniqueID": "s1", "User": "alice", "AssignedTo": "", "TrayID": "T1", "ModificationLog": ""},
            {"UniqueID": "s2", "User": "alice", "AssignedTo": "", "TrayID": "T1", "ModificationLog": ""},
        ],
        "crosses": [
            {"UniqueID": "c1", "User": "alice", "AssignedTo": "", "TrayID": "T1", "ModificationLog": ""},
        ],
    })


def test_bulk_matches_per_item_path_for_identical_inputs():
    single_db = _db_with_reports()
    bulk_db = _db_with_reports()
    targets = [("stocks", "s1"), ("stocks", "s2"), ("crosses", "c1")]

    for collection_name, uid in targets:
        update_document_assignment(collection_name, "alice", uid, "bob", single_db)

    bulk_update_document_assignments("alice", "bob", targets, bulk_db)

    for collection_name, uid in targets:
        single_doc = single_db[collection_name].find_one({"UniqueID": uid})
        bulk_doc = bulk_db[collection_name].find_one({"UniqueID": uid})
        assert single_doc["AssignedTo"] == bulk_doc["AssignedTo"] == "bob"
        assert single_doc["ModificationLog"].split(" : ", 1)[1] == bulk_doc["ModificationLog"].split(" : ", 1)[1]


def test_bulk_rejects_non_direct_report_without_writing():
    db = _db_with_reports()
    updated_count, error = bulk_update_document_assignments(
        "alice", "carol", [("stocks", "s1")], db,
    )
    assert updated_count == 0
    assert error == "Assignee must be one of your direct reports."
    assert db["stocks"].find_one({"UniqueID": "s1"})["AssignedTo"] == ""


def test_bulk_issues_one_find_and_one_bulk_write_per_collection():
    db = _db_with_reports()
    counts = {"stocks_find": 0, "stocks_bulk_write": 0, "crosses_find": 0, "crosses_bulk_write": 0}

    for name in ("stocks", "crosses"):
        collection = db[name]
        original_find = collection.__class__.find
        original_bulk_write = collection.__class__.bulk_write

        def make_find_spy(key, original):
            def spy(self, *args, **kwargs):
                counts[key] += 1
                return original(self, *args, **kwargs)
            return spy

        def make_write_spy(key, original):
            def spy(self, *args, **kwargs):
                counts[key] += 1
                return original(self, *args, **kwargs)
            return spy

        collection.__class__.find = make_find_spy(f"{name}_find", original_find)
        collection.__class__.bulk_write = make_write_spy(f"{name}_bulk_write", original_bulk_write)

    bulk_update_document_assignments(
        "alice", "bob", [("stocks", "s1"), ("stocks", "s2"), ("crosses", "c1")], db,
    )

    assert counts["stocks_find"] == 1
    assert counts["stocks_bulk_write"] == 1
    assert counts["crosses_find"] == 1
    assert counts["crosses_bulk_write"] == 1
```

- [ ] **Step 4: Run test to verify it fails, then passes after Steps 1-2's implementation**

Run: `python -m pytest tests/test_access_assignment.py -v`
Expected: first FAIL (`bulk_update_document_assignments` undefined), then PASS (3 tests) once Steps 1-2 are applied

- [ ] **Step 5: Wire `assign_tray_route` to the batched function**

```python
# flymanager/app/routes/tray.py — replace lines 74-97 in assign_tray_route
    assignment_targets = []
    for collection_name in ('stocks', 'crosses'):
        for document in db[collection_name].find({'User': user, 'TrayID': tray['TrayID']}):
            if document.get('Status') == 'No longer maintained':
                continue
            assignment_targets.append((collection_name, document['UniqueID']))

    if not assignment_targets:
        flash(f"Tray {tray['TrayID']} has no active stocks or crosses to assign.", 'error')
        return redirect(url_for('tray.tray_management'))

    updated_count, error_message = bulk_update_document_assignments(
        user, assignee, assignment_targets, db,
    )
    if error_message:
        flash(error_message, 'error')
        return redirect(url_for('tray.tray_management'))
```

Add `bulk_update_document_assignments` to the `flymanager.utils.mongo` import block at the top of `tray.py`, and to `flymanager/utils/mongo/__init__.py`'s re-export of `access.py` symbols.

- [ ] **Step 6: Manually verify in the running app**

Use the `run-flymanager` skill: create a tray with several active stocks/crosses assigned to a direct report, confirm the flash message and per-item `AssignedTo`/`ModificationLog` match what the old per-item path produced (spot-check one record's `ModificationLog` text).

- [ ] **Step 7: Commit**

```bash
git add flymanager/utils/mongo/access.py flymanager/utils/mongo/__init__.py flymanager/app/routes/tray.py tests/test_access_assignment.py
git commit -m "perf: batch assign_tray_route into one find + one bulk_write per collection"
```

---

## Task 11: Batch phenotype-cache backfill writes

**Files:**
- Modify: `flymanager/utils/phenotypes/backfill.py:42-68`
- Test: `tests/test_phenotype_backfill_batching.py`

**Interfaces:**
- Consumes: `FakeDatabase` (Task 1), `pymongo.UpdateOne`.
- Produces: `_backfill_collection(...)` — same signature and return shape (`{"scanned", "updated", "skipped_valid"}`), writes now flushed via `bulk_write` in chunks of 500 instead of one `update_one` per document.

- [ ] **Step 1: Write the failing test**

```python
# tests/test_phenotype_backfill_batching.py
import flymanager.app  # noqa: F401

from tests.mongo_fakes import FakeDatabase
from flymanager.utils.phenotypes.backfill import backfill_stock_phenotype_cache


def _stocks_db(count):
    return FakeDatabase({
        "stocks": [
            {"_id": i, "User": "alice", "Genotype": f"w[{i}]", "PhenotypeCache": None}
            for i in range(count)
        ]
    })


def test_backfill_updates_all_records_missing_cache():
    db = _stocks_db(3)
    summary = backfill_stock_phenotype_cache(db["stocks"])
    assert summary["updated"] == 3
    for doc in db["stocks"].find({}):
        assert doc["PhenotypeCache"] is not None


def test_backfill_issues_bulk_write_not_one_update_per_document():
    db = _stocks_db(10)
    collection = db["stocks"]
    counts = {"update_one": 0, "bulk_write": 0}
    original_update_one = collection.__class__.update_one
    original_bulk_write = collection.__class__.bulk_write

    def spy_update_one(self, *args, **kwargs):
        counts["update_one"] += 1
        return original_update_one(self, *args, **kwargs)

    def spy_bulk_write(self, *args, **kwargs):
        counts["bulk_write"] += 1
        return original_bulk_write(self, *args, **kwargs)

    collection.__class__.update_one = spy_update_one
    collection.__class__.bulk_write = spy_bulk_write
    try:
        backfill_stock_phenotype_cache(db["stocks"])
    finally:
        collection.__class__.update_one = original_update_one
        collection.__class__.bulk_write = original_bulk_write

    assert counts["update_one"] == 0
    assert counts["bulk_write"] == 1  # 10 records fits in one 500-doc chunk


def test_backfill_flushes_in_chunks_of_500(monkeypatch):
    db = _stocks_db(1200)
    collection = db["stocks"]
    counts = {"bulk_write": 0}
    original_bulk_write = collection.__class__.bulk_write

    def spy_bulk_write(self, *args, **kwargs):
        counts["bulk_write"] += 1
        return original_bulk_write(self, *args, **kwargs)

    collection.__class__.bulk_write = spy_bulk_write
    try:
        backfill_stock_phenotype_cache(db["stocks"])
    finally:
        collection.__class__.bulk_write = original_bulk_write

    assert counts["bulk_write"] == 3  # ceil(1200 / 500)
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_phenotype_backfill_batching.py -v`
Expected: FAIL — current code calls `update_one` once per record, never `bulk_write`

- [ ] **Step 3: Implement chunked `bulk_write`**

```python
# flymanager/utils/phenotypes/backfill.py — replace imports and _backfill_collection
from pymongo import UpdateOne

from flymanager.utils.phenotypes.predictor import (build_cross_phenotype_cache,
                                                   build_stock_phenotype_cache,
                                                   get_cached_cross_phenotype,
                                                   get_cached_stock_phenotype)

_BULK_WRITE_CHUNK_SIZE = 500


def _normalize_users(users):
    if not users:
        return []

    normalized_users = []
    for user in users:
        normalized_user = str(user or "").strip()
        if normalized_user:
            normalized_users.append(normalized_user)
    return normalized_users


def _build_user_query(users):
    normalized_users = _normalize_users(users)
    if not normalized_users:
        return {}
    return {
        "$or": [
            {"User": {"$in": normalized_users}},
            {"AssignedTo": {"$in": normalized_users}},
        ]
    }


def _record_matches_users(record, users):
    normalized_users = set(_normalize_users(users))
    if not normalized_users:
        return True

    owner = str(record.get("User") or "").strip()
    assigned_to = str(record.get("AssignedTo") or "").strip()
    maintainer = assigned_to or owner
    return maintainer in normalized_users


def _backfill_collection(collection, *, query, projection, users=None, cache_getter, cache_builder,
                         cache_selector_builder, dry_run=False, force=False):
    summary = {
        "scanned": 0,
        "updated": 0,
        "skipped_valid": 0,
    }

    pending_operations = []

    def flush():
        if pending_operations:
            collection.bulk_write(list(pending_operations), ordered=False)
            pending_operations.clear()

    for record in collection.find(query, projection):
        if not _record_matches_users(record, users):
            continue
        summary["scanned"] += 1

        if not force and cache_getter(record):
            summary["skipped_valid"] += 1
            continue

        summary["updated"] += 1
        if dry_run:
            continue

        pending_operations.append(
            UpdateOne(
                cache_selector_builder(record),
                {"$set": {"PhenotypeCache": cache_builder(record)}},
            )
        )
        if len(pending_operations) >= _BULK_WRITE_CHUNK_SIZE:
            flush()

    flush()
    return summary
```

`backfill_stock_phenotype_cache`/`backfill_cross_phenotype_cache` below it are unchanged — they only call `_backfill_collection`.

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest tests/test_phenotype_backfill_batching.py -v`
Expected: PASS (3 tests)

- [ ] **Step 5: Run existing phenotype backfill tests, if any**

Run: `grep -rln "backfill_stock_phenotype_cache\|backfill_cross_phenotype_cache" tests/`
Run any matched test files with `python -m pytest <files> -v` to confirm no regression in the dry-run/force/user-scoping behavior.

- [ ] **Step 6: Commit**

```bash
git add flymanager/utils/phenotypes/backfill.py tests/test_phenotype_backfill_batching.py
git commit -m "perf: batch phenotype-cache backfill writes via chunked bulk_write"
```

---

## Task 12: Push explorer filtering, projection, and pagination into MongoDB

**Files:**
- Modify: `flymanager/utils/mongo/access.py` (add new query-building function)
- Modify: `flymanager/app/routes/stock.py:567-780` (`stock_explorer`, `stock_explorer_selection`, `_apply_stock_filters`)
- Modify: `flymanager/app/routes/cross.py:244-427` (`cross_explorer`, `cross_explorer_selection`, `_apply_cross_filters`)
- Test: `tests/test_explorer_query.py`

**Interfaces:**
- Consumes: indexes from Task 2 (`(User/AssignedTo, Status, TrayID)`), `FakeDatabase`'s `aggregate` support (Task 1).
- Produces: `get_accessible_documents_page(collection_name, user, db, *, mongo_filter, sort_field, skip, limit, projection)` in `access.py` — returns `(items, total_count)`. `mongo_filter` is a plain Mongo query dict for the *deterministic* filters only (never the fuzzy search term); the function ANDs it with the existing `{"$or": [{"User": user}, {"AssignedTo": user}]}` owner scope, sorts by `TrayID` then a numeric-converted `TrayPosition` (matching `_stock_sort_key`'s tie-break order) via aggregation, and applies `skip`/`limit`.

**Design decision (verified against `_apply_stock_filters`/`_apply_cross_filters`, [stock.py:700](flymanager/app/routes/stock.py#L700), [cross.py:355](flymanager/app/routes/cross.py#L355)):** `searchQuery` uses `fuzz.partial_ratio(...) > 80` — fuzzy matching that cannot be expressed as a Mongo query without changing which records match. So:
- When `searchQuery` is **absent**: deterministic filters + projection + sort + skip/limit all run in MongoDB. This is the common case (browsing/filtering without free-text search) and gets the full fix — no full-collection fetch, no Python sort over the whole accessible set, real database-level pagination.
- When `searchQuery` is **present**: deterministic filters + projection still run in MongoDB (shrinking what's fetched from "every accessible document, all fields" to "documents matching Type/TrayID/Status/etc., only the fields the explorer needs"), then the fuzzy filter, sort, and pagination happen in Python exactly as today — same result set, same ranking, just over a pre-filtered/projected candidate list instead of the entire accessible collection.
- `unique_values` (the filter dropdowns) are computed via `.distinct()` scoped to the owner query, independent of the current filter/search state — accurate and index-backed, instead of requiring the full fetch that populated them today.

- [ ] **Step 1: Add the projection field list needed by the explorer template (verify against the template before hardcoding)**

Run: `grep -oE "stock\.\w+|stock\[.\w+.\]" flymanager/app/templates/stock/stock_explorer.html | sort -u`
Run: `grep -oE "cross\.\w+|cross\[.\w+.\]" flymanager/app/templates/cross/cross_explorer.html | sort -u`

Record the field lists these produce — they become `STOCK_EXPLORER_PROJECTION`/`CROSS_EXPLORER_PROJECTION` in Step 2. This step exists specifically so the projection isn't guessed: dropping a field the template actually reads would silently blank a column, so the field list must come from the template, not from memory.

- [ ] **Step 2: Add `get_accessible_documents_page` to `access.py`**

```python
# flymanager/utils/mongo/access.py — add near get_accessible_documents
def get_accessible_documents_page(
    collection_name, user, db, *, mongo_filter=None, skip=0, limit=None, projection=None,
):
    """Query, sort, and paginate accessible documents at the database level.

    `mongo_filter` covers only deterministic (non-fuzzy) filters - callers
    that also need fuzzy free-text search should pass `limit=None` to get
    every matching document (still filtered + projected server-side) and
    apply the fuzzy filter + pagination themselves afterward.

    Sorts by TrayID, then TrayPosition (parsed numerically, matching
    _stock_sort_key's tie-break order), then Name, then UniqueID.

    Returns (items, total_count).
    """
    owner_scope = {"$or": [{"User": user}, {"AssignedTo": user}]}
    combined_filter = {"$and": [owner_scope, mongo_filter or {}]}

    total_count = db[collection_name].count_documents(combined_filter)

    pipeline = [
        {"$match": combined_filter},
        {"$addFields": {
            "_sortTrayPosition": {
                "$convert": {"input": "$TrayPosition", "to": "double", "onError": 0, "onNull": 0}
            },
        }},
        {"$sort": {"TrayID": 1, "_sortTrayPosition": 1, "Name": 1, "UniqueID": 1}},
    ]
    if skip:
        pipeline.append({"$skip": skip})
    if limit is not None:
        pipeline.append({"$limit": limit})

    items = list(db[collection_name].aggregate(pipeline))
    if projection:
        items = [
            {field: document.get(field) for field in projection if field in document}
            for document in items
        ]
    else:
        for document in items:
            document.pop("_sortTrayPosition", None)

    return [annotate_document_access(document, user) for document in items], total_count
```

The `_sortTrayPosition` field must be dropped before annotation when no projection is given (the `else` branch above) so it doesn't leak into the response the same way `PhenotypeCache`/`ModificationLog` etc. do today — when a projection *is* given, `_sortTrayPosition` is simply excluded by not being in the requested field list.

- [ ] **Step 3: Write the failing test for `get_accessible_documents_page`**

```python
# tests/test_explorer_query.py
import flymanager.app  # noqa: F401

from tests.mongo_fakes import FakeDatabase
from flymanager.utils.mongo.access import get_accessible_documents_page


def _stock(uid, **overrides):
    stock = {
        "UniqueID": uid, "User": "alice", "AssignedTo": "", "Status": "Healthy",
        "Type": "WT", "TrayID": "", "TrayPosition": "", "Name": uid,
    }
    stock.update(overrides)
    return stock


def test_filters_scoped_to_owner_and_deterministic_filter():
    db = FakeDatabase({
        "stocks": [
            _stock("s1", Status="Healthy"),
            _stock("s2", Status="Sick"),
            _stock("s3", User="bob", Status="Healthy"),
            _stock("s4", User="bob", AssignedTo="alice", Status="Healthy"),
        ]
    })
    items, total = get_accessible_documents_page(
        "stocks", "alice", db, mongo_filter={"Status": "Healthy"},
    )
    assert total == 2
    assert sorted(item["UniqueID"] for item in items) == ["s1", "s4"]


def test_sorts_by_trayid_then_numeric_trayposition():
    db = FakeDatabase({
        "stocks": [
            _stock("s1", TrayID="T1", TrayPosition="10"),
            _stock("s2", TrayID="T1", TrayPosition="2"),
            _stock("s3", TrayID="T1", TrayPosition="1"),
        ]
    })
    items, _ = get_accessible_documents_page("stocks", "alice", db)
    assert [item["UniqueID"] for item in items] == ["s3", "s2", "s1"]


def test_skip_and_limit_paginate_at_the_database_level():
    db = FakeDatabase({
        "stocks": [_stock(f"s{i}", TrayID="T1", TrayPosition=str(i)) for i in range(5)]
    })
    items, total = get_accessible_documents_page("stocks", "alice", db, skip=2, limit=2)
    assert total == 5
    assert [item["UniqueID"] for item in items] == ["s2", "s3"]


def test_projection_limits_returned_fields():
    db = FakeDatabase({"stocks": [_stock("s1", Genotype="w[1118]", Comments="note")]})
    items, _ = get_accessible_documents_page(
        "stocks", "alice", db, projection={"UniqueID", "Name"},
    )
    assert set(items[0].keys()) == {"UniqueID", "Name"}
```

- [ ] **Step 4: Run test to verify it fails**

Run: `python -m pytest tests/test_explorer_query.py -v`
Expected: FAIL — `get_accessible_documents_page` not defined, and `FakeCollection.aggregate` may not yet support `count_documents`-style combined `$and`/`$or` matching depending on Task 1/6's `_matches` extension

- [ ] **Step 5: Run test to verify it passes**

Run: `python -m pytest tests/test_explorer_query.py -v`
Expected: PASS (4 tests). If `_matches` needs `$and` support (it doesn't yet — only `$or` was added in Task 6), extend `tests/mongo_fakes.py`:

```python
# tests/mongo_fakes.py — _matches, add before the $or check
def _matches(record, query):
    if "$and" in query:
        return all(_matches(record, clause) for clause in query["$and"])
    if "$or" in query:
        return any(_matches(record, clause) for clause in query["$or"])
    for key, value in (query or {}).items():
        if key in ("$or", "$and"):
            continue
        if isinstance(value, dict) and "$in" in value:
            if record.get(key) not in value["$in"]:
                return False
        elif record.get(key) != value:
            return False
    return True
```

Add a matching test to `tests/test_mongo_fakes.py`:

```python
def test_find_supports_and_query():
    db = FakeDatabase({
        "stocks": [
            {"UniqueID": "s1", "User": "alice", "Status": "Healthy"},
            {"UniqueID": "s2", "User": "alice", "Status": "Sick"},
            {"UniqueID": "s3", "User": "bob", "Status": "Healthy"},
        ]
    })
    result = list(db["stocks"].find({"$and": [{"User": "alice"}, {"Status": "Healthy"}]}))
    assert [doc["UniqueID"] for doc in result] == ["s1"]
```

Re-run: `python -m pytest tests/test_mongo_fakes.py tests/test_explorer_query.py -v` — expect all PASS.

- [ ] **Step 6: Build the deterministic-filter translator for stocks**

```python
# flymanager/app/routes/stock.py — add near _apply_stock_filters
def _build_stock_mongo_filter(filters):
    """Translate the explorer's deterministic filter fields into a Mongo
    query. Excludes searchQuery - that stays a Python fuzzy post-filter
    (see Task 12 rationale: fuzz.partial_ratio can't be expressed as a
    Mongo query without changing which records match).
    """
    no_longer_maintained_status = "No longer maintained"
    clauses = []

    filter_type = filters.get("filterType")
    if filter_type:
        clauses.append({"Type": filter_type})

    filter_tray_id = filters.get("filterTrayID")
    if filter_tray_id:
        clauses.append({"TrayID": filter_tray_id})

    filter_status = filters.get("filterStatus")
    if filter_status == no_longer_maintained_status:
        clauses.append({"Status": no_longer_maintained_status})
    elif filter_status:
        clauses.append({"Status": filter_status})
    else:
        clauses.append({"Status": {"$ne": no_longer_maintained_status}})

    filter_food_type = filters.get("filterFoodType")
    if filter_food_type:
        clauses.append({"FoodType": filter_food_type})

    filter_provenance = filters.get("filterProvenance")
    if filter_provenance:
        # Provenance is stored as "Source/detail"; the filter matches on the
        # prefix before the first slash, same as the original Python filter.
        import re
        clauses.append({"Provenance": {"$regex": f"^{re.escape(filter_provenance)}(/|$)"}})

    filter_species = filters.get("filterSpecies")
    if filter_species:
        clauses.append({"Species": filter_species})

    if not clauses:
        return {}
    if len(clauses) == 1:
        return clauses[0]
    return {"$and": clauses}
```

Add tests to `tests/test_explorer_query.py`:

```python
from flymanager.app.routes.stock import _build_stock_mongo_filter


def test_build_stock_mongo_filter_excludes_no_longer_maintained_by_default():
    assert _build_stock_mongo_filter({}) == {"Status": {"$ne": "No longer maintained"}}


def test_build_stock_mongo_filter_combines_multiple_fields():
    result = _build_stock_mongo_filter({"filterType": "WT", "filterTrayID": "T1"})
    assert result == {"$and": [{"Type": "WT"}, {"TrayID": "T1"}]}


def test_build_stock_mongo_filter_provenance_matches_prefix_before_slash():
    result = _build_stock_mongo_filter({"filterProvenance": "Bloomington"})
    assert result == {"Provenance": {"$regex": "^Bloomington(/|$)"}}
```

Run: `python -m pytest tests/test_explorer_query.py -v` — expect PASS once `_build_stock_mongo_filter` is added.

- [ ] **Step 7: Mirror the filter translator for crosses**

```python
# flymanager/app/routes/cross.py — add near _apply_cross_filters
def _build_cross_mongo_filter(filters):
    no_longer_maintained_status = "No longer maintained"
    clauses = []

    filter_male_species = filters.get("filterMaleSpecies")
    if filter_male_species:
        clauses.append({"MaleSpecies": filter_male_species})

    filter_female_species = filters.get("filterFemaleSpecies")
    if filter_female_species:
        clauses.append({"FemaleSpecies": filter_female_species})

    filter_tray_id = filters.get("filterTrayID")
    if filter_tray_id:
        clauses.append({"TrayID": filter_tray_id})

    filter_status = filters.get("filterStatus")
    if filter_status == no_longer_maintained_status:
        clauses.append({"Status": no_longer_maintained_status})
    elif filter_status:
        clauses.append({"Status": filter_status})
    else:
        clauses.append({"Status": {"$ne": no_longer_maintained_status}})

    filter_food_type = filters.get("filterFoodType")
    if filter_food_type:
        clauses.append({"FoodType": filter_food_type})

    if not clauses:
        return {}
    if len(clauses) == 1:
        return clauses[0]
    return {"$and": clauses}
```

Add the mirrored tests to `tests/test_explorer_query.py`:

```python
from flymanager.app.routes.cross import _build_cross_mongo_filter


def test_build_cross_mongo_filter_excludes_no_longer_maintained_by_default():
    assert _build_cross_mongo_filter({}) == {"Status": {"$ne": "No longer maintained"}}


def test_build_cross_mongo_filter_combines_multiple_fields():
    result = _build_cross_mongo_filter({"filterMaleSpecies": "D. melanogaster", "filterTrayID": "T1"})
    assert result == {"$and": [{"MaleSpecies": "D. melanogaster"}, {"TrayID": "T1"}]}
```

Run: `python -m pytest tests/test_explorer_query.py -v` — expect PASS.

- [ ] **Step 8: Wire `stock_explorer` to the query-pushdown path**

```python
# flymanager/app/routes/stock.py — replace stock_explorer (lines 567-678)
@bp.route("/explorer", methods=["GET", "POST"])
@login_required
def stock_explorer():
    username = session.get("username")
    pagination_state = get_explorer_pagination_state(
        session_key="stock_explorer_pagination",
    )
    filter_state, redirect_response = get_explorer_filter_state(
        session_key="stock_filter_state",
        clear_endpoint="stock.stock_explorer",
        field_names=(
            "filterType",
            "filterTrayID",
            "filterStatus",
            "filterFoodType",
            "filterProvenance",
            "filterSpecies",
            "searchQuery",
        ),
    )
    if redirect_response is not None:
        return redirect_response

    filter_state = filter_state or {}
    search_query = filter_state.get("searchQuery")
    mongo_filter = _build_stock_mongo_filter(filter_state)

    try:
        scope_counts = _compute_stock_scope_counts(username, db)
        unique_values = _compute_stock_unique_values(username, db, filter_state)

        if search_query:
            # Fuzzy search can't be expressed as a Mongo query - fetch every
            # deterministically-filtered + projected candidate, then apply
            # the fuzzy filter, sort, and pagination in Python exactly as
            # the original route did, just over a far smaller candidate set.
            candidates, _ = get_accessible_documents_page(
                "stocks", username, db, mongo_filter=mongo_filter, limit=None,
            )
            filtered_stocks = _apply_stock_search(candidates, search_query)
            filtered_stocks = sorted(filtered_stocks, key=_stock_sort_key)
            pagination = paginate_explorer_records(
                filtered_stocks,
                page=pagination_state["page"],
                per_page=pagination_state["per_page"],
                per_page_value=pagination_state["per_page_value"],
            )
        else:
            per_page = pagination_state["per_page"]
            skip = (pagination_state["page"] - 1) * per_page if per_page else 0
            page_items, total_count = get_accessible_documents_page(
                "stocks", username, db, mongo_filter=mongo_filter,
                skip=skip, limit=per_page,
            )
            pagination = _build_pagination_from_db_page(
                page_items, total_count, pagination_state,
            )
    except Exception as e:
        print(f"Error fetching stocks for explorer: {e}")
        scope_counts = {"maintain": 0, "assigned_out": 0, "incoming": 0}
        unique_values = {
            k: []
            for k in ["Type", "TrayID", "Status", "FoodType", "Provenance", "Species"]
        }
        pagination = paginate_explorer_records(
            [], page=1, per_page=pagination_state["per_page"],
            per_page_value=pagination_state["per_page_value"],
        )

    pagination["per_page_options"] = pagination_state["per_page_options"]
    page_stocks = pagination["items"]

    for stock in page_stocks:
        stock["FlipIn"] = get_flip_in(stock)
        set_flip_display_fields(
            stock,
            raw_value=stock["FlipIn"],
            display_field="FlipInDisplay",
        )

        stock["EclosesIn"] = get_eclosion_in(stock)
        source_context = enrich_stock_source_context(stock)
        stock["StockSource"] = stock.get("StockSource") or source_context["sourceType"]
        stock["SourceCollection"] = stock.get("SourceCollection") or source_context["sourceCollection"]
        stock["FlyBaseStockID"] = stock.get("FlyBaseStockID") or source_context["flyBaseStockID"]
        stock.update(build_stock_provider_metadata_from_context(source_context))
        stock.update(_build_provider_match_view_fields(_get_valid_provider_match_cache(stock, source_context)))
        phenotype_summary = _get_stock_phenotype_summary(stock)
        stock["PhenotypeGuess"] = phenotype_summary["guess"]
        stock["PhenotypeConfidence"] = phenotype_summary["confidence"]

    return render_template(
        "stock/stock_explorer.html",
        username=username,
        stocks=page_stocks,
        scope_counts=scope_counts,
        unique_values=unique_values,
        filter_state=filter_state,
        pagination=pagination,
    )
```

Supporting helpers added alongside `_build_stock_mongo_filter`:

```python
# flymanager/app/routes/stock.py
def _apply_stock_search(stocks, search_query):
    """The fuzzy-match half of the original _apply_stock_filters - unchanged
    matching logic, just split out so it can run standalone on an
    already-deterministically-filtered candidate list.
    """
    sq_lower = search_query.lower()

    def match(stock):
        search_fields = [
            stock.get("SourceID", ""),
            stock.get("Genotype", ""),
            stock.get("Name", ""),
            stock.get("AltReference", ""),
            stock.get("SeriesID", ""),
            stock.get("TrayID", ""),
            stock.get("TrayPosition", ""),
            stock.get("Comments", ""),
        ]
        search_string = " ".join(str(field) for field in search_fields if field).lower()
        return fuzz.partial_ratio(search_string, sq_lower) > 80

    return [s for s in stocks if match(s)]


def _compute_stock_scope_counts(username, db):
    owner_scope = {"$or": [{"User": username}, {"AssignedTo": username}]}
    assigned_out_filter = {
        "$and": [owner_scope, {"User": username}, {"AssignedTo": {"$nin": ["", username]}}]
    }
    incoming_filter = {
        "$and": [owner_scope, {"AssignedTo": username}, {"User": {"$ne": username}}]
    }
    total = db["stocks"].count_documents(owner_scope)
    assigned_out = db["stocks"].count_documents(assigned_out_filter)
    incoming = db["stocks"].count_documents(incoming_filter)
    return {
        "maintain": total - assigned_out,
        "assigned_out": assigned_out,
        "incoming": incoming,
    }


def _compute_stock_unique_values(username, db, filter_state):
    owner_scope = {"$or": [{"User": username}, {"AssignedTo": username}]}
    mongo_filter = _build_stock_mongo_filter(filter_state) if filter_state else {}
    combined = {"$and": [owner_scope, mongo_filter]} if mongo_filter else owner_scope

    unique_values = {
        field: db["stocks"].distinct(field, combined)
        for field in ("Type", "TrayID", "FoodType", "Species")
    }
    unique_values["Status"] = db["stocks"].distinct("Status", owner_scope)
    unique_values["Provenance"] = sorted({
        str(value).split("/")[0]
        for value in db["stocks"].distinct("Provenance", combined)
        if value
    })
    return unique_values


def _build_pagination_from_db_page(items, total_count, pagination_state):
    """Build the same pagination dict shape as paginate_explorer_records,
    but from a page that MongoDB already sliced via skip/limit - total_count
    comes from count_documents, not len(all_records).
    """
    per_page = pagination_state["per_page"]
    if per_page is None:
        return {
            "items": items,
            "page": 1,
            "page_count": len(items),
            "per_page": None,
            "per_page_value": pagination_state["per_page_value"],
            "total_items": total_count,
            "total_pages": 1,
            "start_index": 1 if total_count else 0,
            "end_index": total_count,
            "has_previous": False,
            "has_next": False,
            "previous_page": None,
            "next_page": None,
            "page_numbers": [{"type": "page", "value": 1}],
            "is_all": True,
        }

    import math
    total_pages = max(1, math.ceil(total_count / per_page))
    current_page = min(max(1, pagination_state["page"]), total_pages)
    start_offset = (current_page - 1) * per_page
    start_index = start_offset + 1 if total_count else 0
    end_index = start_offset + len(items)
    return {
        "items": items,
        "page": current_page,
        "page_count": len(items),
        "per_page": per_page,
        "per_page_value": pagination_state["per_page_value"],
        "total_items": total_count,
        "total_pages": total_pages,
        "start_index": start_index,
        "end_index": end_index,
        "has_previous": current_page > 1,
        "has_next": current_page < total_pages,
        "previous_page": current_page - 1 if current_page > 1 else None,
        "next_page": current_page + 1 if current_page < total_pages else None,
        "page_numbers": _build_page_display(total_pages, current_page),
        "is_all": False,
    }
```

`_build_page_display` is imported from `explorer_utils` (already imported in `stock.py` per its existing `paginate_explorer_records` usage — check the import line and add `_build_page_display` to it, or call `from flymanager.app.routes.explorer_utils import _build_page_display` locally; prefer exporting it from `explorer_utils.py` by adding it to that module's usage rather than importing a private-looking underscore name across files — rename usage stays internally consistent since `explorer_utils.py` already defines `_build_page_display` at module scope).

`_apply_stock_filters` (the original combined filter+search function) stays in the file, unchanged, for the "no query pushdown" fallback used by `stock_explorer_selection` in Step 9 below where behavior must stay byte-identical to today (that endpoint doesn't paginate, only filters+sorts, so pushing it to Mongo has a smaller payoff and a larger risk of drifting from the exact ordering `_stock_sort_key` produces for edge cases like blank `TrayPosition`; verified: `_stock_sort_key` treats unparseable position as `0`, matching this task's `$convert onError: 0` — so they agree, but `stock_explorer_selection` is left on the existing helper since it's not the route driving repeated full-collection fetches on every page view).

- [ ] **Step 9: Reduce `stock_explorer_selection`'s fetch to a projection (smaller, lower-risk fix — this endpoint returns only `id`/`name`/`identifier`/`quantity` per `_build_stock_selection_item`, so it doesn't need full documents even though it still needs the full accessible+filtered set for "select all")**

```python
# flymanager/app/routes/stock.py — replace stock_explorer_selection (lines 681-691)
_STOCK_SELECTION_PROJECTION = {
    "UniqueID", "User", "AssignedTo", "Name", "TrayID", "TrayPosition",
    "Status", "Type", "FoodType", "Provenance", "Species",
    "SourceID", "Genotype", "AltReference", "SeriesID", "Comments",
}


@bp.route("/explorer/selection", methods=["GET"])
@login_required
def stock_explorer_selection():
    username = session.get("username")
    filter_state = session.get("stock_filter_state", {})
    mongo_filter = _build_stock_mongo_filter(filter_state)
    candidates, _ = get_accessible_documents_page(
        "stocks", username, db, mongo_filter=mongo_filter,
        limit=None, projection=_STOCK_SELECTION_PROJECTION,
    )
    search_query = filter_state.get("searchQuery")
    filtered_stocks = (
        _apply_stock_search(candidates, search_query) if search_query else candidates
    )
    filtered_stocks = sorted(filtered_stocks, key=_stock_sort_key)

    items = [_build_stock_selection_item(stock) for stock in filtered_stocks]
    return jsonify({"count": len(items), "items": items})
```

This keeps the Status/Type/etc. filters, the fuzzy search, and the sort all identical to today — the only change is fetching a ~10-field projection instead of full documents (still every field `_build_stock_selection_item` and `_stock_sort_key` touch — verify by re-reading both functions before finalizing `_STOCK_SELECTION_PROJECTION`; `_apply_stock_filters` is fully replaced by `mongo_filter` + `_apply_stock_search` here, so this endpoint no longer calls the old combined helper at all).

- [ ] **Step 10: Mirror Steps 6-9 for crosses** (same structure: `_apply_cross_search` extracted from `_apply_cross_filters`'s search block, `_compute_cross_scope_counts`, `_compute_cross_unique_values`, wire `cross_explorer` and reduce `cross_explorer_selection` to a projection). Read `cross.py:244-333` and `:336+` once more immediately before writing this to confirm field names (`MaleSpecies`/`FemaleSpecies` unique-value fields differ from stock's `Type`/`Provenance`/`Species` — do not copy-paste the stock version's field list).

- [ ] **Step 11: Write equivalence tests comparing old vs. new result ordering and content for both explorers**

```python
# tests/test_explorer_query.py — add
from flymanager.app.routes.stock import (
    _apply_stock_filters, _build_stock_mongo_filter, _apply_stock_search, _stock_sort_key,
)
from flymanager.utils.mongo.access import get_accessible_documents_page


def test_pushdown_path_matches_python_path_for_deterministic_filters():
    db = FakeDatabase({
        "stocks": [
            _stock("s1", TrayID="T1", TrayPosition="3", Type="WT", Status="Healthy"),
            _stock("s2", TrayID="T1", TrayPosition="1", Type="WT", Status="Healthy"),
            _stock("s3", TrayID="T2", TrayPosition="1", Type="Mutant", Status="Healthy"),
            _stock("s4", TrayID="T1", TrayPosition="2", Type="WT", Status="No longer maintained"),
        ]
    })
    filter_state = {"filterType": "WT"}

    old_result = sorted(
        _apply_stock_filters(list(db["stocks"].find({"User": "alice"})), filter_state),
        key=_stock_sort_key,
    )
    new_result, _ = get_accessible_documents_page(
        "stocks", "alice", db, mongo_filter=_build_stock_mongo_filter(filter_state),
    )

    assert [s["UniqueID"] for s in old_result] == [s["UniqueID"] for s in new_result]


def test_pushdown_plus_python_search_matches_fully_python_path():
    db = FakeDatabase({
        "stocks": [
            _stock("s1", Name="rescue line alpha", Genotype="w[1118]; UAS-x"),
            _stock("s2", Name="control", Genotype="w[1118]"),
        ]
    })
    filter_state = {"searchQuery": "rescue"}

    old_result = sorted(
        _apply_stock_filters(list(db["stocks"].find({"User": "alice"})), filter_state),
        key=_stock_sort_key,
    )
    candidates, _ = get_accessible_documents_page(
        "stocks", "alice", db, mongo_filter=_build_stock_mongo_filter(filter_state), limit=None,
    )
    new_result = sorted(
        _apply_stock_search(candidates, filter_state["searchQuery"]), key=_stock_sort_key,
    )

    assert [s["UniqueID"] for s in old_result] == [s["UniqueID"] for s in new_result]
```

- [ ] **Step 12: Run the full explorer test suite**

Run: `python -m pytest tests/test_explorer_query.py -v`
Expected: PASS (all tests added in Steps 3, 6, 7, 11, plus the cross-side mirror from Step 10)

- [ ] **Step 13: Manually verify both explorers in the running app**

Use the `run-flymanager` skill: log in, open `/stock/explorer` and `/cross/explorer`. For each: apply a Type/Status/TrayID filter and confirm the result set and row order match what filtering produced before this change; type a free-text search term and confirm fuzzy matches still appear (e.g. a slight misspelling of a genotype); page forward/backward and confirm counts/totals are correct; switch `per_page` to "All" and confirm every filtered record still renders; use the "select all filtered" action and confirm the count matches the explorer's own filtered total.

- [ ] **Step 14: Commit**

```bash
git add flymanager/utils/mongo/access.py flymanager/app/routes/stock.py flymanager/app/routes/cross.py tests/test_explorer_query.py tests/mongo_fakes.py tests/test_mongo_fakes.py
git commit -m "perf: push deterministic explorer filtering, projection, and pagination into MongoDB"
```

---

## Post-plan verification

- [ ] Run the full test suite: `python -m pytest tests/ -v` and confirm no regressions outside the files touched by this plan.
- [ ] Use the `run-flymanager` skill to start the app and smoke-test each touched page once more end-to-end: dashboard `/home`, `/stock/explorer`, `/cross/explorer`, `/trays`, a single tray's `/tray/<id>` view, a bulk flip, a bulk status change, a bulk permanent delete of a couple of "No longer maintained" stocks, and a tray assignment.
- [ ] Confirm `ensure_mongo_indexes` runs cleanly against the real MongoDB instance (not just the fakes) — restart the app via the `run-flymanager` skill and check its startup logs for index-creation errors.
