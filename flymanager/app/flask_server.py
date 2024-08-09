# setup the main flask server

# each user is a assigned a single worksheet in the master google sheet with the name <username>_<password_hash>

# design goals:
# simple login page with a hash-based password that is stored in the server, provides access to the user's worksheet
# register page to create a new user, create a new worksheet in the master google sheet

# import the necessary packages
from flask import Flask, jsonify, render_template, request, redirect, Response, session
from flask_session import Session
import os
import hashlib
from fuzzywuzzy import fuzz

from flymanager.utils.gsheet import get_all_users, get_user_initials, add_user, change_password, create_client, write_activity, get_user_activities, get_user_stocks
from flymanager.utils.labels import generate_label_pdf

from datetime import datetime


# initialize our Flask application
app = Flask(__name__, template_folder="../templates", static_folder="../static")
# configure the session
app.secret_key = os.urandom(24)
app.config["SESSION_PERMANENT"] = False
app.config["SESSION_COOKIE_NAME"] = "flymanager"
app.config["SESSION_TIMEOUT"] = 3600
app.config["SESSION_USE_SIGNER"] = True
app.config["SESSION_KEY_PREFIX"] = "flymanager"
app.config["SESSION_TYPE"] = "filesystem"
Session(app)

# connect to google sheets
client = create_client()

# define a route for the default URL
@app.route('/')
def index():
    # check if the user is logged in
    if not session.get("name"):
        return redirect("/login")
    # if not, redirect to the login page
    return redirect("/login")


### AUTHENTICATION ROUTES ###

# define a route for the login page (with dropdown for users)
@app.route('/login', methods=["POST", "GET"])
def login():
    # check if the user is logged in
    if session.get("username"):
        print(session["username"], "is already logged in")
        return redirect("/home")
    # get all the users
    users = list(get_all_users(client=client).keys())
    # if the request method is POST
    if request.method == "POST":
        # get the username and password
        username = request.form["username"]
        password = request.form["password"]
        # check if the user exists
        if username in users:
            # check if the password is correct
            if get_all_users(client=client)[username] == hashlib.shake_256((username+password).encode()).hexdigest(5):
                # store the username in the session
                session["username"] = username
                return redirect("/home")
            else:
                return render_template("auth/login.html", users=users, message="Incorrect password")
        else:
            return render_template("auth/login.html", users=users, message="User does not exist")
    return render_template("auth/login.html", users=users)

# define a route for the register page
@app.route('/register', methods=["POST", "GET"])
def register():
    # check if the user is logged in
    if session.get("username"):
        return redirect("/home")
    if request.method == "POST":
        # get the username and password
        username = request.form["username"]
        password = request.form["password"]
        initials = request.form["initials"]
        # add the user
        if add_user(username, password, initials, client=client):
            return redirect("/login")
        else:
            return render_template("auth/register.html", message="User/Initials already exists")
    return render_template("auth/register.html")

# define a route for the forgot password page
@app.route('/forgot_password', methods=["POST", "GET"])
def forgot_password():
    # check if the user is logged in
    if not session.get("name"):
        return redirect("/login")
    users = list(get_all_users(client=client).keys())
    if request.method == "POST":
        # get the username, master password and new password
        username = request.form["username"]
        master_password = request.form["master_password"]
        new_password = request.form["new_password"]
        # if the username is Master, give special instructions
        if username == "Master":
            return render_template("auth/forgot_password.html", users=users, message="Master password cannot be changed here. Run 'python -c 'import hashlib; print(hashlib.shake_256(('admin'+input()).encode()).hexdigest(5))' in the terminal to get the new master hash and update on the users sheet on the google sheet manually")
        # change the password
        if change_password(username, master_password, new_password, client=client):
            return redirect("/login")
        else:
            return render_template("auth/forgot_password.html", users=users, message="Invalid username or master password")
    return render_template("auth/forgot_password.html", users=users)


# define a route for the logout page
@app.route('/logout')
def logout():
    # remove the username from the session
    session["username"] = None
    return redirect("/login")

### END AUTHENTICATION ROUTES ###

### USER ROUTES ###

# define a route for the home page
@app.route('/home')
def home():
    # check if the user is logged in
    if not session.get("username"):
        return redirect("/login")
    # get the user's activity
    username = session.get("username")
    activities = get_user_activities(username, client)
    return render_template("home.html", username=username, activities=activities)

