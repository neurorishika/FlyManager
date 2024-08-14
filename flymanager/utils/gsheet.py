# A collection of functions to interact with google sheets for the Fly Manager app
import gspread
from oauth2client.service_account import ServiceAccountCredentials
from hashlib import shake_256
import datetime
import os
from .genetics import qc_genotype

def create_client():
    """
    Create a client for the google sheet
    Returns:
    client: gspread.Client
        the client for the google sheet
    """
    #Authorize the API
    scope = [
        'https://www.googleapis.com/auth/drive',
        'https://www.googleapis.com/auth/drive.file'
        ]
    file_name = 'data/client.key'
    creds = ServiceAccountCredentials.from_json_keyfile_name(file_name,scope)
    client = gspread.authorize(creds)
    return client

def write_activity(user, activity, client):
    """
    Write an activity to the google sheet
    Parameters:
    user: str
        the username of the user
    activity: str
        the activity of the user
    client: gspread.Client
        the client for the google sheet
    """
    # get timestamp
    timestamp = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    # open the activity worksheet
    activity_sheet = client.open('Ruta Lab Fly Manager').worksheet("activity")
    # append the activity
    activity_sheet.append_row([user, timestamp, activity])

def get_all_users(client):
    """
    Get all the users from the google sheet'
    Parameters:
    client: gspread.Client
        the client for the google sheet
    Returns:
    clients: dict
        a dictionary of all the users and their hashed passwords
    """
    # open the users worksheet
    users = client.open('Ruta Lab Fly Manager').worksheet('users')

    # get all User:Password pairs from the Users and Passwords columns
    clients = {}
    # find the columns for the user and password
    user_col = users.find("Username").col
    password_col = users.find("Password").col
    # get all the users and passwords
    for i in range(2, users.row_count+1):
        user = users.cell(i, user_col).value
        password = users.cell(i, password_col).value
        clients[user] = password
    return clients

def get_user_initials(user, client):
    """
    Get the initials of the user
    Parameters:
    user: str
        the username of the user
    client: gspread.Client
        the client for the google sheet
    Returns:
    initials: str
        the initials of the user
    """
    # open the users worksheet
    users = client.open('Ruta Lab Fly Manager').worksheet('users')
    # find the columns for the user and password
    user_col = users.find("Username").col
    initials_col = users.find("Initials").col
    # find the row for the user
    user_row = users.find(user).row
    # get the initials
    initials = users.cell(user_row, initials_col).value
    return initials

def get_user_stocks(user, client):
    """
    Connect to the google sheet for the given user
    Parameters:
    user: str
        the username of the user
    client: gspread.Client
        the client for the google sheet
    Returns:
    sheet: gspread.Worksheet
        the worksheet for the user
    """
    sheet = client.open('Ruta Lab Fly Manager').worksheet("{}_Stock".format(user))
    return sheet

def get_user_crosses(user, client):
    """
    Connect to the google sheet for the given user
    Parameters:
    user: str
        the username of the user
    client: gspread.Client
        the client for the google sheet
    Returns:
    sheet: gspread.Worksheet
        the worksheet for the user
    """
    sheet = client.open('Ruta Lab Fly Manager').worksheet("{}_Cross".format(user))
    return sheet

def get_user_activities(user, client):
    """
    Connect to the google sheet for the given user
    Parameters:
    user: str
        the username of the user
    client: gspread.Client
        the client for the google sheet
    Returns:
    sheet: gspread.Worksheet
        the worksheet for the user
    """
    sheet = client.open('Ruta Lab Fly Manager').worksheet("activity")
    # filter the activities for the user
    activities = sheet.get_all_records()
    user_activities = []
    for activity in activities:
        if activity["username"] == user:
            user_activities.append(activity)
    return user_activities

