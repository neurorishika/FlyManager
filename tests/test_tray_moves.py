import importlib.util
import sys
from pathlib import Path
from types import ModuleType
from unittest.mock import MagicMock, patch


MODULE_PATH = Path(__file__).resolve().parents[1] / "flymanager" / "utils" / "mongo" / "trays.py"
MODULE_SPEC = importlib.util.spec_from_file_location("test_trays_module", MODULE_PATH)
trays_module = importlib.util.module_from_spec(MODULE_SPEC)
assert MODULE_SPEC.loader is not None
MODULE_SPEC.loader.exec_module(trays_module)


def _stub_item_modules(stock=None, cross=None, stock_edit_result=True, cross_edit_result=True):
    flymanager_module = ModuleType("flymanager")
    flymanager_module.__path__ = []
    utils_module = ModuleType("flymanager.utils")
    utils_module.__path__ = []
    mongo_module = ModuleType("flymanager.utils.mongo")
    mongo_module.__path__ = []

    stock_module = ModuleType("flymanager.utils.mongo.stocks")
    stock_module.get_stock = MagicMock(return_value=stock)
    stock_module.edit_stock = MagicMock(return_value=stock_edit_result)

    cross_module = ModuleType("flymanager.utils.mongo.crosses")
    cross_module.get_cross = MagicMock(return_value=cross)
    cross_module.edit_cross = MagicMock(return_value=cross_edit_result)

    modules = {
        "flymanager": flymanager_module,
        "flymanager.utils": utils_module,
        "flymanager.utils.mongo": mongo_module,
        "flymanager.utils.mongo.stocks": stock_module,
        "flymanager.utils.mongo.crosses": cross_module,
    }
    return modules, stock_module, cross_module


def test_move_item_to_tray_allows_reposition_within_same_tray():
    db = MagicMock()
    tray = {"TrayID": "TRAY-1", "Rows": 10, "Columns": 10}
    stock = {
        "UniqueID": "STK-1",
        "TrayID": "TRAY-1",
        "TrayPosition": "1",
        "Status": "Healthy",
        "VialLifetime": "14",
        "FlipFrequency": "7",
    }
    occupancy = {
        "1": {"type": "stock", "id": "STK-1"},
        "11": {"type": "blocked", "blocked_by": "1"},
    }

    modules, stock_module, _ = _stub_item_modules(stock=stock)

    with patch.object(trays_module, "get_tray", return_value=tray), patch.object(
        trays_module, "get_tray_occupancy", return_value=occupancy
    ), patch.dict(sys.modules, modules):
        success = trays_module.move_item_to_tray("alice", "stock", "STK-1", "TRAY-1", "11", db)

    assert success is True
    stock_module.edit_stock.assert_called_once_with(
        "alice",
        "STK-1",
        db,
        {"TrayID": "TRAY-1", "TrayPosition": "11"},
    )


def test_move_item_to_tray_rejects_conflict_with_other_item():
    db = MagicMock()
    tray = {"TrayID": "TRAY-1", "Rows": 10, "Columns": 10}
    stock = {
        "UniqueID": "STK-1",
        "TrayID": "TRAY-1",
        "TrayPosition": "1",
        "Status": "Healthy",
        "VialLifetime": "14",
        "FlipFrequency": "7",
    }
    occupancy = {
        "1": {"type": "stock", "id": "STK-1"},
        "11": {"type": "blocked", "blocked_by": "1"},
        "21": {"type": "stock", "id": "STK-2"},
    }

    modules, stock_module, _ = _stub_item_modules(stock=stock)

    with patch.object(trays_module, "get_tray", return_value=tray), patch.object(
        trays_module, "get_tray_occupancy", return_value=occupancy
    ), patch.dict(sys.modules, modules):
        success = trays_module.move_item_to_tray("alice", "stock", "STK-1", "TRAY-1", "11", db)

    assert success is False
    stock_module.edit_stock.assert_not_called()