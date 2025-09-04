from flymanager.utils.mongo import create_mongo_client, get_database, reset_database
from flymanager.utils.converter import xls_to_mongo

# setup dotenv
from dotenv import load_dotenv
load_dotenv()

# setup the mongo db
client = create_mongo_client()
db = get_database(client)

# reset the database
reset_database(db)

# load the excel file into the mongo db
file_path = "data/flymanager.xlsx"
xls_to_mongo(file_path, db)

# get the collection names
collection_names = db.list_collection_names()
print(collection_names)

# export the collections to csv
from flymanager.utils.converter import mongo_to_xls

mongo_to_xls(db, "data/flymanager_export.xlsx")
