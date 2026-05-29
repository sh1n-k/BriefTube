(() => {
  function toggleDefaultVideoListVisibility(isHidden) {
    const videoList = document.getElementById("video-list-wrap");
    if (!(videoList instanceof HTMLElement)) return;
    videoList.classList.toggle("hidden", isHidden);
  }

  function syncGlobalSearchUrl(query) {
    if (!(window.history && typeof window.history.replaceState === "function")) return;
    const url = new URL(window.location.href);
    const normalized = String(query || "").trim();
    if (normalized) {
      url.searchParams.set("q", normalized);
    } else {
      url.searchParams.delete("q");
    }
    window.history.replaceState({}, "", url.toString());
  }

  function initGlobalSearchForm(form) {
    if (form.dataset.globalSearchBound === "1") return;
    form.dataset.globalSearchBound = "1";

    const input = form.querySelector("[data-global-search-input]");
    if (!(input instanceof HTMLInputElement)) return;

    const syncState = () => {
      const hasQuery = input.value.trim().length > 0;
      form.dataset.searchActive = hasQuery ? "1" : "0";
      toggleDefaultVideoListVisibility(hasQuery);
      syncGlobalSearchUrl(input.value);
    };

    input.addEventListener("input", syncState);
    input.addEventListener("search", syncState);
    syncState();
  }

  function bindGlobalSearchForms(scope) {
    const root = scope instanceof Element ? scope : document;
    root.querySelectorAll("[data-global-search-form]").forEach(initGlobalSearchForm);
  }

  function initSearchClearButton(button) {
    if (button.dataset.searchClearBound === "1") return;
    button.dataset.searchClearBound = "1";

    button.addEventListener("click", () => {
      const form = document.querySelector("[data-global-search-form]");
      if (!(form instanceof HTMLFormElement)) return;
      const input = form.querySelector("[data-global-search-input]");
      if (!(input instanceof HTMLInputElement)) return;
      const results = document.querySelector("[data-search-results]");
      input.value = "";
      toggleDefaultVideoListVisibility(false);
      if (results instanceof HTMLElement) {
        results.innerHTML = "";
      }
      input.dispatchEvent(new Event("input", { bubbles: true }));
      input.focus();
    });
  }

  function bindSearchClearButtons(scope) {
    const root = scope instanceof Element ? scope : document;
    root.querySelectorAll("[data-search-clear]").forEach(initSearchClearButton);
  }

  window.BrieftubeGlobalSearch = {
    bindGlobalSearchForms,
    bindSearchClearButtons,
  };
})();
