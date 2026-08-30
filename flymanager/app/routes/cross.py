from flask import (Blueprint, current_app, flash, jsonify, redirect,
                   render_template, request, session, url_for)
from fuzzywuzzy import fuzz

from flymanager.app import db
from flymanager.app.routes.auth import login_required
from flymanager.app.routes.explorer_utils import (
    build_pagination_from_db_page, collect_unique_values,
    compute_explorer_scope_counts, get_explorer_filter_state,
    get_explorer_pagination_state, paginate_explorer_records,
    set_flip_display_fields)
from flymanager.app.security import get_json_payload, limiter, require_confirmation
from flymanager.app.settings import DEFAULT_CROSS_PROPERTY_VALUES
from flymanager.utils.genetics import qc_genotype
from flymanager.utils.mongo import (OperationLockConflict, add_metadata,
                                    add_to_cross, edit_cross,
                                    get_accessible_cross,
                                    get_accessible_crosses,
                                    get_accessible_documents_page,
                                    get_all_genotypes,
                                    get_direct_reports, get_eclosion_in,
                                    get_flip_in, get_metadata,
                                    hold_operation_lock,
                                    update_cross_vials,
                                    update_document_assignment, write_activity)
from flymanager.utils.phenotypes.image_library import \
    select_prediction_reference_images
from flymanager.utils.phenotypes.predictor import (build_cross_phenotype_cache,
                                                   get_cached_cross_phenotype)
from flymanager.utils.scanner import get_available_ports
from flymanager.utils.utils import clean_tagify_data

bp = Blueprint("cross", __name__)  # url_prefix defined in app/__init__


