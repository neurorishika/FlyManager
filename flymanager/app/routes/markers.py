"""Marker definition catalog UI and API.

Writes here are the only runtime path that changes marker behaviour. Each one
lands in Mongo, bumps the catalog revision, refreshes this process's snapshot
so the author sees their own change immediately, and enqueues a scoped cache
rebuild for everyone else's stored predictions.
"""
import json

from flask import (Blueprint, abort, current_app, flash, jsonify, redirect,
                   render_template, request, send_file, session, url_for)

from flymanager.app import db
from flymanager.app.jobs import enqueue_job
from flymanager.app.jobs import tasks as job_tasks
from flymanager.app.routes.auth import login_required
from flymanager.app.security import get_json_payload, limiter
from flymanager.utils.mongo import OperationLockConflict
from flymanager.utils.mongo.activity import write_activity
from flymanager.utils.mongo.marker_definitions import (
    MarkerDefinitionError, can_edit_marker_definition, create_marker_definition,
    delete_marker_definition, get_marker_definition,
    get_marker_definition_overlay, get_shipped_definition_keys,
    list_marker_definitions, promote_marker_definition,
    update_marker_definition)
from flymanager.utils.phenotypes.marker_catalog import (MARKER_KINDS,
                                                         get_catalog,
                                                         refresh_catalog)
from flymanager.utils.phenotypes.marker_fields import (MARKER_FIELD_SPECS,
                                                       field_value,
                                                       form_to_document,
                                                       marker_vocabularies,
                                                       repeating_input_name,
                                                       repeating_rows)
from flymanager.utils.phenotypes.image_catalog import (
    bump_image_revision, refresh_image_catalog)
from flymanager.utils.phenotypes.image_library import select_marker_images
from flymanager.utils.phenotypes.image_normalize import (
    ImageRejected, MAX_UPLOAD_BYTES, normalize_image)
from flymanager.utils.phenotypes.image_seed import upsert_image_entry
from flymanager.utils.phenotypes.image_store import GridFSImageStore, ImageNotFound

bp = Blueprint("markers", __name__)

# The envelope sections a definition document carries besides `Key`/`kind`.
# The admin-only raw editor renders one JSON textarea per section, prefilled
# with its current value, so a save through it already carries every section;
# the structured editor everyone else uses covers only the fields its kind
# actually has -- see _parse_form_document / _merge_with_existing below for
# why neither is trusted as the only guard.
ENVELOPE_FIELDS = ("match", "payload", "sorting", "audit", "imaging",
                   "expression", "provenance")

# Envelope sections only an admin may set. Deliberately NOT the whole envelope:
# `match`, `payload`, `imaging` and `provenance` are the marker's own content,
# every one of them is reachable through the structured form's dotted fields,
# and the JSON API must keep accepting them or an ordinary user cannot create a
# marker at all. What is gated is the three sections the form deliberately does
# not expose, because they confer behaviour rather than describe the marker:
# `sorting` feeds stability into the crossing constraint solver, `audit` marks
# FlyBase probe symbols, and `expression` is reserved for a later slice.
RAW_ONLY_FIELDS = ("sorting", "audit", "expression")

# The stored vocabulary is internal; these are what the pages say instead.
KIND_LABELS = {
    "gene_marker": "Gene",
    "allele_marker": "Allele",
    "alias": "Nickname",
    "balancer": "Balancer",
    "construct_marker": "Construct",
}
ORIGIN_LABELS = {
    "shipped": "Built in",
    "user": "Added by the lab",
    "curated": "Reviewed",
}


def _serializable(document):
    """Drop the ObjectId so the document survives RQ's job serialisation."""
    stripped = dict(document or {})
    stripped.pop("_id", None)
    return stripped


def _is_admin():
    return session.get("username") == "admin"


def _reject_unprivileged_envelope(document):
    """Raise unless the caller may set raw envelope sections directly.

    The raw per-section JSON editor is rendered for an admin only, but that is
    a UI affordance rather than authority: both the form parser and the JSON
    API took whole sections from anyone who could edit the row. Only the
    sections in RAW_ONLY_FIELDS are gated -- see the note there for why the
    marker's own content is not.
    """
    if _is_admin():
        return
    raw = sorted(field for field in RAW_ONLY_FIELDS if field in (document or {}))
    if raw:
        raise PermissionError(
            "Only an admin can set " + ", ".join(raw) + " directly. "
            "Use the marker form fields instead.")


