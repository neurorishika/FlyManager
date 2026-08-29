# flymanager/app/routes/stock.py
import hashlib
import json
import os
import re
import traceback
from datetime import datetime
from pathlib import Path
from urllib.parse import unquote

from flask import (Blueprint, current_app, flash, jsonify, redirect,
                   render_template, request, send_file, session, url_for)
from fuzzywuzzy import fuzz

from flymanager.app import db
from flymanager.app.routes.auth import login_required
from flymanager.app.routes.explorer_utils import (
    build_pagination_from_db_page, collect_unique_values,
    compute_explorer_scope_counts, get_explorer_filter_state,
    get_explorer_pagination_state, paginate_explorer_records,
    set_flip_display_fields)
from flymanager.app.security import (csrf, get_json_payload, limiter,
                                     normalize_optional_text, parse_int_value,
                                     require_confirmation)
from flymanager.app.services.stock_standardization import \
    review_stock_standardization
from flymanager.app.settings import DEFAULT_STOCK_PROPERTY_VALUES
from flymanager.utils.genetics import qc_genotype
from flymanager.utils.mongo import (OperationLockConflict, add_metadata,
                                    add_to_stock, edit_stock,
                                    get_accessible_documents_page,
                                    get_accessible_stock,
                                    get_accessible_stocks, get_direct_reports,
                                    get_eclosion_in, get_flip_in, get_metadata,
                                    hold_operation_lock,
                                    update_document_assignment,
                                    update_stock_vials, write_activity)
from flymanager.utils.mongo_records import (current_timestamp,
                                              diff_candidate_against_record)
from flymanager.utils.phenotypes.image_library import \
    select_prediction_reference_images
from flymanager.utils.phenotypes.flybase_pipeline import \
    compute_flybase_pipeline_signature
from flymanager.utils.phenotypes.predictor import (
    build_stock_phenotype_cache, get_cached_stock_phenotype,
    predict_individual_phenotype)
from flymanager.utils.stock_sources import (
    EXTERNAL_SOURCE_OPTIONS, build_stock_provider_metadata_from_context,
    enrich_stock_source_context, find_external_stock_matches,
    get_external_stock_record, resolve_stock_source_collection,
    resolve_stock_source_type)
from flymanager.utils.utils import clean_tagify_data, increment_replicate_id

bp = Blueprint("stock", __name__)  # url_prefix is defined in app/__init__
PROVIDER_MATCH_CACHE_FIELD = "ProviderMatchCache"
PROVIDER_MATCH_CACHE_VERSION = 1


def _get_stock_reference_images(prediction):
    return select_prediction_reference_images(
        prediction,
        base_dir=current_app.config.get("PHENOTYPE_IMAGE_LIBRARY_PATH"),
    )


def _decorate_prediction_for_view(prediction, *, image_limit=None):
    decorated = {
        **prediction,
        "reference_images": select_prediction_reference_images(
            prediction,
            base_dir=current_app.config.get("PHENOTYPE_IMAGE_LIBRARY_PATH"),
            limit=image_limit,
        ),
        "provenance_summary": prediction.get(
            "provenance_summary",
            {"counts": {}, "primary_basis": "unknown"},
        ),
        "viability_status": prediction.get("viability_status", "unknown"),
        "fertility_status": prediction.get("fertility_status", "unknown"),
        "lethal_alleles": prediction.get("lethal_alleles", []),
        "sterile_alleles": prediction.get("sterile_alleles", []),
        "stage_specific_effects": prediction.get("stage_specific_effects", []),
        "epistasis_events": prediction.get("epistasis_events", []),
    }
    return decorated


def _get_stock_phenotype_for_view(stock):
    phenotype_cache = get_cached_stock_phenotype(stock, strict=False)
    if phenotype_cache:
        prediction = {**phenotype_cache["prediction"]}
        return {
            **prediction,
            "female_markers": prediction.get("female_markers", []),
            "male_markers": prediction.get("male_markers", []),
            "female_marker_labels": prediction.get("female_marker_labels", []),
            "male_marker_labels": prediction.get("male_marker_labels", []),
            "female_construct_annotation_labels": prediction.get("female_construct_annotation_labels", []),
            "male_construct_annotation_labels": prediction.get("male_construct_annotation_labels", []),
            "female_split_system_labels": prediction.get("female_split_system_labels", []),
            "male_split_system_labels": prediction.get("male_split_system_labels", []),
            "reference_images": _get_stock_reference_images(prediction),
            "provenance_summary": prediction.get(
                "provenance_summary",
                {"counts": {}, "primary_basis": "unknown"},
            ),
            "viability_status": prediction.get("viability_status", "unknown"),
            "fertility_status": prediction.get("fertility_status", "unknown"),
            "lethal_alleles": prediction.get("lethal_alleles", []),
            "sterile_alleles": prediction.get("sterile_alleles", []),
            "stage_specific_effects": prediction.get("stage_specific_effects", []),
            "epistasis_events": prediction.get("epistasis_events", []),
            "cached_at": phenotype_cache.get("computedAt", ""),
            "is_cached": True,
        }

    return {
        "best_guess_summary": "Phenotype cache not generated",
        "best_guess_basis": "cache_missing",
        "female_summary": "Refresh the cache from this record to generate a phenotype preview.",
        "male_summary": None,
        "female_construct_annotation_labels": [],
        "male_construct_annotation_labels": [],
        "female_split_system_labels": [],
        "male_split_system_labels": [],
        "female_markers": [],
        "male_markers": [],
        "female_marker_labels": [],
        "male_marker_labels": [],
        "shared_summary": None,
        "female_only_labels": [],
        "male_only_labels": [],
        "reference_images": [],
        "provenance_summary": {"counts": {}, "primary_basis": "unknown"},
        "viability_status": "unknown",
        "fertility_status": "unknown",
        "lethal_alleles": [],
        "sterile_alleles": [],
        "stage_specific_effects": [],
        "epistasis_events": [],
        "warnings": [
            "The stored phenotype cache is missing or stale for this genotype.",
        ],
        "confidence_label": "low",
        "cached_at": "",
        "is_cached": False,
    }


