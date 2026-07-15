(() => {
  const PAGE_FADE_ENABLE_KEY = "brieftube.enableNextPageFade";
  const NAV_FADE_DURATION_MS = 120;
  const themeController = window.BrieftubeTheme || null;
  const navTransitionController = window.BrieftubeNavTransition || null;
  const channelComposeController = window.BrieftubeChannelCompose || null;
  const globalSearchController = window.BrieftubeGlobalSearch || null;
  const channelListControls = window.BrieftubeChannelListControls || null;
  const channelActions = window.BrieftubeChannelActions || null;
  const inputControls = window.BrieftubeInputControls || null;
  const alertToasts = window.BrieftubeAlertToasts || null;
  const videoControls = window.BrieftubeVideoControls || null;
  const categoryControls = window.BrieftubeCategoryControls || null;
  const youtubeEmbed = window.BrieftubeYoutubeEmbed || null;
  const queueStatus = window.BrieftubeQueueStatus || null;
  const autoRefresh = window.BrieftubeAutoRefresh || null;
  const downloadSettings = window.BrieftubeDownloadSettings || null;
  const downloadControls = window.BrieftubeDownloadControls || null;
  const downloadProgress = window.BrieftubeDownloadProgress || null;

  navTransitionController?.configure?.({
    pageFadeKey: PAGE_FADE_ENABLE_KEY,
    navFadeDurationMs: NAV_FADE_DURATION_MS,
  });

  function ensureUiToastStack() {
    let stack = document.getElementById("ui-toast-stack");
    if (!stack) {
      stack = document.createElement("div");
      stack.id = "ui-toast-stack";
      stack.className = "pointer-events-none fixed bottom-4 right-4 z-[60] flex w-full max-w-xs flex-col gap-2";
      stack.setAttribute("role", "status");
      stack.setAttribute("aria-live", "polite");
      stack.setAttribute("aria-atomic", "false");
      stack.setAttribute("aria-relevant", "additions");
      document.body.appendChild(stack);
    }
    return stack;
  }

  function flattenSavedValues(value, prefix = "") {
    if (value === null || value === undefined) return [];
    if (typeof value !== "object" || Array.isArray(value)) return [`${prefix}=${String(value)}`];
    const pairs = [];
    Object.entries(value).forEach(([key, child]) => {
      if (key === "ok") return;
      const nextPrefix = prefix ? `${prefix}.${key}` : key;
      if (child !== null && typeof child === "object" && !Array.isArray(child)) {
        pairs.push(...flattenSavedValues(child, nextPrefix));
      } else if (child !== undefined) {
        pairs.push(`${nextPrefix}=${String(child)}`);
      }
    });
    return pairs;
  }

  function buildSaveToastMessage(baseMessage, payload) {
    if (!baseMessage) return "";
    const details = payload ? flattenSavedValues(payload) : [];
    if (details.length !== 1 || !details[0] || details[0].length > 36) return baseMessage;
    return `${baseMessage} (${details[0]})`;
  }

  function showUiToast(message, tone = "success", duration = null) {
    if (!message) return;
    const node = document.createElement("div");
    const toneClass = tone === "error"
      ? "border-rose-300 bg-rose-50 text-rose-800"
      : tone === "info"
        ? "border-sky-300 bg-sky-50 text-sky-800"
        : "border-emerald-300 bg-emerald-50 text-emerald-800";
    node.className = `pointer-events-auto rounded-lg border px-3 py-2 text-sm shadow-lg transition-all duration-200 ${toneClass}`;
    node.setAttribute("role", tone === "error" ? "alert" : "status");
    node.textContent = message;
    ensureUiToastStack().appendChild(node);
    setTimeout(() => {
      node.classList.add("opacity-0", "translate-y-1");
      setTimeout(() => node.remove(), 250);
    }, duration ?? (tone === "error" ? 5000 : 2000));
  }

  async function refreshVideoDetailFragmentNow() {
    await autoRefresh?.refreshVideoDetailFragmentNow?.();
  }

  async function refreshDownloadHistoryFragment(forceReloadFallback = false) {
    if (typeof autoRefresh?.refreshDownloadHistoryFragment !== "function") return true;
    return await autoRefresh.refreshDownloadHistoryFragment(forceReloadFallback);
  }

  videoControls?.configure?.({ showUiToast, refreshVideoDetailFragmentNow });
  categoryControls?.configure?.({ showUiToast });
  queueStatus?.configure?.({ showUiToast });
  autoRefresh?.configure?.({ hydrateUiScope });
  alertToasts?.configure?.({ showUiToast });
  channelActions?.configure?.({ showUiToast });
  downloadSettings?.configure?.({ showUiToast });
  downloadControls?.configure?.({ showUiToast, refreshDownloadHistoryFragment });
  downloadProgress?.configure?.({ showUiToast, refreshDownloadHistoryFragment });

  function parseJsonSafe(text) {
    if (!text) return null;
    try {
      return JSON.parse(text);
    } catch (_err) {
      return null;
    }
  }

  function initPollTriggerButton(button) {
    if (button.dataset.pollTriggerBound === "1") return;
    button.dataset.pollTriggerBound = "1";
    const label = button.querySelector("[data-poll-label]");
    const defaultLabel = label?.textContent || button.textContent || "";
    const busyLabel = button.dataset.busyLabel || defaultLabel;
    const setBusy = (isBusy) => {
      button.disabled = isBusy;
      button.setAttribute("aria-busy", isBusy ? "true" : "false");
      if (label) label.textContent = isBusy ? busyLabel : defaultLabel;
    };
    button.addEventListener("htmx:beforeRequest", (event) => {
      if (event.target === button) setBusy(true);
    });
    button.addEventListener("htmx:afterRequest", (event) => {
      if (event.target !== button) return;
      setBusy(false);
      if (!event.detail.successful) return;
      const payload = parseJsonSafe(event.detail?.xhr?.responseText || "");
      if (payload?.triggered) showUiToast(button.dataset.toastSuccess || "Poll requested.");
      else if (payload?.reason === "rss_worker_disabled") showUiToast(button.dataset.toastDisabled || "Poll is unavailable.", "info");
      else showUiToast(button.dataset.toastFailed || "Failed to request poll.", "error");
    });
    ["htmx:responseError", "htmx:sendError", "htmx:timeout"].forEach((name) => {
      button.addEventListener(name, (event) => {
        if (event.target !== button) return;
        setBusy(false);
        showUiToast(button.dataset.toastFailed || "Failed to request poll.", "error");
      });
    });
  }

  function bindPollTriggerButtons(scope) {
    const root = scope instanceof Element ? scope : document;
    root.querySelectorAll("[data-poll-trigger]").forEach(initPollTriggerButton);
  }

  function hydrateUiScope(scope) {
    themeController?.bindThemeControls?.(scope);
    channelComposeController?.bindChannelCompose?.(scope);
    channelComposeController?.bindChannelComposeForms?.(scope);
    globalSearchController?.bindGlobalSearchForms?.(scope);
    globalSearchController?.bindSearchClearButtons?.(scope);
    channelListControls?.bindChannelSearch?.(scope);
    channelListControls?.bindChannelManageForms?.(scope);
    channelListControls?.bindChannelMetaAccordion?.(scope);
    channelListControls?.bindChannelAvatars?.(scope);
    channelActions?.bindChannelReactivateBulkForms?.(scope);
    bindPollTriggerButtons(scope);
    videoControls?.bindVideoManageForms?.(scope);
    videoControls?.bindThumbPreviews?.(scope);
    inputControls?.bindDigitsOnlyInputs?.(scope);
    alertToasts?.bindAlertToasts?.(scope);
    videoControls?.bindRetentionForms?.(scope);
    videoControls?.bindRetentionNotices?.(scope);
    videoControls?.bindCopyButtons?.(scope);
    videoControls?.bindCollapsibles?.(scope);
    youtubeEmbed?.bindYouTubeEmbeds?.(scope);
    downloadControls?.bindVideoDownloadButtons?.(scope);
    videoControls?.bindVideoArticleRequestButtons?.(scope);
    videoControls?.bindVideoTranscriptRequestButtons?.(scope);
    videoControls?.bindVideoTranscriptCopyButtons?.(scope);
    videoControls?.bindVideoArticlePreviewLoadButtons?.(scope);
    videoControls?.bindArticlePreviewModals?.(scope);
    downloadControls?.bindDownloadDetailButtons?.(scope);
    downloadControls?.bindDownloadRetryButtons?.(scope);
    categoryControls?.bindCategorySortable?.(scope);
    categoryControls?.bindChannelMoveCategory?.(scope);
    categoryControls?.bindCategoryFilterReset?.(scope);
  }

  function revealPageShell() {
    if (navTransitionController?.revealPageShell) {
      navTransitionController.revealPageShell();
      return;
    }
    const shell = document.querySelector("[data-page-shell]");
    if (!(shell instanceof HTMLElement)) return;
    shell.classList.add("is-visible");
    shell.classList.remove("is-leaving");
  }

  document.addEventListener("DOMContentLoaded", () => {
    const themeState = themeController?.getThemeState?.() || { mode: "system", tone: "neutral" };
    themeController?.applyTheme?.(themeState.mode, themeState.tone, { persist: false });
    themeController?.bindSystemThemeObserver?.();
    hydrateUiScope(document);
    alertToasts?.bindChannelReactivateToasts?.();
    alertToasts?.bindChannelMetadataToasts?.();
    alertToasts?.bindVideoDownloadBulkToasts?.();
    alertToasts?.bindVideoArticleRequestToasts?.();
    alertToasts?.bindVideoTranscriptRequestToasts?.();
    alertToasts?.bindLlmRuntimeToasts?.();
    downloadProgress?.startPolling?.();
    downloadSettings?.bindErrorHandlers?.();
    queueStatus?.bindQueueRetryButtons?.(document);
    queueStatus?.bindQueueClearButtons?.(document);
    queueStatus?.startQueuePolling?.();
    autoRefresh?.startVideoDetailAutoRefresh?.();
    autoRefresh?.startChannelListAutoRefresh?.();
    autoRefresh?.startLlmRuntimeAutoRefresh?.();
    navTransitionController?.bindNavTransitions?.(document);
    categoryControls?.bindCategoryRename?.();
    channelActions?.initChannelImportForm?.();
    revealPageShell();
  });

  window.addEventListener("pageshow", revealPageShell);
  document.addEventListener("htmx:afterRequest", (event) => {
    if (!event.detail.successful) return;
    const requestElement = event.detail?.requestConfig?.elt;
    if (requestElement instanceof Element && requestElement.hasAttribute("data-skip-save-toast")) return;
    const target = event.target instanceof Element ? event.target : null;
    if (target?.hasAttribute("data-skip-save-toast")) return;
    const source = (requestElement instanceof Element ? requestElement.closest("[data-save-toast]") : null)
      || target?.closest("[data-save-toast]");
    const baseMessage = source?.getAttribute("data-save-toast");
    if (!baseMessage) return;
    const payload = parseJsonSafe(event.detail?.xhr?.responseText || "");
    showUiToast(buildSaveToastMessage(baseMessage, payload));
  });
  document.addEventListener("htmx:afterSwap", (event) => {
    hydrateUiScope(event.target);
    categoryControls?.bindCategoryRename?.();
  });
})();
