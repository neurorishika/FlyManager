# *D. manager* -  a fruit fly manager for the lab


*D. manager* (short for "*Drosophila manager*") is a comprehensive tool for managing genetic stocks, crosses, and trays in a laboratory setting. It provides features like tracking vial lifetimes, generating labels, and sending reminders.

## Features
- Manage genetic stocks and crosses
- Track tray positions and vial lifetimes
- Generate labels and reminders
- Bulk operations for flipping and status updates

## Installation
1. Clone the repository:
   ```bash
   git clone https://github.com/neurorishika/FlyManager.git
   cd FlyManager
   ```
2. Install dependencies using Poetry:
   ```bash
   poetry install
   ```
3. Run the application:
   ```bash
   poetry run python flymanager/app/run.py
   ```

## Usage
- Access the application at `http://localhost:5234`.
- Navigate through modules like Stock Explorer, Cross Explorer, and Tray Management.

## Contributing
Contributions are welcome! Please fork the repository and submit a pull request.

## License
This project is licensed under the BSD 3-Clause License. See the LICENSE file for details.

<!-- badges: start -->
<!-- badges: end -->

Author: [Rishika Mohanta](https://neurorishika.github.io/)

Latest Build Date: 2024-07-12 10:02:36

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