def add_user(user, password, initials, client):
    """
    Add a user to the google sheet
    Parameters:
    user: str
        the username of the user
    password: str
        the password of the user
    initials: str
        the initials of the user
    client: gspread.Client
        the client for the google sheet
    Returns:
    bool
        True if the user was added, False otherwise
    """
    # get all the users
    clients = get_all_users(client=client)
    users = list(clients.keys())
    # check if the user exists
    if user not in users:
        # add the user to the users
        clients[user] = shake_256((user+password).encode()).hexdigest(5)
        # open the users worksheet
        users = client.open('Ruta Lab Fly Manager').worksheet('users')
        # copy from the template
        client.open('Ruta Lab Fly Manager').worksheet('template(stock)').duplicate(new_sheet_name="{}_Stock".format(user))
        client.open('Ruta Lab Fly Manager').worksheet('template(cross)').duplicate(new_sheet_name="{}_Cross".format(user))
        # add the user to the worksheet (only if everything else was successful)
        users.append_row([user, shake_256((user+password).encode()).hexdigest(5), initials])
        # write the activity
        write_activity(user, "User added", client)
        return True
    return False

def change_password(user, master_password, new_password, client):
    """
    Change the password of the user
    Parameters:
    user: str
        the username of the user
    master_password: str
        the master password
    new_password: str
        the new password of the user
    client: gspread.Client
        the client for the google sheet
    """
    # get all the users
    clients = get_all_users(client=client)
    users = list(clients.keys())
    # check if the user exists
    if user in users:
        # check if the master password is correct
        if shake_256(("admin"+master_password).encode()).hexdigest(5) == clients["Master"]:
            # change the password of the user on the users worksheet
            # open the users worksheet
            users = client.open('Ruta Lab Fly Manager').worksheet('users')
            # find the columns for the user and password
            user_col = users.find("Username").col
            password_col = users.find("Password").col
            # find the row for the user
            user_row = users.find(user).row
            # update the password
            users.update_cell(user_row, password_col, shake_256((user+new_password).encode()).hexdigest(5))
            # write the activity
            write_activity(user, "Password changed", client)
            return True
    return False

### METHODS FOR STOCKS

def flip_stock(user, uid, client, timestamp, new_status=None, added_comment=None):
    """
    Flip the status of the stock
    Parameters:
    user: str
        the username of the user
    uid: str
        the unique identifier of the stock
    client: gspread.Client
        the client for the google sheet
    timestamp: str
        the special timestamp to use
    new_status: str
        the new status of the stock
    added_comment: str
        the comment to add to the stock
    """
    # get the user's stock sheet
    stock = get_user_stocks(user, client)
    # find the row of the stock
    stock_row = stock.find(uid).row
    # find the relevant columns
    last_flipped_col = stock.find("LastFlipDate").col
    flip_log_col = stock.find("FlipLog").col

    # update the last flipped date and append to the flip log
    ts = timestamp.replace('T', ' ')
    stock.update_cell(stock_row, last_flipped_col, ts)
    flip_log = stock.cell(stock_row, flip_log_col).value
    stock.update_cell(stock_row, flip_log_col, ts + "; " + flip_log if flip_log else ts)

    if new_status is not None or added_comment is not None:
        data_modified_col = stock.find("DataModifiedDate").col
        data_modified_log_col = stock.find("ModificationLog").col
        data_modified_log = stock.cell(stock_row, data_modified_log_col).value

    if new_status is not None:
        # update the status
        status_col = stock.find("Status").col
        # get the current status
        current_status = stock.cell(stock_row, status_col).value
        # check if the status is different
        if current_status != new_status:
            stock.update_cell(stock_row, status_col, new_status)
            # update the data modified date
            stock.update_cell(stock_row, data_modified_col, ts)
            # append to the data modified log
            text = "{} : Status changed from {} to {}".format(ts, current_status, new_status)
            stock.update_cell(stock_row, data_modified_log_col, text + "; " + data_modified_log if data_modified_log else text)
    
    if added_comment is not None:
        # update the comments
        comments_col = stock.find("Comments").col
        # get the current comments
        current_comments = stock.cell(stock_row, comments_col).value
        new_comments = added_comment + "; " + current_comments if current_comments else added_comment
        stock.update_cell(stock_row, comments_col, new_comments)
        # update the data modified date
        stock.update_cell(stock_row, data_modified_col, ts)
        # append to the data modified log
        text = "{} : Comments added: {}".format(ts, added_comment)
        stock.update_cell(stock_row, data_modified_log_col, text + "; " + data_modified_log if data_modified_log else text)


