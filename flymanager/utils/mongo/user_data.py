def get_user_stocks(user, db):
    """
    Connect to the MongoDB collection for the given user's stocks.
    Parameters:
    user: str
        The username of the user.
    db: pymongo.database.Database
        The MongoDB database instance.
    Returns:
    stocks: list
        A list of dictionaries representing the user's stocks.
    """
    stock_collection = db["stocks"]
    filtered_stocks = stock_collection.find({"User": user})
    return list(filtered_stocks)


def get_user_initials(user, db):
    """
    Get the initials of the user
    Parameters:
    user: str
        the username of the user
    db: pymongo.database.Database
        the MongoDB database instance
    Returns:
    initials: str
        the initials of the user
    """
    users_collection = db['users']
    
    # Find the user document by username
    user_document = users_collection.find_one({"Username": user})
    
    if user_document:
        return user_document.get("Initials")
    else:
        return None


def get_user_flip_days(user, db):
    """
    Get the preferred flip days of the user from the database.
    Parameters:
    user: str
        The username of the user.
    db: pymongo.database.Database
        The MongoDB database instance.
    Returns:
    list
        A list of the preferred flip days of the user
    """
    users_collection = db['users']
    user_document = users_collection.find_one({"Username": user})
    return user_document.get("FlipDays").split(',') if user_document else []


def get_user_email(user, db):
    """
    Get the email of the user
    Parameters:
    user: str
        the username of the user
    db: pymongo.database.Database
        the MongoDB database instance
    Returns:
    email: str
        the email of the user
    """
    users_collection = db['users']
    
    # Find the user document by username
    user_document = users_collection.find_one({"Username": user})
    
    if user_document:
        return user_document.get("Email")
    else:
        return None


def get_user_crosses(user, db):
    """
    Connect to the MongoDB collection for the given user's crosses.
    Parameters:
    user: str
        The username of the user.
    db: pymongo.database.Database
        The MongoDB database instance.
    Returns:
    collection: pymongo.collection.Collection
        The collection representing the user's crosses.
    """
    crosses_collection = db["crosses"]
    filtered_crosses = crosses_collection.find({"User": user})
    return list(filtered_crosses)


def get_user_activities(user, db):
    """
    Retrieve the user's activities from the MongoDB database.
    
    Parameters:
    user: str
        The username of the user.
    db: pymongo.database.Database
        The MongoDB database instance.
    
    Returns:
    list
        A list of dictionaries representing the user's activities.
    """
    activities_collection = db["activity"]
    
    # Find all activities where the 'username' field matches the given user
    user_activities = list(activities_collection.find({"username": user}))
    
    return user_activities


def get_all_genotypes(user, db):
    """
    Get all the genotypes of the user's stocks.
    
    Parameters:
    user: str
        The username of the user.
    db: pymongo.database.Database
        The MongoDB database instance.

    Returns:
    genotypes: list
        A list of all the unique genotypes.
    """
    stocks_collection = db["stocks"]
    
    # Find all the stocks and crosses of the user
    user_stocks = stocks_collection.find({"User": user})
    
    # Extract the genotypes from the stocks and crosses
    stock_genotypes = [stock["Genotype"] for stock in user_stocks]
    
    # Remove duplicates and sort the genotypes
    genotypes = list(set(stock_genotypes))
    
    return genotypes