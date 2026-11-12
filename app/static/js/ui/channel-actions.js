(() => {
  const UI_BOOTSTRAP = window.BRIEFTUBE_UI_BOOTSTRAP || {};
  const CHANNEL_REACTIVATE_UI_TEXT = {
    confirmTitle: "재활성화 확인",
    confirmDesc: "{count}개 채널을 다시 활성화합니다.",
    confirmWaitHint: "최대 {seconds}초 정도 걸릴 수 있습니다.",
    confirmSubmit: "재활성화",
    confirmCancel: "취소",
    overlayTitle: "재활성화 진행 중",
    overlayDesc: "{count}개 채널을 순차적으로 확인하고 있습니다.",
    limitExceeded: "선택 가능한 최대 채널 수를 초과했습니다.",
    ...(UI_BOOTSTRAP.channelReactivate || {}),
  };

  let showToast = () => {};

  function configure(options = {}) {
    if (typeof options.showUiToast === "function") showToast = options.showUiToast;
  }

  function formatTemplate(template, values = {}) {
    const base = String(template || "");
    return Object.entries(values).reduce((acc, [key, value]) => {
      return acc.replaceAll(`{${key}}`, String(value));
    }, base);
  }

  function ensureChannelReactivateBlockingOverlay() {
    let root = document.getElementById("channel-reactivate-blocking-overlay");
    if (root) return root;
    root = document.createElement("div");
    root.id = "channel-reactivate-blocking-overlay";
    root.className = "fixed inset-0 z-[74] hidden items-center justify-center px-4";
    root.innerHTML = `
      <div class="absolute inset-0 bg-slate-900/35"></div>
      <div class="relative w-full max-w-sm rounded-xl border border-slate-200 bg-white p-5 text-center shadow-xl">
        <div class="mx-auto h-8 w-8 animate-spin rounded-full border-2 border-indigo-200 border-t-indigo-600"></div>
        <p class="mt-3 text-sm font-semibold text-slate-900" data-reactivate-overlay-title></p>
        <p class="mt-1 text-xs text-slate-500" data-reactivate-overlay-desc></p>
      </div>`;
    document.body.appendChild(root);
    return root;
  }

  function showChannelReactivateBlockingOverlay(title, description) {
    const root = ensureChannelReactivateBlockingOverlay();
    const titleNode = root.querySelector("[data-reactivate-overlay-title]");
    const descNode = root.querySelector("[data-reactivate-overlay-desc]");
    if (titleNode) titleNode.textContent = title || CHANNEL_REACTIVATE_UI_TEXT.overlayTitle;
    if (descNode) descNode.textContent = description || "";
    root.classList.remove("hidden");
    root.classList.add("flex");
  }

  function hideChannelReactivateBlockingOverlay() {
    const root = document.getElementById("channel-reactivate-blocking-overlay");
    if (!root) return;
    root.classList.add("hidden");
    root.classList.remove("flex");
  }

  function initChannelReactivateBulkForm(form) {
    if (form.dataset.channelReactivateBulkBound === "1") return;
    form.dataset.channelReactivateBulkBound = "1";
    const resolveSelectedCount = () => form.querySelectorAll("[data-channel-select-item]:checked").length;
    const parsePositiveInt = (value, fallback) => {
      const parsed = Number.parseInt(String(value || ""), 10);
      return Number.isNaN(parsed) || parsed <= 0 ? fallback : parsed;
    };
    const limit = parsePositiveInt(form.dataset.reactivateBatchLimit, 50);
    const timeoutSeconds = parsePositiveInt(form.dataset.reactivateTimeoutSeconds, 20);
    const probeDelaySeconds = parsePositiveInt(form.dataset.reactivateDelaySeconds, 0);
    const clearPendingState = () => {
      delete form.dataset.reactivateInFlight;
      delete form.dataset.reactivateSelectedCount;
      hideChannelReactivateBlockingOverlay();
    };

    // Confirm on htmx:confirm (before the request is issued), not on submit: htmx's own
    // submit listener is registered first, so a submit-time modal could not stop the POST.
    form.addEventListener("htmx:confirm", (event) => {
      // htmx:confirm bubbles; row buttons inside the form have their own confirms.
      if (event.detail?.elt !== form) return;
      const submitter = event.detail?.triggeringEvent?.submitter;
      if (submitter instanceof HTMLButtonElement && submitter.name === "bulk_action" && submitter.value === "delete") {
        clearPendingState();
        return;
      }
      const selectedCount = resolveSelectedCount();
      if (selectedCount <= 0) return;
      event.preventDefault();
      if (selectedCount > limit) {
        showToast(formatTemplate(CHANNEL_REACTIVATE_UI_TEXT.limitExceeded, { selected: selectedCount, limit }), "error");
        return;
      }
      const dialog = window.BrieftubeDialog;
      if (!dialog || dialog.isDroppedBySync(form)) return;
      const detail = event.detail;
      const maxWaitSeconds = (selectedCount * timeoutSeconds) + (Math.max(0, selectedCount - 1) * probeDelaySeconds);
      const description = formatTemplate(CHANNEL_REACTIVATE_UI_TEXT.confirmDesc, { count: selectedCount });
      const hint = formatTemplate(CHANNEL_REACTIVATE_UI_TEXT.confirmWaitHint, { seconds: maxWaitSeconds });
      dialog.confirm({
        title: CHANNEL_REACTIVATE_UI_TEXT.confirmTitle,
        message: `${description}\n${hint}`,
        confirmLabel: CHANNEL_REACTIVATE_UI_TEXT.confirmSubmit,
        cancelLabel: CHANNEL_REACTIVATE_UI_TEXT.confirmCancel,
      }).then((confirmed) => {
        if (!confirmed || !form.isConnected) return;
        form.dataset.reactivateInFlight = "1";
        form.dataset.reactivateSelectedCount = String(selectedCount);
        showChannelReactivateBlockingOverlay(
          CHANNEL_REACTIVATE_UI_TEXT.overlayTitle,
          formatTemplate(CHANNEL_REACTIVATE_UI_TEXT.overlayDesc, { count: selectedCount }),
        );
        detail.issueRequest(true);
        // hx-sync may drop the request (another list request in flight); do not leave the overlay up.
        if (!form.classList.contains("htmx-request")) clearPendingState();
      });
    });
    const settle = (event) => {
      if (event.target === form) clearPendingState();
    };
    ["htmx:afterRequest", "htmx:responseError", "htmx:sendError", "htmx:timeout"].forEach((name) => {
      form.addEventListener(name, settle);
    });
  }

  function bindChannelReactivateBulkForms(scope) {
    const root = scope instanceof Element ? scope : document;
    root.querySelectorAll("[data-channel-reactivate-bulk-form]").forEach(initChannelReactivateBulkForm);
  }

  function initChannelImportForm() {
    const form = document.getElementById("channel-import-form");
    if (!form || form.dataset.importBound === "1") return;
    form.dataset.importBound = "1";
    form.addEventListener("submit", async (event) => {
      event.preventDefault();
      const fileInput = form.querySelector('input[name="import_file"]');
      if (!fileInput?.files?.length) {
        showToast(form.dataset.toastNoFile || "Please select a file.", "info");
        return;
      }
      const submitButton = form.querySelector('button[type="submit"]');
      if (submitButton) submitButton.disabled = true;
      try {
        const body = new FormData();
        body.append("import_file", fileInput.files[0]);
        const response = await fetch("/api/channels/import", { method: "POST", body });
        const data = await response.json();
        if (!response.ok || !data.ok) throw new Error("import_failed");
        const template = data.created_categories > 0 ? form.dataset.toastSuccessCat : form.dataset.toastSuccess;
        const message = String(template || "")
          .replace("{added}", data.added)
          .replace("{duplicate}", data.duplicate)
          .replace("{invalid}", data.invalid)
          .replace("{created_categories}", data.created_categories);
        showToast(message, "success");
        fileInput.value = "";
        const status = document.querySelector("[data-channel-status-input]")?.value || "active";
        const refreshUrl = document.querySelector("[data-channel-list-fragment]")?.dataset.channelListRefreshUrl || "";
        const categoryId = new URLSearchParams(refreshUrl.split("?")[1] || "").get("category_id");
        const params = new URLSearchParams({ status });
        if (categoryId) params.set("category_id", categoryId);
        if (typeof htmx !== "undefined") {
          htmx.ajax("GET", `/views/channel-list?${params}`, { target: "#channel-list-wrap", swap: "innerHTML" });
          if (Number(data.created_categories || 0) > 0) {
            htmx.ajax("GET", `/views/category-sidebar?${params}`, { target: "#category-sidebar", swap: "outerHTML" });
          }
        }
      } catch (_err) {
        showToast(form.dataset.toastFailed || "Import failed.", "error");
      } finally {
        if (submitButton) submitButton.disabled = false;
      }
    });
  }

  window.BrieftubeChannelActions = {
    configure,
    bindChannelReactivateBulkForms,
    initChannelImportForm,
  };
})();