def _get_stock_phenotype_summary(stock):
    phenotype_cache = get_cached_stock_phenotype(stock, strict=False)
    if not phenotype_cache:
        return {
            "guess": "Refresh in record",
            "confidence": "refresh required",
        }

    prediction = phenotype_cache["prediction"]
    return {
        "guess": prediction.get("best_guess_summary", "Phenotype unavailable"),
        "confidence": prediction.get("confidence_label", "low"),
    }


def _stock_sort_key(stock):
    tray_position = str(stock.get("TrayPosition", "0") or "0").strip()
    try:
        position_value = int(float(tray_position))
    except (TypeError, ValueError):
        position_value = 0

    return (
        str(stock.get("TrayID", "")),
        position_value,
        str(stock.get("Name", "")),
        str(stock.get("UniqueID", "")),
    )


def _build_stock_standardization_overview_row(stock):
    review = review_stock_standardization(stock.get("Genotype", ""), candidate_limit=0)
    issues = list(review.get("issues") or [])
    top_tokens = [issue.get("token", "") for issue in issues[:4] if issue.get("token")]
    recommended_replacements = [
        {
            "from": issue.get("token", ""),
            "to": issue.get("recommended_replacement", ""),
        }
        for issue in issues
        if issue.get("token") and issue.get("recommended_replacement")
    ]

    return {
        "uniqueID": str(stock.get("UniqueID", "")),
        "name": str(stock.get("Name", "")),
        "genotype": str(stock.get("Genotype", "")),
        "trayID": str(stock.get("TrayID", "")),
        "trayPosition": str(stock.get("TrayPosition", "")),
        "assignmentScopeLabel": str(stock.get("AssignmentScopeLabel", "Maintain")),
        "assignmentScopeDetail": str(stock.get("AssignmentScopeDetail", "Owned by you")),
        "reviewable": bool(stock.get("ViewerCanEdit")),
        "issueCount": int(review.get("issue_count") or 0),
        "unresolvedCount": int((review.get("summary") or {}).get("unresolved_token", 0)),
        "unmodeledCount": int((review.get("summary") or {}).get("standard_format_unmodeled", 0)),
        "topTokens": top_tokens,
        "recommendedReplacements": recommended_replacements[:3],
    }


@bp.route("/phenotype_image/<path:relative_path>")
@login_required
@limiter.limit("60 per minute")
def phenotype_reference_image(relative_path):
    library_root = Path(current_app.config.get("PHENOTYPE_IMAGE_LIBRARY_PATH", "")).resolve()
    candidate_path = (library_root / relative_path).resolve()
    try:
        candidate_path.relative_to(library_root)
    except ValueError:
        return redirect(url_for("main.home"))
    if not candidate_path.is_file():
        return redirect(url_for("main.home"))
    return send_file(candidate_path)


def _get_phenotype_preview_metadata():
    return {
        "genesX": get_metadata("genesX", db),
        "genes2": get_metadata("genes2nd", db),
        "genes3": get_metadata("genes3rd", db),
        "genes4": get_metadata("genes4th", db),
    }


def _normalize_preview_tagify_input(form_value):
    raw_value = str(form_value or "").strip()
    if not raw_value:
        return []

    if raw_value.startswith("["):
        values = clean_tagify_data(raw_value)
        if isinstance(values, list):
            return [str(value).strip() for value in values if str(value).strip()]

    return [segment.strip() for segment in raw_value.split("/") if segment.strip()]


def _compose_preview_genotype_from_form(form):
    chromosome_values = [
        _normalize_preview_tagify_input(form.get("genotypeX")),
        _normalize_preview_tagify_input(form.get("genotype2")),
        _normalize_preview_tagify_input(form.get("genotype3")),
        _normalize_preview_tagify_input(form.get("genotype4")),
    ]
    if not any(chromosome_values):
        return ""

    normalized_parts = ["/".join(values) if values else "+" for values in chromosome_values]
    return "; ".join(normalized_parts)


def _split_preview_genotype_for_form(genotype):
    genotype_text = str(genotype or "").strip()
    chromosome_parts = [part.strip() for part in genotype_text.split(";")] if genotype_text else []
    chromosome_parts = (chromosome_parts + ["", "", "", ""])[:4]

    return {
        "genotypeX": [token.strip() for token in chromosome_parts[0].split("/") if token.strip()],
        "genotype2": [token.strip() for token in chromosome_parts[1].split("/") if token.strip()],
        "genotype3": [token.strip() for token in chromosome_parts[2].split("/") if token.strip()],
        "genotype4": [token.strip() for token in chromosome_parts[3].split("/") if token.strip()],
    }


@bp.route("/phenotype_preview", methods=["GET", "POST"])
@csrf.exempt
@login_required
@limiter.limit("30 per minute")
def phenotype_preview():
    genotype = ""
    sex = "female"
    prediction = None

    if request.method == "POST" and request.is_json:
        try:
            payload = get_json_payload()
        except ValueError as exc:
            return jsonify({"status": "error", "message": str(exc)}), 400

        genotype = str(payload.get("genotype") or "").strip()
        sex = str(payload.get("sex") or "female").strip().lower()
        if sex not in {"male", "female"}:
            return jsonify({"status": "error", "message": "Sex must be either 'male' or 'female'."}), 400

        prediction = _decorate_prediction_for_view(
            predict_individual_phenotype(genotype, sex),
            image_limit=4,
        )
        return jsonify(
            {
                "status": "success",
                "genotype": genotype,
                "sex": sex,
                "prediction": prediction,
            }
        )

    if request.method == "POST":
        genotype = _compose_preview_genotype_from_form(request.form)
        if not genotype:
            genotype = str(request.form.get("genotype") or "").strip()
        sex = str(request.form.get("sex") or "female").strip().lower()
    else:
        genotype = str(request.args.get("genotype") or "").strip()
        sex = str(request.args.get("sex") or "female").strip().lower()

    if sex not in {"male", "female"}:
        sex = "female"

    if genotype:
        prediction = _decorate_prediction_for_view(
            predict_individual_phenotype(genotype, sex),
            image_limit=4,
        )

    preview_metadata = _get_phenotype_preview_metadata()

    return render_template(
        "stock/phenotype_preview.html",
        preview_genotype=genotype,
        preview_genotype_parts=_split_preview_genotype_for_form(genotype),
        preview_sex=sex,
        preview_prediction=prediction,
        genesX=preview_metadata["genesX"],
        genes2=preview_metadata["genes2"],
        genes3=preview_metadata["genes3"],
        genes4=preview_metadata["genes4"],
    )


