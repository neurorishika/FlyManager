# RPY

<!-- badges: start -->
<!-- badges: end -->

Author: [Rishika Mohanta](https://neurorishika.github.io/)

Latest Build Date: BUILD_DATE

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
rpy-template/
├── rpytemplate/ - Python package for common code
│   ├── __init__.py
│   └── rdp_client.py - for Rishika's Data Protection (RDP) Standard
├── data/ - data directory
│   └── .gitkeep
├── analysis/ - analysis directory
│   └── .gitkeep
├── processed_data/ - processed data directory (tracked by git)
│   └── .gitkeep
├── utils/ - utilities directory for useful scripts (tracked by git)
│   ├── build.py - build package and setup __init__.py for package with lazy imports
│   ├── quickstart.py - quickstart script to setup project
│   └── update.py - update template and build package
├── scripts/ - scripts directory (tracked by git)
│   └── .gitkeep
├── tests/ - tests directory (tracked by git)
│   └── __init__.py
├── .gitignore - gitignore file
├── director.path - file containing path to the repo directory
├── poetry.lock - poetry lock file
├── pyproject.toml - poetry project file
└── README.md - README file
```