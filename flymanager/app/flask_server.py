# setup the main flask server

# each user is a assigned a single worksheet in the master google sheet with the name <username>_<password_hash>

# design goals:
# simple login page with a hash-based password that is stored in the server, provides access to the user's worksheet
# register page to create a new user, create a new worksheet in the master google sheet

# import the necessary packages

# external imports
from datetime import datetime
from flask import Flask, jsonify, render_template, request, redirect, Response, session, jsonify, send_file, flash, url_for
from werkzeug.utils import secure_filename
import io
from flask_cors import CORS
from flask_session import Session
from flask_socketio import SocketIO, emit
from fuzzywuzzy import fuzz
import hashlib
import os
import serial
import threading

# internal imports
from flymanager.utils.mongo import *
from flymanager.utils.labels import *
from flymanager.utils.scanner import *
from flymanager.utils.utils import *
from flymanager.utils.converter import *
from flymanager.utils.genetics import *

# setup dotenv
from dotenv import load_dotenv
load_dotenv()

# define the allowed file extensions
ALLOWED_EXTENSIONS = {'xlsx'}
def allowed_file(filename):
    return '.' in filename and \
           filename.rsplit('.', 1)[1].lower() in ALLOWED_EXTENSIONS

# setup the mongo db
client = create_mongo_client()
db = get_database(client)

# initialize our Flask application
app = Flask(__name__, template_folder="../templates", static_folder="../static")

# configure the session (local filesystem, timeout of 10 minutes)
app.secret_key = os.getenv("SECRET_KEY")

# set the session type to filesystem
app.config["SESSION_TYPE"] = "filesystem"
app.config["SESSION_PERMANENT"] = False
app.config["PERMANENT_SESSION_LIFETIME"] = 3600

# clear the session on restart
app.config["SESSION_FILE_DIR"] = "/tmp"
app.config["SESSION_FILE_THRESHOLD"] = 500
app.config["SESSION_FILE_MODE"] = 384

# set the upload folder
app.config['UPLOAD_FOLDER'] = os.getenv("UPLOAD_FOLDER")

# initialize the session
Session(app)

# initialize the socketio
socketio = SocketIO(app)

# enable CORS
CORS(app)

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
    users = list(get_all_users(db=db).keys())
    # if the request method is POST
    if request.method == "POST":
        # get the username and password
        username = request.form["username"]
        password = request.form["password"]
        # check if the user exists
        if username in users:
            # check if the password is correct
            if get_all_users(db=db)[username] == hashlib.shake_256((username+password).encode()).hexdigest(5):
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
        if add_user(username, password, initials, db=db):
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
    users = list(get_all_users(db=db).keys())
    if request.method == "POST":
        # get the username, master password and new password
        username = request.form["username"]
        master_password = request.form["master_password"]
        new_password = request.form["new_password"]
        # if the username is Master, give special instructions
        if username == "Master":
            return render_template("auth/forgot_password.html", users=users, message="Master password cannot be changed here. Run 'python -c 'import hashlib; print(hashlib.shake_256(('admin'+input()).encode()).hexdigest(5))' in the terminal to get the new master hash and update on the users sheet on the google sheet manually")
        # change the password
        if change_password(username, master_password, new_password, db=db):
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
    activities = get_user_activities(username, db)
    # keep last 5 activities
    activities = activities[-5:][::-1]
    return render_template("home.html", username=username, activities=activities)


### STOCK MANAGEMENT ROUTES ###


