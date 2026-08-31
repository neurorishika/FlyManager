// Shows only the fields that belong to the marker kind currently selected.
// Hidden fieldsets are also disabled so the browser leaves them out of the
// POST entirely -- the server rebuilds a definition from exactly the fields
// it receives, so a stray hidden input from another kind would land in the
// stored document.
(function () {
    function apply(root, kind) {
        root.querySelectorAll('.marker-kind-fields').forEach(function (block) {
            var active = block.dataset.markerKind === kind;
            block.hidden = !active;
            block.querySelectorAll('fieldset').forEach(function (set) {
                set.disabled = !active;
            });
        });
    }

    document.querySelectorAll('[data-marker-kind-select]').forEach(function (select) {
        var root = select.closest('form') || document;
        apply(root, select.value);
        select.addEventListener('change', function () { apply(root, select.value); });
    });
})();
