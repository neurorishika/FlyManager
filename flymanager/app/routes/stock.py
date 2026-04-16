# flymanager/app/routes/stock.py
import os
import traceback
from datetime import datetime
from urllib.parse import unquote

from flask import (Blueprint, current_app, flash, jsonify, redirect,
                   render_template, request, session, url_for)
from fuzzywuzzy import fuzz

from flymanager.app import db
from flymanager.app.routes.auth import login_required
from flymanager.app.routes.explorer_utils import (collect_unique_values,
                                                  get_explorer_filter_state,
                                                  set_flip_display_fields)
from flymanager.app.security import (get_json_payload, limiter,
                                     normalize_identifier_list,
                                     normalize_optional_text, parse_int_value)
from flymanager.app.settings import DEFAULT_STOCK_PROPERTY_VALUES
from flymanager.utils.genetics import get_stock_genotype, qc_genotype
from flymanager.utils.labels import generate_label_pdf
from flymanager.utils.mongo import (add_metadata, add_to_stock, delete_stock,
                                    edit_stock, get_eclosion_in, get_flip_in,
                                    get_metadata, get_user_initials,
                                    get_user_stocks, update_stock_vials,
                                    write_activity)
from flymanager.utils.utils import clean_tagify_data, increment_replicate_id

bp = Blueprint("stock", __name__)  # url_prefix is defined in app/__init__


@bp.route("/explorer", methods=["GET", "POST"])
@login_required
def stock_explorer():
    username = session.get("username")
    try:
        stocks = get_user_stocks(username, db)
        all_stocks_for_filters = list(stocks)
        stocks = sorted(
            stocks,
            key=lambda x: (
                str(x.get("TrayID", "")),
                int(
                    float(x.get("TrayPosition", "0") or "0")
                ),  # Handle empty/non-numeric TrayPosition
            ),
        )

        # Add derived fields
        for stock in stocks:
            stock["FlipIn"] = get_flip_in(stock)
            set_flip_display_fields(
                stock,
                raw_value=stock["FlipIn"],
                display_field="FlipInDisplay",
            )

            stock["EclosesIn"] = get_eclosion_in(stock)

        # Initial unique values for filters
        unique_values = collect_unique_values(
            all_stocks_for_filters,
            {
                "Type": lambda stock: stock.get("Type"),
                "TrayID": lambda stock: stock.get("TrayID"),
                "Status": lambda stock: stock.get("Status"),
                "FoodType": lambda stock: stock.get("FoodType"),
                "Provenance": lambda stock: str(stock.get("Provenance", "")).split("/")[0],
                "Species": lambda stock: stock.get("Species"),
            },
        )

    except Exception as e:
        print(f"Error fetching stocks for explorer: {e}")
        stocks = []
        all_stocks_for_filters = []
        unique_values = {
            k: []
            for k in ["Type", "TrayID", "Status", "FoodType", "Provenance", "Species"]
        }

    filter_state, redirect_response = get_explorer_filter_state(
        session_key="stock_filter_state",
        clear_endpoint="stock.stock_explorer",
        field_names=(
            "filterType",
            "filterTrayID",
            "filterStatus",
            "filterFoodType",
            "filterProvenance",
            "filterSpecies",
            "searchQuery",
        ),
    )
    if redirect_response is not None:
        return redirect_response

    filtered_stocks = _apply_stock_filters(stocks, filter_state)

    if filter_state:
        unique_values = collect_unique_values(
            filtered_stocks,
            {
                "Type": lambda stock: stock.get("Type"),
                "TrayID": lambda stock: stock.get("TrayID"),
                "FoodType": lambda stock: stock.get("FoodType"),
                "Provenance": lambda stock: str(stock.get("Provenance", "")).split("/")[0],
                "Species": lambda stock: stock.get("Species"),
            },
        )
        unique_values["Status"] = collect_unique_values(
            all_stocks_for_filters,
            {"Status": lambda stock: stock.get("Status")},
        )["Status"]

    return render_template(
        "stock/stock_explorer.html",
        username=username,
        stocks=filtered_stocks,
        unique_values=unique_values,
        filter_state=filter_state,
    )


