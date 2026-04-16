from datetime import datetime

from flymanager.app.services.bloomington import update_gene_collections
from flymanager.utils.stock_sources import (
    collect_compatible_gene_metadata_from_flybase, resolve_flybase_stocks_path)


def update_gene_metadata_from_flybase(app, stocks_file_path=None, timestamp=None):
    with app.app_context():
        from flymanager.app import db

        timestamp = timestamp or datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        resolved_stocks_path = resolve_flybase_stocks_path(stocks_file_path)
        print(f"[{timestamp}] Starting FlyBase gene metadata update from {resolved_stocks_path}...")

        all_components, summary = collect_compatible_gene_metadata_from_flybase(
            resolved_stocks_path
        )

        print(f"[{timestamp}] FlyBase rows scanned: {summary['total_rows']}")
        print(f"[{timestamp}] Dmel rows scanned: {summary['dmel_rows']}")
        print(f"[{timestamp}] Compatible rows harvested: {summary['supported_rows']}")
        print(f"[{timestamp}] Unsupported rows skipped: {summary['unsupported_rows']}")

        for chromosome_index, components in all_components.items():
            print(
                f"[{timestamp}] Compatible components for chromosome {chromosome_index + 1}: {len(components)}"
            )

        update_gene_collections(all_components, db, timestamp)
        print(f"[{timestamp}] FlyBase gene metadata update completed successfully")
        return summary


def manual_update_flybase_gene_metadata_only(app, stocks_file_path=None):
    timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    return update_gene_metadata_from_flybase(
        app,
        stocks_file_path=stocks_file_path,
        timestamp=timestamp,
    )