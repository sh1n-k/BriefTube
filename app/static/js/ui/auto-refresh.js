(() => {
  const VIDEO_DETAIL_POLL_INTERVAL_MS = 3000;
  const CHANNEL_LIST_POLL_INTERVAL_MS = 15000;
  const LLM_RUNTIME_POLL_INTERVAL_MS = 10000;

  let hydrate = () => {};
  let videoDetailRefreshInFlight = false;
  let videoDetailRefreshStarted = false;
  let channelListRefreshInFlight = false;
  let channelListRefreshStarted = false;
  let llmRuntimeRefreshInFlight = false;
  let llmRuntimeRefreshStarted = false;

  function configure(options = {}) {
    if (typeof options.hydrateUiScope === "function") {
      hydrate = options.hydrateUiScope;
    }
  }

  function isDocumentVisible() {
    return document.hidden !== true;
  }

  function parseHtmlFragment(html) {
    const template = document.createElement("template");
    template.innerHTML = String(html || "").trim();
    return template.content;
  }

  async function fetchAndSwapFragment({
    url,
    targetSelector,
    swap = "outerHTML",
    beforeSwap = null,
    shouldSwap = null,
  }) {
    const target = document.querySelector(targetSelector);
    if (!(target instanceof Element)) return false;
    try {
      const response = await fetch(url, {
        method: "GET",
        headers: {
          "Accept": "text/html",
          "X-Requested-With": "BriefTubePoll",
        },
      });
      if (!response.ok) return false;
      const html = await response.text();
      if (swap === "innerHTML") {
        const latestTarget = document.querySelector(targetSelector);
        if (!(latestTarget instanceof Element)) return false;
        const fragment = parseHtmlFragment(html);
        const nextNode = fragment.firstElementChild;
        const currentNode = latestTarget.childElementCount === 1
          ? latestTarget.firstElementChild
          : null;
        if (
          currentNode instanceof Element
          && nextNode instanceof Element
          && typeof shouldSwap === "function"
          && shouldSwap({ target: latestTarget, currentNode, nextNode }) === false
        ) {
          return true;
        }
        if (typeof beforeSwap === "function") {
          if (!(nextNode instanceof Element)) return false;
          beforeSwap({ target: latestTarget, nextNode });
        }
        if (nextNode instanceof Element) {
          latestTarget.replaceChildren(nextNode);
        } else {
          latestTarget.innerHTML = html;
        }
        if (typeof htmx !== "undefined" && typeof htmx.process === "function") {
          htmx.process(latestTarget);
        }
        hydrate(latestTarget);
        return true;
      }
      const fragment = parseHtmlFragment(html);
      const nextNode = fragment.firstElementChild;
      const latestTarget = document.querySelector(targetSelector);
      if (!(latestTarget instanceof Element) || !(nextNode instanceof Element)) return false;
      if (
        typeof shouldSwap === "function"
        && shouldSwap({ target: latestTarget, currentNode: latestTarget, nextNode }) === false
      ) {
        return true;
      }
      if (typeof beforeSwap === "function") {
        beforeSwap({ target: latestTarget, nextNode });
      }
      latestTarget.replaceWith(nextNode);
      if (typeof htmx !== "undefined" && typeof htmx.process === "function") {
        htmx.process(nextNode);
      }
      hydrate(nextNode);
      return true;
    } catch (_error) {
      return false;
    }
  }

  async function pollVideoDetailFragment() {
    if (videoDetailRefreshInFlight || !isDocumentVisible()) return;
    const fragment = document.querySelector("[data-video-detail-dynamic-fragment]");
    if (!(fragment instanceof HTMLElement)) return;
    if (fragment.dataset.videoDetailAutoRefresh !== "1") return;
    const refreshUrl = fragment.dataset.videoDetailRefreshUrl || "";
    if (!refreshUrl) return;
    videoDetailRefreshInFlight = true;
    try {
      await fetchAndSwapFragment({
        url: refreshUrl,
        targetSelector: "#video-detail-dynamic-wrap",
        swap: "outerHTML",
        shouldSwap: ({ currentNode, nextNode }) => {
          if (!(currentNode instanceof HTMLElement) || !(nextNode instanceof HTMLElement)) {
            return true;
          }
          const currentFragment = currentNode.matches("[data-video-detail-dynamic-fragment]")
            ? currentNode
            : currentNode.querySelector("[data-video-detail-dynamic-fragment]");
          const nextFragment = nextNode.matches("[data-video-detail-dynamic-fragment]")
            ? nextNode
            : nextNode.querySelector("[data-video-detail-dynamic-fragment]");
          if (!(currentFragment instanceof HTMLElement) || !(nextFragment instanceof HTMLElement)) {
            return true;
          }
          return currentFragment.dataset.videoDetailRefreshKey
            !== nextFragment.dataset.videoDetailRefreshKey;
        },
      });
    } finally {
      videoDetailRefreshInFlight = false;
    }
  }

  async function refreshVideoDetailFragmentNow() {
    if (videoDetailRefreshInFlight || !isDocumentVisible()) return;
    const fragment = document.querySelector("[data-video-detail-dynamic-fragment]");
    if (!(fragment instanceof HTMLElement)) return;
    const refreshUrl = fragment.dataset.videoDetailRefreshUrl || "";
    if (!refreshUrl) return;
    videoDetailRefreshInFlight = true;
    try {
      await fetchAndSwapFragment({
        url: refreshUrl,
        targetSelector: "#video-detail-dynamic-wrap",
        swap: "outerHTML",
      });
    } finally {
      videoDetailRefreshInFlight = false;
    }
  }

  function startVideoDetailAutoRefresh() {
    if (videoDetailRefreshStarted) return;
    videoDetailRefreshStarted = true;
    window.setInterval(() => {
      void pollVideoDetailFragment();
    }, VIDEO_DETAIL_POLL_INTERVAL_MS);
    document.addEventListener("visibilitychange", () => {
      if (!document.hidden) {
        void pollVideoDetailFragment();
      }
    });
  }

  function shouldSkipChannelListAutoRefresh() {
    const searchInput = document.querySelector("[data-channel-search-input]");
    if (searchInput instanceof HTMLInputElement && searchInput.value.trim()) {
      return true;
    }
    return Array.from(document.querySelectorAll("[data-channel-select-item]")).some((node) => (
      node instanceof HTMLInputElement && node.checked
    ));
  }

  async function pollChannelListFragment() {
    if (channelListRefreshInFlight || !isDocumentVisible()) return;
    const fragment = document.querySelector("[data-channel-list-fragment]");
    if (!(fragment instanceof HTMLElement)) return;
    if (fragment.dataset.channelListAutoRefresh !== "1" || shouldSkipChannelListAutoRefresh()) {
      return;
    }
    const refreshUrl = fragment.dataset.channelListRefreshUrl || "";
    if (!refreshUrl) return;
    channelListRefreshInFlight = true;
    try {
      await fetchAndSwapFragment({
        url: refreshUrl,
        targetSelector: "#channel-list-wrap",
        swap: "innerHTML",
      });
    } finally {
      channelListRefreshInFlight = false;
    }
  }

  function startChannelListAutoRefresh() {
    if (channelListRefreshStarted) return;
    channelListRefreshStarted = true;
    window.setInterval(() => {
      void pollChannelListFragment();
    }, CHANNEL_LIST_POLL_INTERVAL_MS);
    document.addEventListener("visibilitychange", () => {
      if (!document.hidden) {
        void pollChannelListFragment();
      }
    });
  }

  async function pollLlmRuntimeFragment() {
    if (llmRuntimeRefreshInFlight || !isDocumentVisible()) return;
    const fragment = document.querySelector("[data-llm-runtime-status]");
    if (!(fragment instanceof HTMLElement)) return;
    if (fragment.dataset.llmRuntimeAutoRefresh !== "1") return;
    const refreshUrl = fragment.dataset.llmRuntimeRefreshUrl || "";
    if (!refreshUrl) return;
    llmRuntimeRefreshInFlight = true;
    try {
      await fetchAndSwapFragment({
        url: refreshUrl,
        targetSelector: "#llm-runtime-status",
        swap: "outerHTML",
      });
    } finally {
      llmRuntimeRefreshInFlight = false;
    }
  }

  function startLlmRuntimeAutoRefresh() {
    if (llmRuntimeRefreshStarted) return;
    llmRuntimeRefreshStarted = true;
    window.setInterval(() => {
      void pollLlmRuntimeFragment();
    }, LLM_RUNTIME_POLL_INTERVAL_MS);
    document.addEventListener("visibilitychange", () => {
      if (!document.hidden) {
        void pollLlmRuntimeFragment();
      }
    });
  }

  async function refreshDownloadHistoryFragment(forceReloadFallback = false) {
    const fragment = document.querySelector("[data-download-history-fragment]");
    if (!(fragment instanceof HTMLElement)) return true;
    const refreshUrl = fragment.dataset.downloadHistoryRefreshUrl || "";
    if (!refreshUrl) return true;
    const updated = await fetchAndSwapFragment({
      url: refreshUrl,
      targetSelector: "#download-history-fragment",
      swap: "outerHTML",
    });
    if (!updated && forceReloadFallback && window.location.pathname === "/downloads") {
      window.location.reload();
    }
    return updated;
  }

  window.BrieftubeAutoRefresh = {
    configure,
    refreshVideoDetailFragmentNow,
    startVideoDetailAutoRefresh,
    startChannelListAutoRefresh,
    startLlmRuntimeAutoRefresh,
    refreshDownloadHistoryFragment,
  };
})();
