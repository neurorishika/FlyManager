import os
from subprocess import check_output,call
import datetime

if __name__ == "__main__":
    
    print("STARTING PROJECT TEMPLATE UPDATE PROCESS")
    print("========================================")

    # download latest update.py into utils folder
    update_web = "https://raw.githubusercontent.com/neurorishika/rpy-template/main/utils/update.py"
    print("Downloading latest update.py into utils folder...")
    os.system("curl {} -o utils/update.py.tmp".format(update_web))
    print("Download complete.")
    
    print()

    #check if update.py.tmp is different from update.py
    if os.system("diff utils/update.py.tmp utils/update.py") != 0:
        # replace update.py with update.py.tmp
        os.system("mv utils/update.py.tmp utils/update.py")
        # exit and ask user to run update.py again
        print("An update fpr update.py was found and replaced. Please run 'poetry run python utils/update.py' again.")
        exit(-1)
    else:
        # delete update.py.tmp
        os.system("rm utils/update.py.tmp")
        print("No updates for update.py found.")

    print()

    # get package name from pyproject.toml
    with open("pyproject.toml", "r") as f:
        lines = f.readlines()
    for line in lines:
        if "name" in line:
            package_name = line.split("=")[1].strip().replace('"', '')
    print("Package name: {}".format(package_name))

    dir_name = os.path.basename(os.getcwd())

    print("Directory name: {}".format(dir_name))

    print()

    # download latest rdp_client.py into package folder
    rdp_client_web = "https://raw.githubusercontent.com/neurorishika/rdp-standard/main/rdp_client.py"

    print("Downloading latest rdp_client.py into package folder...")
    os.system("curl {} -o {}/rdp_client.py".format(rdp_client_web, package_name))
    print("Download complete.")

    print()

    # download latest build.py and quickstart.py into utils folder
    build_web = "https://raw.githubusercontent.com/neurorishika/rpy-template/main/utils/build.py"
    quickstart_web = "https://raw.githubusercontent.com/neurorishika/rpy-template/main/utils/quickstart.py"

    print("Downloading latest build.py and quickstart.py into utils folder...")
    os.system("curl {} -o utils/build.py".format(build_web))
    os.system("curl {} -o utils/quickstart.py".format(quickstart_web))
    print("Download complete.")

    print()

    # download latest pyproject.toml into package folder
    pyproject_web = "https://raw.githubusercontent.com/neurorishika/rpy-template/main/pyproject.toml"

    print("Downloading latest pyproject.toml into package folder...")
    os.system("curl {} -o pyproject.toml.tmp".format(pyproject_web))
    print("Download complete.")

    print()

    # go through pyproject.toml.tmp and:
    # 1. find all dependencies that are in the template but not in the current package
    # 2. add those dependencies to the current package

    print("Adding dependencies to pyproject.toml...")

    with open("pyproject.toml.tmp", "r") as f:
        lines_tmp = f.readlines()
    
    dependencies_tmp = []
    full_dependencies_tmp = []
    start = False
    for line in lines_tmp:
        if line == "[tool.poetry.dependencies]\n":
            start = True
            continue
        if start:
            if line == "\n":
                break
            dependencies_tmp.append(line.split("=")[0].strip())
            full_dependencies_tmp.append(line)
    
    with open("pyproject.toml", "r") as f:
        lines = f.readlines()

    dependencies = []
    full_dependencies = []
    start = False
    for line in lines:
        if line == "[tool.poetry.dependencies]\n":
            start = True
            continue
        if start:
            if line == "\n":
                break
            dependencies.append(line.split("=")[0].strip())
            full_dependencies.append(line)

    full_dependencies_to_add = []
    for i,dependency in enumerate(dependencies_tmp):
        if dependency not in dependencies:
            full_dependencies_to_add.append(full_dependencies_tmp[i])
    
    start = False
    with open("pyproject.toml", "w") as f:
        for line in lines:
            if line == "[tool.poetry.dependencies]\n":
                start = True 
            if start:
                if line == "\n":
                    for dependency in full_dependencies_to_add:
                        f.write(dependency)
                    f.write(line)
                    start = False
                else:
                    f.write(line)
            else:
                f.write(line)

    # remove pyproject.toml.tmp
    os.system("rm pyproject.toml.tmp")

    print("Complete.")

    print()

    # download latest .gitignore into package folder as a temporary file
    gitignore_web = "https://raw.githubusercontent.com/neurorishika/rpy-template/main/.gitignore"

    print("Downloading latest .gitignore into package folder...")
    os.system("curl {} -o .gitignore.tmp".format(gitignore_web))
    print("Download complete.")

    print()

    # go through .gitignore.tmp and:
    # 1. find all files that are in the template but not in the current package
    # 2. add those files to the current package

    print("Adding files to .gitignore...")

    with open(".gitignore.tmp", "r") as f:
        lines_tmp = f.readlines()

    with open(".gitignore", "r") as f:
        lines = f.readlines()
    
    for line in lines_tmp:
        if line not in lines and line != "\n":
            with open(".gitignore", "a") as f:
                f.write('\n')
                f.write(line)

    # remove .gitignore.tmp
    os.system("rm .gitignore.tmp")

    print("Complete.")

    print()

    # go through the entire repo and check if any files are more than 100MB
    # if so, inform the user and add them to .gitignore

    print("Checking for files larger than 100MB...")
    files = []
    for root, dirs, filenames in os.walk("."):
        for filename in filenames:
            files.append(os.path.join(root,filename))
    large_files = []
    for file in files:
        if os.path.getsize(file) > 100000000:
            large_files.append(file)

    # remove previous large files from .gitignore
    with open(".gitignore", "r") as f:
        lines = f.readlines()
    with open(".gitignore", "w") as f:
        for line in lines:
            if line.endswith("# LARGE FILE\n"):
                continue
            else:
                f.write(line)
    
    # add new large files to .gitignore
    if len(large_files) > 0:
        print("The following files are larger than 100MB. Adding them to .gitignore...")
        for file in large_files:
            print(file)
            with open(".gitignore", "a") as f:
                f.write(file + " # LARGE FILE\n")
        print("Files added to .gitignore.")
    else:
        print("No files larger than 100MB found.")
    
    print()

    # go through README.md and update the latest build date
    print("Updating README.md...")
    with open("README.md", "r") as f:
        lines = f.readlines()
    with open("README.md", "w") as f:
        for line in lines:
            if line.startswith("Latest Build Date:"):
                f.write("Latest Build Date: {}\n".format(datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")))
            elif "RPY" in line:
                f.write(line.replace("RPY", dir_name))
            elif "rpytemplate" in line:
                f.write(line.replace("rpytemplate", package_name))
            else:
                f.write(line)
            
    
    # replace the tree in README.md with the latest tree
    print("Updating tree in README.md...")

    # get the latest tree using git and save it to a variable using os.system and output redirection
    try:
        tree_command = ['git', 'tree']
        tree = check_output(tree_command).decode('utf-8')
        print(tree)

        # replace the tree in README.md
        with open("README.md", "r") as f:
            lines = f.readlines()
        with open("README.md", "w") as f:
            for line in lines:
                if line.startswith("The project is organized as follows:"):
                    f.write("The project is organized as follows:\n")
                    f.write("```\n")
                    f.write(tree)
                    f.write("```\n")
                    break
                else:
                    f.write(line)
    except:
        print("Error: Please install git, and add the alias 'tree' provided in the README.md")
        exit(-1)

    print("README.md updated.")

    print()
    
    # run poetry lock
    print("Running poetry lock...")
    call("poetry lock", shell=True)
    print("Poetry lock complete.")

    print("PROJECT TEMPLATE UPDATE COMPLETE")