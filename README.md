# Rishika's Template for Python Projects

This is a [Poetry](https://python-poetry.org/)-enabled template for Python projects for use by [Rishika Mohanta](https://neurorishika.github.io).

Also you need to setup a git alias for tree generation by running the following command on the terminal:

```
git config --global alias.tree '! git ls-tree --full-name --name-only -t -r HEAD | sed -e "s/[^-][^\/]*\//   |/g" -e "s/|\([^ ]\)/|-- \1/"'
```

Expected IDE: [Visual Studio Code](https://code.visualstudio.com/)

Default settings are for Python 3.9 and above and the following packages (and dependencies) are included:
- numpy
- pandas 
- matplotlib
- jupyter
- ipython
- scipy
- scikit-learn
- argparse
- mkinit
- cryptography
- lazy-loader
- split-file-reader
- statannotations
- joblib
- tqdm


## Installation

Create a new project using this template on GitHub and clone it locally. Then, run the following commands in the project directory after installing Poetry:

```
cd <project directory>
python utils/quickstart.py
python utils/update.py
poetry utils/build.py
```

## Update & Build Packages and Setup __init__.py for package with lazy imports

To update the packages and build the package, followed by setting up the __init__.py file for the package with lazy imports, run the following command:

```
poetry run python utils/update.py
poetry run python utils/build.py
```

## Organization

The project is organized as follows:

```
<repo-name(default:rpy-template)>/
├── <package-name(default:rpytemplate)>/ - Python package (save all reusable code here using appropriate subdirectories; tracked by git)
│   ├── __init__.py
│   ├── rdp_client.py - for Rishika's Data Protection (RDP) Standard
│   ├── module1.py
│   ├── <subdirectory1>
│   │   ├── __init__.py
│   │   ├── <module1.1>.py
│   │   └── ...
│   ├── <subdirectory2>
│   │   ├── __init__.py
│   │   ├── <module2.1>.py
│   │   └── ...
│   └── ...
├── data/ - data directory (partially tracked by git)
│   ├── <datafolder1>/  - data folder (not tracked by git)
│   │   └── ...
│   ├── datafolder.ezip - encrypted data zip file (tracked by git)
│   └── ...
├── analysis/ - analysis directory (tracked by git)
│   ├── ...
│   └── .gitkeep
├── processed_data/ - processed data directory (tracked by git)
│   ├── ... (make sure to not save any raw data or files larger than 100 MB here)
│   └── .gitkeep
├── utils/ - utilities directory for useful scripts (tracked by git)
│   ├── build.py - build package and setup __init__.py for package with lazy imports
│   ├── quickstart.py - quickstart script to setup project
│   ├── update.py - update template and build package
│   └── ...
├── scripts/ - scripts directory (tracked by git)
│   ├── .gitkeep
│   └── ...
├── tests/ - tests directory (tracked by git)
│   ├── __init__.py
│   └── ...
├── .gitignore - gitignore file
├── director.path - file containing path to the repo directory
├── poetry.lock - poetry lock file
├── pyproject.toml - poetry project file
├── poetry.toml - poetry configuration file
└── README.md - README file
```











