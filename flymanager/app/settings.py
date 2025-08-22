BASE_CROSS_PROPERTIES = [
    'MaleUniqueID', 'FemaleUniqueID', 'MaleGenotype', 
    'FemaleGenotype', 'Name',
]
REQUIRED_CROSS_PROPERTIES = [
    'MaleSpecies', 'FemaleSpecies', 
    'Status', 'FoodType', 'VialLifetime', 
    'FlipFrequency', 'DevelopmentalTime', 
    'MaxCrossLifetime'
]
OPTIONAL_CROSS_PROPERTIES = [
    'Comments'
]
DEFAULT_CROSS_PROPERTY_VALUES = {
    'MaleSpecies': 'D. melanogaster',
    'FemaleSpecies': 'D. melanogaster',
    'Status': 'Healthy',
    'FoodType': 'Molasses',
    'VialLifetime': '14',
    'FlipFrequency': '7',
    'DevelopmentalTime': '10',
    'MaxCrossLifetime': '18',
    'Comments': ''
}


BASE_STOCK_PROPERTIES = [
    "Genotype", "Name", 
]
REQUIRED_STOCK_PROPERTIES = [
    "SourceID", "Species",
    "SeriesID", "ReplicateID",
    "Type", "Status",
    "FoodType", "VialLifetime", "FlipFrequency",
    "DevelopmentalTime",  "Provenance",
]
OPTIONAL_STOCK_PROPERTIES = [
    "Comments", 
    "AltReference"
]
DEFAULT_STOCK_PROPERTY_VALUES = {
    "SourceID": "UNK",
    "Species": "D. melanogaster",
    "SeriesID": "",
    "ReplicateID": "",
    "Type": "WT",
    "Status": "Healthy",
    "FoodType": "Molasses",
    "VialLifetime": "14",
    "FlipFrequency": "7",
    "DevelopmentalTime": "10",
    "Provenance": "Unknown",
    "AltReference": "",
    "Comments": ""
}