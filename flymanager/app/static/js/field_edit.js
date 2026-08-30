/* field_edit.js — per-field "unlock to edit" behaviour for detail pages.
 *
 * Both the stock and the cross viewer render their records as a fully
 * populated, read-only form with a small edit button next to every field
 * the viewer is allowed to change. Clicking that button unlocks just that
 * one field. This module is the shared implementation so the two pages
 * cannot drift apart again.
 *
 * Markup contract:
 *   <div class="input-group">
 *     <input id="foo" name="foo" class="form-control">
 *     <div class="input-group-append">
 *       <button type="button" data-edit-target="foo">…</button>
 *     </div>
 *   </div>
 *
 * Tagify-backed fields carry the `tag-input` class. They cannot simply be
 * re-enabled (Tagify has replaced them with its own DOM), so the page
 * supplies an `onTagEdit` callback that rebuilds that instance in
 * writable mode.
 */
(function (global) {
    'use strict';

    function escapeId(id) {
        return global.CSS && CSS.escape ? CSS.escape(id) : id;
    }

    function initFieldEdit(options) {
        var form = options.form;
        if (!form) {
            return null;
        }

        var canEdit = !!options.canEdit;
        var buttons = Array.prototype.slice.call(form.querySelectorAll('[data-edit-target]'));
        var fields = [];

        buttons.forEach(function (button) {
            var field = form.querySelector('#' + escapeId(button.dataset.editTarget));
            if (!field) {
                return;
            }
            fields.push(field);

            // Tagify owns the readonly state of its own fields; leaving the
            // underlying input `disabled` breaks instance creation.
            if (!field.classList.contains('tag-input')) {
                field.disabled = true;
            }

            if (!canEdit) {
                button.disabled = true;
                return;
            }

            button.addEventListener('click', function () {
                if (field.classList.contains('tag-input')) {
                    if (options.onTagEdit) {
                        options.onTagEdit(field);
                    }
                } else {
                    field.disabled = false;
                    field.focus();
                }
                button.disabled = true;
            });
        });

        form.addEventListener('submit', function (event) {
            if (!canEdit) {
                return;
            }
            if (options.confirmMessage && !global.confirm(options.confirmMessage)) {
                event.preventDefault();
                return;
            }
            // Disabled controls are not serialized, so every still-locked
            // field would post as missing. Re-enable them all first.
            Array.prototype.forEach.call(
                form.querySelectorAll('input, textarea, select'),
                function (control) {
                    control.disabled = false;
                }
            );
        });

        return { fields: fields, buttons: buttons };
    }

    global.initFieldEdit = initFieldEdit;
})(window);