# define a route for the stock explorer page
@app.route('/stock_explorer', methods=['GET', 'POST'])
def stock():
    # check if the user is logged in
    if not session.get("username"):
        return redirect("/login")
    
    username = session.get("username")
    stocks = get_user_stocks(username, client)
    stocks = stocks.get_all_records()

    # Extract unique values for filtering
    unique_values = {
        'Type': sorted(set(str(stock['Type']) for stock in stocks)),
        'SeriesID': sorted(set(str(stock['SeriesID']) for stock in stocks)),
        'TrayID': sorted(set(str(stock['TrayID']) for stock in stocks)),
        'Status': sorted(set(str(stock['Status']) for stock in stocks)),
        'FoodType': sorted(set(str(stock['FoodType']) for stock in stocks)),
        'Provenance': sorted(set(str(stock['Provenance']).split('/')[0] for stock in stocks))
    }

    if request.method == 'GET':
        # Check if there are filters stored in session
        filter_state = session.get('filter_state', {})
        return render_template("stock_explorer.html", username=username, stocks=stocks, unique_values=unique_values, filter_state=filter_state)
    elif request.method == 'POST':
        if 'clear_filters' in request.form:
            session.pop('filter_state', None)
            return redirect('/stock_explorer')

        # Get filter values from request
        filter_type = str(request.form.get('filterType'))
        filter_series_id = str(request.form.get('filterSeriesID'))
        filter_tray_id = str(request.form.get('filterTrayID'))
        filter_status = str(request.form.get('filterStatus'))
        filter_food_type = str(request.form.get('filterFoodType'))
        filter_provenance = str(request.form.get('filterProvenance'))
        search_query = request.form.get('searchQuery')

        # Store filter state in session
        filter_state = {
            'filterType': filter_type,
            'filterSeriesID': filter_series_id,
            'filterTrayID': filter_tray_id,
            'filterStatus': filter_status,
            'filterFoodType': filter_food_type,
            'filterProvenance': filter_provenance,
            'searchQuery': search_query
        }
        session['filter_state'] = filter_state

        # Apply filters
        filtered_stocks = stocks
        if filter_type:
            filtered_stocks = [stock for stock in filtered_stocks if str(stock['Type']) == filter_type]
        if filter_series_id:
            filtered_stocks = [stock for stock in filtered_stocks if str(stock['SeriesID']) == filter_series_id]
        if filter_tray_id:
            filtered_stocks = [stock for stock in filtered_stocks if str(stock['TrayID']) == filter_tray_id]
        if filter_status:
            filtered_stocks = [stock for stock in filtered_stocks if str(stock['Status']) == filter_status]
        if filter_food_type:
            filtered_stocks = [stock for stock in filtered_stocks if str(stock['FoodType']) == filter_food_type]
        if filter_provenance:
            filtered_stocks = [stock for stock in filtered_stocks if str(stock['Provenance']).split('/')[0] == filter_provenance]

        # Apply search
        if search_query:
            def match(stock):
                search_fields = [
                    stock['SourceID'],
                    stock['Genotype'],
                    stock['Name'],
                    stock['AltReference'],
                    stock['SeriesID'],
                    stock['TrayID'],
                    stock['TrayPosition'],
                    stock['Comments']
                ]
                # combine all fields into a single string
                search_string = ' '.join(str(field) for field in search_fields)
                # find if the search query is a substring of the search string
                return fuzz.partial_ratio(search_string, search_query) > 80
            
            filtered_stocks = [stock for stock in filtered_stocks if match(stock)]

        # Recalculate unique values
        unique_values = {
            'Type': sorted(set(str(stock['Type']) for stock in filtered_stocks)),
            'SeriesID': sorted(set(str(stock['SeriesID']) for stock in filtered_stocks)),
            'TrayID': sorted(set(str(stock['TrayID']) for stock in filtered_stocks)),
            'Status': sorted(set(str(stock['Status']) for stock in filtered_stocks)),
            'FoodType': sorted(set(str(stock['FoodType']) for stock in filtered_stocks)),
            'Provenance': sorted(set(str(stock['Provenance']).split('/')[0] for stock in filtered_stocks))
        }

        return render_template("stock_explorer.html", username=username, stocks=filtered_stocks, unique_values=unique_values, filter_state=filter_state)

    
# define a route for the add stock page
@app.route('/add_stock', methods=['GET', 'POST'])
def add_stock():
    if not session.get("username"):
        return redirect("/login")
    
    if request.method == 'POST':
        # Process the form data to add a new stock
        # Example: new_stock_data = request.form
        # Add the stock to the database or data source
        return redirect('/stock_explorer')

    return render_template('add_stock.html')

# define a route for the generate labels page
@app.route('/generate_labels', methods=['POST'])
def generate_labels():
    if not session.get("username"):
        return redirect("/login")
    
    selected_uids = request.form.get('selected_uids').split(',')
    blank_spaces = int(request.form.get('blank_spaces', 0))
    quantities = request.form.get('quantities').split(',')

    # get the user's initials
    username = session.get("username")
    user_initials = get_user_initials(username, client)

    # get the selected stocks
    stocks = get_user_stocks(username, client)
    stocks = stocks.get_all_records()
    selected_stocks = [stock for stock in stocks if str(stock['UniqueID']) in selected_uids]

    # duplicate the selected stocks based on the quantities
    selected_stocks = [stock for stock, quantity in zip(selected_stocks, quantities) for _ in range(int(quantity))]
    
    # generate the labels
    filename = datetime.now().strftime('%Y-%m-%d')
    generate_label_pdf(
        filename,
        user_initials, 
        selected_stocks, 
        blank_spaces, 
        len(selected_stocks)
    )
    
    pdf_file_path = '/static/generated_labels/{}.pdf'.format(filename)
    
    return redirect(pdf_file_path)

# define a route for the view stock page
@app.route('/view_stock/<unique_id>')
def view_stock(unique_id):
    if not session.get("username"):
        return redirect("/login")
    
    # get user name
    username = session.get("username")
    stock = None #get_stock_by_unique_id(unique_id, client)
    return render_template('view_stock.html', username=username, stock=stock)


# run the app
if __name__ == '__main__':
    app.run(debug=True)


