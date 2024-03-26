# update poetry and rerun mkinit
import os
from subprocess import call
import datetime

print("STARTING BUILD PROCESS")
print("======================")

# update poetry
print("Updating dependencies with poetry...")
call("poetry update", shell=True)

# get package name from pyproject.toml
with open("pyproject.toml", "r") as f:
    lines = f.readlines()
for line in lines:
    if "name" in line:
        package_name = line.split("=")[1].strip().replace('"', '')
print("Package name: {}".format(package_name))

# run mkinit on package
print("Running mkinit on package...",end="")
call("mkinit --lazy_loader {} --recursive -w".format(package_name), shell=True)
print("done.")

# install package
print("Installing all dependencies and package...")
call("poetry install", shell=True)

# update license year
print("Updating license...")
with open("LICENSE", "r") as f:
    lines = f.readlines()
for line in lines:
    if "Copyright" in line:
        line = "Copyright (c) "+datetime.datetime.now().strftime("%Y")+", Rishika Mohanta\n"
with open("LICENSE", "w") as f:
    for line in lines:
        f.write(line)
print("done.")

print("BUILD COMPLETE")