def _apply_stock_filters(stocks, filters):
    """Helper function to apply filters to a list of stocks."""
    filtered_stocks = list(stocks)  # Make a copy

    filter_type = filters.get("filterType")
    filter_tray_id = filters.get("filterTrayID")
    filter_status = filters.get("filterStatus")
    filter_food_type = filters.get("filterFoodType")
    filter_provenance = filters.get("filterProvenance")
    filter_species = filters.get("filterSpecies")
    search_query = filters.get("searchQuery")
    no_longer_maintained_status = "No longer maintained"

    if filter_type:
        filtered_stocks = [
            s for s in filtered_stocks if str(s.get("Type", "")) == filter_type
        ]
    if filter_tray_id:
        filtered_stocks = [
            s for s in filtered_stocks if str(s.get("TrayID", "")) == filter_tray_id
        ]

    if filter_status == no_longer_maintained_status:
        filtered_stocks = [
            s
            for s in filtered_stocks
            if str(s.get("Status", "")) == no_longer_maintained_status
        ]
    elif filter_status:
        filtered_stocks = [
            s for s in filtered_stocks if str(s.get("Status", "")) == filter_status
        ]
    else:
        filtered_stocks = [
            s
            for s in filtered_stocks
            if str(s.get("Status", "")) != no_longer_maintained_status
        ]

    if filter_food_type:
        filtered_stocks = [
            s for s in filtered_stocks if str(s.get("FoodType", "")) == filter_food_type
        ]
    if filter_provenance:
        filtered_stocks = [
            s
            for s in filtered_stocks
            if str(s.get("Provenance", "")).split("/")[0] == filter_provenance
        ]
    if filter_species:
        filtered_stocks = [
            s for s in filtered_stocks if str(s.get("Species", "")) == filter_species
        ]

    if search_query:
        sq_lower = search_query.lower()

        def match(stock):
            search_fields = [
                stock.get("SourceID", ""),
                stock.get("Genotype", ""),
                stock.get("Name", ""),
                stock.get("AltReference", ""),
                stock.get("SeriesID", ""),
                stock.get("TrayID", ""),
                stock.get("TrayPosition", ""),
                stock.get("Comments", ""),
            ]
            search_string = " ".join(
                str(field) for field in search_fields if field
            ).lower()
            # Using partial_ratio might be slow; consider simpler substring check first
            # return sq_lower in search_string
            return (
                fuzz.partial_ratio(search_string, sq_lower) > 80
            )  # Keep original fuzzy logic

        filtered_stocks = [s for s in filtered_stocks if match(s)]

    return filtered_stocks