def _parse_form_document(form, existing=None):
    """Reassemble a full definition document from a plain HTML form POST.

    Two form shapes reach here. The ordinary one is the structured editor
    everyone sees: one input per real field, named for its dotted location
    (`payload.effect`), rebuilt into envelope sections by `form_to_document`.
    The other is the admin-only raw editor, which still renders one JSON
    textarea per envelope section, prefilled with that section's current
    value, precisely so a save carries the whole document rather than a
    partial diff -- see _merge_with_existing for why a partial diff is
    dangerous here. Whichever shape arrives, sections it does not mention are
    filled from the stored document by `_merge_with_existing`.
    """
    document = {}
    if "Key" in form:
        document["Key"] = (form.get("Key") or "").strip()
    kind = (form.get("kind") or "").strip() or (existing or {}).get("kind")
    if "kind" in form:
        document["kind"] = (form.get("kind") or "").strip()

    if any(field in form for field in ENVELOPE_FIELDS):
        for field in ENVELOPE_FIELDS:
            raw = (form.get(field) or "").strip()
            if not raw:
                continue
            try:
                document[field] = json.loads(raw)
            except ValueError as exc:
                raise ValueError(f"{field} must be valid JSON ({exc})") from exc
        return document

    document.update(form_to_document(kind, form, existing=existing))
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
    duplicate_labels = get_catalog()["duplicate_stability_labels"]
    return render_template("markers/list.html", page_title="Marker Catalog",
                           definitions=rows, kinds=MARKER_KINDS,
                           invalid_definitions=invalid_definitions,
                           duplicate_labels=duplicate_labels,
                           selected_kind=request.args.get("kind", ""),
                           selected_origin=request.args.get("origin", ""),
                           search_query=request.args.get("q", ""),
                           kind_labels=KIND_LABELS, origin_labels=ORIGIN_LABELS)


@bp.get("/markers/new")
@login_required
def new_marker():
    """The create form, on its own page.

    Creating used to be a JSON-textarea card wedged under the catalog table.
    The structured editor renders a different set of fields per kind, which
    needs the room and the kind chosen up front.
    """
    kind = (request.args.get("kind") or "").strip()
    if kind not in MARKER_KINDS:
        kind = MARKER_KINDS[0]
    return render_template("markers/new.html", page_title="Add a Marker",
                           kinds=MARKER_KINDS, selected_kind=kind,
                           field_specs=MARKER_FIELD_SPECS,
                           kind_labels=KIND_LABELS, definition={},
                           field_value=field_value, repeating_rows=repeating_rows,
                           repeating_input_name=repeating_input_name,
                           vocabularies=marker_vocabularies())


@bp.get("/markers/key-check")
@login_required
def marker_key_check():
    """Is this Key free, taken, or a shipped key someone is about to override?

    Three states, not two. Reusing a SHIPPED key is the supported way to keep
    a lab's own version of a built-in marker -- create_marker is that override
    path -- so calling it a duplicate would talk someone out of doing the
    right thing. Only an overlay row is an actual collision, and that is the
    one create_marker_definition 409s on.

    Registered above `/markers/<path:key>` so the literal wins the route
    match; a `path` converter would otherwise swallow "key-check".
    """
    key = (request.args.get("key") or "").strip()
    if not key:
        return jsonify({"status": "empty", "message": ""})

    # The overlay is consulted directly rather than through the compiled
    # snapshot: that snapshot can be a refresh interval stale, so a marker
    # another worker created seconds ago would read as free and the person
    # would fill in the whole form before the 409.
    if get_marker_definition_overlay(db, key) is not None:
        return jsonify({
            "status": "taken",
            "message": f"{key} is already a marker in this lab's catalog.",
            "url": url_for("markers.marker_detail", key=key),
        })

    if key in get_shipped_definition_keys():
        return jsonify({
            "status": "shipped",
            "message": (f"{key} is one of FlyManager's built-in markers. "
                        "Saving creates a lab override that is used instead of "
                        "the built-in one everywhere."),
            "url": url_for("markers.marker_detail", key=key),
        })

    return jsonify({"status": "free", "message": f"{key} is free."})


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
        image_groups=select_marker_images(key),
        can_edit=can_edit_marker_definition(definition, username),
        can_promote=(username == "admin" and definition.get("origin") == "user"),
        is_shipped=(definition.get("origin") == "shipped"),
        is_admin=(username == "admin"),
        field_specs=MARKER_FIELD_SPECS, field_value=field_value,
        repeating_rows=repeating_rows, repeating_input_name=repeating_input_name,
        vocabularies=marker_vocabularies(),
        kind_labels=KIND_LABELS, origin_labels=ORIGIN_LABELS,
    )


