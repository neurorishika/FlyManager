# Core database functions
from .db import create_mongo_client, get_database, reset_database, uid_exists

# Authentication functions
from .auth import write_activity, add_user, get_all_users, change_password

# User data functions
from .user_data import (
    get_user_stocks, get_user_initials, get_user_flip_days, get_user_email, 
    get_user_crosses, get_user_activities, get_all_genotypes
)

# Stock functions
from .stocks import (
    add_to_stock, get_stock, flip_stock, delete_stock, edit_stock, update_stock_vials
)

# Cross functions
from .crosses import (
    add_to_cross, get_cross, flip_cross, delete_cross, edit_cross, update_cross_vials
)

# Tray functions
from .trays import (
    add_tray, get_user_trays, get_tray, delete_tray, update_tray, 
    get_tray_occupancy, calculate_required_vials, move_item_to_tray
)

# Helper functions
from .helpers import (
    get_metadata, add_metadata, delete_metadata, edit_metadata,
    get_flip_schedule, get_flip_in, get_eclosion_in, find_closest_flip_day
)

# Activity logging
from .activity import write_activity

# Make all functions available directly from the mongo module
__all__ = [
    # DB functions
    'create_mongo_client', 'get_database', 'reset_database', 'uid_exists',
    
    # Auth functions
    'add_user', 'get_all_users', 'change_password',
    
    # User data functions
    'get_user_stocks', 'get_user_initials', 'get_user_flip_days', 'get_user_email',
    'get_user_crosses', 'get_user_activities', 'get_all_genotypes',
    
    # Stock functions
    'add_to_stock', 'get_stock', 'flip_stock', 'delete_stock', 'edit_stock', 'update_stock_vials',
    
    # Cross functions
    'add_to_cross', 'get_cross', 'flip_cross', 'delete_cross', 'edit_cross', 'update_cross_vials',
    
    # Tray functions
    'add_tray', 'get_user_trays', 'get_tray', 'delete_tray', 'update_tray',
    'get_tray_occupancy', 'calculate_required_vials', 'move_item_to_tray',
    
    # Helper/metadata functions
    'get_metadata', 'add_metadata', 'delete_metadata', 'edit_metadata',
    'get_flip_schedule', 'get_flip_in', 'get_eclosion_in', 'find_closest_flip_day',
    
    # Activity functions
    'write_activity'
]