@bp.route("/add", methods=["GET", "POST"])
@bp.route(
    "/add/<source_stock_id>", methods=["GET", "POST"]
)  # Route for pre-filling from existing stock
@login_required
def add_stock(source_stock_id=None):
    username = session.get("username")
    error_message = None
    stock_data = {
        "altReference": DEFAULT_STOCK_PROPERTY_VALUES["AltReference"],
        "type": DEFAULT_STOCK_PROPERTY_VALUES["Type"],
        "foodType": DEFAULT_STOCK_PROPERTY_VALUES["FoodType"],
        "status": DEFAULT_STOCK_PROPERTY_VALUES["Status"],
        "seriesID": DEFAULT_STOCK_PROPERTY_VALUES["SeriesID"],
        "replicateID": DEFAULT_STOCK_PROPERTY_VALUES["ReplicateID"],
        "vialLifetime": DEFAULT_STOCK_PROPERTY_VALUES["VialLifetime"],
        "flipFrequency": DEFAULT_STOCK_PROPERTY_VALUES["FlipFrequency"],
        "developmentalTime": DEFAULT_STOCK_PROPERTY_VALUES["DevelopmentalTime"],
        "comments": DEFAULT_STOCK_PROPERTY_VALUES["Comments"],
        "provenance": DEFAULT_STOCK_PROPERTY_VALUES["Provenance"],
        "species": DEFAULT_STOCK_PROPERTY_VALUES["Species"],
    }

    # Get metadata for dropdowns/tagify
    try:
        types = get_metadata("types", db)
        food_types = get_metadata("food_types", db)
        provenances = get_metadata("provenances", db)
        genesX = get_metadata("genesX", db)
        genes2 = get_metadata("genes2nd", db)
        genes3 = get_metadata("genes3rd", db)
        genes4 = get_metadata("genes4th", db)
        species_list = get_metadata("species", db)
    except Exception as e:
        print(f"Error fetching metadata: {e}")
        return "Error fetching metadata", 500

    # If source_stock_id is provided, fetch data to pre-fill the form
    if source_stock_id and request.method == "GET":
        print(f"Pre-filling form with source stock ID: {source_stock_id}")
        try:
            stock = db["stocks"].find_one(
                {"UniqueID": source_stock_id, "User": username}
            )  # Ensure user owns source stock
            if stock:
                # Prepare data for form pre-filling, increment replicate ID
                stock_data = {
                    "sourceType": "INTERNAL",  # Indicate source is internal
                    "sourceID": stock["UniqueID"],
                    "genotype": stock["Genotype"],
                    "name": stock["Name"],
                    "altReference": stock.get(
                        "AltReference", DEFAULT_STOCK_PROPERTY_VALUES["AltReference"]
                    ),
                    "type": stock.get("Type", DEFAULT_STOCK_PROPERTY_VALUES["Type"]),
                    "foodType": stock.get(
                        "FoodType", DEFAULT_STOCK_PROPERTY_VALUES["FoodType"]
                    ),
                    "status": stock.get(
                        "Status", DEFAULT_STOCK_PROPERTY_VALUES["Status"]
                    ),
                    "seriesID": stock.get(
                        "SeriesID", DEFAULT_STOCK_PROPERTY_VALUES["SeriesID"]
                    ),
                    "replicateID": increment_replicate_id(
                        stock.get(
                            "ReplicateID", DEFAULT_STOCK_PROPERTY_VALUES["ReplicateID"]
                        )
                    ),
                    "vialLifetime": stock.get(
                        "VialLifetime", DEFAULT_STOCK_PROPERTY_VALUES["VialLifetime"]
                    ),
                    "flipFrequency": stock.get(
                        "FlipFrequency", DEFAULT_STOCK_PROPERTY_VALUES["FlipFrequency"]
                    ),
                    "developmentalTime": stock.get(
                        "DevelopmentalTime",
                        DEFAULT_STOCK_PROPERTY_VALUES["DevelopmentalTime"],
                    ),
                    "comments": stock.get(
                        "Comments", DEFAULT_STOCK_PROPERTY_VALUES["Comments"]
                    ),
                    "provenance": stock.get(
                        "Provenance", DEFAULT_STOCK_PROPERTY_VALUES["Provenance"]
                    ),
                    "species": stock.get(
                        "Species", DEFAULT_STOCK_PROPERTY_VALUES["Species"]
                    ),
                }
            else:
                flash(f"Source stock with ID {source_stock_id} not found.", "warning")
        except Exception as e:
            print(f"Error fetching source stock {source_stock_id}: {e}")
            flash("Error fetching source stock data.", "error")

    if request.method == "POST":
        try:
            # --- Process Genotype Inputs ---
            genesX_input = clean_tagify_data(request.form.get("genotypeX"))
            for gene in genesX_input:
                if gene not in genesX:
                    add_metadata("genesX", gene, db)
            genesX_str = "/".join(genesX_input) if len(genesX_input) > 0 else ""

            genes2_input = clean_tagify_data(request.form.get("genotype2"))
            for gene in genes2_input:
                if gene not in genes2:
                    add_metadata("genes2nd", gene, db)
            genes2_str = "/".join(genes2_input) if len(genes2_input) > 0 else ""

            genes3_input = clean_tagify_data(request.form.get("genotype3"))
            for gene in genes3_input:
                if gene not in genes3:
                    add_metadata("genes3rd", gene, db)
            genes3_str = "/".join(genes3_input) if len(genes3_input) > 0 else ""

            genes4_input = clean_tagify_data(request.form.get("genotype4"))
            for gene in genes4_input:
                if gene not in genes4:
                    add_metadata("genes4th", gene, db)
            genes4_str = "/".join(genes4_input) if len(genes4_input) > 0 else ""

            full_genotype = ";".join([genesX_str, genes2_str, genes3_str, genes4_str])
            print(f"Full genotype string: {full_genotype}")
            qc_passed, final_genotype = qc_genotype(
                full_genotype
            )  # Assuming qc_genotype exists
            if not qc_passed:
                raise ValueError(
                    f"Genotype QC failed: {final_genotype}"
                )  # Raise error to be caught below

            # --- Process Other Tagify Inputs ---
            type_input = clean_tagify_data(request.form.get("type"))[
                0
            ]  # Expect single value
            if type_input not in types:
                add_metadata("types", type_input, db)

            food_type_input = clean_tagify_data(request.form.get("foodType"))[
                0
            ]  # Expect single value
            if food_type_input not in food_types:
                add_metadata("food_types", food_type_input, db)

            provenance_input = clean_tagify_data(request.form.get("provenance"))
            for prov in provenance_input:
                if prov not in provenances:
                    add_metadata("provenances", prov, db)
            provenance_str = "/".join(provenance_input)

            species_input = clean_tagify_data(request.form.get("species"))[
                0
            ]  # Expect single value
            if species_input not in species_list:
                add_metadata("species", species_input, db)

            # --- Collect Form Data ---
            new_stock_data = {
                "SourceID": request.form.get("sourceID"),
                "Genotype": final_genotype,
                "Name": request.form.get("name"),
                "AltReference": request.form.get("altReference"),
                "Type": type_input,
                "SeriesID": request.form.get("seriesID"),
                "ReplicateID": request.form.get("replicateID"),
                "Status": request.form.get("status"),
                "FoodType": food_type_input,
                "Provenance": provenance_str,
                "VialLifetime": request.form.get("vialLifetime", type=int),
                "FlipFrequency": request.form.get("flipFrequency", type=int),
                "DevelopmentalTime": request.form.get("developmentalTime", type=int),
                "Comments": request.form.get(
                    "comments", DEFAULT_STOCK_PROPERTY_VALUES["Comments"]
                ),
                "Species": species_input,
            }

            # Remove empty/None fields before saving? Original code did this.
            new_stock_data = {
                k: v for k, v in new_stock_data.items() if v is not None and v != ""
            }

            # --- Add to Database ---
            success, uid_or_message = add_to_stock(username, new_stock_data, db)

            if success:
                print(f"Stock {uid_or_message} added successfully for {username}.")
                # Update vials for the newly added stock
                try:
                    newly_added_stock = db["stocks"].find_one(
                        {"UniqueID": uid_or_message, "User": username}
                    )
                    if newly_added_stock:
                        update_stock_vials(newly_added_stock, username, db)
                        print(f"Vials updated for new stock {uid_or_message}.")
                    else:
                        print(
                            f"Warning: Could not find newly added stock {uid_or_message} to update vials."
                        )
                except Exception as vial_e:
                    print(
                        f"Error updating vials for new stock {uid_or_message}: {vial_e}"
                    )

                # Log activity
                write_activity(username, f"Added stock {uid_or_message}", db)
                return redirect(url_for("stock.stock_explorer"))
            else:
                error_message = uid_or_message  # Error message from add_to_stock
                stock_data = new_stock_data  # Keep submitted data in form

        except ValueError as ve:
            error_message = str(ve)
            stock_data = request.form.to_dict()  # Keep submitted data
        except Exception as e:
            print(f"Error adding stock for {username}: {e}")
            error_message = f"An unexpected error occurred: {e}"
            stock_data = request.form.to_dict()  # Keep submitted data
            # give traceback for debugging
            traceback.print_exc()

    # Render template for GET or failed POST
    return render_template(
        "stock/add_stock.html",
        username=username,
        types=types,
        food_types=food_types,
        provenances=provenances,
        genesX=genesX,
        genes2=genes2,
        genes3=genes3,
        genes4=genes4,
        species_list=species_list,
        stock_data=stock_data,  # Pre-fill data
        error=error_message,
    )


