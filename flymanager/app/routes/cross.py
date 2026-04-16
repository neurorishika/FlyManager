import datetime

from flask import (Blueprint, current_app, jsonify, redirect, render_template,
                   request, session, url_for)
from fuzzywuzzy import fuzz

from flymanager.app import db
from flymanager.app.routes.auth import login_required
from flymanager.app.routes.explorer_utils import (collect_unique_values,
                                                  get_explorer_filter_state,
                                                  set_flip_display_fields)
from flymanager.app.security import (get_json_payload, limiter,
                                     normalize_identifier_list,
                                     parse_int_value, require_confirmation)
from flymanager.app.settings import DEFAULT_CROSS_PROPERTY_VALUES
from flymanager.utils.genetics import cross_genotypes, qc_genotype
from flymanager.utils.labels import generate_label_pdf
from flymanager.utils.mongo import (add_metadata, add_to_cross, edit_cross,
                                    get_all_genotypes, get_eclosion_in,
                                    get_flip_in, get_metadata,
                                    get_user_crosses, get_user_initials,
                                    update_cross_vials, write_activity)
from flymanager.utils.scanner import get_available_ports
from flymanager.utils.utils import clean_tagify_data

bp = Blueprint("cross", __name__)  # url_prefix defined in app/__init__


@bp.route("/cross_explorer", methods=["GET", "POST"])
@login_required
def cross_explorer():
    username = session.get("username")

    crosses = get_user_crosses(username, db)
    all_crosses_for_filters = list(crosses)

    # nested sort by TrayID and TrayPosition
    crosses = sorted(
        crosses,
        key=lambda x: (
            str(x["TrayID"]),
            int(float(x["TrayPosition"])) if x["TrayPosition"] != "" else 0,
        ),
    )

    # add FlipIn and EclosesIn fields
    for cross in crosses:
        cross["FlipIn"] = get_flip_in(cross)
        set_flip_display_fields(cross, raw_value=cross["FlipIn"], display_field="FlipIn")

        cross["EclosesIn"] = get_eclosion_in(cross)

    # Extract unique values for filtering from all crosses (unfiltered)
    unique_values = collect_unique_values(
        all_crosses_for_filters,
        {
            "MaleSpecies": lambda cross: cross.get("MaleSpecies", "D. melanogaster"),
            "FemaleSpecies": lambda cross: cross.get("FemaleSpecies", "D. melanogaster"),
            "TrayID": lambda cross: cross.get("TrayID"),
            "Status": lambda cross: cross.get("Status"),
            "FoodType": lambda cross: cross.get("FoodType"),
        },
    )

    filter_state, redirect_response = get_explorer_filter_state(
        session_key="filter_state",
        clear_endpoint="cross.cross_explorer",
        field_names=(
            "filterMaleSpecies",
            "filterFemaleSpecies",
            "filterTrayID",
            "filterStatus",
            "filterFoodType",
            "searchQuery",
        ),
    )
    if redirect_response is not None:
        return redirect_response

    filtered_crosses = _apply_cross_filters(crosses, filter_state)

    if filter_state:
        unique_values = collect_unique_values(
            filtered_crosses,
            {
                "TrayID": lambda cross: cross.get("TrayID"),
                "FoodType": lambda cross: cross.get("FoodType"),
            },
        )
        unique_values["Status"] = collect_unique_values(
            all_crosses_for_filters,
            {"Status": lambda cross: cross.get("Status")},
        )["Status"]

    return render_template(
        "cross/cross_explorer.html",
        username=username,
        crosses=filtered_crosses,
        unique_values=unique_values,
        filter_state=filter_state,
    )


