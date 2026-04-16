import secrets
from datetime import datetime, timedelta
from hashlib import sha256, shake_256
from hmac import compare_digest

from werkzeug.security import check_password_hash, generate_password_hash

from flymanager.utils.mongo.activity import write_activity

PASSWORD_RESET_TOKEN_TTL_MINUTES = 60


def normalize_email(email):
    """Return a normalized email address or None when it is blank."""
    if email is None:
        return None
    normalized = email.strip().lower()
    return normalized or None


def _utcnow():
    return datetime.utcnow()


def _legacy_password_hash(user, password):
    return shake_256((user + password).encode()).hexdigest(5)


def _is_modern_password_hash(password_hash):
    return password_hash.startswith(("pbkdf2:", "scrypt:"))


def _hash_reset_token(token):
    return sha256(token.encode()).hexdigest()


def get_user(user, db):
    """Return the stored user document for a username."""
    return db["users"].find_one({"Username": user})


def add_user(user, password, initials, db, email=None):
    """Add a user to the MongoDB database."""
    users_collection = db["users"]
    normalized_email = normalize_email(email)

    if users_collection.find_one({"Username": user}):
        return False

    if normalized_email and users_collection.find_one({"Email": normalized_email}):
        return False

    user_document = {
        "Username": user,
        "Password": generate_password_hash(password),
        "Initials": initials,
        "PasswordChangedAt": _utcnow(),
    }

    if normalized_email:
        user_document["Email"] = normalized_email

    users_collection.insert_one(user_document)
    write_activity(user, "User added", db)
    return True


def get_all_users(db):
    """Get all users as a mapping of username to password hash."""
    users_collection = db["users"]
    clients = {}
    for user in users_collection.find({}, {"Username": 1, "Password": 1}):
        clients[user["Username"]] = user["Password"]
    return clients


def verify_user_password(user, password, db):
    """Verify a user's password and migrate legacy hashes on success."""
    users_collection = db["users"]
    user_document = users_collection.find_one({"Username": user})
    if not user_document:
        return False, False

    stored_hash = user_document.get("Password", "")
    if _is_modern_password_hash(stored_hash):
        return check_password_hash(stored_hash, password), False

    legacy_hash = _legacy_password_hash(user, password)
    if not compare_digest(stored_hash, legacy_hash):
        return False, False

    users_collection.update_one(
        {"_id": user_document["_id"]},
        {
            "$set": {
                "Password": generate_password_hash(password),
                "PasswordChangedAt": _utcnow(),
                "PasswordMigratedAt": _utcnow(),
            }
        },
    )
    return True, True


def change_password(user, new_password, db):
    """Change the password of a user."""
    users_collection = db["users"]
    result = users_collection.update_one(
        {"Username": user},
        {
            "$set": {
                "Password": generate_password_hash(new_password),
                "PasswordChangedAt": _utcnow(),
            }
        },
    )

    if result.matched_count > 0:
        write_activity(user, "Password changed", db)

    return result.matched_count > 0


def create_password_reset_token(user, db, ttl_minutes=PASSWORD_RESET_TOKEN_TTL_MINUTES):
    """Create a one-time password reset token for the given user."""
    if not get_user(user, db):
        return None

    token = secrets.token_urlsafe(32)
    issued_at = _utcnow()
    expires_at = issued_at + timedelta(minutes=ttl_minutes)
    tokens_collection = db["password_reset_tokens"]
    tokens_collection.delete_many({"Username": user})
    tokens_collection.insert_one(
        {
            "Username": user,
            "TokenHash": _hash_reset_token(token),
            "CreatedAt": issued_at,
            "ExpiresAt": expires_at,
            "UsedAt": None,
        }
    )
    write_activity(user, "Password reset requested", db)
    return token


def get_reset_token_username(token, db):
    """Return the username for a valid reset token, otherwise None."""
    tokens_collection = db["password_reset_tokens"]
    token_document = tokens_collection.find_one(
        {"TokenHash": _hash_reset_token(token), "UsedAt": None}
    )
    if not token_document:
        return None

    if token_document["ExpiresAt"] < _utcnow():
        return None

    return token_document["Username"]


def consume_password_reset_token(token, new_password, db):
    """Consume a valid password reset token and update the password."""
    tokens_collection = db["password_reset_tokens"]
    token_document = tokens_collection.find_one(
        {"TokenHash": _hash_reset_token(token), "UsedAt": None}
    )
    if not token_document:
        return None

    if token_document["ExpiresAt"] < _utcnow():
        return None

    username = token_document["Username"]
    if not change_password(username, new_password, db):
        return None

    tokens_collection.update_one(
        {"_id": token_document["_id"]},
        {"$set": {"UsedAt": _utcnow()}},
    )
    write_activity(username, "Password reset completed", db)
    return username