def add_to_stock(user, properties, client):
    """
    Add a stock to the user's stock sheet
    Parameters:
    user: str
        the username of the user
    properties: dict
        the properties of the stock
        Properties:
            SourceID (required)
            Genotype (required)
            Name (required)
            AltReference (optional)
            Type (required)
            SeriesID (required)
            ReplicateID (required)
            TrayID (optional)
            TrayPosition (optional)
            Status (required)
            FoodType (optional)
            Provenance (required)
            Comments (optional)
    client: gspread.Client
        the client for the google sheet
    Returns:
    bool
        True if the stock was added, False otherwise
    uid: str
        the unique identifier of the stock
    Note the Stock sheet has the following columns:
    UID, SourceID, Genotype, Name, AltReference, Type, SeriesID, ReplicateID, TrayID, TrayPosition, Status, FoodType, Provenance, Comments, CreationDate, LastFlipDate, FlipLog, DataModifiedDate, ModificationLog
    """
    assert "SourceID" in properties, "SourceID is required"
    assert "Genotype" in properties, "Genotype is required"
    assert "Name" in properties, "Name is required"
    assert "Type" in properties, "Type is required"
    assert "SeriesID" in properties, "SeriesID is required"
    assert "ReplicateID" in properties, "ReplicateID is required"
    assert "Status" in properties, "Status is required"

    # make sure genotype meets the qc
    qc, genotype = qc_genotype(properties["Genotype"])

    if not qc:
        return False, genotype

    # get the user's stock sheet
    stock = get_user_stocks(user, client)
    # create UID as a hash of the (User+ Genotype + SeriesID + ReplicateID)
    uid = str(user) + str(properties["Genotype"]) + str(properties["SeriesID"]) + str(properties["ReplicateID"])
    uid = shake_256(uid.encode()).hexdigest(5)
    # get creation timestamp
    timestamp = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    # create the row
    keys = ["SourceID", "Genotype", "Name", "AltReference", "Type", "SeriesID", "ReplicateID", "TrayID", "TrayPosition", "Status", "FoodType", "Provenance", "Comments"]
    row = [uid]
    for key in keys:
        if key == "Genotype":
            row.append(genotype)
        elif key in properties:
            row.append(properties[key])
        else:
            row.append("")
    # add CreationDate, LastFlipDate, FlipLog, DataModifiedDate, ModificationLog
    row.append(timestamp)
    row.append(timestamp)
    row.append(timestamp)
    row.append(timestamp)
    row.append("{} : Stock created".format(timestamp))
    # add the row
    stock.append_row(row)
    return True, uid

def get_stock(user, uid, client, admin_include=False):
    """
    Get the stock from the user's stock sheet
    Parameters:
    user: str
        the username of the user
    uid: str
        the unique identifier of the stock
    client: gspread.Client
        the client for the google sheet
    admin_include: bool
        whether to include admin sheets as well
    Returns:
    stock: dict
        the properties of the stock
    """
    # get the user's stock sheet
    stock = get_user_stocks(user, client)
    if admin_include and user != "admin":
        # get the admin stock sheet
        admin_stock = get_user_stocks("admin", client)
        try:
            stock_row = stock.find(uid).row
        except:
            stock_row = admin_stock.find(uid).row
    else:
        try:
            stock_row = stock.find(uid).row
        except:
            return None
    # get the stock properties
    stock_dict = {}
    for prop in stock.row_values(1):
        stock_dict[prop] = stock.cell(stock_row, stock.find(prop).col).value
    return stock_dict