def _get_cross_phenotype_for_view(cross):
    def _normalize_simulation_summary(summary):
        summary = summary or {}
        best_sortable_class = summary.get("best_sortable_class") or {}
        return {
            "raw_class_count": summary.get("raw_class_count", 0),
            "viable_class_count": summary.get("viable_class_count", 0),
            "pruned_class_count": summary.get("pruned_class_count", 0),
            "viable_fraction": summary.get("viable_fraction", 0.0),
            "identifiable_viable_fraction": summary.get("identifiable_viable_fraction", 0.0),
            "confident_sortable_fraction": summary.get("confident_sortable_fraction", 0.0),
            "low_confidence_sorting_fraction": summary.get("low_confidence_sorting_fraction", 0.0),
            "weighted_identifiability_score": summary.get("weighted_identifiability_score", 0.0),
            "estimated_sortable_targets_per_vial": summary.get("estimated_sortable_targets_per_vial", 0.0),
            "visible_marker_class_count": summary.get("visible_marker_class_count", 0),
            "best_sortable_class": {
                **best_sortable_class,
                "identifiability": best_sortable_class.get("identifiability") or {},
                "yield_estimate": best_sortable_class.get("yield_estimate") or {},
            } if best_sortable_class else None,
        }

    def _normalize_direction_evaluation(direction_evaluation):
        if not isinstance(direction_evaluation, dict):
            return None

        def _normalize_side(side):
            payload = direction_evaluation.get(side) or {}
            return {
                "male_genotype": payload.get("male_genotype", ""),
                "female_genotype": payload.get("female_genotype", ""),
                "score": payload.get("score", 0.0),
                "summary": _normalize_simulation_summary(payload.get("summary") or {}),
            }

        return {
            "forward": _normalize_side("forward"),
            "reverse": _normalize_side("reverse"),
            "recommended_direction": direction_evaluation.get("recommended_direction", "equivalent"),
            "recommended_cross": direction_evaluation.get("recommended_cross"),
            "same_outcome": bool(direction_evaluation.get("same_outcome")),
            "rationale": list(direction_evaluation.get("rationale") or []),
            "operational_notes": list(direction_evaluation.get("operational_notes") or []),
        }

    phenotype_cache = get_cached_cross_phenotype(cross, strict=False)
    if phenotype_cache:
        parent_phenotypes = phenotype_cache["parentPhenotypes"]
        normalized_parent_phenotypes = {
            **parent_phenotypes,
            "male": {
                **parent_phenotypes.get("male", {}),
                "construct_annotation_labels": parent_phenotypes.get("male", {}).get("construct_annotation_labels", []),
                "split_system_labels": parent_phenotypes.get("male", {}).get("split_system_labels", []),
                "provenance_summary": parent_phenotypes.get("male", {}).get("provenance_summary", {"counts": {}, "primary_basis": "unknown"}),
                "viability_status": parent_phenotypes.get("male", {}).get("viability_status", "unknown"),
                "fertility_status": parent_phenotypes.get("male", {}).get("fertility_status", "unknown"),
                "lethal_alleles": parent_phenotypes.get("male", {}).get("lethal_alleles", []),
                "sterile_alleles": parent_phenotypes.get("male", {}).get("sterile_alleles", []),
                "reference_images": select_prediction_reference_images(
                    parent_phenotypes.get("male", {}),
                    limit=3,
                ),
            },
            "female": {
                **parent_phenotypes.get("female", {}),
                "construct_annotation_labels": parent_phenotypes.get("female", {}).get("construct_annotation_labels", []),
                "split_system_labels": parent_phenotypes.get("female", {}).get("split_system_labels", []),
                "provenance_summary": parent_phenotypes.get("female", {}).get("provenance_summary", {"counts": {}, "primary_basis": "unknown"}),
                "viability_status": parent_phenotypes.get("female", {}).get("viability_status", "unknown"),
                "fertility_status": parent_phenotypes.get("female", {}).get("fertility_status", "unknown"),
                "lethal_alleles": parent_phenotypes.get("female", {}).get("lethal_alleles", []),
                "sterile_alleles": parent_phenotypes.get("female", {}).get("sterile_alleles", []),
                "reference_images": select_prediction_reference_images(
                    parent_phenotypes.get("female", {}),
                    limit=3,
                ),
            },
        }
        normalized_predicted_offspring = []
        for offspring in phenotype_cache["predictedOffspring"]:
            phenotype = offspring.get("phenotype", {})
            identifiability = offspring.get("identifiability") or {}
            validation = offspring.get("validation") or {}
            yield_estimate = offspring.get("yield_estimate") or {}
            normalized_predicted_offspring.append(
                {
                    **offspring,
                    "is_viable": offspring.get("is_viable", True),
                    "pruned": offspring.get("pruned", False),
                    "prune_reasons": offspring.get("prune_reasons", []),
                    "viable_probability": offspring.get("viable_probability", offspring.get("probability", 0.0)),
                    "viable_probability_percent": offspring.get("viable_probability_percent", offspring.get("probability_percent", 0.0)),
                    "phenotype": {
                        **phenotype,
                        "construct_annotation_labels": phenotype.get("construct_annotation_labels", []),
                        "split_system_labels": phenotype.get("split_system_labels", []),
                        "provenance_summary": phenotype.get("provenance_summary", {"counts": {}, "primary_basis": "unknown"}),
                        "viability_status": phenotype.get("viability_status", "unknown"),
                        "fertility_status": phenotype.get("fertility_status", "unknown"),
                        "lethal_alleles": phenotype.get("lethal_alleles", []),
                        "sterile_alleles": phenotype.get("sterile_alleles", []),
                        "stage_specific_effects": phenotype.get("stage_specific_effects", []),
                        "reference_images": select_prediction_reference_images(
                            phenotype,
                            limit=3,
                        ),
                    },
                    "identifiability": {
                        "label": identifiability.get("label", "Sortability unavailable"),
                        "confidence_label": identifiability.get("confidence_label", "low"),
                        "score": identifiability.get("score", 0.0),
                        "identifiable": identifiability.get("identifiable", False),
                        "distinguishing_features": identifiability.get("distinguishing_features", []),
                        "exclusion_features": identifiability.get("exclusion_features", []),
                        "low_confidence_features": identifiability.get("low_confidence_features", []),
                        "confusable_genotypes": identifiability.get("confusable_genotypes", []),
                        "confusable_rows": identifiability.get("confusable_rows", []),
                        "warnings": identifiability.get("warnings", []),
                        "selection_instructions": identifiability.get("selection_instructions", ""),
                        "depends_on_low_confidence_sorting": identifiability.get("depends_on_low_confidence_sorting", False),
                        "uses_exclusion_sorting": identifiability.get("uses_exclusion_sorting", False),
                    },
                    "validation": {
                        "viable": validation.get("viable", False),
                        "fertile": validation.get("fertile", False),
                        "maintainable": validation.get("maintainable", False),
                        "requires_recombination": validation.get("requires_recombination", False),
                        "impossible_reasons": validation.get("impossible_reasons", []),
                        "unsupported_reasons": validation.get("unsupported_reasons", []),
                        "issues": validation.get("issues", []),
                    },
                    "yield_estimate": {
                        "expected_targets_per_vial": yield_estimate.get("expected_targets_per_vial", 0.0),
                        "yield_label": yield_estimate.get("yield_label", "low"),
                        "recommended_parallel_vials": yield_estimate.get("recommended_parallel_vials", 0),
                    },
                }
            )
        return (
            normalized_parent_phenotypes,
            normalized_predicted_offspring,
            {
                "cached_at": phenotype_cache.get("computedAt", ""),
                "is_cached": True,
                "simulation_summary": _normalize_simulation_summary(
                    phenotype_cache.get("simulationSummary") or {}
                ),
                "direction_evaluation": _normalize_direction_evaluation(
                    phenotype_cache.get("directionEvaluation")
                ),
            },
        )

    parent_phenotypes = {
        "male": {
            "summary": "Phenotype cache not generated",
            "confidence_label": "low",
            "construct_annotation_labels": [],
            "split_system_labels": [],
            "provenance_summary": {"counts": {}, "primary_basis": "unknown"},
            "viability_status": "unknown",
            "fertility_status": "unknown",
            "lethal_alleles": [],
            "sterile_alleles": [],
            "reference_images": [],
            "warnings": [
                "Refresh the cache from this record to generate phenotype previews.",
            ],
        },
        "female": {
            "summary": "Phenotype cache not generated",
            "confidence_label": "low",
            "construct_annotation_labels": [],
            "split_system_labels": [],
            "provenance_summary": {"counts": {}, "primary_basis": "unknown"},
            "viability_status": "unknown",
            "fertility_status": "unknown",
            "lethal_alleles": [],
            "sterile_alleles": [],
            "reference_images": [],
            "warnings": [
                "Refresh the cache from this record to generate phenotype previews.",
            ],
        },
        "summary": "Phenotype cache not generated",
        "source_counts": {},
    }
    return parent_phenotypes, [], {
        "cached_at": "",
        "is_cached": False,
        "simulation_summary": None,
        "direction_evaluation": None,
    }