def _attach_image(key, uploaded, caption=""):
    """Store an uploaded image against a marker. Returns (message, category).

    Split out of the upload route so the create page can attach a photo in
    the same request that makes the marker, without the two paths drifting.
    Returns rather than flashes because its two callers report differently:
    a failure here is the whole story for an upload, and a footnote for a
    create that otherwise succeeded.
    """
    if uploaded is None or not (uploaded.filename or "").strip():
        return "No image was submitted.", "danger"
    raw = uploaded.read(MAX_UPLOAD_BYTES + 1)
    if len(raw) > MAX_UPLOAD_BYTES:
        return "Images must be 2 MB or smaller.", "danger"
    try:
        normalized = normalize_image(raw)
    except ImageRejected as exc:
        return str(exc), "danger"

    entry, created, bound = upsert_image_entry(
        db, GridFSImageStore(db), normalized, marker_key=key,
        uploaded_by=session.get("username", ""),
        display={"caption": (caption or "").strip()},
    )
    refresh_image_catalog(db, force=True)
    if created:
        action = f"Uploaded image {entry['imageId']} for marker {key}"
        message, category = "Image uploaded.", "success"
    elif bound:
        # Identical bytes already existed; this binds them to another marker
        # rather than storing a second copy.
        action = f"Linked existing image {entry['imageId']} to marker {key}"
        message = "That image was already stored, so it was linked to this marker."
        category = "success"
    else:
        action = f"Re-uploaded image {entry['imageId']} already on marker {key}"
        message, category = "That image is already on this marker.", "info"
    write_activity(session.get("username", ""), action, db)
    return message, category


@bp.post("/markers/<path:key>/images")
@login_required
@limiter.limit("20 per minute")
def upload_marker_image(key):
    # The in-process catalog can be up to one probe interval behind, so a
    # marker created seconds ago by another worker would 404 here. Fall back
    # to the overlay collection before refusing.
    if key not in get_catalog()["definitions"] and get_marker_definition(db, key) is None:
        abort(404)
    message, category = _attach_image(key, request.files.get("image"),
                                      request.form.get("caption", ""))
    if message:
        flash(message, category)
    return redirect(url_for("markers.marker_detail", key=key))


@bp.get("/markers/images/<image_id>")
@login_required
@limiter.limit("60 per minute")
def serve_marker_image(image_id):
    entry = db["marker_images"].find_one({"imageId": image_id})
    if entry is None:
        abort(404)
    try:
        stream = GridFSImageStore(db).open(entry["storageId"])
    except (ImageNotFound, KeyError):
        current_app.logger.warning("Marker image %s has missing bytes", image_id)
        abort(404)
    response = send_file(stream, mimetype=entry.get("contentType", "image/webp"),
                         conditional=True)
    response.set_etag(entry["sha256"])
    response.headers["Cache-Control"] = "private, max-age=31536000, immutable"
    return response.make_conditional(request)


