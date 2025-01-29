# Description: This file contains functions for fly genetics.

import numpy as np
from itertools import product
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

# Function to check if the genotype is in the correct format
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

# Function to get the genetic components from the genotype
def get_genetic_components(genotype, sex):
    """
    Get the chromosomes from the genotype.
    """
    assert sex in ["male","female"], "Sex must be either male or female"
    chromosomes = genotype.split(";")
    chromosomes = [x.strip() for x in chromosomes]
    # for each chromosome, get the alleles
    components = []
    # handle the X chromosome
    if chromosomes[0] == "":
        components.append(["+", "+"] if sex == "female" else ["+","0"])
    else:
        if chromosomes[0].count("/") == 1:
            chromosomes[0] = chromosomes[0].split("/")
            chromosomes[0].sort()
            components.append([chromosomes[0][0], chromosomes[0][1]] if sex == "female" else ["[" + "or".join(chromosomes[0]) + "]", "0"])
        else:
            components.append([chromosomes[0], chromosomes[0]] if sex == "female" else [chromosomes[0], "0"])
    for chr in chromosomes[1:]:
        if chr.count("/") == 1:
            alleles = chr.split("/")
            alleles.sort()
            # replace "" with "+"
            alleles = [allele if allele != "" else "+" for allele in alleles]
            components.append(alleles)
        else:
            components.append([chr if chr != "" else "+", chr if chr != "" else "+"])
    return components

# Function to get the genotype from the genetic components
def get_genotype_from_components(components):
    """
    Convert genetic components back into a genotype string, inferring sex from the X chromosome.
    If both alleles are '+', the field will be left empty (for homozygous '+' cases).
    """
    # Prepare the list to store chromosomes
    genotype = []

    sex = "female"
    
    # Handle the X chromosome (infer sex from the first chromosome component)
    x_chromosome = components[0]
    if "0" in x_chromosome:
        # Male case (X0), one of the X alleles is "0"
        # append the other allele
        genotype.append(x_chromosome[0] if x_chromosome[0] != "0" else x_chromosome[1])
        sex = "male"

    else:
        # Female case (XX or heterozygous X chromosomes)
        if x_chromosome[0] == x_chromosome[1]:
            # Homozygous X chromosome case
            genotype.append(x_chromosome[0] if x_chromosome[0] != "+" else "")
        else:
            # Heterozygous X chromosomes case
            x_chromosome.sort()  # Sort to maintain consistency
            genotype.append("/".join(x_chromosome))
    
    # Handle the autosomal chromosomes
    for chr_components in components[1:]:
        if chr_components[0] == chr_components[1]:
            # Homozygous case (e.g., both alleles are the same)
            # Leave empty if both are '+'
            genotype.append("" if chr_components[0] == "+" else chr_components[0])
        else:
            # Heterozygous case (e.g., different alleles)
            chr_components.sort()  # Ensure the alleles are sorted alphabetically
            genotype.append("/".join(chr_components))
    
    # Join the chromosomes with "; " and return the genotype string
    return qc_genotype("; ".join(genotype))[1], sex

# Function to sort the alleles in each chromosome pair alphabetically
def sort_components_alphabetically(t):
    """
    Sort the alleles in each chromosome pair alphabetically.
    """
    return [list(sorted(x)) for x in t]

# Function to cross two genotypes
def cross_genotypes(male_genotype,female_genotype):
    """
    Cross two genotypes.
    """
    male_components = get_genetic_components(qc_genotype(male_genotype)[1],"male")
    female_components = get_genetic_components(qc_genotype(female_genotype)[1],"female")
    # get all possible combinations of chromosomes for each chromosome pair
    combinations = [sort_components_alphabetically(list(product(m,f))) for m,f in zip(male_components,female_components)]
    # get all possible combinations of chromosomes
    combinations = list(product(*combinations))
    # convert the combinations into genotypes
    genotypes = ["|".join(get_genotype_from_components(list(comb))) for comb in combinations]
    # get the unique genotypes along with probabilities
    genotypes, counts = np.unique(genotypes, return_counts=True)
    probabilities = counts/np.sum(counts)
    # sort the genotypes by probability AND alphabetically
    indices = np.lexsort((genotypes,probabilities))[::-1]
    genotypes = genotypes[indices]
    probabilities = probabilities[indices]
    
    # indices = np.argsort(probabilities)[::-1]
    # genotypes = genotypes[indices]
    # probabilities = probabilities[indices]
    # convert back to [genotype, sex, probability] format
    combinations = [[genotype.split("|")[0],genotype.split("|")[1],probability] for genotype,probability in zip(genotypes,probabilities)]

    return combinations
    