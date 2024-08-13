import gspread
from oauth2client.service_account import ServiceAccountCredentials
import pprint
import hashlib

#Authorize the API
scope = [
    'https://www.googleapis.com/auth/drive',
    'https://www.googleapis.com/auth/drive.file'
    ]
file_name = 'data/client.key'
creds = ServiceAccountCredentials.from_json_keyfile_name(file_name,scope)
client = gspread.authorize(creds)

sheet = client.open('Ruta Lab Fly Manager').worksheet("admin_Stock")
python_sheet = sheet.get_all_records()

# for record get the Genotype + SeriesID + ReplicateID
for record in python_sheet:
    value = "rmohanta" + str(record['Genotype']) + str(record['SeriesID']) + str(record['ReplicateID'])
    print(hashlib.shake_256(value.encode()).hexdigest(5))