from flymanager.utils.mongo import create_mongo_client, get_database, reset_database
from flymanager.utils.converter import xls_to_mongo
from flymanager.utils.genetics import qc_genotype, get_genetic_components

# setup dotenv
from dotenv import load_dotenv
load_dotenv()

# setup the mongo db
client = create_mongo_client()
db = get_database(client)

# load the csv in pandas
import pandas as pd
import re

# custom print function that writes to a file
import builtins
# remove the output file if it exists
import os
if os.path.exists("output.txt"):
    os.remove("output.txt")
def print(*args, **kwargs):
    with open("output.txt", "a") as f:
        builtins.print(*args, file=f, **kwargs)

# Read the CSV file
df = pd.read_csv("data/bloomington.csv")

# Remove rows with Y, mt, U, or f in "Ch # all" or nan in "Ch # all"
df = df[df["Ch # all"].notna()]
df = df[~df["Ch # all"].str.contains("Y")]
df = df[~df["Ch # all"].str.contains("mt")]
df = df[~df["Ch # all"].str.contains("U")]
df = df[~df["Ch # all"].str.contains("f")]


# Define the chromosome components dictionary
all_components = {
    0: [],
    1: [],
    2: [],
    3: []
}

# Iterate over the rows in the dataframe
for index, row in df.iterrows():
    ch_all = row["Ch # all"]
    genotype = row["Genotype"]
    stock = row["Stk #"]
    
    # If "Ch # all" is "wt", handle wild type cases
    if ch_all == "wt":
        genotype = "[" + genotype + "]"
        all_components[0].append("w" + genotype)
        all_components[1].append("+" + genotype)
        all_components[2].append("+" + genotype)
        all_components[3].append("+" + genotype)
    else:
        # Skip if duplications or other complex constructs are present
        if any(x in genotype for x in ["Dp(", "Df(", "T(", "C(", "In(", "Tp(", "l("]):
            continue
        
        ch_components = ch_all.split(";")
        genotype_components = genotype.split(";")
        
        # Check if the number of chromosome components matches genotype components
        if len(ch_components) != len(genotype_components):
            print(stock, genotype, ch_all)
            continue
        
        # Assign each genotype component to the corresponding chromosome
        for ch, gen in zip(ch_components, genotype_components):
            try:
                ch_num = int(ch)
                # split by / and add each component to the corresponding chromosome
                compos = gen.strip().split("/")
                for comp in compos:
                    all_components[ch_num-1].append(comp.strip())
            except ValueError:
                print(f"Error: Unable to parse chromosome number '{ch}' in row {index}")
            except IndexError:
                print(f"Error: Chromosome number '{ch}' out of bounds in row {index}")

# get existing components from the database
all_components_db = {
    0: [x["Value"] for x in db.genesX.find()],
    1: [x["Value"] for x in db.genes2nd.find()],
    2: [x["Value"] for x in db.genes3rd.find()],
    3: [x["Value"] for x in db.genes4th.find()]
}

for n in range(4):
    print(f"Existing components for chromosome {n+1}: {len(all_components_db[n])}")

# Add the new components to the existing components
for n in range(4):
    all_components[n].extend(all_components_db[n])

# Get the unique components for each chromosome
for n, chr in all_components.items():
    all_components[n] = list(set(chr))
    print(f"Unique components for chromosome {n+1}: {len(all_components[n])}")
    
# Drop the existing components
db.genesX.drop()
db.genes2nd.drop()
db.genes3rd.drop()
db.genes4th.drop()

# add components to the database
db.genesX.insert_many([{"Value": x} for x in all_components[0]])
db.genes2nd.insert_many([{"Value": x} for x in all_components[1]])
db.genes3rd.insert_many([{"Value": x} for x in all_components[2]])
db.genes4th.insert_many([{"Value": x} for x in all_components[3]])