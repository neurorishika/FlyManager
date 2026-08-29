"""Covers making bulk_remove_from_tray_records() (and the route/task above
it) accept a mixed cart of stocks and crosses in one call, instead of one
`item_type` applied to the whole batch - part of sharing the explorer cart
between stock and cross pages so both can be bulk-removed from trays
together.
"""
import flymanager.app  # noqa: F401  (see test_bulk_operations.py for why)

from unittest.mock import patch

from flymanager.utils.mongo.bulk_operations import bulk_remove_from_tray_records


def test_bulk_remove_from_tray_records_dispatches_per_item_type():
    calls = []

    def fake_move_item_to_tray(user, item_type, item_id, tray_id, position, db):
        calls.append((item_type, item_id))
        return True

    with patch(
        "flymanager.utils.mongo.bulk_operations.move_item_to_tray",
        side_effect=fake_move_item_to_tray,
    ):
        results = bulk_remove_from_tray_records(
            "alice",
            ["s1", "x1"],
            ["stock", "cross"],
            db=object(),
        )

    assert calls == [("stock", "s1"), ("cross", "x1")]
    assert [entry["uid"] for entry in results["success"]] == ["s1", "x1"]
    assert [entry["type"] for entry in results["success"]] == ["stock", "cross"]
