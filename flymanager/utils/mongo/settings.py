from pymongo.database import Database

DEFAULT_SETTINGS = {
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


def get_settings(db: Database) -> dict:
    """Get application settings, filling in any missing defaults.

    The singleton is not owned solely by this module: revision counters
    (markerCatalogRevision, markerImageRevision) are bumped with
    find_one_and_update(..., upsert=True), which on an empty database
    creates a document holding only that counter. Initializing defaults
    only when the document is entirely absent left such a skeleton in
    place forever, and every template that reads settings.lab_info then
    raised UndefinedError on every page. So merge missing top-level keys
    rather than testing for the document's existence.
    """
    settings = db['settings'].find_one({})
    if not settings:
        settings = dict(DEFAULT_SETTINGS)
        db['settings'].insert_one(settings)
        return settings

    missing = {
        key: value
        for key, value in DEFAULT_SETTINGS.items()
        if key not in settings
    }
    if missing:
        db['settings'].update_one({'_id': settings['_id']}, {'$set': missing})
        settings.update(missing)
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