@bp.route("/view/<unique_id>", methods=["GET", "POST"])
@login_required
def view_stock(unique_id):

    username = session.get("username")
    error_message = None

    # Get metadata for dropdowns/tagify
    try:
        types = get_metadata("types", db)
        food_types = get_metadata("food_types", db)
        provenances = get_metadata("provenances", db)
        genesX = get_metadata("genesX", db)
        genes2 = get_metadata("genes2nd", db)
        genes3 = get_metadata("genes3rd", db)
        genes4 = get_metadata("genes4th", db)
        species_list = get_metadata("species", db)
    except Exception as e:
        print(f"Error fetching metadata: {e}")
        return "Error fetching metadata", 500

    # Fetch stock data
    try:
        stock = db["stocks"].find_one({"UniqueID": unique_id, "User": username})
        if not stock:
            flash(f"Stock {unique_id} not found.", "error")
            return redirect(url_for("stock.stock_explorer"))

        # Prepare data for template display
        stock_data = {
            "sourceID": stock.get("SourceID", ""),
            "uniqueID": stock.get("UniqueID", ""),
            "genotype": stock.get("Genotype", ""),
            "name": stock.get("Name", ""),
            "altReference": stock.get("AltReference", ""),
            "type": stock.get("Type", ""),
            "foodType": stock.get("FoodType", "Molasses"),
            "status": stock.get("Status", ""),
            "seriesID": stock.get("SeriesID", ""),
            "replicateID": stock.get("ReplicateID", ""),
            "vialLifetime": stock.get("VialLifetime", ""),
            "flipFrequency": stock.get("FlipFrequency", ""),
            "developmentalTime": stock.get("DevelopmentalTime", ""),
            "species": stock.get("Species", ""),
            "comments": stock.get("Comments", ""),
            "provenance": stock.get("Provenance", ""),
            "trayID": stock.get("TrayID", ""),
            "trayPosition": stock.get("TrayPosition", ""),
            "creationDate": stock.get("CreationDate", ""),
            "lastFlipDate": stock.get("LastFlipDate", ""),
            "currentlyAliveVials": stock.get("CurrentlyAliveVials", ""),
            # Format logs/dates for display
            "flipLog": str(stock.get("FlipLog", "")).replace("; ", "\n"),
            "nextFlipDates": str(stock.get("NextFlipDates", "")).replace(
                "; ", "\n"
            ),  # Original used ', ' - check utils.py
            "nextEclosionDates": str(stock.get("NextEclosionDates", "")).replace(
                "; ", "\n"
            ),  # Original used ', ' - check utils.py
            "dataModifiedDate": stock.get("DataModifiedDate", ""),
            "modificationLog": str(stock.get("ModificationLog", "")).replace(
                "; ", "\n"
            ),
        }

    except Exception as e:
        print(f"Error fetching stock {unique_id} for view: {e}")
        flash("Error fetching stock data.", "error")
        return redirect(url_for("stock.stock_explorer"))

    if request.method == "POST":
        try:
            # --- Process Genotype Inputs ---
            genesX_input = clean_tagify_data(request.form.get("genotypeX"))
            for gene in genesX_input:
                if gene not in genesX:
                    add_metadata("genesX", gene, db)
            genesX_str = "/".join(genesX_input) if len(genesX_input) > 0 else ""

            genes2_input = clean_tagify_data(request.form.get("genotype2"))
            for gene in genes2_input:
                if gene not in genes2:
                    add_metadata("genes2nd", gene, db)
            genes2_str = "/".join(genes2_input) if len(genes2_input) > 0 else ""

            genes3_input = clean_tagify_data(request.form.get("genotype3"))
            for gene in genes3_input:
                if gene not in genes3:
                    add_metadata("genes3rd", gene, db)
            genes3_str = "/".join(genes3_input) if len(genes3_input) > 0 else ""

            genes4_input = clean_tagify_data(request.form.get("genotype4"))
            for gene in genes4_input:
                if gene not in genes4:
                    add_metadata("genes4th", gene, db)
            genes4_str = "/".join(genes4_input) if len(genes4_input) > 0 else ""

            full_genotype = ";".join([genesX_str, genes2_str, genes3_str, genes4_str])
            qc_passed, final_genotype = qc_genotype(full_genotype)
            if not qc_passed:
                raise ValueError(f"Genotype QC failed: {final_genotype}")

            # --- Process Other Tagify Inputs ---
            type_input = clean_tagify_data(request.form.get("type"))[0]
            if type_input not in types:
                add_metadata("types", type_input, db)

            food_type_input = clean_tagify_data(request.form.get("foodType"))[0]
            if food_type_input not in food_types:
                add_metadata("food_types", food_type_input, db)

            provenance_input = clean_tagify_data(request.form.get("provenance"))
            for prov in provenance_input:
                if prov not in provenances:
                    add_metadata("provenances", prov, db)
            provenance_str = "/".join(provenance_input)

            species_input = clean_tagify_data(request.form.get("species"))[0]
            if species_input not in species_list:
                add_metadata("species", species_input, db)

            # --- Collect Form Data for Update ---
            updated_stock_data = {
                "SourceID": request.form.get("sourceID"),
                "Genotype": final_genotype,
                "Name": request.form.get("name"),
                "Species": species_input,
                "AltReference": request.form.get("altReference"),
                "Type": type_input,
                "SeriesID": request.form.get("seriesID"),
                "ReplicateID": request.form.get("replicateID"),
                "Status": request.form.get("status"),
                "FoodType": food_type_input,
                "Provenance": provenance_str,
                "VialLifetime": request.form.get(
                    "vialLifetime", type=int
                ),  # Let mongo handle type or ensure type
                "FlipFrequency": request.form.get("flipFrequency", type=int),
                "DevelopmentalTime": request.form.get("developmentalTime", type=int),
                "Comments": request.form.get("comments"),
            }

            # Remove empty/None fields
            updated_stock_data = {
                k: v for k, v in updated_stock_data.items() if v is not None and v != ""
            }

            # --- Calculate Changed Fields ---
            changed_fields = {
                k: v
                for k, v in updated_stock_data.items()
                # Careful with type comparison (e.g., form '14' vs db 14)
                if str(v) != str(stock.get(k, ""))  # Compare as strings for simplicity
            }

            if not changed_fields:
                flash("No changes detected.", "info")
                # Re-render view page, no redirect needed
                return render_template(
                    "stock/view_stock.html",
                    username=username,
                    types=types,
                    food_types=food_types,
                    provenances=provenances,
                    genesX=genesX,
                    genes2=genes2,
                    genes3=genes3,
                    genes4=genes4,
                    species_list=species_list,
                    stock_data=stock_data,
                    error=error_message,
                )

            # --- Edit Stock in Database ---
            print(
                f"Attempting to edit stock {unique_id} with changes: {changed_fields}"
            )
            success = edit_stock(username, unique_id, db, changed_fields)

            if success:
                print(f"Stock {unique_id} edited successfully.")
                # Update vials for the edited stock
                try:
                    edited_stock = db["stocks"].find_one(
                        {"UniqueID": unique_id, "User": username}
                    )
                    if edited_stock:
                        update_stock_vials(edited_stock, username, db)
                        print(f"Vials updated for edited stock {unique_id}.")
                    else:
                        print(
                            f"Warning: Could not find edited stock {unique_id} to update vials."
                        )
                except Exception as vial_e:
                    print(
                        f"Error updating vials for edited stock {unique_id}: {vial_e}"
                    )

                # Log activity
                changed_keys = ", ".join(changed_fields.keys())
                write_activity(
                    username, f"Edited stock {unique_id} (Fields: {changed_keys})", db
                )
                flash(f"Stock {unique_id} updated successfully.", "success")
                return redirect(url_for("stock.stock_explorer"))
            else:
                # edit_stock should ideally return a reason for failure
                error_message = (
                    "Failed to update stock. Please check data and try again."
                )
                # Keep submitted data in form for correction
                stock_data.update(
                    updated_stock_data
                )  # Update stock_data with submitted values

        except ValueError as ve:
            error_message = str(ve)
            stock_data.update(request.form.to_dict())  # Keep submitted data
        except Exception as e:
            print(f"Error editing stock {unique_id}: {e}")
            error_message = f"An unexpected error occurred: {e}"
            stock_data.update(request.form.to_dict())  # Keep submitted data

    # Render template for GET or failed POST
    return render_template(
        "stock/view_stock.html",
        username=username,
        types=types,
        food_types=food_types,
        provenances=provenances,
        genesX=genesX,
        genes2=genes2,
        genes3=genes3,
        genes4=genes4,
        species_list=species_list,
        stock_data=stock_data,
        error=error_message,
    )