@bp.post("/markers/images/<image_id>/delete")
@login_required
def delete_marker_image(image_id):
    entry = db["marker_images"].find_one({"imageId": image_id})
    if entry is None:
        abort(404)
    username = session.get("username", "")
    is_admin = username == "admin"
    if entry.get("origin") == "shipped" and not is_admin:
        abort(403)
    if not is_admin and entry.get("UploadedBy") != username:
        abort(403)
    marker_keys = list((entry.get("match") or {}).get("markerKeys") or [])
    unbind_key = request.form.get("marker_key", "").strip()
    if unbind_key and unbind_key not in marker_keys:
        # A stale form (the image was already unbound elsewhere) must not
        # fall through to destroying the image for every other marker.
        abort(409)

    if unbind_key and len(marker_keys) > 1:
        # $pull rather than writing back a list computed before a concurrent
        # bind, which would resurrect or drop keys under 12 threads.
        db["marker_images"].update_one(
            {"imageId": image_id}, {"$pull": {"match.markerKeys": unbind_key}})
        action = f"Unlinked image {image_id} from marker {unbind_key}"
        message = "Image removed from this marker."
    else:
        # Delete the metadata first: an orphaned blob is inert, whereas a
        # document pointing at bytes that are already gone renders broken.
        db["marker_images"].delete_one({"imageId": image_id})
        try:
            GridFSImageStore(db).delete(entry["storageId"])
        except Exception as exc:
            current_app.logger.warning(
                "Orphaned GridFS bytes %s for deleted image %s: %s",
                entry.get("storageId"), image_id, exc)
        action = f"Deleted marker image {image_id}"
        message = "Image deleted."

    bump_image_revision(db)
    refresh_image_catalog(db, force=True)
    write_activity(username, action, db)
    flash(message, "success")
    return redirect(request.referrer or url_for("markers.marker_catalog"))


@bp.post("/markers")
@login_required
@limiter.limit("60 per hour")
def create_marker():
    try:
        # Captured before the write: when this Key is a shipped key,
        # create_marker_definition is the OVERRIDE path (_require_editable
        # 409s any other attempt to edit a shipped row in place), and the
        # pre-edit state is the shipped row, which exists nowhere the
        # rebuild scoping can see once the overlay row shadows it. Merging
        # it in the same way update_marker does also stops a partial JSON
        # override from silently blanking the shipped row's
        # sorting/audit/imaging sections.
        # The structured form needs `previous` to parse against (it carries
        # over the keys it does not render), so the key is read from the raw
        # request before the document is assembled.
        if request.is_json:
            payload = get_json_payload()
            _reject_unprivileged_envelope(payload)
            previous = get_marker_definition(db, (payload or {}).get("Key"))
        else:
            _reject_unprivileged_envelope(request.form)
            previous = get_marker_definition(db, (request.form.get("Key") or "").strip())
            payload = _parse_form_document(request.form, existing=previous)
        payload = _merge_with_existing(payload, previous)
        created = create_marker_definition(db, payload, username=session.get("username"))
    except PermissionError as exc:
        # 403, not 400: this is an authority failure, and a JSON caller should
        # be able to tell "you may not" from "your payload was malformed".
        if request.is_json:
            return jsonify({"status": "error", "message": str(exc)}), 403
        flash(str(exc), "danger")
        return redirect(url_for("markers.marker_catalog"))
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
    # The photo is attached after the marker exists, not with it: the upload
    # endpoint 404s without one, so this cannot be a single request. A photo
    # the store rejects is a footnote on a create that already succeeded --
    # the marker stays, and the person is told the picture did not.
    uploaded = request.files.get("image")
    if uploaded is not None and (uploaded.filename or "").strip():
        message, category = _attach_image(created["Key"], uploaded,
                                          request.form.get("caption", ""))
        if category == "danger":
            flash(f"The marker was created, but its photo was not attached: {message}",
                  "warning")
        elif message:
            flash(message, category)
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
        if request.is_json:
            payload = get_json_payload()
            _reject_unprivileged_envelope(payload)
        else:
            _reject_unprivileged_envelope(request.form)
            payload = _parse_form_document(request.form, existing=previous)
        payload = _merge_with_existing(payload, previous)
        update_marker_definition(db, key, payload, username=session.get("username"))
    except PermissionError as exc:
        # 403, not 400: this is an authority failure, and a JSON caller should
        # be able to tell "you may not" from "your payload was malformed".
        if request.is_json:
            return jsonify({"status": "error", "message": str(exc)}), 403
        flash(str(exc), "danger")
        return redirect(url_for("markers.marker_detail", key=key))
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
