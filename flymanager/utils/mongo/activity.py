import datetime

def write_activity(user, activity, db):
    """
    Write an activity to the MongoDB collection.
    Parameters:
    user: str
        the username of the user
    activity: str
        the activity of the user
    db: pymongo.database.Database
        the database instance for MongoDB
    """
    # get timestamp
    timestamp = datetime.datetime.now().strftime("%Y-%m-%d %H:%M")
    
    # create the activity document
    activity_document = {
        "user": user,
        "timestamp": timestamp,
        "activity": activity
    }
    
    # insert the document into the activities collection
    db.activity.insert_one(activity_document)