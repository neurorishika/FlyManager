"""Marker definition catalog UI and API.

Writes here are the only runtime path that changes marker behaviour. Each one
lands in Mongo, bumps the catalog revision, refreshes this process's snapshot
so the author sees their own change immediately, and enqueues a scoped cache
rebuild for everyone else's stored predictions.
"""
import json

from flask import (Blueprint, abort, flash, jsonify, redirect, render_template,
                   request, session, url_for)

from flymanager.app import db
from flymanager.app.jobs import enqueue_job
from flymanager.app.jobs import tasks as job_tasks
from flymanager.app.routes.auth import login_required
from flymanager.app.security import get_json_payload, limiter
from flymanager.utils.mongo import OperationLockConflict
from flymanager.utils.mongo.marker_definitions import (
    MarkerDefinitionError, can_edit_marker_definition, create_marker_definition,
    delete_marker_definition, get_marker_definition, list_marker_definitions,
    promote_marker_definition, update_marker_definition)
from flymanager.utils.phenotypes.marker_catalog import (MARKER_KINDS,
                                                         get_catalog,
                                                         refresh_catalog)

bp = Blueprint("markers", __name__)

# The envelope sections a definition document carries besides `Key`/`kind`.
# The edit and override forms render one JSON textarea per section, prefilled
# with its current value, so a normal save already carries every section --
# see _parse_form_document / _merge_with_existing below for why that alone
# is not trusted as the only guard.
ENVELOPE_FIELDS = ("match", "payload", "sorting", "audit", "imaging",
                   "expression", "provenance")


def _serializable(document):
    """Drop the ObjectId so the document survives RQ's job serialisation."""
    stripped = dict(document or {})
    stripped.pop("_id", None)
    return stripped


def _parse_form_document(form):
    """Reassemble a full definition document from a plain HTML form POST.

    The edit/override forms render one JSON textarea per envelope section
    (match/payload/sorting/audit/imaging/expression/provenance), prefilled
    with that section's current value, precisely so a save carries the whole
    document rather than a partial diff -- see the module docstring on
    _merge_with_existing for why a partial diff is dangerous here. A section
    left blank is simply omitted; `_merge_with_existing` (for updates) or the
    store's own defaulting (for creates) fills it in.
    """
    document = {}
    if "Key" in form:
        document["Key"] = (form.get("Key") or "").strip()
    if "kind" in form:
        document["kind"] = (form.get("kind") or "").strip()
    for field in ENVELOPE_FIELDS:
        raw = (form.get(field) or "").strip()
        if not raw:
            continue
        try:
            document[field] = json.loads(raw)
        except ValueError as exc:
            raise ValueError(f"{field} must be valid JSON ({exc})") from exc
    return document


def _merge_with_existing(payload, existing):
    """Fill in any envelope section the caller omitted with its current value.

    `_normalized()` in the store setdefaults `sorting`/`audit`/`imaging`/
    `expression`/`provenance` to empty structures for any key not present in
    the posted document, so an update payload that omits one of them silently
    RESETS it -- e.g. saving a marker after only touching `payload` wipes its
    stability score and image aliases. The templates already submit every
    section (see _parse_form_document), but this is the one boundary every
    write -- form POST or JSON API call -- passes through, so filling any gap
    here guarantees the store always receives the whole document regardless
    of what the caller actually sent.
    """
    merged = dict(payload or {})
    if existing:
        for field in ENVELOPE_FIELDS:
            merged.setdefault(field, existing.get(field))
    return merged


def _after_write(keys, *, previous_documents=(), deleted_override_keys=()):
    """Refresh locally, then hand the cache rebuild to the worker.

    A queue conflict means a rebuild is already in flight; the definition
    write itself is already committed and must not be rolled back, so this is
    a warning, not an error.
    """
    refresh_catalog(db, force=True)
    username = session.get("username")
    job_key = f"markers:rebuild:{'-'.join(keys) or 'catalog'}"
    try:
        # Every task in jobs.tasks takes `key` as its first argument, so it is
        # passed through kwargs the same way settings.py does it.
        enqueue_job(
            db,
            key=job_key,
            actor=username,
            label="Marker catalog cache rebuild",
            func=job_tasks.task_rebuild_marker_caches,
            kwargs={"key": job_key, "username": username, "keys": list(keys),
                    "previous_documents": list(previous_documents),
                    "deleted_override_keys": list(deleted_override_keys)},
        )
    except OperationLockConflict as exc:
        flash(str(exc), "warning")


def _error_response(exc):
    if request.is_json:
        return jsonify({"status": "error", "message": str(exc)}), exc.status_code
    # A plain 302, not (redirect(...), status): pairing redirect() with a
    # non-3xx status makes Werkzeug send its raw "Redirecting..." stub body
    # instead of actually redirecting, so a non-JS caller sees a blank stub
    # rather than the flashed message on the page it lands on.
    flash(str(exc), "danger")
    return redirect(url_for("markers.marker_catalog"))


