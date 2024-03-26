import os

if __name__ == "__main__":
    
    print("STARTING PROJECT SETUP PROCESS")
    print("==============================")

    # get the current directory name to use as the project name
    project_name = os.path.basename(os.getcwd()).lower()
    dir_name = os.path.basename(os.getcwd())

    # verify that the project name is valid
    if not project_name.isidentifier():
        # remove any non-alphanumeric characters
        project_name = "".join([c for c in project_name if c.isalnum()])
        # add an underscore if the project name starts with a number
        if project_name[0].isnumeric():
            project_name = "_" + project_name
    
    # ask user to confirm project name
    while True:
        confirm = input("Project name will be {}. Proceed? (y/n) ".format(project_name))
        if confirm.lower() == "y":
            break
        elif confirm.lower() == "n":
            # ask user to enter a new project name
            while True:
                project_name = input("Please enter a new project name (all lowercase, no spaces): ")
                if project_name.isidentifier():
                    project_name = project_name.lower()
                    project_name = "".join([c for c in project_name if c.isalnum()])
                    break
                else:
                    print("Invalid project name. Please enter a valid project name.")
            break
        else:
            print("Invalid input. Please enter 'y' or 'n'.")
      
    # change rpytemplate to the project name
    print("Setting project name to {}...".format(project_name),end="")
    os.rename("rpytemplate", project_name)
    # change name in pyproject.toml
    with open("pyproject.toml", "r") as f:
        lines = f.readlines()
    with open("pyproject.toml", "w") as f:
        for line in lines:
            f.write(line.replace("rpytemplate", project_name))
    print("done.")

    # set directory.path as the place where the project is located
    print("Setting directory.path as the place where the project is located...",end="")
    with open("directory.path", "w") as f:
        f.write(os.getcwd())
    print("done.")

    if os.path.exists("project_readme.md"):
        # go through project_readme.md and replace all instances of rpytemplate with the project name
        # and rpy-template with the directory name, and save it as README.md and finally delete project_readme.md
        print("Replacing all instances of rpytemplate with the project name in project_readme.md...",end="")
        with open("project_readme.md", "r") as f:
            lines = f.readlines()
        with open("README.md", "w") as f:
            for line in lines:
                f.write(line.replace("rpytemplate", project_name).replace("RPY", dir_name))
        print("done.")
    
    print("Project {} created! Please run 'poetry run python utils/update.py' to finish installation.".format(project_name))
    