@bp.route("/get_internal/<internal_stock_id>", methods=["GET"])
@login_required
def get_internal_stock_data(internal_stock_id):
    """Endpoint to fetch data for pre-filling based on an internal stock ID."""
    if not session.get("username"):
        return jsonify({"error": "User not logged in."}), 401

    username = session.get("username")  # Current user making the request

    try:
        stock = db["stocks"].find_one({"UniqueID": internal_stock_id, "User": username})

        if not stock:
            return jsonify({"error": "Stock not found."}), 404

        # QC Genotype? Original code commented this out. Let's keep it commented.
        # qc_passed, final_genotype = qc_genotype(stock.get('Genotype', ''))
        # genotype = final_genotype if qc_passed else stock.get('Genotype', '') # Fallback?

        stock_data = {
            "genotype": stock.get("Genotype", ""),
            "name": stock.get("Name", ""),
            "altReference": stock.get("AltReference", ""),
            "type": stock.get("Type", ""),
            "foodType": stock.get("FoodType", ""),
            "provenance": stock.get("Provenance", ""),
            "status": stock.get(
                "Status", ""
            ),  # Usually not copied? Or default to Active?
            "vialLifetime": stock.get("VialLifetime", ""),
            "flipFrequency": stock.get("FlipFrequency", ""),
            "developmentalTime": stock.get("DevelopmentalTime", ""),
            # Don't include SeriesID/ReplicateID, TrayID/Pos, Comments by default
        }
        return jsonify(stock_data), 200

    except Exception as e:
        current_app.logger.exception(
            "Error in get_internal_stock_data for %s: %s", internal_stock_id, e
        )
        return jsonify({"error": "An internal error occurred."}), 500


