(() => {
  const DEFAULT_PAGE_FADE_KEY = "brieftube.enableNextPageFade";
  const DEFAULT_NAV_FADE_DURATION_MS = 120;

  const state = {
    pageFadeKey: DEFAULT_PAGE_FADE_KEY,
    navFadeDurationMs: DEFAULT_NAV_FADE_DURATION_MS,
  };

  function configure(options) {
    if (!options || typeof options !== "object") return;
    const nextKey = String(options.pageFadeKey || "").trim();
    if (nextKey) state.pageFadeKey = nextKey;
    const nextDuration = Number(options.navFadeDurationMs);
    if (Number.isFinite(nextDuration) && nextDuration > 0) {
      state.navFadeDurationMs = Math.floor(nextDuration);
    }
  }

  function enableNextPageFade() {
    try {
      sessionStorage.setItem(state.pageFadeKey, "1");
    } catch (_err) {
      // ignore storage write errors.
    }
  }

  function revealPageShell() {
    const shell = document.querySelector("[data-page-shell]");
    if (!(shell instanceof HTMLElement)) return;
    shell.classList.remove("is-leaving");
    if (window.matchMedia && window.matchMedia("(prefers-reduced-motion: reduce)").matches) {
      shell.classList.add("is-visible");
      return;
    }
    window.requestAnimationFrame(() => {
      shell.classList.add("is-visible");
    });
  }

  function isPrimaryNavigationEvent(event) {
    return (
      event.button === 0
      && !event.metaKey
      && !event.ctrlKey
      && !event.shiftKey
      && !event.altKey
    );
  }

  function initNavTransitionLink(link) {
    if (!(link instanceof HTMLAnchorElement)) return;
    if (link.dataset.navTransitionBound === "1") return;
    link.dataset.navTransitionBound = "1";

    link.addEventListener("click", (event) => {
      if (!isPrimaryNavigationEvent(event)) return;
      if (link.target && link.target !== "_self") return;
      if (event.defaultPrevented) return;
      if (window.matchMedia && window.matchMedia("(prefers-reduced-motion: reduce)").matches) return;

      const href = link.getAttribute("href");
      if (!href) return;
      const nextUrl = new URL(href, window.location.href);
      if (nextUrl.origin !== window.location.origin) return;

      const current = `${window.location.pathname}${window.location.search}${window.location.hash}`;
      const target = `${nextUrl.pathname}${nextUrl.search}${nextUrl.hash}`;
      if (current === target) return;

      const shell = document.querySelector("[data-page-shell]");
      if (!(shell instanceof HTMLElement)) return;
      if (window.__navTransitionLock === true) {
        event.preventDefault();
        return;
      }

      event.preventDefault();
      window.__navTransitionLock = true;
      enableNextPageFade();
      shell.classList.remove("is-visible");
      shell.classList.add("is-leaving");
      window.setTimeout(() => {
        window.location.assign(nextUrl.toString());
      }, state.navFadeDurationMs);
    });
  }

  function bindNavTransitions(scope) {
    const root = scope instanceof Element ? scope : document;
    root.querySelectorAll("[data-nav-transition]").forEach((node) => {
      initNavTransitionLink(node);
    });
  }

  window.BrieftubeNavTransition = {
    configure,
    revealPageShell,
    bindNavTransitions,
  };
})();
