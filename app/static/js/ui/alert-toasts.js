(() => {
  let showToast = () => {};

  function configure(options = {}) {
    if (typeof options.showUiToast === "function") {
      showToast = options.showUiToast;
    }
  }

  function initAlertToast(form) {
    if (form.dataset.alertToastBound === "1") return;
    form.dataset.alertToastBound = "1";

    const checkbox = form.querySelector("[data-alert-confirm]");
    const submit = form.querySelector("[data-alert-submit]");
    const dismiss = form.querySelector("[data-alert-dismiss]");
    if (!checkbox || !submit) return;

    const sync = () => {
      submit.disabled = !checkbox.checked;
    };
    checkbox.addEventListener("change", sync);
    dismiss?.addEventListener("click", () => {
      form.classList.add("opacity-0");
      setTimeout(() => form.remove(), 150);
    });
    sync();
  }

  function bindAlertToasts(scope) {
    const root = scope instanceof Element ? scope : document;
    root.querySelectorAll("[data-alert-toast]").forEach(initAlertToast);
  }

  function bindEventToast(eventName, boundKey, defaultTone = "success") {
    const root = document.body;
    if (!root || root.dataset[boundKey] === "1") return;
    root.dataset[boundKey] = "1";
    root.addEventListener(eventName, (event) => {
      const detail = event.detail;
      const message = typeof detail === "string" ? detail : detail?.message;
      const tone = detail && typeof detail === "object" && detail.tone === "error"
        ? "error"
        : detail && typeof detail === "object" && detail.tone === "info"
          ? "info"
          : defaultTone;
      showToast(message, tone);
    });
  }

  function bindChannelReactivateToasts() {
    bindEventToast("channel-reactivate-toast", "channelReactivateToastBound");
  }

  function bindChannelMetadataToasts() {
    bindEventToast("channel-metadata-toast", "channelMetadataToastBound");
  }

  function bindVideoDownloadBulkToasts() {
    bindEventToast("video-download-bulk-toast", "videoDownloadBulkToastBound");
  }

  function bindVideoArticleRequestToasts() {
    bindEventToast("video-article-request-toast", "videoArticleRequestToastBound");
  }

  function bindVideoTranscriptRequestToasts() {
    bindEventToast("video-transcript-request-toast", "videoTranscriptRequestToastBound");
  }

  function bindLlmRuntimeToasts() {
    bindEventToast("llm-runtime-toast", "llmRuntimeToastBound");
  }

  window.BrieftubeAlertToasts = {
    configure,
    bindAlertToasts,
    bindChannelReactivateToasts,
    bindChannelMetadataToasts,
    bindVideoDownloadBulkToasts,
    bindVideoArticleRequestToasts,
    bindVideoTranscriptRequestToasts,
    bindLlmRuntimeToasts,
  };
})();
