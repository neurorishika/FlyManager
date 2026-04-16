# Description: This file contains functions to convert data between different formats (e.g., CSV, Excel, MongoDB).

import pandas as pd

from flymanager.utils.genetics import qc_genotype


def build_import_plan(file_path):
    """Return workbook data as a collection->records mapping."""
    xls = pd.ExcelFile(file_path)

    stocks = []
    crosses = []
    collection_payloads = {}

    for sheet_name in xls.sheet_names:
        if "Stock" in sheet_name:
            stocks.append(sheet_name)
            continue
        if "Cross" in sheet_name:
            crosses.append(sheet_name)
            continue

        df = pd.read_excel(xls, sheet_name)
        collection_payloads[sheet_name] = df.fillna("").astype(str).to_dict(orient="records")

    stock_df = pd.DataFrame()
    for stock in stocks:
        username = stock.split("_")[0]
        user_stock = pd.read_excel(xls, stock)
        user_stock["User"] = username
        user_stock["Genotype"] = user_stock["Genotype"].apply(lambda x: qc_genotype(x)[1])
        stock_df = pd.concat([stock_df, user_stock], ignore_index=True)

    cross_df = pd.DataFrame()
    for cross in crosses:
        username = cross.split("_")[0]
        user_cross = pd.read_excel(xls, cross)
        user_cross["User"] = username
        user_cross["MaleGenotype"] = user_cross["MaleGenotype"].apply(lambda x: qc_genotype(x)[1])
        user_cross["FemaleGenotype"] = user_cross["FemaleGenotype"].apply(lambda x: qc_genotype(x)[1])
        cross_df = pd.concat([cross_df, user_cross], ignore_index=True)

    collection_payloads["stocks"] = stock_df.fillna("").astype(str).to_dict(orient="records")
    collection_payloads["crosses"] = cross_df.fillna("").astype(str).to_dict(orient="records")
    return collection_payloads


def replace_collections_with_plan(collection_payloads, db):
    """Replace workbook-backed collections using staged swaps instead of destructive clears."""
    temp_prefix = "__import_tmp__"
    backup_prefix = "__import_backup__"
    created_targets = []
    renamed_backups = []

    try:
        for collection_name, records in collection_payloads.items():
            temp_collection_name = f"{temp_prefix}{collection_name}"
            if temp_collection_name in get_collection_names(db):
                db[temp_collection_name].drop()

            if records:
                db[temp_collection_name].insert_many(records)
            else:
                db.create_collection(temp_collection_name)

        for collection_name in collection_payloads:
            temp_collection_name = f"{temp_prefix}{collection_name}"
            backup_collection_name = f"{backup_prefix}{collection_name}"

            if backup_collection_name in get_collection_names(db):
                db[backup_collection_name].drop()

            if collection_name in get_collection_names(db):
                db[collection_name].rename(backup_collection_name)
                renamed_backups.append((collection_name, backup_collection_name))

            db[temp_collection_name].rename(collection_name)
            created_targets.append(collection_name)
    except Exception:
        for collection_name in reversed(created_targets):
            if collection_name in get_collection_names(db):
                db[collection_name].drop()

        for collection_name, backup_collection_name in reversed(renamed_backups):
            if backup_collection_name in get_collection_names(db):
                db[backup_collection_name].rename(collection_name)

        for collection_name in collection_payloads:
            temp_collection_name = f"{temp_prefix}{collection_name}"
            if temp_collection_name in get_collection_names(db):
                db[temp_collection_name].drop()
        raise

    for _, backup_collection_name in renamed_backups:
        if backup_collection_name in get_collection_names(db):
            db[backup_collection_name].drop()

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
    collection_payloads = build_import_plan(file_path)
    replace_collections_with_plan(collection_payloads, db)


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
