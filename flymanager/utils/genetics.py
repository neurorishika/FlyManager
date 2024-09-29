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

def get_bloomington_genes(ch_all, genotype):
    """
    Clean the bloomington genotype.
    """
    genes ={
        0: "",
        1: "",
        2: "",
        3: ""
    }
    if ch_all == "wt":
        genotype = "[" + genotype + "]"
        genes[0] = "w" + genotype
        genes[1] = "+" + genotype
        genes[2] = "+" + genotype
        genes[3] = "+" + genotype
    else:
        ch_components = ch_all.split(";")
        genotype_components = genotype.split(";")

        # check if the number of components are the same
        if len(ch_components) != len(genotype_components):
            return None, "Number of chromosome components does not match genotype components"
        
        # check if the genotype is in the correct format
        # Assign each genotype component to the corresponding chromosome
        for ch, gen in zip(ch_components, genotype_components):
            try:
                ch_num = int(ch)
                genes[ch_num-1] = gen.strip()
            except ValueError:
                return None, f"Error: Unable to parse chromosome number '{ch}'"
            except IndexError:
                return None, f"Error: Chromosome number '{ch}' out of bounds"
    return genes, None

def get_stock_genotype(stock_id):
    """
    Get the stock genotype from the bloomington data.

    Parameters:
    stock_id: str
        The stock ID.

    Returns:
    stock_info: pd.DataFrame
        The stock information.
    error: str
        The error message.
    """
    df = get_bloomington_data()
    stock_info = df[df["Stk #"] == stock_id]
    # check for stock ID availability
    if stock_info.empty:
        return None, "Stock ID not found"
    if len(stock_info) > 1:
        return None, "Multiple stock IDs found"
    stock_info = stock_info.reset_index(drop=True)
    # get the stock information
    ch_all = stock_info["Ch # all"].values[0]
    genotype = stock_info["Genotype"].values[0]
    # check if there are chromosomal variants
    if ch_all == "Y" or ch_all == "mt" or ch_all == "U" or ch_all == "f" or ch_all == "" or ch_all is None:
        return None, "Complex variants not supported"
    if any([x in genotype for x in ["Dp(", "Df(", "T(", "C(", "In(", "Tp(", "l("]]) or genotype == "" or genotype is None:
        return None, "Complex variants not supported"
    # clean the genotype
    genes, error = get_bloomington_genes(ch_all, genotype)
    if error:
        return None, error
    # join the genes to get the stock information
    genotype = "; ".join([genes[0], genes[1], genes[2], genes[3]])
    # assure that the stock information is in the correct format
    qc, error = qc_genotype(genotype)
    if not qc:
        return None, error
    return genotype, None

def qc_genotype(genotype):
    """
    Check if the genotype is in the correct format.
    """
    if not isinstance(genotype, str):
        return False, "Genotype must be a string"
    
    # Check if the genotype is in the correct format xchromosome; chromosome2; chromosome3; chromosome4
    if not genotype.count(";") == 3:
        return False, "Genotype must be in the format xchromosome; chromosome2; chromosome3; chromosome4"
    
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

    