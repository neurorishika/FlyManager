# FlyManager

<!-- badges: start -->
<!-- badges: end -->

Author: [Rishika Mohanta](https://neurorishika.github.io/)

Latest Build Date: 2024-07-12 10:02:36

## About the Project

Project description is being updated. Please check back later.

## Instructions

This is a [Poetry](https://python-poetry.org/)-enabled python project. Poetry installs a virtual environment in the project directory and all packages are installed in this virtual environment. This means that you do not need to install any packages in your system. The virtual environment is automatically activated when you run the project through Poetry. 

If you use [VS Code](https://code.visualstudio.com/), you can set the Python interpreter to the Poetry virtual environment `.venv` in the project directory for script execution and debugging and use the Poetry virtual environment `.venv` for the Jupyter kernel.

First, you need to setup a git alias for tree generation by running the following command on the terminal:

```
git config --global alias.tree '! git ls-tree --full-name --name-only -t -r HEAD | sed -e "s/[^-][^\/]*\//   |/g" -e "s/|\([^ ]\)/|-- \1/"'
```

To run the project, make sure you have Poetry installed and run the following commands in the project directory:

```
poetry run python utils/update.py
poetry run python utils/build.py
```

To run the Jupyter notebook, run the following command in the project directory:

```
poetry run jupyter notebook
```

## Project Organization

The project is organized as follows:
```
.DS_Store
.gitignore
LICENSE
README.md
analysis
   |-- .gitkeep
data
   |-- .gitkeep
poetry.lock
poetry.toml
processed_data
   |-- .gitkeep
project_readme.md
pyproject.toml
rpytemplate
   |-- __init__.py
   |-- rdp_client.py
scripts
   |-- .gitkeep
tests
   |-- __init__.py
utils
   |-- build.py
   |-- quickstart.py
   |-- update.py
```
