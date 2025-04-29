# Description: This file contains functions to convert data between different formats (e.g., CSV, Excel, MongoDB).

import pandas as pd
from flymanager.utils.genetics import qc_genotype

def get_collection_names(db):
    """
    Get the names of all the collections in the MongoDB database.
    Parameters:
    db: pymongo.database.Database
        The MongoDB database instance.
    Returns:
    collection_names: list
        A list of the names of the collections in the database.
    """
    collection_names = db.list_collection_names()
    return collection_names

def xls_to_mongo(file_path, db):
    """
    Load an Excel file into a MongoDB database with each sheet as a collection.
    Parameters:
    file_path: str
        The path to the Excel file.
    db: pymongo.database.Database
        The MongoDB database instance.
    """
    # Load the Excel file into a pandas ExcelFile object
    xls = pd.ExcelFile(file_path)
    
    # Iterate over each sheet in the Excel file
    stocks = []
    crosses = []

    # clear the database
    for collection_name in get_collection_names(db):
        db[collection_name].delete_many({})


    for sheet_name in xls.sheet_names:
        # Skip sheets that contain stock or cross data for now
        if "Stock" in sheet_name:
            stocks.append(sheet_name)
            continue
        if "Cross" in sheet_name:
            crosses.append(sheet_name)
            continue

        # Load the sheet into a DataFrame
        df = pd.read_excel(xls, sheet_name)
        
        # Convert the DataFrame to a dictionary
        data = df.to_dict(orient="records")
        
        # Insert the data into the MongoDB collection
        if len(data) > 0:
            db[sheet_name].insert_many(data)
    
    # Process stock and cross data by combining them into a single dataframe with the new column "User"
    stock_df = pd.DataFrame()
    for stock in stocks:
        username = stock.split("_")[0]
        user_stock = pd.read_excel(xls, stock)
        user_stock["User"] = username
        # qc the genotypes
        user_stock["Genotype"] = user_stock["Genotype"].apply(lambda x: qc_genotype(x)[1])
        stock_df = pd.concat([stock_df, user_stock], ignore_index=True)
    
    # replace NaN values with empty strings
    stock_df = stock_df.fillna("").astype(str).to_dict(orient="records")
    
    cross_df = pd.DataFrame()
    for cross in crosses:
        username = cross.split("_")[0]
        user_cross = pd.read_excel(xls, cross)
        user_cross["User"] = username
        # qc the genotypes
        user_cross["MaleGenotype"] = user_cross["MaleGenotype"].apply(lambda x: qc_genotype(x)[1])
        user_cross["FemaleGenotype"] = user_cross["FemaleGenotype"].apply(lambda x: qc_genotype(x)[1])
        cross_df = pd.concat([cross_df, user_cross], ignore_index=True)

    # replace NaN values with empty strings
    cross_df = cross_df.fillna("").astype(str).to_dict(orient="records")

    # # Insert the stock and cross data into the MongoDB collection
    if len(stock_df) > 0:
        db["stocks"].insert_many(stock_df)
    if len(cross_df) > 0:
        db["crosses"].insert_many(cross_df)


def mongo_to_xls(db, file_path):
    """
    Load a MongoDB database into an Excel file with each collection as a sheet.
    Parameters:
    db: pymongo.database.Database
        The MongoDB database instance.
    file_path: str
        The path to the Excel file.
    """
    # Create a pandas ExcelWriter object
    writer = pd.ExcelWriter(file_path)
    
    # Get the names of all the collections in the database
    collection_names = get_collection_names(db)
    
    # Iterate over each collection
    for collection_name in collection_names:

        # Skip the stock, cross and metadata collections and process them separately
        if collection_name == "stocks" or collection_name == "crosses":
            continue

        # Query all documents in the collection
        documents = db[collection_name].find()
        
        # Convert the documents to a DataFrame
        df = pd.DataFrame(documents)

        # Remove the "_id" field
        df = df.drop(columns=["_id"])
        
        # Save the DataFrame to the Excel file
        df.to_excel(writer, sheet_name=collection_name, index=False)
    
    # Process stock data
    # find all unique usernames
    usernames = db["stocks"].distinct("User")
    for username in usernames:
        stock_df = pd.DataFrame(list(db["stocks"].find({"User": username})))
        # Remove the "_id" field
        stock_df = stock_df.drop(columns=["_id"])
        stock_df.to_excel(writer, sheet_name=username + "_Stock", index=False)

    # Process cross data
    # find all unique usernames
    usernames = db["crosses"].distinct("User")
    for username in usernames:
        cross_df = pd.DataFrame(list(db["crosses"].find({"User": username})))
        # Remove the "_id" field
        cross_df = cross_df.drop(columns=["_id"])
        cross_df.to_excel(writer, sheet_name=username + "_Cross", index=False)
    
    # Save the Excel file
    writer.close()
