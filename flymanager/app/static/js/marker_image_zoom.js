/* Zoom overlay for phenotype reference images.
 *
 * Cards rendered by _phenotype_images_macro.html expose their metadata as
 * data-zoom-* attributes; this reads them on activation and fills the
 * overlay from _phenotype_image_zoom.html. Delegated from document so it
 * works for cards injected after load (the standalone preview replaces its
 * grid via fetch).
 */
(function () {
    "use strict";

    function overlay() {
        return document.getElementById("phenotypeZoomOverlay");
    }

    var lastFocused = null;

    function setText(id, value) {
        var node = document.getElementById(id);
        if (node) {
            node.textContent = value || "";
            node.hidden = !value;
        }
    }

    function open(trigger) {
        var root = overlay();
        if (!root) {
            return;
        }
        var image = document.getElementById("phenotypeZoomImage");
        var label = trigger.getAttribute("data-zoom-label") || "";
        var bodyPart = trigger.getAttribute("data-zoom-body-part") || "";
        var source = trigger.getAttribute("data-zoom-source") || "";

        image.src = trigger.getAttribute("data-zoom-src") || "";
        image.alt = label ? "Enlarged reference image for " + label : "";

        setText("phenotypeZoomTitle", label);
        setText("phenotypeZoomEffect", trigger.getAttribute("data-zoom-effect"));
        setText("phenotypeZoomMeta", [bodyPart, source].filter(Boolean).join(" · "));
        setText("phenotypeZoomCredit", [
            trigger.getAttribute("data-zoom-provenance"),
            trigger.getAttribute("data-zoom-credit")
        ].filter(Boolean).join(" · "));

        lastFocused = document.activeElement;
        root.hidden = false;
        document.body.classList.add("phenotype-zoom-open");
        var closeButton = root.querySelector(".phenotype-zoom-close");
        if (closeButton) {
            closeButton.focus();
        }
    }

    function close() {
        var root = overlay();
        if (!root || root.hidden) {
            return;
        }
        root.hidden = true;
        document.body.classList.remove("phenotype-zoom-open");
        // Drop the src so a large image is not held in memory while hidden.
        var image = document.getElementById("phenotypeZoomImage");
        if (image) {
            image.src = "";
        }
        if (lastFocused && typeof lastFocused.focus === "function") {
            lastFocused.focus();
        }
        lastFocused = null;
    }

    document.addEventListener("click", function (event) {
        var trigger = event.target.closest(".js-phenotype-image-zoom");
        if (trigger) {
            event.preventDefault();
            open(trigger);
            return;
        }
        if (event.target.closest("[data-phenotype-zoom-close]")) {
            event.preventDefault();
            close();
        }
    });

    document.addEventListener("keydown", function (event) {
        if (event.key === "Escape") {
            close();
        }
    });
}());