def _apply_cross_filters(crosses, filters):
    """Helper function to apply filters to a list of crosses."""
    filtered_crosses = list(crosses)  # Make a copy

    filter_male_species = filters.get("filterMaleSpecies")
    filter_female_species = filters.get("filterFemaleSpecies")
    filter_tray_id = filters.get("filterTrayID")
    filter_status = filters.get("filterStatus")
    filter_food_type = filters.get("filterFoodType")
    search_query = filters.get("searchQuery")
    no_longer_maintained_status = "No longer maintained"

    if filter_male_species:
        filtered_crosses = [
            cross
            for cross in filtered_crosses
            if str(cross.get("MaleSpecies")) == filter_male_species
        ]
    if filter_female_species:
        filtered_crosses = [
            cross
            for cross in filtered_crosses
            if str(cross.get("FemaleSpecies")) == filter_female_species
        ]
    if filter_tray_id:
        filtered_crosses = [
            cross
            for cross in filtered_crosses
            if str(cross["TrayID"]) == filter_tray_id
        ]

    if filter_status == no_longer_maintained_status:
        filtered_crosses = [
            cross
            for cross in filtered_crosses
            if str(cross["Status"]) == no_longer_maintained_status
        ]
    elif filter_status:
        filtered_crosses = [
            cross
            for cross in filtered_crosses
            if str(cross["Status"]) == filter_status
        ]
    else:
        filtered_crosses = [
            cross
            for cross in filtered_crosses
            if str(cross["Status"]) != no_longer_maintained_status
        ]

    if filter_food_type:
        filtered_crosses = [
            cross
            for cross in filtered_crosses
            if str(cross["FoodType"]) == filter_food_type
        ]

    # Apply search
    if search_query:

        def match(cross):
            search_fields = [
                cross["Name"],
                cross["TrayID"],
                cross["TrayPosition"],
                cross["Comments"],
            ]
            search_string = " ".join(str(field) for field in search_fields)
            return fuzz.partial_ratio(search_string, search_query) > 80

        filtered_crosses = [cross for cross in filtered_crosses if match(cross)]

    return filtered_crosses