@bp.route("/get_bloomington/<bdsc_stock_id>", methods=["GET"])
@login_required
def get_bloomington_stock_data(bdsc_stock_id):
    """Endpoint to fetch genotype from Bloomington based on BDSC ID."""
    if not session.get("username"):
        return jsonify({"error": "User not logged in."}), 401

    try:
        # Assuming get_stock_genotype handles fetching from BDSC data source (e.g., CSV)
        genotype, error = get_stock_genotype(bdsc_stock_id)  # Pass db if needed by util

        if error:
            return (
                jsonify({"error": error}),
                400,
            )  # Use 400 for client-side errors like not found

        # QC Genotype?
        # qc_passed, final_genotype = qc_genotype(genotype)
        # genotype_to_return = final_genotype if qc_passed else genotype

        return jsonify({"genotype": genotype}), 200  # Return original fetched genotype

    except Exception as e:
        current_app.logger.exception(
            "Error in get_bloomington_stock_data for %s: %s", bdsc_stock_id, e
        )
        return jsonify({"error": "An internal error occurred fetching BDSC data."}), 500


@bp.route("/autopopulate_ids", methods=["POST"])
@login_required
@limiter.limit("30 per minute")
def autopopulate_series_replicate_ids():
    """Auto-populates Series ID and Replicate ID based on genotype."""
    if not session.get("username"):
        return jsonify({"error": "User not logged in."}), 401

    username = session.get("username")
    try:
        payload = get_json_payload()
    except ValueError as exc:
        return jsonify({"error": str(exc)}), 400

    genotype_str = normalize_optional_text(
        payload.get("genotype"), field_name="Genotype", max_length=500
    )

    if not genotype_str:
        return jsonify({"error": "Genotype is required."}), 400

    try:
        # Clean and QC the genotype string (similar to add/view stock)
        # This assumes genotype_str is the full ";"-separated string
        qc_passed, final_genotype = qc_genotype(genotype_str)
        if not qc_passed:
            # Return QC error message
            return jsonify({"error": f"Genotype QC failed: {final_genotype}"}), 400

        # Find existing stocks with the *exact* same final genotype for this user
        matching_stocks = list(
            db["stocks"].find({"User": username, "Genotype": final_genotype})
        )
        count = len(matching_stocks)

        if count == 0:
            # No existing stocks with this genotype for the user. Find the next available SeriesID.
            all_user_stocks = list(
                db["stocks"].find({"User": username}, {"SeriesID": 1})
            )  # Fetch only SeriesID
            if not all_user_stocks:
                next_series_id = 1
            else:
                # Find max numeric SeriesID, handling potential non-numeric values
                max_series = 0
                for s in all_user_stocks:
                    try:
                        sid = int(s.get("SeriesID", "0"))
                        if sid > max_series:
                            max_series = sid
                    except (ValueError, TypeError):
                        continue  # Ignore non-integer SeriesIDs
                next_series_id = max_series + 1
            next_replicate_id = "a"
        else:
            # Stocks with this genotype exist. Find the highest SeriesID and ReplicateID among them.
            max_series = 0
            max_replicate_for_max_series = (
                ""  # Track replicate only for the highest series
            )

            for stock in matching_stocks:
                try:
                    series_id = int(stock.get("SeriesID", "0"))
                    replicate_id = stock.get("ReplicateID", "a")

                    if series_id > max_series:
                        max_series = series_id
                        max_replicate_for_max_series = (
                            replicate_id  # Reset max replicate for new max series
                        )
                    elif series_id == max_series:
                        # Use simple string comparison for replicates ('aa' > 'z')
                        if replicate_id > max_replicate_for_max_series:
                            max_replicate_for_max_series = replicate_id
                except (ValueError, TypeError):
                    continue  # Ignore malformed entries

            # If max_series remained 0 (e.g., only malformed entries found), handle appropriately
            if max_series == 0:
                # Fallback: treat as if no stocks found? Or assign Series 1?
                # Let's assign based on overall max series + 1 as in the count==0 case.
                all_user_stocks = list(
                    db["stocks"].find({"User": username}, {"SeriesID": 1})
                )
                if not all_user_stocks:
                    next_series_id = 1
                else:
                    overall_max_series = 0
                    for s in all_user_stocks:
                        try:
                            sid = int(s.get("SeriesID", "0"))
                            if sid > overall_max_series:
                                overall_max_series = sid
                        except (ValueError, TypeError):
                            continue
                    next_series_id = overall_max_series + 1
                next_replicate_id = "a"
            else:
                # Use the found max series and increment the corresponding max replicate
                next_series_id = max_series
                next_replicate_id = increment_replicate_id(
                    max_replicate_for_max_series or "a"
                )  # Ensure we increment 'a' if max_replicate was empty

        return (
            jsonify(
                {"seriesID": str(next_series_id), "replicateID": next_replicate_id}
            ),
            200,
        )

    except Exception as e:
        current_app.logger.exception(
            "Error in autopopulate_series_replicate_ids for %s: %s", username, e
        )
        return jsonify({"error": "An internal error occurred."}), 500


