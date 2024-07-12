import pandas as pd
from flymanager.labels import AveryLabel
from datetime import datetime
import os
import segno
import numpy as np

def validated_input(prompt, valid_options, default=None, show_options=True):
    """
    Ask the user for input, validate it against a list of valid options
    and return the response. If the user enters an invalid option, they
    will be prompted to try again.
    """
    while True:
        if show_options:
            response = input(f"{prompt} ({'/'.join(valid_options)}) ({default}): ").lower().strip()
        else:
            response = input(f"{prompt} ({default}): ").lower().strip()
        if response == "" and default is not None:
            return default
        elif response in valid_options:
            return response
        else:
            print(f"Invalid option '{response}'. Please try again.")

def hex_to_rgb(hex):
    """
    Convert a hex color code to RGB
    """
    hex = hex.lstrip("#")
    return tuple(int(hex[i:i+2], 16)/255 for i in (0, 2, 4))


print("""
WELCOME TO THE FLY LABEL GENERATOR
==================================
""")

# Load the data from published google sheet
link = "https://docs.google.com/spreadsheets/d/e/2PACX-1vQd7fEA1nt90nDyirXr1GXmi66u-3J0dcrlk_bZ70SJFGmveEs-ahxX63PWKdfxJo5P3Rv156P8lfLt/pub?gid=0&single=true&output=csv"

# ask user to confirm the link
print(f"Data Source: {link}")
df = pd.read_csv(link)

print("Data loaded successfully.")
print(f"Data has {len(df)} rows and {len(df.columns)} columns.")

# filter out the rows Status = "Ordered" or "No longer maintained"
df = df[df["Status"] != "Ordered"]
df = df[df["Status"] != "No longer maintained"]

# replicate the rows based on the Replicates column and add a replicate number
df = df.loc[df.index.repeat(df["Replicates"])].reset_index(drop=True)
df["Replicate"] = df.groupby("Stock").cumcount() + 1

# ask which ones to print
print("Which labels to print? 1: All, 2: Vial Stocks, 3: Bottle Stocks, 4: Crosses")
response = validated_input("Enter your choice:", ["1", "2", "3", "4"], default="1", show_options=False)

if response == "2":
    # remove ones with no BB or X in the stock name
    df = df[np.logical_not(df["Stock"].str.contains("BB"))]
    df = df[np.logical_not(df["Stock"].str.contains("X"))]
elif response == "3":
    # keep only the ones with BB in the stock name
    df = df[df["Stock"].str.contains("BB")]
elif response == "4":
    # keep only the ones with X in the stock name
    df = df[df["Stock"].str.contains("X")]

# ask how many blank spaces to leave
num_blank = validated_input("How many blank spaces to leave?", [str(i) for i in range(30)], default="0", show_options=False)
num_blank = int(num_blank)

# how many labels to generate
num_labels = len(df)
print(f"Generating {num_labels} labels...")

# delete all existing qr codes

if os.path.exists("data/generated_qr"):
    os.system("rm -r data/generated_qr")
os.mkdir("data/generated_qr")

# generate a reportlab using AveryLabel
label = AveryLabel(5160)
# open the pdf files
label.open("data/generated_labels/{0}.pdf".format(datetime.now().strftime("%Y-%m-%d")))

# set debug to True to see the labels
label.debug = False
# create a callable function to render the labels
# LABEL TEMPLATE
# Stock | Replicate | Date
# Genotype (colored by Status: Healthy = Green, Showing Issues = Orange, Needs refresh = Red)
# watermark: common name

def render_label(canvas, width, height, stock, replicate, genotype, status, common_name):
    if stock != "" and replicate != "":
        
        # write the common name as a watermark
        canvas.setFont("Courier-Bold", 10)
        canvas.setFillColorRGB(0.8, 0.8, 0.8)
        canvas.drawString(5, 5, common_name)

        # draw the stock
        canvas.setFont("Courier-Bold", 10)
        canvas.setFillColorRGB(0, 0, 0)
        canvas.drawString(10, height - 15, f"{stock} | R{replicate} | {datetime.now().strftime('%Y-%m-%d')}")
        # draw the genotype
        canvas.setFont("Courier", 8)
        if status == "Healthy":
            canvas.setFillColorRGB(*hex_to_rgb("#006400"))
        elif status == "Showing Issues":
            canvas.setFillColorRGB(*hex_to_rgb("#964B00"))
        elif status == "Needs refresh":
            canvas.setFillColorRGB(*hex_to_rgb("#8B0000"))
        # if the genotype is too long, split it into multiple lines
        split_genotype = [genotype[i:i+25] for i in range(0, len(genotype), 25)]
        for i, genotype in enumerate(split_genotype):
            canvas.drawString(10, height - 30 - i*10, f"{genotype}")
        
        # add a qr code on the bottom right corner
        
        text = f"{stock[3:]}_{replicate}_{datetime.now().strftime('%Y-%m-%d')}"
        qr = segno.make(f"{stock[3:]}_{replicate}_{datetime.now().strftime('%Y-%m-%d')}", micro=False)
        qr.save("data/generated_qr/{0}_{1}.png".format(stock, replicate), scale=5)
        canvas.drawImage("data/generated_qr/{0}_{1}.png".format(stock, replicate),
                        width - 50, 5, width=45, height=45)

# render the blank spaces
for i in range(num_blank):
    label.render(render_label, 1, "", "", "", "")

# render the labels
for i in range(num_labels):
    label.render(render_label, 1, df["Stock"].iloc[i], df["Replicate"].iloc[i], df["Genotype"].iloc[i], df["Status"].iloc[i], df["Common Name"].iloc[i])

# close the pdf file
label.close()

# start the printing process
print("Printing labels...")
# os.system(f"lpr data/generated_labels/{datetime.now().strftime('%Y-%m-%d')}.pdf")