@bp.route("/add_cross", methods=["GET", "POST"])
@bp.route("/add_cross/<unique_id>", methods=["GET", "POST"])
@login_required
def add_cross(unique_id=None):
    username = session.get("username")
    ports = get_available_ports()
    error_message = None

    # Initialize cross_data with default values from settings
    cross_data = {
        "maleSpecies": DEFAULT_CROSS_PROPERTY_VALUES["MaleSpecies"],
        "femaleSpecies": DEFAULT_CROSS_PROPERTY_VALUES["FemaleSpecies"],
        "status": DEFAULT_CROSS_PROPERTY_VALUES["Status"],
        "foodType": DEFAULT_CROSS_PROPERTY_VALUES["FoodType"],
        "vialLifetime": DEFAULT_CROSS_PROPERTY_VALUES["VialLifetime"],
        "flipFrequency": DEFAULT_CROSS_PROPERTY_VALUES["FlipFrequency"],
        "developmentalTime": DEFAULT_CROSS_PROPERTY_VALUES["DevelopmentalTime"],
        "maxCrossLifetime": DEFAULT_CROSS_PROPERTY_VALUES["MaxCrossLifetime"],
        "comments": DEFAULT_CROSS_PROPERTY_VALUES["Comments"],
    }

    # Get metadata lists
    try:
        food_types = get_metadata("food_types", db)
        genotypes = get_all_genotypes(username, db)
    except Exception as e:
        print(f"Error fetching metadata: {e}")
        return "Error fetching metadata", 500

    # If a unique_id is provided, fetch the cross data to duplicate it
    if unique_id and request.method == "GET":
        try:
            cross = db["crosses"].find_one({"UniqueID": unique_id})
            if cross:
                cross_data = {
                    "maleUniqueID": cross["MaleUniqueID"],
                    "femaleUniqueID": cross["FemaleUniqueID"],
                    "maleGenotype": cross["MaleGenotype"],
                    "femaleGenotype": cross["FemaleGenotype"],
                    "maleSpecies": cross.get(
                        "MaleSpecies", DEFAULT_CROSS_PROPERTY_VALUES["MaleSpecies"]
                    ),
                    "femaleSpecies": cross.get(
                        "FemaleSpecies", DEFAULT_CROSS_PROPERTY_VALUES["FemaleSpecies"]
                    ),
                    "status": cross.get(
                        "Status", DEFAULT_CROSS_PROPERTY_VALUES["Status"]
                    ),
                    "foodType": cross.get(
                        "FoodType", DEFAULT_CROSS_PROPERTY_VALUES["FoodType"]
                    ),
                    "name": cross["Name"],
                    "vialLifetime": cross.get(
                        "VialLifetime", DEFAULT_CROSS_PROPERTY_VALUES["VialLifetime"]
                    ),
                    "flipFrequency": cross.get(
                        "FlipFrequency", DEFAULT_CROSS_PROPERTY_VALUES["FlipFrequency"]
                    ),
                    "developmentalTime": cross.get(
                        "DevelopmentalTime",
                        DEFAULT_CROSS_PROPERTY_VALUES["DevelopmentalTime"],
                    ),
                    "maxCrossLifetime": cross.get(
                        "MaxCrossLifetime",
                        DEFAULT_CROSS_PROPERTY_VALUES["MaxCrossLifetime"],
                    ),
                    "comments": cross.get(
                        "Comments", DEFAULT_CROSS_PROPERTY_VALUES["Comments"]
                    ),
                }
            else:
                return jsonify({"error": "Cross not found."}), 404
        except Exception as e:
            print(f"Error fetching source cross {unique_id}: {e}")
            return jsonify({"error": "Error fetching cross data."}), 500

    if request.method == "POST":
        try:
            require_confirmation(
                request.form.get("creationConfirmation"),
                action_name="cross creation",
            )

            # Process input data
            male_genotype_input = clean_tagify_data(request.form.get("maleGenotype"))[0]
            female_genotype_input = clean_tagify_data(
                request.form.get("femaleGenotype")
            )[0]

            food_type_input = clean_tagify_data(request.form.get("foodType"))[0]
            if food_type_input not in food_types:
                add_metadata("food_types", food_type_input, db)

            new_cross_data = {
                "MaleUniqueID": request.form.get("maleUniqueID"),
                "FemaleUniqueID": request.form.get("femaleUniqueID"),
                "MaleGenotype": male_genotype_input,
                "FemaleGenotype": female_genotype_input,
                "MaleSpecies": request.form.get(
                    "maleSpecies", DEFAULT_CROSS_PROPERTY_VALUES["MaleSpecies"]
                ),
                "FemaleSpecies": request.form.get(
                    "femaleSpecies", DEFAULT_CROSS_PROPERTY_VALUES["FemaleSpecies"]
                ),
                "Status": request.form.get(
                    "status", DEFAULT_CROSS_PROPERTY_VALUES["Status"]
                ),
                "FoodType": food_type_input,
                "Name": request.form.get("name"),
                "VialLifetime": request.form.get(
                    "vialLifetime", DEFAULT_CROSS_PROPERTY_VALUES["VialLifetime"]
                ),
                "FlipFrequency": request.form.get(
                    "flipFrequency", DEFAULT_CROSS_PROPERTY_VALUES["FlipFrequency"]
                ),
                "DevelopmentalTime": request.form.get(
                    "developmentalTime",
                    DEFAULT_CROSS_PROPERTY_VALUES["DevelopmentalTime"],
                ),
                "MaxCrossLifetime": request.form.get(
                    "maxCrossLifetime",
                    DEFAULT_CROSS_PROPERTY_VALUES["MaxCrossLifetime"],
                ),
                "Comments": request.form.get(
                    "comments", DEFAULT_CROSS_PROPERTY_VALUES["Comments"]
                ),
            }

            # Remove empty/None fields before saving
            new_cross_data = {
                k: v for k, v in new_cross_data.items() if v is not None and v != ""
            }

            # Add cross to the user's collection
            success, uid_or_message = add_to_cross(username, new_cross_data, db)

            if success:
                # Get the cross data and update vials
                cross = db["crosses"].find_one({"UniqueID": uid_or_message})
                if cross:
                    update_cross_vials(cross, username, db)
                    print(f"Vials updated for new cross {uid_or_message}.")

                # Log activity
                write_activity(username, f"Added cross {uid_or_message}", db)
                return redirect(url_for("cross.cross_explorer"))
            else:
                # Handle error (e.g., QC failure)
                error_message = uid_or_message
                cross_data = new_cross_data  # Keep submitted data in form

        except ValueError as ve:
            error_message = str(ve)
            cross_data = request.form.to_dict()  # Keep submitted data
        except Exception as e:
            print(f"Error adding cross for {username}: {e}")
            error_message = f"An unexpected error occurred: {e}"
            cross_data = request.form.to_dict()  # Keep submitted data

    # Render template for GET or failed POST
    return render_template(
        "cross/add_cross.html",
        username=username,
        food_types=food_types,
        genotypes=genotypes,
        ports=ports,
        cross_data=cross_data,
        error=error_message,
    )