### METHODS FOR METADATA
def get_types(client):
    """
    Get all the types from the google sheet
    Parameters:
    client: gspread.Client
        the client for the google sheet
    Returns:
    types: list
        a list of all the types
    """
    # open the metadata worksheet
    metadata = client.open('Ruta Lab Fly Manager').worksheet('metadata')
    # get all the types
    type_col = metadata.find("Type").col
    types = metadata.col_values(type_col)[1:]
    return types

def add_type(user, new_type, client):
    """
    Add a type to the google sheet
    Parameters:
    new_type: str
        the new type to add
    client: gspread.Client
        the client for the google sheet
    """
    # get all the types
    types = get_types(client=client)
    # check if the type exists
    if new_type not in types:
        # open the metadata worksheet
        metadata = client.open('Ruta Lab Fly Manager').worksheet('metadata')
        # add the type to the worksheet
        metadata.append_row([new_type])
        # write the activity
        write_activity(user, "Type added: {}".format(new_type), client)

def get_food_types(client):
    """
    Get all the food types from the google sheet
    Parameters:
    client: gspread.Client
        the client for the google sheet
    Returns:
    food_types: list
        a list of all the food types
    """
    # open the metadata worksheet
    metadata = client.open('Ruta Lab Fly Manager').worksheet('metadata')
    # get all the food types
    food_type_col = metadata.find("FoodType").col
    food_types = metadata.col_values(food_type_col)[1:]
    return food_types

def add_food_type(user, new_food_type, client):
    """
    Add a food type to the google sheet
    Parameters:
    new_food_type: str
        the new food type to add
    client: gspread.Client
        the client for the google sheet
    """
    # get all the food types
    food_types = get_food_types(client=client)
    # check if the food type exists
    if new_food_type not in food_types:
        # open the metadata worksheet
        metadata = client.open('Ruta Lab Fly Manager').worksheet('metadata')
        # add the food type to the worksheet
        metadata.append_row([new_food_type])
        # write the activity
        write_activity(user, "Food type added: {}".format(new_food_type), client)

def get_provenances(client):
    """
    Get all the provenances from the google sheet
    Parameters:
    client: gspread.Client
        the client for the google sheet
    Returns:
    provenances: list
        a list of all the provenances
    """
    # open the metadata worksheet
    metadata = client.open('Ruta Lab Fly Manager').worksheet('metadata')
    # get all the provenances
    provenance_col = metadata.find("Provenance").col
    provenances = metadata.col_values(provenance_col)[1:]
    return provenances

def add_provenance(user, new_provenance, client):
    """
    Add a provenance to the google sheet
    Parameters:
    new_provenance: str
        the new provenance to add
    client: gspread.Client
        the client for the google sheet
    """
    # get all the provenances
    provenances = get_provenances(client=client)
    # check if the provenance exists
    if new_provenance not in provenances:
        # open the metadata worksheet
        metadata = client.open('Ruta Lab Fly Manager').worksheet('metadata')
        # add the provenance to the worksheet
        metadata.append_row([new_provenance])
        # write the activity
        write_activity(user, "Provenance added: {}".format(new_provenance), client)

def get_xchr_alleles(client):
    """
    Get all the x alleles from the google sheet
    Parameters:
    client: gspread.Client
        the client for the google sheet
    Returns:
    x_alleles: list
        a list of all the x alleles
    """
    # open the metadata worksheet
    metadata = client.open('Ruta Lab Fly Manager').worksheet('metadata')
    # get all the x alleles
    x_allele_col = metadata.find("Allele(X)").col
    x_alleles = metadata.col_values(x_allele_col)[1:]
    return x_alleles

def add_xchr_allele(user, new_allele, client):
    """
    Add an x allele to the google sheet
    Parameters:
    new_allele: str
        the new x allele to add
    client: gspread.Client
        the client for the google sheet
    """
    # get all the x alleles
    x_alleles = get_xchr_alleles(client=client)
    # check if the x allele exists
    if new_allele not in x_alleles:
        # open the metadata worksheet
        metadata = client.open('Ruta Lab Fly Manager').worksheet('metadata')
        # add the x allele to the worksheet
        metadata.append_row([new_allele])
        # write the activity
        write_activity(user, "X allele added: {}".format(new_allele), client)