@bp.get("/markers")
@login_required
def marker_catalog():
    rows = list_marker_definitions(
        db,
        kind=(request.args.get("kind") or "").strip() or None,
        origin=(request.args.get("origin") or "").strip() or None,
        search=(request.args.get("q") or "").strip() or None,
        username=session.get("username"),
    )
    # list_marker_definitions merges every overlay document, valid or not --
    # unlike compile_catalog it does not validate. A row with no Key (e.g. a
    # malformed overlay row) is exactly what invalid_definitions below
    # already surfaces as a warning; showing it a second time as a broken,
    # unclickable table row would just confuse without adding information.
    rows = [row for row in rows if str(row.get("Key") or "").strip()]
    invalid_definitions = get_catalog()["invalid_definitions"]
    return render_template("markers/list.html", page_title="Marker Catalog",
                           definitions=rows, kinds=MARKER_KINDS,
                           invalid_definitions=invalid_definitions,
                           selected_kind=request.args.get("kind", ""),
                           selected_origin=request.args.get("origin", ""),
                           search_query=request.args.get("q", ""))


@bp.get("/markers/<path:key>")
@login_required
def marker_detail(key):
    definition = get_marker_definition(db, key)
    if definition is None:
        abort(404)
    definition = dict(definition)
    definition.pop("_id", None)
    username = session.get("username")
    return render_template(
        "markers/detail.html", page_title=f"Marker: {key}",
        definition=definition, kinds=MARKER_KINDS,
        can_edit=can_edit_marker_definition(definition, username),
        can_promote=(username == "admin" and definition.get("origin") == "user"),
        is_shipped=(definition.get("origin") == "shipped"),
    )


@bp.post("/markers")
@login_required
@limiter.limit("60 per hour")
def create_marker():
    try:
        payload = get_json_payload() if request.is_json else _parse_form_document(request.form)
        # Captured before the write: when this Key is a shipped key,
        # create_marker_definition is the OVERRIDE path (_require_editable
        # 409s any other attempt to edit a shipped row in place), and the
        # pre-edit state is the shipped row, which exists nowhere the
        # rebuild scoping can see once the overlay row shadows it. Merging
        # it in the same way update_marker does also stops a partial JSON
        # override from silently blanking the shipped row's
        # sorting/audit/imaging sections.
        previous = get_marker_definition(db, (payload or {}).get("Key"))
        payload = _merge_with_existing(payload, previous)
        created = create_marker_definition(db, payload, username=session.get("username"))
    except ValueError as exc:
        if request.is_json:
            return jsonify({"status": "error", "message": str(exc)}), 400
        flash(str(exc), "danger")
        return redirect(url_for("markers.marker_catalog"))
    except MarkerDefinitionError as exc:
        return _error_response(exc)

    _after_write([created["Key"]],
                 previous_documents=[_serializable(previous)] if previous else ())
    if request.is_json:
        return jsonify({"status": "success", "key": created["Key"]}), 201
    # Form path returns a plain 302; a 201 with a Location header is not
    # followed by browsers.
    flash(f"Created marker definition {created['Key']}", "success")
    return redirect(url_for("markers.marker_detail", key=created["Key"]))


@bp.post("/markers/<path:key>")
@login_required
@limiter.limit("60 per hour")
def update_marker(key):
    # Captured before the write: an edit that removes an alias or renames a
    # symbol leaves no trace of the old value in the new snapshot, and the
    # rebuild scope has to sweep genotypes that used the old spelling.
    previous = get_marker_definition(db, key)
    try:
        payload = get_json_payload() if request.is_json else _parse_form_document(request.form)
        payload = _merge_with_existing(payload, previous)
        update_marker_definition(db, key, payload, username=session.get("username"))
    except ValueError as exc:
        if request.is_json:
            return jsonify({"status": "error", "message": str(exc)}), 400
        flash(str(exc), "danger")
        return redirect(url_for("markers.marker_detail", key=key))
    except MarkerDefinitionError as exc:
        return _error_response(exc)

    _after_write([key], previous_documents=[_serializable(previous)] if previous else ())
    if request.is_json:
        return jsonify({"status": "success", "key": key}), 200
    flash(f"Updated marker definition {key}", "success")
    return redirect(url_for("markers.marker_detail", key=key))


@bp.post("/markers/<path:key>/delete")
@login_required
@limiter.limit("60 per hour")
def delete_marker(key):
    previous = get_marker_definition(db, key)
    try:
        result = delete_marker_definition(db, key, username=session.get("username"))
    except MarkerDefinitionError as exc:
        return _error_response(exc)

    _after_write([key],
                 previous_documents=[_serializable(previous)] if previous else (),
                 deleted_override_keys=[key] if result["restored_shipped"] else ())
    flash(f"Deleted marker definition {key}", "success")
    # Always JSON: the detail page submits this via fetch and redirects
    # client-side on success (see markers/detail.html), and a bare form POST
    # (as from an API client with no JS) still gets a clean 200 + body rather
    # than a redirect that only makes sense to a browser.
    return jsonify({"status": "success", **result}), 200


@bp.post("/markers/<path:key>/promote")
@login_required
@limiter.limit("60 per hour")
def promote_marker(key):
    try:
        promote_marker_definition(db, key, username=session.get("username"))
    except MarkerDefinitionError as exc:
        return _error_response(exc)

    _after_write([key])
    flash(f"Promoted marker definition {key} to curated", "success")
    # Always JSON; see the comment on delete_marker above.
    return jsonify({"status": "success", "key": key}), 200