def _get_cross_phenotype_summary(cross):
    phenotype_cache = get_cached_cross_phenotype(cross, strict=False)
    if not phenotype_cache:
        return "Refresh in record"
    return phenotype_cache["parentPhenotypes"].get("summary", "Phenotype unavailable")


@bp.route("/cross_explorer", methods=["GET", "POST"])
@login_required
def cross_explorer():
    username = session.get("username")

    pagination_state = get_explorer_pagination_state(
        session_key="cross_explorer_pagination",
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

    filter_state = filter_state or {}
    search_query = filter_state.get("searchQuery")
    mongo_filter = _build_cross_mongo_filter(filter_state)

    try:
        scope_counts = compute_explorer_scope_counts("crosses", username, db)
        unique_values = _compute_cross_unique_values(username, db, filter_state)

        if search_query:
            # Fuzzy search can't be expressed as a Mongo query - fetch every
            # deterministically-filtered + projected candidate, then apply
            # the fuzzy filter, sort, and pagination in Python exactly as
            # the original route did, just over a far smaller candidate set.
            candidates, _ = get_accessible_documents_page(
                "crosses", username, db, mongo_filter=mongo_filter, limit=None,
                extra_sort_keys=(),
            )
            filtered_crosses = _apply_cross_search(candidates, search_query)
            filtered_crosses = sorted(filtered_crosses, key=_cross_sort_key)
            pagination = paginate_explorer_records(
                filtered_crosses,
                page=pagination_state["page"],
                per_page=pagination_state["per_page"],
                per_page_value=pagination_state["per_page_value"],
            )
        else:
            per_page = pagination_state["per_page"]
            skip = (pagination_state["page"] - 1) * per_page if per_page else 0
            page_items, total_count = get_accessible_documents_page(
                "crosses", username, db, mongo_filter=mongo_filter,
                skip=skip, limit=per_page, extra_sort_keys=(),
            )
            pagination = build_pagination_from_db_page(
                page_items, total_count, pagination_state,
            )
    except Exception as e:
        current_app.logger.exception("Error fetching crosses for explorer: %s", e)
        scope_counts = {"maintain": 0, "assigned_out": 0, "incoming": 0}
        unique_values = {
            k: []
            for k in ["MaleSpecies", "FemaleSpecies", "TrayID", "Status", "FoodType"]
        }
        pagination = paginate_explorer_records(
            [], page=1, per_page=pagination_state["per_page"],
            per_page_value=pagination_state["per_page_value"],
        )

    pagination["per_page_options"] = pagination_state["per_page_options"]
    page_crosses = pagination["items"]

    for cross in page_crosses:
        cross["FlipIn"] = get_flip_in(cross)
        set_flip_display_fields(cross, raw_value=cross["FlipIn"], display_field="FlipIn")
        cross["EclosesIn"] = get_eclosion_in(cross)
        cross["ParentPhenotypeGuess"] = _get_cross_phenotype_summary(cross)

    return render_template(
        "cross/cross_explorer.html",
        username=username,
        crosses=page_crosses,
        scope_counts=scope_counts,
        unique_values=unique_values,
        filter_state=filter_state,
        pagination=pagination,
    )


_CROSS_SELECTION_PROJECTION = {
    "UniqueID", "User", "AssignedTo", "Name", "TrayID", "TrayPosition",
    "Status", "MaleSpecies", "FemaleSpecies", "FoodType", "Comments",
}


@bp.route("/cross_explorer/selection", methods=["GET"])
@login_required
def cross_explorer_selection():
    username = session.get("username")
    filter_state = session.get("filter_state", {})
    mongo_filter = _build_cross_mongo_filter(filter_state)
    candidates, _ = get_accessible_documents_page(
        "crosses", username, db, mongo_filter=mongo_filter,
        limit=None, projection=_CROSS_SELECTION_PROJECTION, extra_sort_keys=(),
    )
    search_query = filter_state.get("searchQuery")
    filtered_crosses = (
        _apply_cross_search(candidates, search_query) if search_query else candidates
    )
    filtered_crosses = sorted(filtered_crosses, key=_cross_sort_key)

    items = [_build_cross_selection_item(cross) for cross in filtered_crosses]
    return jsonify({"count": len(items), "items": items})


def _cross_sort_key(cross):
    tray_position = str(cross.get("TrayPosition", "0") or "0").strip()
    try:
        position_value = int(float(tray_position))
    except (TypeError, ValueError):
        position_value = 0
    return (str(cross.get("TrayID", "")), position_value)


def _build_cross_mongo_filter(filters):
    """Translate the explorer's deterministic filter fields into a Mongo
    query. Excludes searchQuery - that stays a Python fuzzy post-filter
    (see Task 12 rationale: fuzz.partial_ratio can't be expressed as a
    Mongo query without changing which records match).
    """
    no_longer_maintained_status = "No longer maintained"
    clauses = []

    filter_male_species = filters.get("filterMaleSpecies")
    if filter_male_species:
        clauses.append({"MaleSpecies": filter_male_species})

    filter_female_species = filters.get("filterFemaleSpecies")
    if filter_female_species:
        clauses.append({"FemaleSpecies": filter_female_species})

    filter_tray_id = filters.get("filterTrayID")
    if filter_tray_id:
        clauses.append({"TrayID": filter_tray_id})

    filter_status = filters.get("filterStatus")
    if filter_status == no_longer_maintained_status:
        clauses.append({"Status": no_longer_maintained_status})
    elif filter_status:
        clauses.append({"Status": filter_status})
    else:
        clauses.append({"Status": {"$ne": no_longer_maintained_status}})

    filter_food_type = filters.get("filterFoodType")
    if filter_food_type:
        clauses.append({"FoodType": filter_food_type})

    if not clauses:
        return {}
    if len(clauses) == 1:
        return clauses[0]
    return {"$and": clauses}


def _apply_cross_search(crosses, search_query):
    """The fuzzy-match half of the original _apply_cross_filters - unchanged
    matching logic, just split out so it can run standalone on an
    already-deterministically-filtered candidate list.
    """
    def match(cross):
        search_fields = [
            cross.get("Name", ""),
            cross.get("TrayID", ""),
            cross.get("TrayPosition", ""),
            cross.get("Comments", ""),
        ]
        search_string = " ".join(str(field) for field in search_fields)
        return fuzz.partial_ratio(search_string, search_query) > 80

    return [cross for cross in crosses if match(cross)]


def _compute_cross_unique_values(username, db, filter_state):
    owner_scope = {"$or": [{"User": username}, {"AssignedTo": username}]}
    mongo_filter = _build_cross_mongo_filter(filter_state) if filter_state else {}
    combined = {"$and": [owner_scope, mongo_filter]} if mongo_filter else owner_scope

    unique_values = {
        field: sorted(
            str(v) for v in db["crosses"].distinct(field, combined) if v not in (None, "")
        )
        for field in ("MaleSpecies", "FemaleSpecies", "TrayID", "FoodType")
    }
    unique_values["Status"] = sorted(
        str(v) for v in db["crosses"].distinct("Status", owner_scope) if v not in (None, "")
    )
    return unique_values


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


def _build_cross_selection_item(cross):
    tray_id = str(cross.get("TrayID", "") or "").strip()
    tray_position = str(cross.get("TrayPosition", "") or "").strip()

    if tray_id and tray_position:
        identifier = f"Tray {tray_id}-{tray_position}"
    elif tray_id:
        identifier = f"Tray {tray_id}"
    else:
        identifier = "Unassigned tray"

    return {
        "id": str(cross.get("UniqueID", "") or "").strip(),
        "quantity": 1,
        "identifier": identifier,
        "name": str(cross.get("Name", "") or "").strip(),
        "uid": str(cross.get("UniqueID", "") or "").strip(),
    }


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
            cross = get_accessible_cross(username, unique_id, db)
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
        cross = get_accessible_cross(username, unique_id, db, annotate=True)
        if not cross:
            return redirect(url_for("cross.cross_explorer"))

        owner_username = cross.get("User", "")
        can_edit_record = bool(cross.get("ViewerCanEdit"))
        direct_reports = get_direct_reports(username, db) if can_edit_record else []

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
            "ownerUser": cross.get("OwnerUser", owner_username),
            "assignedTo": cross.get("AssignedTo", ""),
            "maintainerUser": cross.get("MaintainerUser", owner_username),
            "assignmentScopeLabel": cross.get("AssignmentScopeLabel", "Maintain"),
            "assignmentScopeDetail": cross.get("AssignmentScopeDetail", "Owned by you"),
        }
    except Exception as e:
        print(f"Error fetching cross {unique_id} for view: {e}")
        return redirect(url_for("cross.cross_explorer"))

    parent_phenotypes, predicted_offspring, phenotype_cache_meta = _get_cross_phenotype_for_view(cross)

    if request.method == "POST":
        try:
            if not can_edit_record:
                raise ValueError("Only the owner can edit cross metadata.")

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
                    parent_phenotypes=parent_phenotypes,
                    predicted_offspring=predicted_offspring,
                    phenotype_cache_meta=phenotype_cache_meta,
                    can_edit_record=can_edit_record,
                    direct_reports=direct_reports,
                    error=error_message,
                    message="No changes detected.",
                )

            # Edit cross in the user's collection
            success = edit_cross(owner_username, unique_id, db, changed_fields)

            if success:
                # Get the updated cross data
                try:
                    edited_cross = db["crosses"].find_one(
                        {"UniqueID": unique_id, "User": owner_username}
                    )
                    if edited_cross:
                        # Update the cross vials
                        update_cross_vials(edited_cross, owner_username, db)
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
        parent_phenotypes=parent_phenotypes,
        predicted_offspring=predicted_offspring,
        phenotype_cache_meta=phenotype_cache_meta,
        can_edit_record=can_edit_record,
        direct_reports=direct_reports,
        error=error_message,
    )