def get_chr2_alleles(client):
    """
    Get all the chr2 alleles from the google sheet
    Parameters:
    client: gspread.Client
        the client for the google sheet
    Returns:
    chr2_alleles: list
        a list of all the chr2 alleles
    """
    # open the metadata worksheet
    metadata = client.open('Ruta Lab Fly Manager').worksheet('metadata')
    # get all the chr2 alleles
    chr2_allele_col = metadata.find("Allele(2nd)").col
    chr2_alleles = metadata.col_values(chr2_allele_col)[1:]
    return chr2_alleles

def add_chr2_allele(user, new_allele, client):
    """
    Add a chr2 allele to the google sheet
    Parameters:
    new_allele: str
        the new chr2 allele to add
    client: gspread.Client
        the client for the google sheet
    """
    # get all the chr2 alleles
    chr2_alleles = get_chr2_alleles(client=client)
    # check if the chr2 allele exists
    if new_allele not in chr2_alleles:
        # open the metadata worksheet
        metadata = client.open('Ruta Lab Fly Manager').worksheet('metadata')
        # add the chr2 allele to the worksheet
        metadata.append_row([new_allele])
        # write the activity
        write_activity(user, "Chr2 allele added: {}".format(new_allele), client)


def get_chr3_alleles(client):
    """
    Get all the chr3 alleles from the google sheet
    Parameters:
    client: gspread.Client
        the client for the google sheet
    Returns:
    chr3_alleles: list
        a list of all the chr3 alleles
    """
    # open the metadata worksheet
    metadata = client.open('Ruta Lab Fly Manager').worksheet('metadata')
    # get all the chr3 alleles
    chr3_allele_col = metadata.find("Allele(3rd)").col
    chr3_alleles = metadata.col_values(chr3_allele_col)[1:]
    return chr3_alleles

def add_chr3_allele(user, new_allele, client):
    """
    Add a chr3 allele to the google sheet
    Parameters:
    new_allele: str
        the new chr3 allele to add
    client: gspread.Client
        the client for the google sheet
    """
    # get all the chr3 alleles
    chr3_alleles = get_chr3_alleles(client=client)
    # check if the chr3 allele exists
    if new_allele not in chr3_alleles:
        # open the metadata worksheet
        metadata = client.open('Ruta Lab Fly Manager').worksheet('metadata')
        # add the chr3 allele to the worksheet
        metadata.append_row([new_allele])
        # write the activity
        write_activity(user, "Chr3 allele added: {}".format(new_allele), client)


def get_chr4_alleles(client):
    """
    Get all the chr4 alleles from the google sheet
    Parameters:
    client: gspread.Client
        the client for the google sheet
    Returns:
    chr4_alleles: list
        a list of all the chr4 alleles
    """
    # open the metadata worksheet
    metadata = client.open('Ruta Lab Fly Manager').worksheet('metadata')
    # get all the chr4 alleles
    chr4_allele_col = metadata.find("Allele(4th)").col
    chr4_alleles = metadata.col_values(chr4_allele_col)[1:]
    return chr4_alleles


def add_chr4_allele(user, new_allele, client):
    """
    Add a chr4 allele to the google sheet
    Parameters:
    new_allele: str
        the new chr4 allele to add
    client: gspread.Client
        the client for the google sheet
    """
    # get all the chr4 alleles
    chr4_alleles = get_chr4_alleles(client=client)
    # check if the chr4 allele exists
    if new_allele not in chr4_alleles:
        # open the metadata worksheet
        metadata = client.open('Ruta Lab Fly Manager').worksheet('metadata')
        # add the chr4 allele to the worksheet
        metadata.append_row([new_allele])
        # write the activity
        write_activity(user, "Chr4 allele added: {}".format(new_allele), client)