# define a route for the stock explorer page
@app.route('/stock_explorer', methods=['GET', 'POST'])
def stock():
    # check if the user is logged in
    if not session.get("username"):
        return redirect("/login")
    
    username = session.get("username")
    stocks = get_user_stocks(username, db)

    # sort by TrayID and TrayPosition
    stocks = sorted(stocks, key=lambda x: (x['TrayID'], int(x['TrayPosition'] if x['TrayPosition']!='' else 0)))

    # Extract unique values for filtering
    unique_values = {
        'Type': sorted(set(str(stock['Type']) for stock in stocks)),
        'TrayID': sorted(set(str(stock['TrayID']) for stock in stocks)),
        'Status': sorted(set(str(stock['Status']) for stock in stocks)),
        'FoodType': sorted(set(str(stock['FoodType']) for stock in stocks)),
        'Provenance': sorted(set(str(stock['Provenance']).split('/')[0] for stock in stocks))
    }

    if request.method == 'GET':
        # Check if there are filters stored in session
        filter_state = session.get('filter_state', {})

        # remove the 'No longer maintained' stocks from the list
        stocks = [stock for stock in stocks if str(stock['Status']) != 'No longer maintained']

        return render_template("stock_explorer.html", username=username, stocks=stocks, unique_values=unique_values, filter_state=filter_state)
    elif request.method == 'POST':
        if 'clear_filters' in request.form:
            session.pop('filter_state', None)
            return redirect('/stock_explorer')

        # Get filter values from request
        filter_type = str(request.form.get('filterType'))
        filter_tray_id = str(request.form.get('filterTrayID'))
        filter_status = str(request.form.get('filterStatus'))
        filter_food_type = str(request.form.get('filterFoodType'))
        filter_provenance = str(request.form.get('filterProvenance'))
        search_query = request.form.get('searchQuery')

        # Store filter state in session
        filter_state = {
            'filterType': filter_type,
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
        if filter_tray_id:
            filtered_stocks = [stock for stock in filtered_stocks if str(stock['TrayID']) == filter_tray_id]
        if filter_status:
            filtered_stocks = [stock for stock in filtered_stocks if str(stock['Status']) == filter_status]
        else:
            filtered_stocks = [stock for stock in filtered_stocks if str(stock['Status']) != 'No longer maintained']
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
                return fuzz.partial_ratio(search_string.lower(), search_query.lower()) > 80
            
            filtered_stocks = [stock for stock in filtered_stocks if match(stock)]

        # Recalculate unique values
        unique_values = {
            'Type': sorted(set(str(stock['Type']) for stock in filtered_stocks)),
            'TrayID': sorted(set(str(stock['TrayID']) for stock in filtered_stocks)),
            'Status': sorted(set(str(stock['Status']) for stock in filtered_stocks)),
            'FoodType': sorted(set(str(stock['FoodType']) for stock in filtered_stocks)),
            'Provenance': sorted(set(str(stock['Provenance']).split('/')[0] for stock in filtered_stocks))
        }

        return render_template("stock_explorer.html", username=username, stocks=filtered_stocks, unique_values=unique_values, filter_state=filter_state)


# define a route for the add stock page
@app.route('/add_stock', methods=['GET', 'POST'])
@app.route('/add_stock/<unique_id>', methods=['GET', 'POST'])
def add_stock(unique_id=None):
    if not session.get("username"):
        return redirect("/login")

    username = session.get("username")

    # Get metadata lists
    types = get_metadata('types', db)
    food_types = get_metadata('food_types', db)
    provenances = get_metadata('provenances', db)
    genesX = get_metadata('genesX', db)
    genes2 = get_metadata('genes2nd', db)
    genes3 = get_metadata('genes3rd', db)
    genes4 = get_metadata('genes4th', db)

    # If a unique_id is provided, fetch the stock data
    stock_data = {}
    if unique_id:
        stock = db['stocks'].find_one({"UniqueID": unique_id})
        if stock:
            stock_data = {
                'sourceType': 'INTERNAL',
                'sourceID': stock['UniqueID'],
                'genotype': stock['Genotype'],
                'name': stock['Name'],
                'altReference': stock.get('AltReference', ''),
                'type': stock['Type'],
                'foodType': stock.get('FoodType', 'Molasses'),
                'status': stock['Status'],
                'seriesID': stock['SeriesID'],
                'replicateID': increment_replicate_id(stock['ReplicateID']),
                'vialLifetime': stock.get('VialLifetime', 14),
                'flipFrequency': stock.get('FlipFrequency', 7),
                'comments': stock.get('Comments', ''),
                'provenance': stock.get('Provenance', '')
            }
        else:
            return jsonify({"error": "Stock not found."}), 404
    
    if request.method == 'POST':

        # Handle chromosome data
        genesX_input = clean_tagify_data(request.form.get('genotypeX'))
        for gene in genesX_input:
            if gene not in genesX:
                add_metadata('genesX', gene, db)
        genesX_input = "/".join(genesX_input) if len(genesX_input) > 0 else ""

        genes2_input = clean_tagify_data(request.form.get('genotype2'))
        for gene in genes2_input:
            if gene not in genes2:
                add_metadata('genes2nd', gene, db)
        genes2_input = "/".join(genes2_input) if len(genes2_input) > 0 else ""

        genes3_input = clean_tagify_data(request.form.get('genotype3'))
        for gene in genes3_input:
            if gene not in genes3:
                add_metadata('genes3rd', gene, db)
        genes3_input = "/".join(genes3_input) if len(genes3_input) > 0 else ""

        genes4_input = clean_tagify_data(request.form.get('genotype4'))
        for gene in genes4_input:
            if gene not in genes4:
                add_metadata('genes4th', gene, db)
        genes4_input = "/".join(genes4_input) if len(genes4_input) > 0 else ""

        # Handle other fields
        type_input = clean_tagify_data(request.form.get('type'))[0]
        if type_input not in types:
            add_metadata('types', type_input, db)

        food_type_input = clean_tagify_data(request.form.get('foodType'))[0]
        if food_type_input not in food_types:
            add_metadata('food_types', food_type_input, db)

        provenance_input = clean_tagify_data(request.form.get('provenance'))
        for provenance in provenance_input:
            if provenance not in provenances:
                add_metadata('provenances', provenance, db)
        provenance_input = "/".join(provenance_input) if len(provenance_input) > 1 else provenance_input[0]

        # Collect form data
        new_stock_data = {
            'SourceID': request.form.get('sourceID'),
            'Genotype': genesX_input + ";" + genes2_input + ";" + genes3_input + ";" + genes4_input,
            'Name': request.form.get('name'),
            'AltReference': request.form.get('altReference'),
            'Type': type_input,
            'SeriesID': request.form.get('seriesID'),
            'ReplicateID': request.form.get('replicateID'),
            'TrayID': request.form.get('trayID'),
            'TrayPosition': request.form.get('trayPosition'),
            'Status': request.form.get('status'),
            'FoodType': food_type_input,
            'Provenance': provenance_input,
            'VialLifetime': request.form.get('vialLifetime', 14),
            'FlipFrequency': request.form.get('flipFrequency', 7),
            'Comments': request.form.get('comments')
        }

        # remove empty fields
        new_stock_data = {k: v for k, v in new_stock_data.items() if v}

        # Add stock to the user's sheet
        success, uid_or_message = add_to_stock(username, new_stock_data, db)

        if success:
            return redirect('/stock_explorer')
        else:
            # Handle error (e.g., QC failure)
            return render_template('add_stock.html', error=uid_or_message, username=username, 
                                   types=types, food_types=food_types, provenances=provenances, 
                                   genesX=genesX, genes2=genes2, genes3=genes3, 
                                   genes4=genes4, stock_data=stock_data)

    return render_template('add_stock.html', username=username, types=types, food_types=food_types, 
                           provenances=provenances, genesX=genesX, genes2=genes2, 
                           genes3=genes3, genes4=genes4, stock_data=stock_data)

@app.route('/get_internal_stock/<unique_id>', methods=['GET'])
def get_internal_stock(unique_id):
    """
    Fetches stock data for an internal stock using its Source ID (unique_id).
    
    Parameters:
    unique_id: str
        The Source ID of the internal stock.
    
    Returns:
    JSON object containing stock details or an error message.
    """
    # Check if the user is logged in
    if not session.get("username"):
        return jsonify({"error": "User not logged in."}), 401
    
    username = session.get("username")
    
    # Check if stock exists in internal user's stock collection
    stock = db['stocks'].find_one({"UniqueID": unique_id})
    
    if not stock:
        return jsonify({"error": "Stock not found in INTERNAL records."}), 404
    
    # get the user who owns the stock
    user = stock.get('User')

    # Append provenance to the stock data if its a different user's stock
    if user != username:
        stock['Provenance'] = username + "@" + os.getenv("ORG_ABV") + '/' + stock.get('Provenance', "")

    # Structure the response to include the necessary fields
    stock_data = {
        # "genotype": qc_genotype(stock.get('Genotype'))[1],
        "genotype": stock.get('Genotype'),
        "name": stock.get('Name'),
        "altReference": stock.get('AltReference', ""),
        "type": stock.get('Type', ""),
        "foodType": stock.get('FoodType', ""),
        "provenance": stock.get('Provenance', ""),
        "status": stock.get('Status'),
        "vialLifetime": stock.get('VialLifetime', ""),
        "flipFrequency": stock.get('FlipFrequency', ""),
    }
    
    return jsonify(stock_data), 200

@app.route('/get_bloomington_genotype/<stock_id>', methods=['GET'])
def get_bloomington_genotype(stock_id):
    """
    Fetches genotype data for a BDSC stock using its Source ID (stock_id).
    
    Parameters:
    stock_id: str
        The Source ID of the BDSC stock.
    
    Returns:
    JSON object containing genotype details or an error message.
    """
    genotype, error = get_stock_genotype(stock_id)
    
    if error:
        return jsonify({"error": error}), 400

    return jsonify({"genotype": genotype}), 200

@app.route('/autopopulate_series_replicate', methods=['POST'])
def autopopulate_series_replicate():
    """
    Auto-populates Series ID and Replicate ID based on the user's existing stocks and genotype.
    """
    if not session.get("username"):
        return jsonify({"error": "User not logged in."}), 401
    
    username = session.get("username")
    genotype = request.json.get('genotype')

    # split the genotype into its components
    genotype = genotype.split(';')

    # clean each genotype component using clean_tagify_data
    genotype = ["/".join(clean_tagify_data(gene)) for gene in genotype]

    # join the cleaned genotype components back together
    genotype = "; ".join(genotype)
    
    # QC the genotype
    qc_result = qc_genotype(genotype)
    if qc_result[0] == False:
        return jsonify({"error": qc_result[1]}), 400
    genotype = qc_result[1]

    if not genotype:
        return jsonify({"error": "Genotype is required."}), 400

    # find the number of stocks with the same genotype
    count = db['stocks'].count_documents({"User": username, "Genotype": genotype})
    
    if count == 0:
        # If no stocks with the same genotype, find the overall highest SeriesID for the user
        highest_series = db['stocks'].find_one({"User": username}, sort=[("SeriesID", -1)])
        if highest_series:
            series_id = int(highest_series.get('SeriesID', '0')) + 1
        else:
            series_id = 1

        return jsonify({"seriesID": str(series_id), "replicateID": "a"}), 200
    
    # Fetch all stocks with the same genotype for the user
    user_stocks = db['stocks'].find({"User": username, "Genotype": genotype})

    # Find highest SeriesID and ReplicateID
    max_series = 0
    max_replicate = 'a'

    for stock in user_stocks:
        series_id = int(stock.get('SeriesID', '0'))
        replicate_id = stock.get('ReplicateID', 'a')

        # Update max seriesID
        if series_id > max_series:
            max_series = series_id
        
        # If this is the max series, check for the highest replicate
        if series_id == max_series and replicate_id > max_replicate:
            max_replicate = replicate_id

    # If exact genotype exists, increment replicate ID
    next_replicate = increment_replicate_id(max_replicate)
    
    return jsonify({"seriesID": str(max_series), "replicateID": next_replicate}), 200

def increment_replicate_id(replicate_id):
    """
    Increments the replicate ID by following alphabetical order (a, b, ..., z, aa, ab, ..., az, ba, ..., zz, aaa, etc.).
    
    Parameters:
    replicate_id: str
        The current replicate ID.
    
    Returns:
    str
        The next replicate ID in sequence.
    """

    # If replicate_id is empty, return 'a'
    if replicate_id == "":
        return "a"
    
    def incr_chr(c):
        """
        Helper function that increments a single character, handling wraparound from 'z' to 'a'.
        
        Returns:
        (carry, next_char) where:
        - carry: 1 if wrapping from 'z' to 'a', otherwise 0.
        - next_char: the next character in sequence.
        """
        if c == 'z':
            return 1, 'a'  # wrap from 'z' to 'a' with carry 1
        else:
            return 0, chr(ord(c) + 1)  # normal increment with no carry
    
    # Convert the replicate_id into a list of characters
    lst = list(replicate_id)
    result = []
    
    # Loop through the list from the rightmost character (the least significant)
    while lst:
        carry, next_ = incr_chr(lst.pop())  # increment the last letter in the list
        result.append(next_)                # add incremented character to the result

        if not carry:                       # if no carry, we are done
            break
        if not lst:                         # if the list is empty but we still have a carry, prepend 'a'
            result.append('a')
    
    result += lst[::-1]                     # append the remaining characters (if any) in reverse order
    return ''.join(result[::-1])            # convert list back to string in reverse order

# define a route for the generate labels page
@app.route('/generate_stock_labels', methods=['POST'])
def generate_stock_labels():
    if not session.get("username"):
        return redirect("/login")
    
    selected_uids = request.form.get('selected_uids').split(',')
    blank_spaces = int(request.form.get('blank_spaces', 0))
    quantities = request.form.get('quantities').split(',')

    # get the user's initials
    username = session.get("username")
    user_initials = get_user_initials(username, db)

    # get the selected stocks
    stocks = get_user_stocks(username, db)
    selected_stocks = [stock for stock in stocks if str(stock['UniqueID']) in selected_uids]

    # Sort the selected stocks by TrayID and TrayPosition
    selected_stocks = sorted(selected_stocks, key=lambda x: (x['TrayID'], int(x['TrayPosition'] if x['TrayPosition']!='' else 0)))

    # duplicate the selected stocks based on the quantities
    selected_stocks = [stock for stock, quantity in zip(selected_stocks, quantities) for _ in range(int(quantity))]
    
    # generate the labels
    filename = datetime.now().strftime('%Y-%m-%d')
    generate_stock_label_pdf(
        filename,
        user_initials, 
        selected_stocks, 
        blank_spaces, 
        len(selected_stocks)
    )
    
    pdf_file_path = '/static/generated_labels/{}.pdf'.format(filename)

    # write the activity to the user's activity sheet
    write_activity(username, 'Generated labels for {} stocks'.format(len(selected_stocks)), db)
    
    return redirect(pdf_file_path)

# define a route for the generate labels page
@app.route('/generate_cross_labels', methods=['POST'])
def generate_cross_labels():
    if not session.get("username"):
        return redirect("/login")
    
    selected_uids = request.form.get('selected_uids').split(',')
    blank_spaces = int(request.form.get('blank_spaces', 0))
    quantities = request.form.get('quantities').split(',')

    # get the user's initials
    username = session.get("username")
    user_initials = get_user_initials(username, db)

    # get the selected stocks
    crosses = get_user_crosses(username, db)
    selected_crosses = [cross for cross in crosses if str(cross['UniqueID']) in selected_uids]

    # Sort the selected stocks by TrayID and TrayPosition
    selected_crosses = sorted(selected_crosses, key=lambda x: (x['TrayID'], int(x['TrayPosition'] if x['TrayPosition']!='' else 0)))

    # duplicate the selected stocks based on the quantities
    selected_crosses = [cross for cross, quantity in zip(selected_crosses, quantities) for _ in range(int(quantity))]
    
    # generate the labels
    filename = datetime.now().strftime('%Y-%m-%d')
    generate_cross_label_pdf(
        filename,
        user_initials, 
        selected_crosses, 
        blank_spaces, 
        len(selected_crosses)
    )
    
    pdf_file_path = '/static/generated_labels/{}.pdf'.format(filename)

    # write the activity to the user's activity sheet
    write_activity(username, 'Generated labels for {} stocks'.format(len(selected_crosses)), db)
    
    return redirect(pdf_file_path)

# define a route for the view stock page
@app.route('/view_stock/<unique_id>')
def view_stock(unique_id):
    if not session.get("username"):
        return redirect("/login")
    
    # get user name
    username = session.get("username")
    stock = get_stock(username, unique_id, db)
    return render_template('view_stock.html', username=username, stock=stock)


### CROSS MANAGEMENT ROUTES ###

# define a route for the cross explorer page
@app.route('/cross_explorer', methods=['GET', 'POST'])
def cross():
    # check if the user is logged in
    if not session.get("username"):
        return redirect("/login")
    
    username = session.get("username")
    crosses = get_user_crosses(username, db)

    # nested sort by TrayID and TrayPosition
    crosses = sorted(crosses, key=lambda x: (str(x['TrayID']), int(x['TrayPosition'] if x['TrayPosition']!='' else 0)))

    # Extract unique values for filtering
    unique_values = {
        'MaleGenotype': sorted(set(str(cross['MaleGenotype']) for cross in crosses)),
        'FemaleGenotype': sorted(set(str(cross['FemaleGenotype']) for cross in crosses)),
        'TrayID': sorted(set(str(cross['TrayID']) for cross in crosses)),
        'Status': sorted(set(str(cross['Status']) for cross in crosses)),
        'FoodType': sorted(set(str(cross['FoodType']) for cross in crosses)),
    }

    if request.method == 'GET':
        # Check if there are filters stored in session
        filter_state = session.get('filter_state', {})
        # remove the 'No longer maintained' crosses from the list
        crosses = [cross for cross in crosses if str(cross['Status']) != 'No longer maintained']
        return render_template("cross_explorer.html", username=username, crosses=crosses, unique_values=unique_values, filter_state=filter_state)
    elif request.method == 'POST':
        if 'clear_filters' in request.form:
            session.pop('filter_state', None)
            return redirect('/cross_explorer')

        # Get filter values from request
        filter_MaleGenotype = str(request.form.get('filterMaleGenotype'))
        filter_FemaleGenotype = str(request.form.get('filterFemaleGenotype'))
        filter_tray_id = str(request.form.get('filterTrayID'))
        filter_status = str(request.form.get('filterStatus'))
        filter_food_type = str(request.form.get('filterFoodType'))
        search_query = request.form.get('searchQuery')

        # Store filter state in session
        filter_state = {
            'filterMaleGenotype': filter_MaleGenotype,
            'filterFemaleGenotype': filter_FemaleGenotype,
            'filterTrayID': filter_tray_id,
            'filterStatus': filter_status,
            'filterFoodType': filter_food_type,
            'searchQuery': search_query
        }
        session['filter_state'] = filter_state

        # Apply filters
        filtered_crosses = crosses
        if filter_MaleGenotype:
            filtered_crosses = [cross for cross in filtered_crosses if str(cross['MaleGenotype']) == filter_MaleGenotype]
        if filter_FemaleGenotype:
            filtered_crosses = [cross for cross in filtered_crosses if str(cross['FemaleGenotype']) == filter_FemaleGenotype]
        if filter_tray_id:
            filtered_crosses = [cross for cross in filtered_crosses if str(cross['TrayID']) == filter_tray_id]
        if filter_status:
            filtered_crosses = [cross for cross in filtered_crosses if str(cross['Status']) == filter_status]
        else:
            filtered_crosses = [cross for cross in filtered_crosses if str(cross['Status']) != 'No longer maintained']
        if filter_food_type:
            filtered_crosses = [cross for cross in filtered_crosses if str(cross['FoodType']) == filter_food_type]
        
        # Apply search
        if search_query:
            def match(cross):
                search_fields = [
                    cross['Name'],
                    cross['MaleGenotype'],
                    cross['FemaleGenotype'],
                    cross['TrayID'],
                    cross['TrayPosition'],
                    cross['Comments']
                ]
                # combine all fields into a single string
                search_string = ' '.join(str(field) for field in search_fields)
                # find if the search query is a substring of the search string
                return fuzz.partial_ratio(search_string, search_query) > 80
            
            filtered_crosses = [cross for cross in filtered_crosses if match(stock)]

        # Recalculate unique values
        unique_values = {
            'MaleGenotype': sorted(set(str(cross['MaleGenotype']) for cross in filtered_crosses)),
            'FemaleGenotype': sorted(set(str(cross['FemaleGenotype']) for cross in filtered_crosses)),
            'TrayID': sorted(set(str(cross['TrayID']) for cross in filtered_crosses)),
            'Status': sorted(set(str(cross['Status']) for cross in filtered_crosses)),
            'FoodType': sorted(set(str(cross['FoodType']) for cross in filtered_crosses))
        }

        return render_template("cross_explorer.html", username=username, crosses=filtered_crosses, unique_values=unique_values, filter_state=filter_state)
    
# define a route for the add cross page
@app.route('/add_cross', methods=['GET', 'POST'])
def add_cross():
    if not session.get("username"):
        return redirect("/login")

    username = session.get("username")
    ports = get_available_ports()

    # get metadata lists
    food_types = get_metadata('food_types', db)
    genotypes = get_all_genotypes(username, db)

    if request.method == 'POST':
        # Collect form data
        new_cross_data = {
            'MaleUniqueID': request.form.get('maleUniqueID'),
            'FemaleUniqueID': request.form.get('femaleUniqueID'),
            'MaleGenotype': request.form.get('maleGenotype').split('"')[-2],
            'FemaleGenotype': request.form.get('femaleGenotype').split('"')[-2],
            'TrayID': request.form.get('trayID'),
            'TrayPosition': request.form.get('trayPosition'),
            'Status': request.form.get('status'),
            'FoodType': request.form.get('foodType'),
            'Name': request.form.get('name'),
            'Comments': request.form.get('comments')
        }

        # remove empty fields
        new_cross_data = {k: v for k, v in new_cross_data.items() if v}

        # Add cross to the user's sheet
        success, uid_or_message = add_to_cross(username, new_cross_data, db)

        if success:
            return redirect('/cross_explorer')
        else:
            # Handle error (e.g., QC failure)
            return render_template('add_cross.html', error=uid_or_message, username=username,
                                   food_types=food_types, genotypes=genotypes, ports=ports)

    return render_template('add_cross.html', username=username,
                           food_types=food_types, genotypes=genotypes, ports=ports)

# Route to fetch genotype based on Unique ID
@app.route('/get_genotype/<unique_id>')
def get_genotype(unique_id):
    username = session.get("username")
    stock = db['stocks'].find_one({"UniqueID": unique_id, "User": username})
    if stock:
        return jsonify({'genotype': stock['Genotype']})
    else:
        return jsonify({'genotype': ''}), 404
    
from urllib.parse import unquote

@app.route('/get_uids/<genotype>', methods=['GET'])
def get_uids(genotype):
    decoded_genotype = unquote(unquote(genotype))
    
    uids = db['stocks'].find({"Genotype": decoded_genotype}, {"UniqueID": 1})
    uid_list = [doc['UniqueID'] for doc in uids]
    
    return jsonify({'uids': uid_list})


### FLY FLIPPING ROUTES ###

# define a route for the flip stock page
@app.route('/flip', methods=['GET'])
def flip():
    if not session.get("username"):
        return redirect("/login")
    
    username = session.get("username")
    ports = get_available_ports()
    return render_template('flip.html', username=username, ports=ports)

# Dictionary to store active threads
active_threads = {}

def scan_qr_code(port_index, ports, username, thread_id, baudrate=9600, size=11):
    # Start listening for QR code scan
    port_device = ports[port_index].device
    port = serial.Serial(port_device, baudrate)
    
    emergency_stop = False

    while True:

        # loop to passively listen to the port (until a carriage return is received)
        keystrokes = []
        while True:
            
            if active_threads[thread_id][1] == False:
                print('Stopping thread:', thread_id)
                emergency_stop = True
                break

            data = port.read().decode("utf-8")
            if data:
                keystrokes.append(data)
            if data == "\r":
                qr_code = "".join(keystrokes)
                if len(qr_code) == size:
                    break
                else:
                    keystrokes = []

        if emergency_stop:
            break

        # Check if the UID exists in the stocks
        print('Scanned QR code:', qr_code)
        uid = qr_code.strip()

        # Check if the UID exists in the stocks
        stocks = get_user_stocks(username, db)
        matching_stock = next((stock for stock in stocks if stock['UniqueID'] == uid), None)

        # Check if the UID exists in the crosses
        crosses = get_user_crosses(username, db)
        matching_cross = next((cross for cross in crosses if cross['UniqueID'] == uid), None)

        if matching_stock and not matching_cross:
            print('Stock scanned:', uid)
            socketio.emit('stock_scanned', {
                'uniqueID': uid,
                'seriesID': matching_stock['SeriesID'],
                'replicateID': matching_stock['ReplicateID'],
                'trayID': matching_stock['TrayID'],
                'trayPosition': matching_stock['TrayPosition'],
                'foodType': matching_stock['FoodType'],
                'provenance': matching_stock['Provenance'],
                'name': matching_stock['Name'],
                'altReference': matching_stock['AltReference'],
                'genotype': matching_stock['Genotype'],
                'status': matching_stock['Status']
            })
        elif matching_cross and not matching_stock:
            print('Cross scanned:', uid)
            socketio.emit('cross_scanned', {
                'uniqueID': uid,
                'maleGenotype': matching_cross['MaleGenotype'],
                'femaleGenotype': matching_cross['FemaleGenotype'],
                'trayID': matching_cross['TrayID'],
                'trayPosition': matching_cross['TrayPosition'],
                'foodType': matching_cross['FoodType'],
                'name': matching_cross['Name'],
                'status': matching_cross['Status']
            })
        else:
            socketio.emit('qr_not_recognized')

@app.route('/start_scan', methods=['POST'])
def start_scan():
    data = request.json
    port_index = int(data['port_index'])
    ports = get_available_ports()
    username = session.get("username")

    # Create a unique thread ID
    thread_id = threading.get_ident()

    # Start a thread to handle the QR code scanning
    scan_thread = threading.Thread(target=scan_qr_code, args=(port_index, ports, username, thread_id))
    active_threads[thread_id] = (scan_thread,True)
    scan_thread.start()
    print('Started scanning thread:', thread_id)

    return jsonify({'success': True, 'message': 'Started scanning', 'thread_id': thread_id})

@app.route('/stop_scan', methods=['POST'])
def stop_scan():
    data = request.json
    thread_id = data.get('thread_id')

    if thread_id in active_threads:
        # set the thread to stop
        active_threads[thread_id] = (active_threads[thread_id][0], False)
        # join the thread
        active_threads[thread_id][0].join()
        # remove the thread from the active threads
        active_threads.pop(thread_id)

    return jsonify({'success': True, 'message': 'Stopped scanning'})

@app.route('/flip_vial', methods=['POST'])
def handle_flip_vial():
    if not session.get("username"):
        return redirect("/login")
    username = session.get("username")
    
    data = request.json
    status = data.get('status')
    flip_time = data.get('flipTime')
    comment = data.get('comment') if data.get('comment') else None
    uid = data.get('uniqueID')

    # Check if the UID exists in the stocks
    stocks = get_user_stocks(username, db)
    matching_stock = next((stock for stock in stocks if stock['UniqueID'] == uid), None)

    # Check if the UID exists in the crosses
    crosses = get_user_crosses(username, db)
    matching_cross = next((cross for cross in crosses if cross['UniqueID'] == uid), None)

    if matching_stock and not matching_cross:
        print('Flipping stock:', uid)
        flip_stock(username, uid, db, flip_time, new_status=status, added_comment=comment)
        return jsonify({'message': 'Stock flipped successfully!'})
    elif matching_cross and not matching_stock:
        print('Flipping cross:', uid)
        flip_cross(username, uid, db, flip_time, new_status=status, added_comment=comment)
        return jsonify({'message': 'Cross flipped successfully!'})
    else:
        return jsonify({'message': 'UID not recognized!'})


@app.route('/download_data', methods=['GET'])
def download_data():
    if not session.get("username"):
        return redirect("/login")

    # Create an in-memory bytes buffer
    output = io.BytesIO()
    
    try:
        # Generate Excel file from MongoDB data
        mongo_to_xls(db, output)
        
        # Set the buffer's position to the beginning
        output.seek(0)
        
        # Prepare the response with appropriate headers
        return send_file(
            output,
            mimetype='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet',
            as_attachment=True,
            download_name='fly_manager_data.xlsx'
        )
    except Exception as e:
        print(f"Error generating Excel file: {e}")
        return Response(
            f"An error occurred while generating the Excel file: {e}",
            status=500
        )

@app.route('/upload_data', methods=['GET', 'POST'])
def upload_data():
    if not session.get("username"):
        return redirect("/login")

    if request.method == 'POST':
        # Check if the post request has the file part
        if 'file' not in request.files:
            flash('No file part')
            return redirect(request.url)
        
        file = request.files['file']
        
        # If no file is selected
        if file.filename == '':
            flash('No selected file')
            return redirect(request.url)
        
        # Validate file
        if file and allowed_file(file.filename):
            filename = secure_filename(file.filename)
            file_path = os.path.join(app.config['UPLOAD_FOLDER'], filename)
            
            try:
                # Save the uploaded file temporarily
                file.save(file_path)
                
                # Process the Excel file and update MongoDB
                xls_to_mongo(file_path, db)
                
                # Remove the file after processing
                os.remove(file_path)
                
                flash('Data successfully uploaded and updated.')
                return redirect("/home")
            except Exception as e:
                print(f"Error processing Excel file: {e}")
                flash(f'An error occurred while processing the file: {e}')
                return redirect(request.url)
        else:
            flash('Allowed file type is .xlsx')
            return redirect(request.url)
    
    return render_template('upload_data.html')

# run the app
if __name__ == '__main__':
    socketio.run(app, debug=True)