@bp.route("/assign_cross/<unique_id>", methods=["POST"])
@login_required
@limiter.limit("20 per hour")
def assign_cross(unique_id):
    username = session.get("username")
    assignee = request.form.get("assignee", "")

    cross = get_accessible_cross(username, unique_id, db, annotate=True)
    if not cross or not cross.get("ViewerCanEdit"):
        return redirect(url_for("cross.view_cross", unique_id=unique_id))

    success, error_message = update_document_assignment(
        "crosses",
        username,
        unique_id,
        assignee,
        db,
    )
    if not success:
        return redirect(url_for("cross.view_cross", unique_id=unique_id))

    write_activity(username, f"Updated cross assignment for {unique_id}", db)
    return redirect(url_for("cross.view_cross", unique_id=unique_id))


@bp.route("/view_cross/<unique_id>/refresh_phenotype", methods=["POST"])
@login_required
@limiter.limit("20 per hour")
def refresh_cross_phenotype(unique_id):
    username = session.get("username")

    cross = get_accessible_cross(username, unique_id, db, annotate=True)
    if not cross:
        return redirect(url_for("cross.cross_explorer"))

    owner_username = cross.get("User", "")
    try:
        with hold_operation_lock(
            db,
            key=f"record:phenotype-refresh:cross:{unique_id}",
            actor=username,
            label=f"Cross phenotype refresh {unique_id}",
            ttl_seconds=300,
            metadata={"route": "refresh_cross_phenotype", "uid": unique_id},
            conflict_message=f"A phenotype refresh for cross {unique_id} is already running. Please wait for it to finish.",
        ):
            phenotype_cache = build_cross_phenotype_cache(
                cross.get("MaleGenotype", ""),
                cross.get("FemaleGenotype", ""),
            )
            db["crosses"].update_one(
                {"UniqueID": unique_id, "User": owner_username},
                {"$set": {"PhenotypeCache": phenotype_cache}},
            )
            write_activity(username, f"Refreshed phenotype cache for cross {unique_id}", db)
    except OperationLockConflict as exc:
        flash(str(exc), "warning")
    return redirect(url_for("cross.view_cross", unique_id=unique_id))
