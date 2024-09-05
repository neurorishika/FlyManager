# Description: This file contains functions for fly genetics.

import requests
import pandas as pd

def refresh_bloomington_data():
    """
    Refresh the bloomington data.
    """
    url = "https://bdsc.indiana.edu/pdf/bloomington.csv"
    r = requests.get(url, allow_redirects=True)
    open('data/bloomington.csv', 'wb').write(r.content)

def get_bloomington_data():
    """
    Get the bloomington data.
    """    
    df = pd.read_csv("data/bloomington.csv")
    return df

def qc_genotype(genotype):
    """
    Check if the genotype is in the correct format.
    """
    if not isinstance(genotype, str):
        return False, "Genotype must be a string"
    
    # Check if the genotype is in the correct format xchromosome; chromosome1; chromosome2
    if not genotype.count(";") == 3:
        return False, "Genotype must be in the format xchromosome; chromosome1; chromosome2"
    
    # clean the genotype
    genotype = genotype.split(";")
    genotype = [x.strip() for x in genotype]
    # make sure each chromosome is in the correct format (atmost 1 '/' symbol)
    if any([x.count("/") > 1 for x in genotype]):
        return False, "Chromosomes must be in the format chromosomeA/chromosomeB (heterozygous) or chromosomeBoth (homozygous)"
    
    chrs = []
    for chr in genotype:
        # check if the chromosome is in the correct format
        if chr.count("/") == 1:
            # arrange the chromosome in the alphabetical order
            chr = chr.split("/")
            chr.sort()
            chr = "/".join(chr)
        chrs.append(chr)
    
    genotype = "; ".join(chrs)

    return True, genotype

def get_genetic_components(genotype):
    """
    Get the chromosomes from the genotype.
    """
    chromosomes = genotype.split(";")
    chromosomes = [x.strip() for x in chromosomes]
    # for each chromosome, get the alleles
    components = []
    for chr in chromosomes:
        if chr.count("/") == 1:
            alleles = chr.split("/")
            alleles.sort()
            # replace "" with "+"
            alleles = [allele if allele != "" else "+" for allele in alleles]
            components.append(alleles)
        else:
            components.append([chr if chr != "" else "+", chr if chr != "" else "+"])
    return components