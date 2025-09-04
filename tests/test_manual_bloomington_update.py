from flymanager.app import create_app
from flymanager.app.services.bloomington import manual_update_gene_metadata_only
from flymanager.app import db

app = create_app()

print("Before update:")
with app.app_context():
    print(f"X chromosome genes: {db.genesX.count_documents({})}")
    print(f"2nd chromosome genes: {db.genes2nd.count_documents({})}")
    print(f"3rd chromosome genes: {db.genes3rd.count_documents({})}")
    print(f"4th chromosome genes: {db.genes4th.count_documents({})}")

print("\nRunning gene metadata update...")
manual_update_gene_metadata_only(app)

print("\nAfter update:")
with app.app_context():
    print(f"X chromosome genes: {db.genesX.count_documents({})}")
    print(f"2nd chromosome genes: {db.genes2nd.count_documents({})}")
    print(f"3rd chromosome genes: {db.genes3rd.count_documents({})}")
    print(f"4th chromosome genes: {db.genes4th.count_documents({})}")
