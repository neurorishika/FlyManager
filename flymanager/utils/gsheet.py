# A collection of functions to interact with google sheets for the Fly Manager app
import gspread
from oauth2client.service_account import ServiceAccountCredentials
from hashlib import shake_256
import datetime
import os

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
    stock.update_cell(stock_row, flip_log_col, ts + "\n" + flip_log if flip_log else ts)

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
            stock.update_cell(stock_row, data_modified_log_col, text + "\n" + data_modified_log if data_modified_log else text)
    
    if added_comment is not None:
        # update the comments
        comments_col = stock.find("Comments").col
        # get the current comments
        current_comments = stock.cell(stock_row, comments_col).value
        new_comments = added_comment + "\n" + current_comments if current_comments else added_comment
        stock.update_cell(stock_row, comments_col, new_comments)
        # update the data modified date
        stock.update_cell(stock_row, data_modified_col, ts)
        # append to the data modified log
        text = "{} : Comments added: {}".format(ts, added_comment)
        stock.update_cell(stock_row, data_modified_log_col, text + "\n" + data_modified_log if data_modified_log else text)


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
            Provenance (optional)
            Comments (optional)
    client: gspread.Client
        the client for the google sheet
    """
    # get the user's stock sheet
    stock = get_user_stocks(user, client)