def _build_provider_match_cache_signature(stock, source_context):
    signature_payload = {
        "sourceType": source_context.get("sourceType", ""),
        "sourceCollection": source_context.get("sourceCollection", ""),
        "sourceID": str(stock.get("SourceID") or stock.get("sourceID") or "").strip(),
        "flyBaseStockID": source_context.get("flyBaseStockID", ""),
        "genotype": str(stock.get("Genotype") or stock.get("genotype") or "").strip(),
        "rawGenotype": str(
            stock.get("ExternalRawGenotype")
            or stock.get("rawGenotype")
            or ""
        ).strip(),
        "altReference": str(stock.get("AltReference") or stock.get("altReference") or "").strip(),
        "providerURL": source_context.get("providerURL", ""),
    }
    serialized_payload = json.dumps(signature_payload, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(serialized_payload.encode("utf-8")).hexdigest()


def _parse_provider_match_cache_timestamp(timestamp_text):
    normalized_text = str(timestamp_text or "").strip()
    if not normalized_text:
        return None

    for timestamp_format in ("%Y-%m-%d %H:%M", "%Y-%m-%d %H:%M:%S"):
        try:
            return datetime.strptime(normalized_text, timestamp_format)
        except ValueError:
            continue

    return None


def _format_provider_match_cache_age(cached_at_text):
    cached_at = _parse_provider_match_cache_timestamp(cached_at_text)
    if cached_at is None:
        return ""

    age_delta = datetime.now() - cached_at
    total_minutes = max(0, int(age_delta.total_seconds() // 60))
    if total_minutes < 1:
        return "just now"
    if total_minutes < 60:
        return f"{total_minutes}m ago"

    total_hours = total_minutes // 60
    if total_hours < 24:
        return f"{total_hours}h ago"

    total_days = total_hours // 24
    if total_days < 7:
        return f"{total_days}d ago"

    total_weeks = total_days // 7
    if total_weeks < 5:
        return f"{total_weeks}w ago"

    return cached_at.strftime("%Y-%m-%d")


def _build_provider_match_preview(candidate):
    if not isinstance(candidate, dict):
        return ""

    source_label = candidate.get("sourceCollection") or candidate.get("stockSource") or "Provider"
    source_id = str(candidate.get("sourceID") or "").strip()
    if source_id:
        return f"{source_label} {source_id}"
    return str(source_label)


def _build_provider_match_metadata(candidates, *, cached, cached_at):
    normalized_candidates = candidates if isinstance(candidates, list) else []
    count = len(normalized_candidates)
    age_label = _format_provider_match_cache_age(cached_at)
    count_label = f"{count} cached match{'es' if count != 1 else ''}" if cached else f"{count} match{'es' if count != 1 else ''}"
    status_label = ""
    if cached and age_label:
        status_label = f"Cached {age_label}"
    elif cached:
        status_label = "Cached"
    elif age_label:
        status_label = f"Updated {age_label}"
    else:
        status_label = "Updated"

    primary_match = _build_provider_match_preview(normalized_candidates[0]) if normalized_candidates else ""
    return {
        "count": count,
        "countLabel": count_label,
        "statusLabel": status_label,
        "ageLabel": age_label,
        "primaryMatch": primary_match,
    }


def _build_provider_match_payload(candidates, *, cached, cached_at):
    metadata = _build_provider_match_metadata(candidates, cached=cached, cached_at=cached_at)
    return {
        "candidates": candidates,
        "count": len(candidates),
        "cached": cached,
        "cachedAt": cached_at,
        "cacheCountLabel": metadata["countLabel"],
        "cacheStatusLabel": metadata["statusLabel"],
        "cacheAgeLabel": metadata["ageLabel"],
        "primaryMatchLabel": metadata["primaryMatch"],
    }


def _build_provider_match_view_fields(cache_payload):
    if cache_payload is None:
        return {
            "ProviderMatches": [],
            "ProviderMatchesLoaded": False,
            "ProviderMatchesCachedAt": "",
            "ProviderMatchesCached": False,
            "ProviderMatchesCount": 0,
            "ProviderMatchesCountLabel": "",
            "ProviderMatchesStatusLabel": "",
            "ProviderMatchesAgeLabel": "",
            "ProviderMatchesPrimaryLabel": "",
        }

    candidates = cache_payload.get("candidates") or []
    cached = bool(cache_payload.get("cached"))
    cached_at = str(cache_payload.get("cachedAt") or "")
    metadata = _build_provider_match_metadata(
        candidates,
        cached=cached,
        cached_at=cached_at,
    )

    return {
        "ProviderMatches": candidates,
        "ProviderMatchesLoaded": True,
        "ProviderMatchesCachedAt": cached_at,
        "ProviderMatchesCached": cached,
        "ProviderMatchesCount": cache_payload.get("count", len(candidates)),
        "ProviderMatchesCountLabel": cache_payload.get("cacheCountLabel", metadata["countLabel"]),
        "ProviderMatchesStatusLabel": cache_payload.get("cacheStatusLabel", metadata["statusLabel"]),
        "ProviderMatchesAgeLabel": cache_payload.get("cacheAgeLabel", metadata["ageLabel"]),
        "ProviderMatchesPrimaryLabel": cache_payload.get("primaryMatchLabel", metadata["primaryMatch"]),
    }


def _is_provider_match_cache_entry_valid(cache_payload, stock, source_context):
    if not isinstance(cache_payload, dict):
        return False
    if cache_payload.get("version") != PROVIDER_MATCH_CACHE_VERSION:
        return False
    if str(cache_payload.get("pipelineSignature", "")) != compute_flybase_pipeline_signature():
        return False
    if not isinstance(cache_payload.get("candidates"), list):
        return False
    if _parse_provider_match_cache_timestamp(str(cache_payload.get("cachedAt") or "")) is None:
        return False
    if cache_payload.get("signature") != _build_provider_match_cache_signature(stock, source_context):
        return False
    return True


def _build_provider_match_cache_envelope(stock, source_context, candidates):
    return {
        "version": PROVIDER_MATCH_CACHE_VERSION,
        "pipelineSignature": compute_flybase_pipeline_signature(),
        "signature": _build_provider_match_cache_signature(stock, source_context),
        "candidates": candidates,
        "count": len(candidates),
        "cachedAt": current_timestamp(),
    }


def _provider_match_cache_getter(record):
    source_context = enrich_stock_source_context(record)
    cache_payload = record.get(PROVIDER_MATCH_CACHE_FIELD)
    if _is_provider_match_cache_entry_valid(cache_payload, record, source_context):
        return cache_payload
    return None


def _provider_match_cache_builder(record):
    source_context = enrich_stock_source_context(record)
    candidates = find_external_stock_matches(record)
    return _build_provider_match_cache_envelope(record, source_context, candidates)


def backfill_stock_provider_match_cache(collection, *, users=None, dry_run=False, force=False):
    from flymanager.utils.materialized_cache import backfill_materialized_cache

    return backfill_materialized_cache(
        collection,
        cache_field=PROVIDER_MATCH_CACHE_FIELD,
        cache_getter=_provider_match_cache_getter,
        cache_builder=_provider_match_cache_builder,
        cache_selector_builder=lambda record: {
            "UniqueID": record.get("UniqueID"), "User": record.get("User"),
        },
        projection={
            "_id": 1, "UniqueID": 1, "User": 1, "AssignedTo": 1,
            "SourceID": 1, "StockSource": 1, "SourceCollection": 1,
            "FlyBaseStockID": 1, "Genotype": 1, "ExternalRawGenotype": 1,
            "AltReference": 1, "Provenance": 1, PROVIDER_MATCH_CACHE_FIELD: 1,
        },
        users=users,
        dry_run=dry_run,
        force=force,
    )


def _get_valid_provider_match_cache(stock, source_context):
    cache_payload = stock.get(PROVIDER_MATCH_CACHE_FIELD)
    if not _is_provider_match_cache_entry_valid(cache_payload, stock, source_context):
        return None

    return _build_provider_match_payload(
        cache_payload.get("candidates") or [],
        cached=True,
        cached_at=str(cache_payload.get("cachedAt") or ""),
    )


def _store_provider_match_cache(stock, source_context, candidates):
    cache_payload = _build_provider_match_cache_envelope(stock, source_context, candidates)
    db["stocks"].update_one(
        {"UniqueID": stock["UniqueID"], "User": stock["User"]},
        {"$set": {PROVIDER_MATCH_CACHE_FIELD: cache_payload}},
    )
    return _build_provider_match_payload(
        candidates,
        cached=False,
        cached_at=cache_payload["cachedAt"],
    )


def _get_provider_match_payload(stock, *, refresh=False):
    source_context = enrich_stock_source_context(stock)
    if not refresh:
        cached_payload = _get_valid_provider_match_cache(stock, source_context)
        if cached_payload is not None:
            return cached_payload

    candidates = find_external_stock_matches(stock)
    return _store_provider_match_cache(stock, source_context, candidates)


@bp.route("/view/<unique_id>/refresh_phenotype", methods=["POST"])
@login_required
@limiter.limit("20 per hour")
def refresh_stock_phenotype(unique_id):
    username = session.get("username")

    stock = get_accessible_stock(username, unique_id, db, annotate=True)
    if not stock:
        return redirect(url_for("stock.stock_explorer"))

    owner_username = stock.get("User", "")
    try:
        with hold_operation_lock(
            db,
            key=f"record:phenotype-refresh:stock:{unique_id}",
            actor=username,
            label=f"Stock phenotype refresh {unique_id}",
            ttl_seconds=300,
            metadata={"route": "refresh_stock_phenotype", "uid": unique_id},
            conflict_message=f"A phenotype refresh for stock {unique_id} is already running. Please wait for it to finish.",
        ):
            phenotype_cache = build_stock_phenotype_cache(stock.get("Genotype", ""))
            db["stocks"].update_one(
                {"UniqueID": unique_id, "User": owner_username},
                {"$set": {"PhenotypeCache": phenotype_cache}},
            )
            write_activity(username, f"Refreshed phenotype cache for stock {unique_id}", db)
    except OperationLockConflict as exc:
        flash(str(exc), "warning")
    return redirect(url_for("stock.view_stock", unique_id=unique_id))


@bp.route("/explorer", methods=["GET", "POST"])
@login_required
def stock_explorer():
    username = session.get("username")
    pagination_state = get_explorer_pagination_state(
        session_key="stock_explorer_pagination",
    )
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

    filter_state = filter_state or {}
    search_query = filter_state.get("searchQuery")
    mongo_filter = _build_stock_mongo_filter(filter_state)

    try:
        scope_counts = compute_explorer_scope_counts("stocks", username, db)
        unique_values = _compute_stock_unique_values(username, db, filter_state)

        if search_query:
            # Fuzzy search can't be expressed as a Mongo query - fetch every
            # deterministically-filtered + projected candidate, then apply
            # the fuzzy filter, sort, and pagination in Python exactly as
            # the original route did, just over a far smaller candidate set.
            candidates, _ = get_accessible_documents_page(
                "stocks", username, db, mongo_filter=mongo_filter, limit=None,
            )
            filtered_stocks = _apply_stock_search(candidates, search_query)
            filtered_stocks = sorted(filtered_stocks, key=_stock_sort_key)
            pagination = paginate_explorer_records(
                filtered_stocks,
                page=pagination_state["page"],
                per_page=pagination_state["per_page"],
                per_page_value=pagination_state["per_page_value"],
            )
        else:
            per_page = pagination_state["per_page"]
            skip = (pagination_state["page"] - 1) * per_page if per_page else 0
            page_items, total_count = get_accessible_documents_page(
                "stocks", username, db, mongo_filter=mongo_filter,
                skip=skip, limit=per_page,
            )
            pagination = build_pagination_from_db_page(
                page_items, total_count, pagination_state,
            )
    except Exception as e:
        current_app.logger.exception("Error fetching stocks for explorer: %s", e)
        scope_counts = {"maintain": 0, "assigned_out": 0, "incoming": 0}
        unique_values = {
            k: []
            for k in ["Type", "TrayID", "Status", "FoodType", "Provenance", "Species"]
        }
        pagination = paginate_explorer_records(
            [], page=1, per_page=pagination_state["per_page"],
            per_page_value=pagination_state["per_page_value"],
        )

    pagination["per_page_options"] = pagination_state["per_page_options"]
    page_stocks = pagination["items"]

    for stock in page_stocks:
        stock["FlipIn"] = get_flip_in(stock)
        set_flip_display_fields(
            stock,
            raw_value=stock["FlipIn"],
            display_field="FlipInDisplay",
        )

        stock["EclosesIn"] = get_eclosion_in(stock)
        source_context = enrich_stock_source_context(stock)
        stock["StockSource"] = stock.get("StockSource") or source_context["sourceType"]
        stock["SourceCollection"] = stock.get("SourceCollection") or source_context["sourceCollection"]
        stock["FlyBaseStockID"] = stock.get("FlyBaseStockID") or source_context["flyBaseStockID"]
        stock.update(build_stock_provider_metadata_from_context(source_context))
        stock.update(_build_provider_match_view_fields(_get_valid_provider_match_cache(stock, source_context)))
        phenotype_summary = _get_stock_phenotype_summary(stock)
        stock["PhenotypeGuess"] = phenotype_summary["guess"]
        stock["PhenotypeConfidence"] = phenotype_summary["confidence"]

    return render_template(
        "stock/stock_explorer.html",
        username=username,
        stocks=page_stocks,
        scope_counts=scope_counts,
        unique_values=unique_values,
        filter_state=filter_state,
        pagination=pagination,
    )


_STOCK_SELECTION_PROJECTION = {
    "UniqueID", "User", "AssignedTo", "Name", "TrayID", "TrayPosition",
    "Status", "Type", "FoodType", "Provenance", "Species",
    "SourceID", "Genotype", "AltReference", "SeriesID", "Comments",
}


@bp.route("/explorer/selection", methods=["GET"])
@login_required
def stock_explorer_selection():
    username = session.get("username")
    filter_state = session.get("stock_filter_state", {})
    mongo_filter = _build_stock_mongo_filter(filter_state)
    candidates, _ = get_accessible_documents_page(
        "stocks", username, db, mongo_filter=mongo_filter,
        limit=None, projection=_STOCK_SELECTION_PROJECTION,
    )
    search_query = filter_state.get("searchQuery")
    filtered_stocks = (
        _apply_stock_search(candidates, search_query) if search_query else candidates
    )
    filtered_stocks = sorted(filtered_stocks, key=_stock_sort_key)

    items = [_build_stock_selection_item(stock) for stock in filtered_stocks]
    return jsonify({"count": len(items), "items": items})


@bp.route("/standardization_overview", methods=["GET"])
@login_required
def stock_standardization_overview():
    return redirect(url_for("main.standardization_reviewer"))


def _build_stock_mongo_filter(filters):
    """Translate the explorer's deterministic filter fields into a Mongo
    query. Excludes searchQuery - that stays a Python fuzzy post-filter
    (see Task 12 rationale: fuzz.partial_ratio can't be expressed as a
    Mongo query without changing which records match).
    """
    no_longer_maintained_status = "No longer maintained"
    clauses = []

    filter_type = filters.get("filterType")
    if filter_type:
        clauses.append({"Type": filter_type})

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

    filter_provenance = filters.get("filterProvenance")
    if filter_provenance:
        # Provenance is stored as "Source/detail"; the filter matches on the
        # prefix before the first slash, same as the original Python filter.
        clauses.append({"Provenance": {"$regex": f"^{re.escape(filter_provenance)}(/|$)"}})

    filter_species = filters.get("filterSpecies")
    if filter_species:
        clauses.append({"Species": filter_species})

    if not clauses:
        return {}
    if len(clauses) == 1:
        return clauses[0]
    return {"$and": clauses}


def _apply_stock_search(stocks, search_query):
    """The fuzzy-match half of the original _apply_stock_filters - unchanged
    matching logic, just split out so it can run standalone on an
    already-deterministically-filtered candidate list.
    """
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
        search_string = " ".join(str(field) for field in search_fields if field).lower()
        return fuzz.partial_ratio(search_string, sq_lower) > 80

    return [s for s in stocks if match(s)]


def _compute_stock_unique_values(username, db, filter_state):
    owner_scope = {"$or": [{"User": username}, {"AssignedTo": username}]}
    mongo_filter = _build_stock_mongo_filter(filter_state) if filter_state else {}
    combined = {"$and": [owner_scope, mongo_filter]} if mongo_filter else owner_scope

    unique_values = {
        field: sorted(
            str(v) for v in db["stocks"].distinct(field, combined) if v not in (None, "")
        )
        for field in ("Type", "TrayID", "FoodType", "Species")
    }
    unique_values["Status"] = sorted(
        str(v) for v in db["stocks"].distinct("Status", owner_scope) if v not in (None, "")
    )
    unique_values["Provenance"] = sorted({
        str(value).split("/")[0]
        for value in db["stocks"].distinct("Provenance", combined)
        if value not in (None, "")
    })
    return unique_values


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


def _build_stock_selection_item(stock):
    tray_id = str(stock.get("TrayID", "") or "").strip()
    tray_position = str(stock.get("TrayPosition", "") or "").strip()

    if tray_id and tray_position:
        identifier = f"Tray {tray_id}-{tray_position}"
    elif tray_id:
        identifier = f"Tray {tray_id}"
    else:
        identifier = "Unassigned"

    return {
        "id": str(stock.get("UniqueID", "") or "").strip(),
        "quantity": 1,
        "identifier": identifier,
        "name": str(stock.get("Name", "") or "").strip(),
        "uid": str(stock.get("UniqueID", "") or "").strip(),
    }


@bp.route("/add", methods=["GET", "POST"])
@bp.route(
    "/add/<source_stock_id>", methods=["GET", "POST"]
)  # Route for pre-filling from existing stock
@login_required
def add_stock(source_stock_id=None):
    username = session.get("username")
    error_message = None
    stock_data = {
        "sourceType": DEFAULT_STOCK_PROPERTY_VALUES["StockSource"],
        "sourceID": DEFAULT_STOCK_PROPERTY_VALUES["SourceID"],
        "sourceCollection": DEFAULT_STOCK_PROPERTY_VALUES["SourceCollection"],
        "flyBaseStockID": DEFAULT_STOCK_PROPERTY_VALUES["FlyBaseStockID"],
        "externalRawGenotype": DEFAULT_STOCK_PROPERTY_VALUES["ExternalRawGenotype"],
        "externalSupportStatus": DEFAULT_STOCK_PROPERTY_VALUES["ExternalSupportStatus"],
        "externalSupportReason": DEFAULT_STOCK_PROPERTY_VALUES["ExternalSupportReason"],
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
            stock = get_accessible_stock(username, source_stock_id, db)
            if stock:
                # Prepare data for form pre-filling, increment replicate ID
                stock_data = {
                    "sourceType": "INTERNAL",  # Indicate source is internal
                    "sourceID": stock["UniqueID"],
                    "sourceCollection": stock.get(
                        "SourceCollection",
                        DEFAULT_STOCK_PROPERTY_VALUES["SourceCollection"],
                    ),
                    "flyBaseStockID": stock.get(
                        "FlyBaseStockID",
                        DEFAULT_STOCK_PROPERTY_VALUES["FlyBaseStockID"],
                    ),
                    "externalRawGenotype": stock.get(
                        "ExternalRawGenotype",
                        DEFAULT_STOCK_PROPERTY_VALUES["ExternalRawGenotype"],
                    ),
                    "externalSupportStatus": stock.get(
                        "ExternalSupportStatus",
                        DEFAULT_STOCK_PROPERTY_VALUES["ExternalSupportStatus"],
                    ),
                    "externalSupportReason": stock.get(
                        "ExternalSupportReason",
                        DEFAULT_STOCK_PROPERTY_VALUES["ExternalSupportReason"],
                    ),
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
            require_confirmation(
                request.form.get("creationConfirmation"),
                action_name="stock creation",
            )

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
                "StockSource": request.form.get(
                    "sourceType",
                    DEFAULT_STOCK_PROPERTY_VALUES["StockSource"],
                ),
                "SourceCollection": request.form.get("sourceCollection"),
                "FlyBaseStockID": request.form.get("flyBaseStockID"),
                "ExternalRawGenotype": request.form.get("externalRawGenotype"),
                "ExternalSupportStatus": request.form.get("externalSupportStatus"),
                "ExternalSupportReason": request.form.get("externalSupportReason"),
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
                stock_data = request.form.to_dict()
                stock_data["genotype"] = final_genotype

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
        external_source_options=EXTERNAL_SOURCE_OPTIONS,
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
        stock = get_accessible_stock(username, unique_id, db, annotate=True)
        if not stock:
            flash(f"Stock {unique_id} not found.", "error")
            return redirect(url_for("stock.stock_explorer"))

        owner_username = stock.get("User", "")
        can_edit_record = bool(stock.get("ViewerCanEdit"))
        direct_reports = get_direct_reports(username, db) if can_edit_record else []

        # Prepare data for template display
        source_context = enrich_stock_source_context(stock)
        resolved_source_type = source_context["sourceType"]
        resolved_source_collection = source_context["sourceCollection"]
        stock_data = {
            "sourceID": stock.get("SourceID", ""),
            "sourceType": resolved_source_type,
            "sourceCollection": resolved_source_collection,
            "flyBaseStockID": stock.get("FlyBaseStockID", "") or source_context["flyBaseStockID"],
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
            "externalSupportStatus": stock.get("ExternalSupportStatus", ""),
            "externalSupportReason": stock.get("ExternalSupportReason", ""),
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
            "ownerUser": stock.get("OwnerUser", owner_username),
            "assignedTo": stock.get("AssignedTo", ""),
            "maintainerUser": stock.get("MaintainerUser", owner_username),
            "assignmentScopeLabel": stock.get("AssignmentScopeLabel", "Maintain"),
            "assignmentScopeDetail": stock.get("AssignmentScopeDetail", "Owned by you"),
        }
        cached_provider_matches = _get_valid_provider_match_cache(stock, source_context)
        cached_provider_match_fields = _build_provider_match_view_fields(cached_provider_matches)
        stock_data.update({
            "providerURL": source_context["providerURL"],
            "providerLinkLabel": source_context["providerLinkLabel"],
            "providerLinkKind": source_context["providerLinkKind"],
            "providerMatches": cached_provider_match_fields["ProviderMatches"],
            "providerMatchesCachedAt": cached_provider_match_fields["ProviderMatchesCachedAt"],
            "providerMatchesLoaded": cached_provider_match_fields["ProviderMatchesLoaded"],
            "providerMatchesCached": cached_provider_match_fields["ProviderMatchesCached"],
            "providerMatchesCount": cached_provider_match_fields["ProviderMatchesCount"],
            "providerMatchesCountLabel": cached_provider_match_fields["ProviderMatchesCountLabel"],
            "providerMatchesStatusLabel": cached_provider_match_fields["ProviderMatchesStatusLabel"],
            "providerMatchesAgeLabel": cached_provider_match_fields["ProviderMatchesAgeLabel"],
            "providerMatchesPrimaryLabel": cached_provider_match_fields["ProviderMatchesPrimaryLabel"],
        })
        stock_phenotype = _get_stock_phenotype_for_view(stock)

    except Exception as e:
        print(f"Error fetching stock {unique_id} for view: {e}")
        flash("Error fetching stock data.", "error")
        return redirect(url_for("stock.stock_explorer"))

    if request.method == "POST":
        try:
            if not can_edit_record:
                raise ValueError("Only the owner can edit stock metadata.")

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
                    stock_phenotype=stock_phenotype,
                    error=error_message,
                )

            # --- Edit Stock in Database ---
            print(
                f"Attempting to edit stock {unique_id} with changes: {changed_fields}"
            )
            success = edit_stock(owner_username, unique_id, db, changed_fields)

            if success:
                print(f"Stock {unique_id} edited successfully.")
                # Update vials for the edited stock
                try:
                    edited_stock = db["stocks"].find_one(
                        {"UniqueID": unique_id, "User": owner_username}
                    )
                    if edited_stock:
                        update_stock_vials(edited_stock, owner_username, db)
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
        stock_phenotype=stock_phenotype,
        can_edit_record=can_edit_record,
        direct_reports=direct_reports,
        error=error_message,
    )


@bp.route("/assign/<unique_id>", methods=["POST"])
@login_required
@limiter.limit("20 per hour")
def assign_stock(unique_id):
    username = session.get("username")
    assignee = normalize_optional_text(
        request.form.get("assignee"),
        field_name="Assignee",
        max_length=32,
    )

    stock = get_accessible_stock(username, unique_id, db, annotate=True)
    if not stock or not stock.get("ViewerCanEdit"):
        flash("Only the owner can update stock assignments.", "error")
        return redirect(url_for("stock.view_stock", unique_id=unique_id))

    success, error_message = update_document_assignment(
        "stocks",
        username,
        unique_id,
        assignee,
        db,
    )
    if not success:
        flash(error_message or "Unable to update stock assignment.", "error")
    else:
        if assignee:
            flash(f"Stock {unique_id} assigned to {assignee}.", "success")
        else:
            flash(f"Stock {unique_id} returned to owner maintenance.", "success")
        write_activity(username, f"Updated stock assignment for {unique_id}", db)

    return redirect(url_for("stock.view_stock", unique_id=unique_id))


@bp.route("/reverse_search/<unique_id>", methods=["GET"])
@login_required
def reverse_search_stock(unique_id):
    username = session.get("username")
    force_refresh = str(request.args.get("refresh", "")).strip().lower() in {"1", "true", "yes"}

    try:
        stock = get_accessible_stock(username, unique_id, db)
        if not stock:
            return jsonify({"error": "Stock not found."}), 404

        payload = _get_provider_match_payload(stock, refresh=force_refresh)
        return jsonify(payload)
    except Exception as e:
        current_app.logger.exception(
            "Error reverse searching stock %s for %s: %s", unique_id, username, e
        )
        return jsonify({"error": "An internal error occurred while reverse searching providers."}), 500


@bp.route("/apply_provider_match/<unique_id>", methods=["POST"])
@login_required
@limiter.limit("30 per minute")
def apply_provider_match(unique_id):
    username = session.get("username")

    try:
        payload = get_json_payload()
        candidate_index = parse_int_value(
            payload.get("candidateIndex"), field_name="candidateIndex", minimum=0,
        )
    except ValueError as exc:
        return jsonify({"error": str(exc)}), 400

    confirmed = bool(payload.get("confirm"))

    stock = get_accessible_stock(username, unique_id, db, annotate=True)
    if not stock:
        return jsonify({"error": "Stock not found."}), 404
    if not stock.get("ViewerCanEdit"):
        return jsonify({"error": "Only the owner can apply a provider match."}), 403

    source_context = enrich_stock_source_context(stock)
    cache_payload = stock.get(PROVIDER_MATCH_CACHE_FIELD)
    if not _is_provider_match_cache_entry_valid(cache_payload, stock, source_context):
        return jsonify({
            "error": "Provider matches are out of date. Refresh matches and try again.",
        }), 409

    candidates = cache_payload.get("candidates") or []
    if candidate_index >= len(candidates):
        return jsonify({
            "error": "That match is no longer available. Refresh matches and try again.",
        }), 409

    candidate = candidates[candidate_index]
    field_mapping = {
        "StockSource": candidate.get("stockSource", ""),
        "SourceCollection": candidate.get("sourceCollection", ""),
        "SourceID": candidate.get("sourceID", ""),
        "FlyBaseStockID": candidate.get("flyBaseStockID", ""),
        "ExternalSupportStatus": candidate.get("supportStatus", ""),
    }

    diff = diff_candidate_against_record(stock, field_mapping)
    if not diff:
        return jsonify({
            "applied": False,
            "message": "This stock already matches the selected candidate.",
            "diff": {},
        })

    has_conflict = any(entry["conflict"] for entry in diff.values())
    if has_conflict and not confirmed:
        return jsonify({"applied": False, "requiresConfirmation": True, "diff": diff})

    updates = {field: entry["candidate"] for field, entry in diff.items()}
    success = edit_stock(stock["User"], unique_id, db, updates, refresh_vials=False)
    if not success:
        return jsonify({"error": "Unable to update stock record."}), 500

    updated_stock = dict(stock)
    updated_stock.update(updates)
    updated_source_context = enrich_stock_source_context(updated_stock)
    refreshed_cache_payload = _build_provider_match_cache_envelope(
        updated_stock, updated_source_context, candidates,
    )
    db["stocks"].update_one(
        {"UniqueID": unique_id, "User": stock["User"]},
        {"$set": {PROVIDER_MATCH_CACHE_FIELD: refreshed_cache_payload}},
    )

    match_label = candidate.get("sourceCollection") or candidate.get("stockSource") or "provider"
    write_activity(
        username,
        f"Applied {match_label} {candidate.get('sourceID', '')} provider match to stock {unique_id}",
        db,
    )

    return jsonify({"applied": True, "diff": diff, "fields": updates})


@bp.route("/review_standardization/<unique_id>", methods=["GET"])
@login_required
def review_stock_standardization_route(unique_id):
    username = session.get("username")
    requested_token = str(request.args.get("token") or "").strip()
    requested_query = str(request.args.get("query") or "").strip()

    try:
        stock = get_accessible_stock(username, unique_id, db)
        if not stock:
            return jsonify({"error": "Stock not found."}), 404

        token_search_overrides = {}
        if requested_token and requested_query:
            token_search_overrides[requested_token] = requested_query

        payload = review_stock_standardization(
            stock.get("Genotype", ""),
            token_search_overrides=token_search_overrides,
        )
        payload.update(
            {
                "unique_id": str(stock.get("UniqueID") or unique_id),
                "reviewable": bool(stock.get("ViewerCanEdit")),
            }
        )
        return jsonify(payload)
    except Exception as e:
        current_app.logger.exception(
            "Error reviewing stock standardization %s for %s: %s", unique_id, username, e
        )
        return jsonify({"error": "An internal error occurred while building the stock reviewer."}), 500


@bp.route("/mark_ordered", methods=["POST"])
@login_required
@limiter.limit("20 per minute")
def mark_ordered_stocks():
    username = session.get("username")

    try:
        payload = get_json_payload()
        order_items = payload.get("items", [])
        if not isinstance(order_items, list):
            return jsonify({"message": "items must be a list."}), 400
        if not order_items:
            return jsonify({"message": "At least one order queue item is required."}), 400
        if len(order_items) > 100:
            return jsonify({"message": "items cannot contain more than 100 entries."}), 400

        results = {"success": [], "failed": []}

        for raw_item in order_items:
            try:
                if not isinstance(raw_item, dict):
                    raise ValueError("Each order queue item must be an object.")

                uid = normalize_optional_text(raw_item.get("uid"), field_name="UID", max_length=64)
                if not uid:
                    raise ValueError("UID is required.")

                order_note = normalize_optional_text(
                    raw_item.get("note"),
                    field_name="Order note",
                    max_length=500,
                )

                stock = get_accessible_stock(username, uid, db)
                if not stock:
                    raise ValueError("Stock not found for this user.")

                updates = {"Status": "Ordered"}
                if order_note:
                    existing_comments = str(stock.get("Comments", "") or "").strip()
                    formatted_note = f"Order note: {order_note}"
                    updates["Comments"] = (
                        f"{formatted_note}; {existing_comments}"
                        if existing_comments
                        else formatted_note
                    )

                success = edit_stock(
                    stock["User"],
                    uid,
                    db,
                    updates,
                    refresh_vials=False,
                )
                if not success:
                    raise ValueError("Unable to update stock status.")

                write_activity(username, f"Marked stock {uid} as Ordered from order queue", db)
                results["success"].append({"uid": uid})
            except Exception as item_error:
                results["failed"].append(
                    {
                        "uid": raw_item.get("uid") if isinstance(raw_item, dict) else "",
                        "reason": str(item_error),
                    }
                )

        return jsonify(
            {
                "message": f'Order queue update completed. {len(results["success"])} successful, {len(results["failed"])} failed.',
                "results": results,
            }
        )
    except ValueError as exc:
        return jsonify({"message": str(exc)}), 400
    except Exception as e:
        current_app.logger.exception("Error marking queued stocks ordered for %s: %s", username, e)
        return jsonify({"message": "An internal error occurred while updating ordered stocks."}), 500


@bp.route("/get_internal/<internal_stock_id>", methods=["GET"])
@login_required
def get_internal_stock_data(internal_stock_id):
    """Endpoint to fetch data for pre-filling based on an internal stock ID."""
    if not session.get("username"):
        return jsonify({"error": "User not logged in."}), 401

    username = session.get("username")  # Current user making the request

    try:
        stock = get_accessible_stock(username, internal_stock_id, db)

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
        stock_data, error = get_external_stock_record("BDSC", bdsc_stock_id)
        if error:
            return jsonify({"error": error}), 400

        return jsonify(stock_data), 200

    except Exception as e:
        current_app.logger.exception(
            "Error in get_bloomington_stock_data for %s: %s", bdsc_stock_id, e
        )
        return jsonify({"error": "An internal error occurred fetching BDSC data."}), 500


@bp.route("/get_external/<source_type>/<path:source_stock_id>", methods=["GET"])
@login_required
def get_external_stock_data(source_type, source_stock_id):
    """Endpoint to fetch stock metadata from external source catalogs."""
    if not session.get("username"):
        return jsonify({"error": "User not logged in."}), 401

    try:
        stock_data, error = get_external_stock_record(source_type, source_stock_id)
        if error:
            return jsonify({"error": error}), 400
        return jsonify(stock_data), 200
    except Exception as e:
        current_app.logger.exception(
            "Error in get_external_stock_data for %s/%s: %s",
            source_type,
            source_stock_id,
            e,
        )
        return jsonify({"error": "An internal error occurred fetching external stock data."}), 500


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



# Moved from cross blueprint as it queries stocks
@bp.route("/get_genotype_for_uid/<unique_id>")
@login_required
def get_genotype_for_uid(unique_id):
    """Route to fetch genotype based on a stock's Unique ID."""
    try:
        # Find stock by UniqueID - potentially across all users if needed by JS?
        # Original code checked username, let's keep that for now.
        username = session.get("username")
        stock = get_accessible_stock(username, unique_id, db)

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
        uid_list = [
            stock["UniqueID"]
            for stock in get_accessible_stocks(username, db)
            if stock.get("Genotype") == final_genotype and "UniqueID" in stock
        ]

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
        stock = get_accessible_stock(username, unique_id, db)

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