@bp.route("/generate_labels", methods=["POST"])
@login_required
@limiter.limit("10 per hour")
def generate_stock_labels():
    """Endpoint to generate stock labels based on selected stocks and quantities."""
    username = session.get("username")
    try:
        selected_uids_str = request.form.get("selected_uids")
        quantities_str = request.form.get("quantities")
        blank_spaces = parse_int_value(
            request.form.get("blank_spaces", 0),
            field_name="Blank spaces",
            minimum=0,
            maximum=200,
        )

        if not selected_uids_str or not quantities_str:
            flash("Missing selected stocks or quantities.", "error")
            return redirect(url_for("stock.stock_explorer"))

        selected_uids = selected_uids_str.split(",")
        quantities = [int(q) for q in quantities_str.split(",")]

        if len(selected_uids) != len(quantities):
            flash("Mismatch between selected stocks and quantities.", "error")
            return redirect(url_for("stock.stock_explorer"))

        user_initials = get_user_initials(username, db)
        all_stocks = get_user_stocks(username, db)  # Fetch all user stocks

        # Filter and duplicate based on selection and quantities
        selected_stocks_data = []
        stock_map = {
            str(s.get("UniqueID")): s for s in all_stocks
        }  # Map for quick lookup

        for uid, quantity in zip(selected_uids, quantities):
            stock = stock_map.get(uid)
            if stock and quantity > 0:
                selected_stocks_data.extend([stock] * quantity)

        if not selected_stocks_data:
            flash("No valid stocks selected for label generation.", "warning")
            return redirect(url_for("stock.stock_explorer"))

        # Sort for printing (optional, but good practice)
        selected_stocks_data = sorted(
            selected_stocks_data,
            key=lambda x: (
                str(x.get("TrayID", "")),
                int(float(x.get("TrayPosition", "0") or "0")),
            ),
        )

        # Generate PDF
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        filename_base = f"{username}_stock_labels_{timestamp}"
        pdf_filename = f"{filename_base}.pdf"
        # Ensure the target directory exists (using UPLOAD_FOLDER for generated files?)
        labels_dir = os.path.join(current_app.static_folder, "generated_labels")
        os.makedirs(labels_dir, exist_ok=True)
        pdf_full_path = os.path.join(labels_dir, pdf_filename)

        generate_label_pdf(
            pdf_full_path,  # Pass full path instead of just filename
            user_initials,
            selected_stocks_data,
            ["stock"] * len(selected_stocks_data),  # Type identifier
            blank_spaces,
            len(selected_stocks_data),
        )

        pdf_url = url_for("static", filename=f"generated_labels/{pdf_filename}")

        # Log activity
        write_activity(
            username, f"Generated {len(selected_stocks_data)} stock labels", db
        )

        # Redirect to the generated PDF file
        return redirect(pdf_url)

    except ValueError as ve:
        flash(f"Invalid input: {ve}", "error")
        return redirect(url_for("stock.stock_explorer"))
    except Exception as e:
        current_app.logger.exception("Error generating stock labels for %s: %s", username, e)
        flash(f"Error generating labels: {e}", "error")
        return redirect(url_for("stock.stock_explorer"))


