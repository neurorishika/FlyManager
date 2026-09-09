// Turns the marker form's short list fields into tag inputs.
//
// Every value in one of these fields is a key that has to match something
// exactly -- a marker a balancer carries, a balancer a stability rule applies
// to, a spelling an image filename should be found by. Typed as
// comma-separated text, "Sb, Ser" is one stray keystroke from "Sb Ser", a
// single value that resolves to nothing and reports as a missing marker much
// later. Tags make each value a discrete object the person can see and delete.
//
// Tagify is told to write plain comma-separated text back into the original
// input (originalInputValueFormat), so the server parses exactly the string
// the bare textarea posted and nothing on the parsing side had to change.
// With JavaScript off, every one of these is still that textarea.
(function () {
    if (typeof Tagify === 'undefined') {
        return;
    }

    function whitelistFor(input) {
        var raw = input.dataset.whitelist;
        if (!raw) {
            return [];
        }
        try {
            return JSON.parse(raw);
        } catch (error) {
            // A malformed list must not cost the person their tag input; they
            // simply get one with no suggestions.
            return [];
        }
    }

    function initialize(input) {
        if (input.dataset.tagifyReady) {
            return;
        }
        input.dataset.tagifyReady = '1';
        new Tagify(input, {
            whitelist: whitelistFor(input),
            // Suggest, never enforce. A balancer may legitimately carry a
            // marker nobody has defined yet, and an alias is a novel spelling
            // by definition. The catalog page already reports a carried marker
            // that resolves to nothing.
            enforceWhitelist: false,
            dropdown: { enabled: 1, maxItems: 12, closeOnSelect: false },
            originalInputValueFormat: function (values) {
                return values.map(function (item) { return item.value; }).join(',');
            },
        });
    }

    document.querySelectorAll('[data-marker-tags]').forEach(initialize);
})();

// Checks the Key on the create page while it is typed.
//
// A collision was previously a 409 flash after the whole form had been filled
// in. And two of the three answers are not collisions: reusing a shipped key
// is the supported way to keep a lab's own version of a built-in marker, so
// that case is explained rather than warned about.
(function () {
    var status = document.getElementById('newMarkerKeyStatus');
    var input = document.getElementById('newMarkerKey');
    if (!status || !input) {
        return;
    }

    var TONE = {
        free: 'text-success',
        taken: 'text-danger',
        shipped: 'text-info',
    };
    var timer = null;
    var sequence = 0;

    function render(result) {
        status.className = 'form-text mt-1 ' + (TONE[result.status] || '');
        if (result.status === 'empty' || !result.message) {
            status.hidden = true;
            status.textContent = '';
            return;
        }
        status.hidden = false;
        status.textContent = '';
        status.appendChild(document.createTextNode(result.message + ' '));
        if (result.url && result.status === 'taken') {
            var link = document.createElement('a');
            link.href = result.url;
            link.textContent = 'Open it';
            status.appendChild(link);
        }
    }

    function check() {
        var key = input.value.trim();
        if (!key) {
            render({ status: 'empty' });
            return;
        }
        var ticket = ++sequence;
        var url = status.dataset.keyCheckUrl + '?key=' + encodeURIComponent(key);
        fetch(url, { headers: { Accept: 'application/json' } })
            .then(function (response) {
                return response.ok ? response.json() : null;
            })
            .then(function (result) {
                // A slow earlier request must not overwrite a later answer.
                if (result && ticket === sequence) {
                    render(result);
                }
            })
            .catch(function () { /* the check is an aid; submitting still validates */ });
    }

    input.addEventListener('input', function () {
        window.clearTimeout(timer);
        timer = window.setTimeout(check, 300);
    });
})();
