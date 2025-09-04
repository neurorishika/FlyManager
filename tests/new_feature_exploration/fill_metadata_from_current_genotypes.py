from flymanager.utils.mongo import create_mongo_client, get_database, reset_database
from flymanager.utils.converter import xls_to_mongo
from flymanager.utils.genetics import qc_genotype, get_genetic_components

# setup dotenv
from dotenv import load_dotenv
load_dotenv()

# setup the mongo db
client = create_mongo_client()
db = get_database(client)

# get the genotypes collection
stocks = list(db.stocks.find())
print("Number of stocks: ", len(stocks))
# verify the genotypes collection
genotypes_qc = []
types = []
food_types = []
provenances = []
all_components = {
    0: [],
    1: [],
    2: [],
    3: []
}
for stock in stocks:
    genotype = stock["Genotype"]
    types.append(stock["Type"])
    food_types.append(stock["FoodType"])
    provenances.extend(stock["Provenance"].split("/") if len(stock["Provenance"].split("/")) > 1 else [stock["Provenance"]])
    success, genotype_qc = qc_genotype(genotype)
    if success:
        genotypes_qc.append(genotype_qc)
        # get the genetic components
        components = get_genetic_components(genotype_qc)
        # add the components to the dictionary
        for n, chr in enumerate(components):
            all_components[n].extend(chr)
    else:
        print("Genotype failed QC: ", genotype)

# get the unique components
for n, chr in all_components.items():
    all_components[n] = list(set(chr))

print("All components: ", all_components)

# add components to the database
db.genesX.drop()
db.genes2nd.drop()
db.genes3rd.drop()
db.genes4th.drop()
db.genesX.insert_many([{"Value": x} for x in all_components[0]])
db.genes2nd.insert_many([{"Value": x} for x in all_components[1]])
db.genes3rd.insert_many([{"Value": x} for x in all_components[2]])
db.genes4th.insert_many([{"Value": x} for x in all_components[3]])

db.types.drop()
types = list(set(types))
db.types.insert_many([{"Value": x} for x in types])

db.food_types.drop()
food_types = list(set(food_types))
db.food_types.insert_many([{"Value": x} for x in food_types])

db.provenances.drop()
provenances = list(set(provenances))
db.provenances.insert_many([{"Value": x} for x in provenances])

