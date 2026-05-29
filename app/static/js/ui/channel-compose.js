(() => {
  function clearComposeResults(root) {
    root.querySelectorAll("#channel-add-result, #bulk-resolve-result").forEach((node) => {
      node.innerHTML = "";
    });
  }

  function initChannelCompose(section) {
    if (section.dataset.channelComposeBound === "1") return;
    section.dataset.channelComposeBound = "1";

    const toggle = section.querySelector("[data-channel-compose-toggle]");
    const body = section.querySelector("[data-channel-compose-body]");
    const summary = section.querySelector("[data-channel-compose-summary]");
    const icon = section.querySelector("[data-channel-compose-icon]");
    if (!toggle || !body) return;

    const storageKey = section.dataset.channelComposeStorageKey || "channels.compose.expanded";

    const applyState = (expanded, options = {}) => {
      const persist = options.persist !== false;
      const clearOnCollapse = options.clearOnCollapse === true;
      toggle.setAttribute("aria-expanded", expanded ? "true" : "false");
      body.classList.toggle("hidden", !expanded);
      summary?.classList.toggle("hidden", expanded);
      icon?.classList.toggle("rotate-180", expanded);
      if (persist) {
        sessionStorage.setItem(storageKey, expanded ? "1" : "0");
      }
      if (!expanded && clearOnCollapse) {
        clearComposeResults(section);
      }
    };

    const stored = sessionStorage.getItem(storageKey);
    applyState(stored !== "0", { persist: false });

    toggle.addEventListener("click", () => {
      const isExpanded = toggle.getAttribute("aria-expanded") === "true";
      applyState(!isExpanded, { clearOnCollapse: isExpanded });
    });
  }

  function bindChannelCompose(scope) {
    const root = scope instanceof Element ? scope : document;
    root.querySelectorAll("[data-channel-compose]").forEach(initChannelCompose);
  }

  function initChannelComposeForm(form) {
    if (form.dataset.channelComposeFormBound === "1") return;
    form.dataset.channelComposeFormBound = "1";

    const submitButton = form.querySelector("button[type='submit'], button:not([type])");
    if (!submitButton) return;
    const defaultLabel = submitButton.textContent || "";
    const busyLabel = form.dataset.submitBusyLabel || defaultLabel;

    const setBusy = (isBusy) => {
      form.dataset.requestInFlight = isBusy ? "1" : "0";
      form.setAttribute("aria-busy", isBusy ? "true" : "false");
      submitButton.disabled = isBusy;
      submitButton.textContent = isBusy ? busyLabel : defaultLabel;
    };

    form.addEventListener("submit", (event) => {
      if (form.dataset.requestInFlight === "1") {
        event.preventDefault();
        return;
      }
      setBusy(true);
    });
    form.addEventListener("htmx:beforeRequest", (event) => {
      if (event.target === form) {
        setBusy(true);
      }
    });
    form.addEventListener("htmx:afterRequest", (event) => {
      if (event.target === form) {
        setBusy(false);
      }
    });
    form.addEventListener("htmx:responseError", (event) => {
      if (event.target === form) {
        setBusy(false);
      }
    });
  }

  function bindChannelComposeForms(scope) {
    const root = scope instanceof Element ? scope : document;
    root.querySelectorAll("[data-channel-compose-form]").forEach(initChannelComposeForm);
  }

  window.BrieftubeChannelCompose = {
    bindChannelCompose,
    bindChannelComposeForms,
  };
})();
