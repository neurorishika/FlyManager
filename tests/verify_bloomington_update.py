from flymanager.app import create_app
from flymanager.app.services.stock_updater import manual_update_bloomington_stock
from pathlib import Path
from datetime import datetime

app = create_app()

print("Testing Bloomington stock data update...")
manual_update_bloomington_stock(app)

# Define paths
base_dir = Path(__file__).parent
data_dir = base_dir / "data"
backup_dir = data_dir / "backup"
current_file = data_dir / "bloomington.csv"

print("=== Bloomington Stock Data Status ===")
print(f"Data directory: {data_dir}")
print(f"Backup directory: {backup_dir}")
print()

# Check current file
if current_file.exists():
    stat = current_file.stat()
    modified_time = datetime.fromtimestamp(stat.st_mtime)
    print(f"Current file: {current_file.name}")
    print(f"  Size: {stat.st_size:,} bytes")
    print(f"  Last modified: {modified_time.strftime('%Y-%m-%d %H:%M:%S')}")
else:
    print("Current file: NOT FOUND")

print()

# Check backups
if backup_dir.exists():
    backup_files = sorted(backup_dir.glob("bloomington_*.csv"))
    print(f"Backup files ({len(backup_files)} found):")
    for backup_file in backup_files:
        stat = backup_file.stat()
        modified_time = datetime.fromtimestamp(stat.st_mtime)
        print(f"  {backup_file.name}")
        print(f"    Size: {stat.st_size:,} bytes")
        print(f"    Created: {modified_time.strftime('%Y-%m-%d %H:%M:%S')}")
else:
    print("Backup directory: NOT FOUND")

print("Test completed!")
