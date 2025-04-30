from pymongo.database import Database

def get_settings(db: Database) -> dict:
    """Get application settings from MongoDB"""
    settings = db['settings'].find_one({})
    if not settings:
        # Initialize default settings if none exist
        settings = {
            'theme': {
                'dark_mode': False,
                'accent_color': '#007bff'
            },
            'lab_info': {
                'lab_name': 'Ruta Lab',
                'admin_name': 'Lab Administrator',
                'admin_email': 'admin@example.com'
            }
        }
        db['settings'].insert_one(settings)
    return settings

def update_settings(updates: dict, db: Database) -> bool:
    """Update application settings in MongoDB"""
    try:
        result = db['settings'].update_one(
            {}, 
            {'$set': updates},
            upsert=True
        )
        return bool(result.modified_count > 0 or result.upserted_id)
    except Exception as e:
        print(f"Error updating settings: {e}")
        return False