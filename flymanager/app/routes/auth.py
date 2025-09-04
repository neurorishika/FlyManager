# flymanager/app/routes/auth.py
import hashlib
from flask import Blueprint, render_template, request, redirect, session, url_for, flash
from functools import wraps
from flymanager.app import db
from flymanager.utils.mongo import (
    get_all_users,
    add_user,
    change_password,
    get_user_stocks,
    update_stock_vials,
    get_user_crosses,
    update_cross_vials,
    write_activity,
)

bp = Blueprint("auth", __name__, url_prefix="/auth")


# Login required decorator
def login_required(f):
    @wraps(f)
    def decorated_function(*args, **kwargs):
        if "username" not in session:
            flash("Please log in to access this page", "warning")
            return redirect(url_for("auth.login", next=request.url))
        return f(*args, **kwargs)

    return decorated_function


# Another decorator for making sure the user is equal to admin
def admin_required(f):
    @wraps(f)
    def decorated_function(*args, **kwargs):
        if session.get("username") != "admin":
            flash("Admin access required", "danger")
            return redirect(url_for("auth.login"))
        return f(*args, **kwargs)

    return decorated_function


@bp.route("/login", methods=["POST", "GET"])
def login():
    if session.get("username"):
        print(f"{session['username']} is already logged in, redirecting to home.")
        return redirect(url_for("main.home"))  # Use url_for

    users_dict = get_all_users(db=db)
    users = list(users_dict.keys()) if users_dict else []
    message = None

    if request.method == "POST":
        username = request.form.get("username")
        password = request.form.get("password")

        if not username or not password:
            message = "Username and password are required."
        elif username in users:
            stored_hash = users_dict.get(username)
            input_hash = hashlib.shake_256((username + password).encode()).hexdigest(5)

            if stored_hash == input_hash:
                session["username"] = username
                print(f"User {username} logged in successfully.")
                try:
                    # Update vials on login
                    print(f"Updating vials for {username}...")
                    stocks = get_user_stocks(username, db)
                    for stock in stocks:
                        update_stock_vials(stock, username, db)
                    print("Updated stock vials.")
                    crosses = get_user_crosses(username, db)
                    for cross in crosses:
                        update_cross_vials(cross, username, db)
                    print("Updated cross vials.")
                    # Log activity
                    write_activity(username, "Logged in", db)
                except Exception as e:
                    print(
                        f"Error updating vials or logging activity for {username}: {e}"
                    )
                    # Decide if login should fail here? For now, continue.

                return redirect(url_for("main.home"))  # Use url_for
            else:
                message = "Incorrect password"
        else:
            message = "User does not exist"

    # For GET request or failed POST
    return render_template("auth/login.html", users=users, message=message)


@bp.route("/register", methods=["POST", "GET"])
def register():
    if session.get("username"):
        return redirect(url_for("main.home"))  # Use url_for

    message = None
    if request.method == "POST":
        username = request.form.get("username")
        password = request.form.get("password")
        initials = request.form.get("initials")

        if not username or not password or not initials:
            message = "Username, password, and initials are required."
        else:
            try:
                if add_user(username, password, initials, db=db):
                    print(f"User {username} registered successfully.")
                    return redirect(url_for("auth.login"))  # Use url_for
                else:
                    message = "Username or Initials already exist."
            except Exception as e:
                print(f"Error during registration for {username}: {e}")
                message = "An error occurred during registration."

    return render_template("auth/register.html", message=message)


@bp.route("/forgot_password", methods=["POST", "GET"])
def forgot_password():
    # Original code checked session.get("name"), assuming it meant "username"
    # This route usually doesn't require login, but let's keep original logic for now
    # if not session.get("username"):
    #     return redirect(url_for('auth.login')) # Use url_for

    users_dict = get_all_users(db=db)
    users = list(users_dict.keys()) if users_dict else []
    message = None

    if request.method == "POST":
        username = request.form.get("username")
        master_password = request.form.get(
            "master_password"
        )  # Assuming a master password exists
        new_password = request.form.get("new_password")

        if not username or not master_password or not new_password:
            message = "All fields are required."
        elif username == "Master":  # Specific check from original code
            message = "Master password cannot be changed here. Please contact the administrator."
            # Original message included terminal command - omitting for security/usability.
        else:
            try:
                if change_password(username, master_password, new_password, db=db):
                    print(f"Password changed successfully for {username}.")
                    return redirect(url_for("auth.login"))  # Use url_for
                else:
                    message = "Invalid username or master password."
            except Exception as e:
                print(f"Error changing password for {username}: {e}")
                message = "An error occurred while changing the password."

    return render_template("auth/forgot_password.html", users=users, message=message)


@bp.route("/logout")
@login_required
def logout():
    username = session.get("username")
    if username:
        print(f"User {username} logging out.")
        # Log activity before clearing session
        try:
            write_activity(username, "Logged out", db)
        except Exception as e:
            print(f"Error logging logout activity for {username}: {e}")
        session.pop("username", None)
    return redirect(url_for("auth.login"))  # Use url_for