@bp.route("/view_cross/<unique_id>", methods=["GET", "POST"])
@login_required
def view_cross(unique_id):
    username = session.get("username")
    error_message = None

    # Get metadata lists
    try:
        food_types = get_metadata("food_types", db)
        genotypes = get_all_genotypes(username, db)
    except Exception as e:
        print(f"Error fetching metadata: {e}")
        return "Error fetching metadata", 500

    # Fetch cross data
    try:
        cross = db["crosses"].find_one({"UniqueID": unique_id, "User": username})
        if not cross:
            return jsonify({"error": "Cross not found."}), 404

        # Prepare data for template display
        cross_data = {
            "uniqueID": cross["UniqueID"],
            "maleUniqueID": cross["MaleUniqueID"],
            "femaleUniqueID": cross["FemaleUniqueID"],
            "maleGenotype": cross["MaleGenotype"],
            "femaleGenotype": cross["FemaleGenotype"],
            "maleSpecies": cross.get(
                "MaleSpecies", DEFAULT_CROSS_PROPERTY_VALUES["MaleSpecies"]
            ),
            "femaleSpecies": cross.get(
                "FemaleSpecies", DEFAULT_CROSS_PROPERTY_VALUES["FemaleSpecies"]
            ),
            "trayID": cross.get("TrayID", ""),
            "trayPosition": cross.get("TrayPosition", ""),
            "status": cross.get("Status", DEFAULT_CROSS_PROPERTY_VALUES["Status"]),
            "foodType": cross.get(
                "FoodType", DEFAULT_CROSS_PROPERTY_VALUES["FoodType"]
            ),
            "name": cross["Name"],
            "comments": cross.get(
                "Comments", DEFAULT_CROSS_PROPERTY_VALUES["Comments"]
            ),
            "vialLifetime": cross.get(
                "VialLifetime", DEFAULT_CROSS_PROPERTY_VALUES["VialLifetime"]
            ),
            "flipFrequency": cross.get(
                "FlipFrequency", DEFAULT_CROSS_PROPERTY_VALUES["FlipFrequency"]
            ),
            "developmentalTime": cross.get(
                "DevelopmentalTime", DEFAULT_CROSS_PROPERTY_VALUES["DevelopmentalTime"]
            ),
            "maxCrossLifetime": cross.get(
                "MaxCrossLifetime", DEFAULT_CROSS_PROPERTY_VALUES["MaxCrossLifetime"]
            ),
            "creationDate": cross.get("CreationDate", ""),
            "lastFlipDate": cross.get("LastFlipDate", ""),
            "currentlyAliveVials": cross.get("CurrentlyAliveVials", ""),
            "flipLog": str(cross.get("FlipLog", "")).replace("; ", "\n"),
            "nextFlipDates": str(cross.get("NextFlipDates", "")).replace("; ", "\n"),
            "nextEclosionDates": str(cross.get("NextEclosionDates", "")).replace(
                "; ", "\n"
            ),
            "dataModifiedDate": cross.get("DataModifiedDate", ""),
            "modificationLog": str(cross.get("ModificationLog", "")).replace(
                "; ", "\n"
            ),
        }
    except Exception as e:
        print(f"Error fetching cross {unique_id} for view: {e}")
        return jsonify({"error": "Error fetching cross data."}), 500

    # Predict offspring genotypes
    predicted_offspring = cross_genotypes(
        cross_data["maleGenotype"], cross_data["femaleGenotype"]
    )

    if request.method == "POST":
        try:
            # Process input data
            male_genotype_input = clean_tagify_data(request.form.get("maleGenotype"))[0]
            female_genotype_input = clean_tagify_data(
                request.form.get("femaleGenotype")
            )[0]

            # Validate genotypes using QC (similar to add_to_cross)
            qc, male_genotype = qc_genotype(male_genotype_input)
            if not qc:
                raise ValueError(f"Male genotype QC failed: {male_genotype}")

            qc, female_genotype = qc_genotype(female_genotype_input)
            if not qc:
                raise ValueError(f"Female genotype QC failed: {female_genotype}")

            food_type_input = clean_tagify_data(request.form.get("foodType"))[0]
            if food_type_input not in food_types:
                add_metadata("food_types", food_type_input, db)

            # Collect form data
            updated_cross_data = {
                "MaleUniqueID": request.form.get("maleUniqueID"),
                "FemaleUniqueID": request.form.get("femaleUniqueID"),
                "MaleGenotype": male_genotype,
                "FemaleGenotype": female_genotype,
                "MaleSpecies": request.form.get(
                    "maleSpecies", DEFAULT_CROSS_PROPERTY_VALUES["MaleSpecies"]
                ),
                "FemaleSpecies": request.form.get(
                    "femaleSpecies", DEFAULT_CROSS_PROPERTY_VALUES["FemaleSpecies"]
                ),
                "Status": request.form.get(
                    "status", DEFAULT_CROSS_PROPERTY_VALUES["Status"]
                ),
                "FoodType": food_type_input,
                "Name": request.form.get("name"),
                "Comments": request.form.get(
                    "comments", DEFAULT_CROSS_PROPERTY_VALUES["Comments"]
                ),
                "VialLifetime": request.form.get(
                    "vialLifetime", DEFAULT_CROSS_PROPERTY_VALUES["VialLifetime"]
                ),
                "FlipFrequency": request.form.get(
                    "flipFrequency", DEFAULT_CROSS_PROPERTY_VALUES["FlipFrequency"]
                ),
                "DevelopmentalTime": request.form.get(
                    "developmentalTime",
                    DEFAULT_CROSS_PROPERTY_VALUES["DevelopmentalTime"],
                ),
                "MaxCrossLifetime": request.form.get(
                    "maxCrossLifetime",
                    DEFAULT_CROSS_PROPERTY_VALUES["MaxCrossLifetime"],
                ),
            }

            # Remove empty/None fields
            updated_cross_data = {
                k: v for k, v in updated_cross_data.items() if v is not None and v != ""
            }

            # Calculate changed fields by comparing with the original cross data
            changed_fields = {
                k: v
                for k, v in updated_cross_data.items()
                if str(v) != str(cross.get(k, ""))  # Compare as strings for consistency
            }

            if not changed_fields:
                # No changes detected
                return render_template(
                    "cross/view_cross.html",
                    username=username,
                    food_types=food_types,
                    genotypes=genotypes,
                    cross_data=cross_data,
                    predicted_offspring=predicted_offspring,
                    error=error_message,
                    message="No changes detected.",
                )

            # Edit cross in the user's collection
            success = edit_cross(username, unique_id, db, changed_fields)

            if success:
                # Get the updated cross data
                try:
                    edited_cross = db["crosses"].find_one(
                        {"UniqueID": unique_id, "User": username}
                    )
                    if edited_cross:
                        # Update the cross vials
                        update_cross_vials(edited_cross, username, db)
                        print(f"Vials updated for edited cross {unique_id}.")
                    else:
                        print(
                            f"Warning: Could not find edited cross {unique_id} to update vials."
                        )
                except Exception as vial_e:
                    print(
                        f"Error updating vials for edited cross {unique_id}: {vial_e}"
                    )

                # Log activity with changed fields
                changed_keys = ", ".join(changed_fields.keys())
                write_activity(
                    username, f"Edited cross {unique_id} (Fields: {changed_keys})", db
                )

                return redirect(url_for("cross.cross_explorer"))
            else:
                # Error updating cross
                error_message = (
                    "Failed to update cross. Please check data and try again."
                )
                # Keep submitted data in form for correction
                cross_data.update({k.lower(): v for k, v in updated_cross_data.items()})

        except ValueError as ve:
            error_message = str(ve)
            # Keep submitted data for form
            cross_data.update({k.lower(): v for k, v in request.form.items()})
        except Exception as e:
            print(f"Error editing cross {unique_id}: {e}")
            error_message = f"An unexpected error occurred: {e}"
            # Keep submitted data for form
            cross_data.update({k.lower(): v for k, v in request.form.items()})

    # Render template for GET or failed POST
    return render_template(
        "cross/view_cross.html",
        username=username,
        food_types=food_types,
        genotypes=genotypes,
        cross_data=cross_data,
        predicted_offspring=predicted_offspring,
        error=error_message,
    )


