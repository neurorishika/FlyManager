import os
import requests
import shutil
import pandas as pd
from datetime import datetime
from pathlib import Path


def update_bloomington_stock_data(app):
    """
    Monthly scheduled task to backup current Bloomington data, download the latest version,
    and update gene metadata from the new data.
    """
    with app.app_context():
        timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        print(f"[{timestamp}] Starting Bloomington stock data update...")

        try:
            # Define paths
            base_dir = Path(app.root_path).parent.parent  # Go up to FlyManager root
            data_dir = base_dir / "data"
            backup_dir = data_dir / "backup"
            current_file = data_dir / "bloomington.csv"

            # Create backup directory if it doesn't exist
            backup_dir.mkdir(exist_ok=True)

            # Create timestamped backup of current file
            if current_file.exists():
                backup_timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
                backup_filename = f"bloomington_{backup_timestamp}.csv"
                backup_path = backup_dir / backup_filename

                print(f"[{timestamp}] Backing up current file to {backup_filename}")
                shutil.copy2(current_file, backup_path)
                print(f"[{timestamp}] Backup created successfully")
            else:
                print(
                    f"[{timestamp}] No existing bloomington.csv found, skipping backup"
                )

            # Download new file
            url = "https://bdsc.indiana.edu/pdf/bloomington.csv"
            print(f"[{timestamp}] Downloading latest data from {url}")

            response = requests.get(url, timeout=30)
            response.raise_for_status()  # Raises an HTTPError for bad responses

            # Save new file
            with open(current_file, "wb") as f:
                f.write(response.content)

            print(
                f"[{timestamp}] Successfully downloaded and saved new bloomington.csv"
            )
            print(f"[{timestamp}] File size: {len(response.content)} bytes")

            # Update gene metadata from the new data
            print(f"[{timestamp}] Updating gene metadata from new Bloomington data...")
            update_gene_metadata_from_bloomington(current_file, app, timestamp)

            # Clean up old backups (keep only last 12 months)
            cleanup_old_backups(backup_dir, timestamp)

        except requests.exceptions.RequestException as e:
            print(f"[{timestamp}] Error downloading file: {e}")
        except Exception as e:
            print(f"[{timestamp}] Error during Bloomington stock update: {e}")


def update_gene_metadata_from_bloomington(csv_file_path, app, timestamp):
    """
    Parse the Bloomington CSV file and update gene metadata in the database.
    """
    try:
        from flymanager.app import db

        # Read the CSV file
        df = pd.read_csv(csv_file_path)

        # Filter the data (same logic as in your script)
        df = df[df["Ch # all"].notna()]
        df = df[~df["Ch # all"].str.contains("Y")]
        df = df[~df["Ch # all"].str.contains("mt")]
        df = df[~df["Ch # all"].str.contains("U")]
        df = df[~df["Ch # all"].str.contains("f")]

        print(f"[{timestamp}] Processing {len(df)} valid Bloomington entries...")

        # Define the chromosome components dictionary
        all_components = {0: [], 1: [], 2: [], 3: []}

        # Iterate over the rows in the dataframe
        for index, row in df.iterrows():
            ch_all = row["Ch # all"]
            genotype = row["Genotype"]
            stock = row["Stk #"]

            # If "Ch # all" is "wt", handle wild type cases
            if ch_all == "wt":
                genotype = "[" + genotype + "]"
                all_components[0].append("w" + genotype)
                all_components[1].append("+" + genotype)
                all_components[2].append("+" + genotype)
                all_components[3].append("+" + genotype)
            else:
                # Skip if duplications or other complex constructs are present
                if any(
                    x in genotype
                    for x in ["Dp(", "Df(", "T(", "C(", "In(", "Tp(", "l("]
                ):
                    continue

                ch_components = ch_all.split(";")
                genotype_components = genotype.split(";")

                # Check if the number of chromosome components matches genotype components
                if len(ch_components) != len(genotype_components):
                    print(f"[{timestamp}] Skipping stock {stock}: component mismatch")
                    continue

                # Assign each genotype component to the corresponding chromosome
                for ch, gen in zip(ch_components, genotype_components):
                    try:
                        ch_num = int(ch)
                        # split by / and add each component to the corresponding chromosome
                        compos = gen.strip().split("/")
                        for comp in compos:
                            all_components[ch_num - 1].append(comp.strip())
                    except ValueError:
                        print(
                            f"[{timestamp}] Error: Unable to parse chromosome number '{ch}' in row {index}"
                        )
                    except IndexError:
                        print(
                            f"[{timestamp}] Error: Chromosome number '{ch}' out of bounds in row {index}"
                        )

        # Get existing components from the database
        all_components_db = {
            0: [x["Value"] for x in db.genesX.find()],
            1: [x["Value"] for x in db.genes2nd.find()],
            2: [x["Value"] for x in db.genes3rd.find()],
            3: [x["Value"] for x in db.genes4th.find()],
        }

        for n in range(4):
            print(
                f"[{timestamp}] Existing components for chromosome {n+1}: {len(all_components_db[n])}"
            )

        # Add the new components to the existing components
        for n in range(4):
            all_components[n].extend(all_components_db[n])

        # Get the unique components for each chromosome
        for n, chr in all_components.items():
            all_components[n] = list(set(chr))
            print(
                f"[{timestamp}] Unique components for chromosome {n+1}: {len(all_components[n])}"
            )

        # Update the database with new gene components
        update_gene_collections(all_components, db, timestamp)

        print(f"[{timestamp}] Gene metadata update completed successfully")

    except Exception as e:
        print(f"[{timestamp}] Error updating gene metadata: {e}")


