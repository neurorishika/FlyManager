# Core database functions
# Activity logging
# Access and assignment helpers
from flymanager.utils.mongo.access import (get_accessible_cross,
                                           get_accessible_crosses,
                                           get_accessible_stock,
                                           get_accessible_stocks,
                                           get_direct_reports,
                                           get_maintainable_crosses,
                                           get_maintainable_stocks,
                                           get_reporting_manager,
                                           get_user_profiles,
                                           update_document_assignment,
                                           update_user_reporting_manager)
from flymanager.utils.mongo.activity import write_activity
# Authentication functions
from flymanager.utils.mongo.auth import (add_user, change_password,
                                         consume_password_reset_token,
                                         create_password_reset_token,
                                         get_all_users,
                                         get_reset_token_username, get_user,
                                         verify_user_password)
# Cross functions
from flymanager.utils.mongo.crosses import (add_to_cross, delete_cross,
                                            edit_cross, flip_cross, get_cross,
                                            update_cross_vials)
from flymanager.utils.mongo.db import (create_mongo_client,
                                       ensure_mongo_indexes, get_database,
                                       ping_database, reset_database,
                                       uid_exists)
# Helper functions
from flymanager.utils.mongo.helpers import (add_metadata, delete_metadata,
                                            edit_metadata,
                                            find_closest_flip_day,
                                            get_eclosion_in, get_flip_in,
                                            get_flip_schedule, get_metadata,
                                            preload_metadata_cache)
from flymanager.utils.mongo.operation_locks import (OperationLockConflict,
                                                    hold_operation_lock,
                                                    hold_operation_locks,
                                                    record_operation_lock_keys)
# Settings functions
from flymanager.utils.mongo.settings import get_settings, update_settings
# Stock functions
from flymanager.utils.mongo.stocks import (add_to_stock, delete_stock,
                                           edit_stock, flip_stock, get_stock,
                                           update_stock_vials)
# Tray functions
from flymanager.utils.mongo.trays import (add_tray, annotate_tray_access,
                                          calculate_required_vials,
                                          delete_tray, get_accessible_tray,
                                          get_accessible_trays, get_tray,
                                          get_tray_occupancy, get_user_trays,
                                          move_item_to_tray, update_tray)
# User data functions
from flymanager.utils.mongo.user_data import (get_all_genotypes,
                                              get_user_activities,
                                              get_user_crosses, get_user_email,
                                              get_user_flip_days,
                                              get_user_initials,
                                              get_user_stocks)

# Make all functions available directly from the mongo module
__all__ = [
    # DB functions
    "create_mongo_client",
    "get_database",
    "ping_database",
    "ensure_mongo_indexes",
    "reset_database",
    "uid_exists",
    "OperationLockConflict",
    "hold_operation_lock",
    "hold_operation_locks",
    "record_operation_lock_keys",
    # Access helpers
    "get_accessible_stock",
    "get_accessible_stocks",
    "get_accessible_cross",
    "get_accessible_crosses",
    "get_maintainable_stocks",
    "get_maintainable_crosses",
    "get_direct_reports",
    "get_reporting_manager",
    "get_user_profiles",
    "update_document_assignment",
    "update_user_reporting_manager",
    # Auth functions
    "add_user",
    "get_all_users",
    "change_password",
    "create_password_reset_token",
    "consume_password_reset_token",
    # User data functions
    "get_user_stocks",
    "get_user_initials",
    "get_user_flip_days",
    "get_user_email",
    "get_user_crosses",
    "get_user_activities",
    "get_all_genotypes",
    # Stock functions
    "add_to_stock",
    "get_stock",
    "flip_stock",
    "delete_stock",
    "edit_stock",
    "update_stock_vials",
    # Cross functions
    "add_to_cross",
    "get_cross",
    "flip_cross",
    "delete_cross",
    "edit_cross",
    "update_cross_vials",
    # Tray functions
    "add_tray",
    "annotate_tray_access",
    "get_accessible_trays",
    "get_accessible_tray",
    "get_user_trays",
    "get_tray",
    "delete_tray",
    "update_tray",
    "get_tray_occupancy",
    "calculate_required_vials",
    "move_item_to_tray",
    # Helper/metadata functions
    "get_metadata",
    "preload_metadata_cache",
    "add_metadata",
    "delete_metadata",
    "edit_metadata",
    "get_flip_schedule",
    "get_flip_in",
    "get_eclosion_in",
    "find_closest_flip_day",
    "get_user",
    "get_reset_token_username",
    "verify_user_password",
    # Activity functions
    "write_activity",
    # Settings functions
    "get_settings",
    "update_settings",
]