@bp.route("/generate_cross_labels", methods=["POST"])
@login_required
@limiter.limit("10 per hour")
def generate_cross_labels():
    selected_uids = request.form.get("selected_uids").split(",")
    blank_spaces = parse_int_value(
        request.form.get("blank_spaces", 0),
        field_name="Blank spaces",
        minimum=0,
        maximum=200,
    )
    quantities = request.form.get("quantities").split(",")

    # get the user's initials
    username = session.get("username")
    user_initials = get_user_initials(username, db)

    # get the selected stocks
    crosses = get_user_crosses(username, db)
    selected_crosses = [
        cross for cross in crosses if str(cross["UniqueID"]) in selected_uids
    ]

    # Sort the selected stocks by TrayID and TrayPosition
    selected_crosses = sorted(
        selected_crosses,
        key=lambda x: (
            x["TrayID"],
            int(float(x["TrayPosition"])) if x["TrayPosition"] != "" else 0,
        ),
    )

    # duplicate the selected stocks based on the quantities
    selected_crosses = [
        cross
        for cross, quantity in zip(selected_crosses, quantities)
        for _ in range(int(quantity))
    ]

    # generate the labels
    filename = datetime.datetime.now().strftime("%Y-%m-%d")
    generate_label_pdf(
        filename,
        user_initials,
        selected_crosses,
        ["cross"] * len(selected_crosses),
        blank_spaces,
        len(selected_crosses),
    )

    pdf_file_path = "/static/generated_labels/{}.pdf".format(filename)

    # write the activity to the user's activity sheet
    write_activity(
        username, "Generated labels for {} crosses".format(len(selected_crosses)), db
    )

    return redirect(pdf_file_path)


@bp.route("/delete_cross_permanently", methods=["POST"])
@login_required
@limiter.limit("10 per hour")
def delete_cross_permanently():
    """
    Permanently delete crosses that have the 'No longer maintained' status.
    Only crosses with this status will be deleted, all others will be skipped.

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
                {"success": False, "message": "No cross IDs provided for deletion"}
            ),
            400,
        )
    deleted_count = 0
    skipped_count = 0

    for uid in unique_ids:
        # Get the cross and check its status
        cross = db["crosses"].find_one({"UniqueID": uid, "User": username})

        if not cross:
            skipped_count += 1
            continue

        # Only delete crosses with 'No longer maintained' status
        if cross.get("Status") == "No longer maintained":
            from flymanager.utils.mongo import delete_cross

            success = delete_cross(username, uid, db)
            if success:
                # Log the deletion activity
                write_activity(username, f"Permanently deleted cross {uid}", db)
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
            "message": f'Successfully deleted {deleted_count} crosses with status "No longer maintained". Skipped {skipped_count} crosses.',
        }
    )