# Moved from cross blueprint as it queries stocks
@bp.route("/get_genotype_for_uid/<unique_id>")
@login_required
def get_genotype_for_uid(unique_id):
    """Route to fetch genotype based on a stock's Unique ID."""
    try:
        # Find stock by UniqueID - potentially across all users if needed by JS?
        # Original code checked username, let's keep that for now.
        username = session.get("username")
        stock = db["stocks"].find_one(
            {"UniqueID": unique_id, "User": username}, {"Genotype": 1}
        )  # Fetch only Genotype

        if stock:
            return jsonify({"genotype": stock.get("Genotype", "")})
        else:
            # Check crosses as well? Original route was just /get_genotype/
            # Let's assume it's only for stocks based on DB query.
            return jsonify({"genotype": "", "error": "Stock not found"}), 404
    except Exception as e:
        current_app.logger.exception(
            "Error in get_genotype_for_uid for %s: %s", unique_id, e
        )
        return jsonify({"genotype": "", "error": "Internal server error"}), 500


# Moved from cross blueprint as it queries stocks
@bp.route("/get_uids_for_genotype/<path:genotype_str>")  # Use path converter for '/'
@login_required
def get_uids_for_genotype(genotype_str):
    """Route to fetch stock UIDs based on genotype."""
    username = session.get("username")
    # Genotype string might be URL encoded twice in original code? Let's decode once.
    # The <path:..> converter handles '/' correctly.
    decoded_genotype = unquote(genotype_str)

    try:
        # Clean and QC the genotype
        qc_passed, final_genotype = qc_genotype(decoded_genotype)
        if not qc_passed:
            # Maybe return empty list or error?
            return (
                jsonify(
                    {"uids": [], "error": f"Invalid genotype format: {final_genotype}"}
                ),
                400,
            )

        # Find stocks matching the final genotype for the user
        matching_stocks = db["stocks"].find(
            {"Genotype": final_genotype, "User": username},
            {"UniqueID": 1},  # Fetch only UniqueID
        )
        uid_list = [doc["UniqueID"] for doc in matching_stocks if "UniqueID" in doc]

        return jsonify({"uids": uid_list})

    except Exception as e:
        current_app.logger.exception(
            "Error in get_uids_for_genotype for '%s': %s", decoded_genotype, e
        )
        return jsonify({"uids": [], "error": "Internal server error"}), 500


@bp.route("/get_stock_data_for_uid/<unique_id>")
@login_required
def get_stock_data_for_uid(unique_id):
    """Route to fetch stock data (including species) based on a stock's Unique ID."""
    username = session.get("username")

    try:
        # Find stock by UniqueID
        stock = db["stocks"].find_one({"UniqueID": unique_id, "User": username})

        if stock:
            stock_data = {
                "uniqueID": stock.get("UniqueID", ""),
                "genotype": stock.get("Genotype", ""),
                "name": stock.get("Name", ""),
                "type": stock.get("Type", ""),
                "status": stock.get("Status", ""),
                "species": stock.get(
                    "Species", "D. melanogaster"
                ),  # Include species with default
            }
            return jsonify(stock_data)

        return jsonify({"error": "Stock not found"}), 404
    except Exception as e:
        current_app.logger.exception(
            "Error in get_stock_data_for_uid for %s: %s", unique_id, e
        )
        return jsonify({"error": "Internal server error"}), 500


@bp.route("/delete_permanently", methods=["POST"])
@login_required
@limiter.limit("10 per hour")
def delete_stock_permanently():
    """
    Permanently delete stocks that have the 'No longer maintained' status.
    Only stocks with this status will be deleted, all others will be skipped.

    Expected JSON payload:
    {
        "uniqueIDs": ["uid1", "uid2", ...]
    }

    Returns:
    JSON response with success status, count of deleted items, and count of skipped items.
    """
    username = session.get("username")
    try:
        data = get_json_payload()
        unique_ids = normalize_identifier_list(data.get("uniqueIDs", []), field_name="uniqueIDs")
    except ValueError as exc:
        return jsonify({"success": False, "message": str(exc)}), 400

    if not unique_ids:
        return (
            jsonify(
                {"success": False, "message": "No stock IDs provided for deletion"}
            ),
            400,
        )
    deleted_count = 0
    skipped_count = 0

    for uid in unique_ids:
        # Get the stock and check its status
        stock = db["stocks"].find_one({"UniqueID": uid, "User": username})

        if not stock:
            skipped_count += 1
            continue

        # Only delete stocks with 'No longer maintained' status
        if stock.get("Status") == "No longer maintained":
            success = delete_stock(username, uid, db)
            if success:
                # Log the deletion activity
                write_activity(username, f"Permanently deleted stock {uid}", db)
                deleted_count += 1
            else:
                skipped_count += 1
        else:
            skipped_count += 1

    return jsonify(
        {
            "success": True,
            "deleted": deleted_count,
            "skipped": skipped_count,
            "message": f'Successfully deleted {deleted_count} stocks with status "No longer maintained". Skipped {skipped_count} stocks.',
        }
    )
