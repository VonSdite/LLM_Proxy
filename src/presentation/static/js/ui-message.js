(function () {
    const TOAST_CONTAINER_ID = "ui-toast-container";

    function ensureToastContainer() {
        let container = document.getElementById(TOAST_CONTAINER_ID);
        if (container) return container;

        container = document.createElement("div");
        container.id = TOAST_CONTAINER_ID;
        container.className = "toast-container position-fixed top-0 end-0 p-3";
        container.style.zIndex = "1080";
        document.body.appendChild(container);
        return container;
    }

    function createToastElement(message, toastTypeClass) {
        const toast = document.createElement("div");
        toast.className = `toast align-items-center border-0 ${toastTypeClass}`;
        toast.setAttribute("role", "alert");
        toast.setAttribute("aria-live", "assertive");
        toast.setAttribute("aria-atomic", "true");

        const wrapper = document.createElement("div");
        wrapper.className = "d-flex";

        const body = document.createElement("div");
        body.className = "toast-body";
        body.textContent = String(message || "");

        const closeBtn = document.createElement("button");
        closeBtn.type = "button";
        closeBtn.className = "btn-close btn-close-white me-2 m-auto";
        closeBtn.setAttribute("data-bs-dismiss", "toast");
        closeBtn.setAttribute("aria-label", "Close");

        wrapper.appendChild(body);
        wrapper.appendChild(closeBtn);
        toast.appendChild(wrapper);
        return toast;
    }

    function showMessage(message, type, options) {
        const opts = options || {};
        const level = type || "error";
        const dismissMs = Number.isFinite(opts.dismissMs) ? opts.dismissMs : 3000;
        const container = ensureToastContainer();

        const typeMap = {
            success: "text-bg-success",
            error: "text-bg-danger",
            warning: "text-bg-warning",
            info: "text-bg-info",
        };
        const toastTypeClass = typeMap[level] || typeMap.error;

        const toastEl = createToastElement(message, toastTypeClass);
        container.appendChild(toastEl);

        const delay = dismissMs > 0 ? dismissMs : 600000;
        if (window.bootstrap && window.bootstrap.Toast) {
            const toast = window.bootstrap.Toast.getOrCreateInstance(toastEl, {
                autohide: dismissMs > 0,
                delay,
            });
            toast.show();
            if (!toastEl.hasAttribute("data-bs-dismiss-bound")) {
                toastEl.setAttribute("data-bs-dismiss-bound", "true");
                toastEl.addEventListener("hidden.bs.toast", function () {
                    toastEl.remove();
                });
            }
            return;
        }

        if (dismissMs > 0) {
            window.setTimeout(function () {
                toastEl.remove();
            }, dismissMs);
        }
    }

    window.showMessage = showMessage;
})();