def update_gene_collections(all_components, db, timestamp):
    """
    Update the gene collections in the database with new components.
    """
    try:
        collections = {0: db.genesX, 1: db.genes2nd, 2: db.genes3rd, 3: db.genes4th}

        chromosome_names = {0: "X", 1: "2nd", 2: "3rd", 3: "4th"}

        for n in range(4):
            collection = collections[n]
            new_components = all_components[n]
            chr_name = chromosome_names[n]

            # Get existing components
            existing_components = set(x["Value"] for x in collection.find())
            new_unique_components = set(new_components) - existing_components

            if new_unique_components:
                # Insert new components
                new_docs = [{"Value": comp} for comp in new_unique_components]
                collection.insert_many(new_docs)
                print(
                    f"[{timestamp}] Added {len(new_unique_components)} new components to chromosome {chr_name}"
                )
            else:
                print(
                    f"[{timestamp}] No new components found for chromosome {chr_name}"
                )

            print(
                f"[{timestamp}] Total components for chromosome {chr_name}: {collection.count_documents({})}"
            )

    except Exception as e:
        print(f"[{timestamp}] Error updating gene collections: {e}")


def cleanup_old_backups(backup_dir, timestamp):
    """
    Remove backup files older than 12 months to save space.
    """
    try:
        from datetime import datetime, timedelta

        cutoff_date = datetime.now() - timedelta(days=365)  # 12 months ago

        backup_files = list(backup_dir.glob("bloomington_*.csv"))
        removed_count = 0

        for backup_file in backup_files:
            try:
                # Extract timestamp from filename (bloomington_YYYYMMDD_HHMMSS.csv)
                filename_parts = backup_file.stem.split("_")
                if len(filename_parts) >= 3:
                    date_str = filename_parts[1]
                    time_str = filename_parts[2]
                    file_datetime = datetime.strptime(
                        f"{date_str}_{time_str}", "%Y%m%d_%H%M%S"
                    )

                    if file_datetime < cutoff_date:
                        backup_file.unlink()
                        removed_count += 1
                        print(f"[{timestamp}] Removed old backup: {backup_file.name}")
            except (ValueError, IndexError) as e:
                # Skip files that don't match the expected format
                print(
                    f"[{timestamp}] Skipping file with unexpected format: {backup_file.name}"
                )
                continue

        if removed_count > 0:
            print(f"[{timestamp}] Cleaned up {removed_count} old backup files")
        else:
            print(f"[{timestamp}] No old backup files to clean up")

    except Exception as e:
        print(f"[{timestamp}] Error during backup cleanup: {e}")


def manual_update_bloomington_stock(app):
    """
    Manual trigger for updating Bloomington stock data (for testing or manual updates).
    """
    update_bloomington_stock_data(app)


def manual_update_gene_metadata_only(app):
    """
    Manual trigger for updating gene metadata from existing Bloomington data.
    """
    with app.app_context():
        timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        print(f"[{timestamp}] Starting manual gene metadata update...")

        try:
            # Define paths
            base_dir = Path(app.root_path).parent.parent
            data_dir = base_dir / "data"
            current_file = data_dir / "bloomington.csv"

            if not current_file.exists():
                print(
                    f"[{timestamp}] Error: bloomington.csv not found at {current_file}"
                )
                return

            # Update gene metadata from existing data
            update_gene_metadata_from_bloomington(current_file, app, timestamp)

        except Exception as e:
            print(f"[{timestamp}] Error during manual gene metadata update: {e}")
