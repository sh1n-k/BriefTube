    (() => {
      const PAGE_FADE_ENABLE_KEY = "brieftube.enableNextPageFade";
      const NAV_FADE_DURATION_MS = 120;
      const themeController = window.BrieftubeTheme || null;
      const navTransitionController = window.BrieftubeNavTransition || null;
      const channelComposeController = window.BrieftubeChannelCompose || null;
      const globalSearchController = window.BrieftubeGlobalSearch || null;
      const channelListControls = window.BrieftubeChannelListControls || null;
      const inputControls = window.BrieftubeInputControls || null;
      const alertToasts = window.BrieftubeAlertToasts || null;
      const videoControls = window.BrieftubeVideoControls || null;
      const categoryControls = window.BrieftubeCategoryControls || null;
      const youtubeEmbed = window.BrieftubeYoutubeEmbed || null;
      const queueStatus = window.BrieftubeQueueStatus || null;
      const autoRefresh = window.BrieftubeAutoRefresh || null;
      const downloadControls = window.BrieftubeDownloadControls || null;
      if (navTransitionController && typeof navTransitionController.configure === "function") {
        navTransitionController.configure({
          pageFadeKey: PAGE_FADE_ENABLE_KEY,
          navFadeDurationMs: NAV_FADE_DURATION_MS,
        });
      }

      function getThemeState() {
        if (themeController && typeof themeController.getThemeState === "function") {
          return themeController.getThemeState();
        }
        return { mode: "system", tone: "neutral" };
      }

      function applyTheme(modeInput, toneInput, options = {}) {
        if (themeController && typeof themeController.applyTheme === "function") {
          themeController.applyTheme(modeInput, toneInput, options);
        }
      }

      function bindSystemThemeObserver() {
        if (themeController && typeof themeController.bindSystemThemeObserver === "function") {
          themeController.bindSystemThemeObserver();
        }
      }

      function bindThemeControls(scope) {
        if (themeController && typeof themeController.bindThemeControls === "function") {
          themeController.bindThemeControls(scope);
        }
      }

      function bindChannelSearch(scope) {
        channelListControls?.bindChannelSearch(scope);
      }

      function bindChannelManageForms(scope) {
        channelListControls?.bindChannelManageForms(scope);
      }

      function bindChannelMetaAccordion(scope) {
        channelListControls?.bindChannelMetaAccordion(scope);
      }

      function bindChannelAvatars(scope) {
        channelListControls?.bindChannelAvatars(scope);
      }

      function bindChannelCompose(scope) {
        channelComposeController?.bindChannelCompose(scope);
      }

      function bindChannelComposeForms(scope) {
        channelComposeController?.bindChannelComposeForms(scope);
      }

      function bindGlobalSearchForms(scope) {
        globalSearchController?.bindGlobalSearchForms(scope);
      }

      function bindSearchClearButtons(scope) {
        globalSearchController?.bindSearchClearButtons(scope);
      }

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

      function buildSaveToastMessage(baseMessage, payload) {
        if (!baseMessage) return "";
        const details = payload ? flattenSavedValues(payload) : [];
        if (details.length !== 1) return baseMessage;
        const [detail] = details;
        if (!detail || detail.length > 36) return baseMessage;
        return `${baseMessage} (${detail})`;
      }

      function showUiToast(message, tone = "success") {
        if (!message) return;
        const stack = ensureUiToastStack();
        const node = document.createElement("div");
        const toneClass = tone === "error"
          ? "border-rose-300 bg-rose-50 text-rose-800"
          : tone === "info"
            ? "border-sky-300 bg-sky-50 text-sky-800"
            : "border-emerald-300 bg-emerald-50 text-emerald-800";
        node.className = `pointer-events-auto rounded-lg border px-3 py-2 text-sm shadow-lg transition-all duration-200 ${toneClass}`;
        node.setAttribute("role", tone === "error" ? "alert" : "status");
        node.textContent = message;
        stack.appendChild(node);
        setTimeout(() => {
          node.classList.add("opacity-0", "translate-y-1");
          setTimeout(() => node.remove(), 250);
        }, 2000);
      }
      videoControls?.configure?.({ showUiToast, refreshVideoDetailFragmentNow });
      categoryControls?.configure?.({ showUiToast });
      queueStatus?.configure?.({ showUiToast });
      autoRefresh?.configure?.({ hydrateUiScope });
      alertToasts?.configure?.({ showUiToast });
      downloadControls?.configure?.({ showUiToast, refreshDownloadHistoryFragment });

      function initPollTriggerButton(button) {
        if (button.dataset.pollTriggerBound === "1") return;
        button.dataset.pollTriggerBound = "1";

        const label = button.querySelector("[data-poll-label]");
        const defaultLabel = label?.textContent || button.textContent || "";
        const busyLabel = button.dataset.busyLabel || defaultLabel;

        const setBusy = (isBusy) => {
          button.disabled = isBusy;
          button.setAttribute("aria-busy", isBusy ? "true" : "false");
          if (label) {
            label.textContent = isBusy ? busyLabel : defaultLabel;
          }
        };

        button.addEventListener("htmx:beforeRequest", (event) => {
          if (event.target === button) {
            setBusy(true);
          }
        });
        button.addEventListener("htmx:afterRequest", (event) => {
          if (event.target !== button) return;
          setBusy(false);
          if (!event.detail.successful) return;
          const payload = parseJsonSafe(event.detail?.xhr?.responseText || "");
          if (payload?.triggered) {
            showUiToast(button.dataset.toastSuccess || "Poll requested.", "success");
            return;
          }
          if (payload?.reason === "rss_worker_disabled") {
            showUiToast(button.dataset.toastDisabled || "Poll is unavailable.", "info");
            return;
          }
          showUiToast(button.dataset.toastFailed || "Failed to request poll.", "error");
        });
        button.addEventListener("htmx:responseError", (event) => {
          if (event.target !== button) return;
          setBusy(false);
          showUiToast(button.dataset.toastFailed || "Failed to request poll.", "error");
        });
        button.addEventListener("htmx:sendError", (event) => {
          if (event.target !== button) return;
          setBusy(false);
          showUiToast(button.dataset.toastFailed || "Failed to request poll.", "error");
        });
        button.addEventListener("htmx:timeout", (event) => {
          if (event.target !== button) return;
          setBusy(false);
          showUiToast(button.dataset.toastFailed || "Failed to request poll.", "error");
        });
      }

      function bindPollTriggerButtons(scope) {
        const root = scope instanceof Element ? scope : document;
        root.querySelectorAll("[data-poll-trigger]").forEach(initPollTriggerButton);
      }

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
      function formatTemplate(template, values = {}) {
        const base = String(template || "");
        return Object.entries(values).reduce((acc, [key, value]) => {
          return acc.replaceAll(`{${key}}`, String(value));
        }, base);
      }

      function ensureChannelReactivateConfirmModal() {
        let root = document.getElementById("channel-reactivate-confirm-modal");
        if (root) return root;

        root = document.createElement("div");
        root.id = "channel-reactivate-confirm-modal";
        root.className = "fixed inset-0 z-[75] hidden items-center justify-center px-4";
        root.innerHTML = `
          <div class="absolute inset-0 bg-slate-900/45" data-reactivate-confirm-backdrop></div>
          <div class="relative w-full max-w-md rounded-xl border border-slate-200 bg-white p-5 shadow-2xl">
            <h3 class="text-base font-semibold text-slate-900" data-reactivate-confirm-title></h3>
            <p class="mt-2 text-sm text-slate-600" data-reactivate-confirm-desc></p>
            <p class="mt-1 text-xs text-slate-500" data-reactivate-confirm-hint></p>
            <div class="mt-4 flex justify-end gap-2">
              <button type="button" class="rounded-md border border-slate-300 bg-white px-3 py-2 text-sm font-medium text-slate-700 hover:bg-slate-50" data-reactivate-confirm-cancel></button>
              <button type="button" class="rounded-md bg-indigo-600 px-3 py-2 text-sm font-medium text-white hover:bg-indigo-700" data-reactivate-confirm-submit></button>
            </div>
          </div>
        `;
        document.body.appendChild(root);
        return root;
      }

      function openChannelReactivateConfirmModal(options) {
        const root = ensureChannelReactivateConfirmModal();
        const titleNode = root.querySelector("[data-reactivate-confirm-title]");
        const descNode = root.querySelector("[data-reactivate-confirm-desc]");
        const hintNode = root.querySelector("[data-reactivate-confirm-hint]");
        const cancelButton = root.querySelector("[data-reactivate-confirm-cancel]");
        const submitButton = root.querySelector("[data-reactivate-confirm-submit]");
        if (!titleNode || !descNode || !hintNode || !cancelButton || !submitButton) {
          return Promise.resolve(window.confirm(options?.description || ""));
        }

        titleNode.textContent = options?.title || CHANNEL_REACTIVATE_UI_TEXT.confirmTitle;
        descNode.textContent = options?.description || "";
        hintNode.textContent = options?.hint || "";
        cancelButton.textContent = options?.cancelLabel || CHANNEL_REACTIVATE_UI_TEXT.confirmCancel;
        submitButton.textContent = options?.confirmLabel || CHANNEL_REACTIVATE_UI_TEXT.confirmSubmit;

        root.classList.remove("hidden");
        root.classList.add("flex");

        return new Promise((resolve) => {
          let done = false;
          const finalize = (confirmed) => {
            if (done) return;
            done = true;
            root.classList.add("hidden");
            root.classList.remove("flex");
            document.removeEventListener("keydown", onKeydown);
            cancelButton.removeEventListener("click", onCancel);
            submitButton.removeEventListener("click", onConfirm);
            root.removeEventListener("click", onBackdropClick);
            resolve(confirmed);
          };
          const onCancel = () => finalize(false);
          const onConfirm = () => finalize(true);
          const onBackdropClick = (event) => {
            if (event.target === root || event.target?.hasAttribute("data-reactivate-confirm-backdrop")) {
              finalize(false);
            }
          };
          const onKeydown = (event) => {
            if (event.key === "Escape") {
              finalize(false);
            }
          };

          cancelButton.addEventListener("click", onCancel);
          submitButton.addEventListener("click", onConfirm);
          root.addEventListener("click", onBackdropClick);
          document.addEventListener("keydown", onKeydown);
        });
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
          </div>
        `;
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

        const resolveSelectedCount = () => {
          return form.querySelectorAll("[data-channel-select-item]:checked").length;
        };
        const parsePositiveInt = (value, fallback) => {
          const parsed = Number.parseInt(String(value || ""), 10);
          if (Number.isNaN(parsed) || parsed <= 0) return fallback;
          return parsed;
        };
        const limit = parsePositiveInt(form.dataset.reactivateBatchLimit, 50);
        const timeoutSeconds = parsePositiveInt(form.dataset.reactivateTimeoutSeconds, 20);
        const probeDelaySeconds = parsePositiveInt(form.dataset.reactivateDelaySeconds, 0);

        const clearPendingState = () => {
          delete form.dataset.reactivateInFlight;
          delete form.dataset.reactivateSelectedCount;
          hideChannelReactivateBlockingOverlay();
        };

        form.addEventListener("submit", (event) => {
          const submitter = event.submitter;
          if (
            submitter instanceof HTMLButtonElement
            && submitter.name === "bulk_action"
            && submitter.value === "delete"
          ) {
            clearPendingState();
            return;
          }

          const selectedCount = resolveSelectedCount();
          if (selectedCount <= 0) return;

          if (selectedCount > limit) {
            event.preventDefault();
            showUiToast(
              formatTemplate(CHANNEL_REACTIVATE_UI_TEXT.limitExceeded, {
                selected: selectedCount,
                limit,
              }),
              "error",
            );
            return;
          }

          if (form.dataset.reactivateConfirmed === "1") {
            delete form.dataset.reactivateConfirmed;
            form.dataset.reactivateInFlight = "1";
            form.dataset.reactivateSelectedCount = String(selectedCount);
            showChannelReactivateBlockingOverlay(
              CHANNEL_REACTIVATE_UI_TEXT.overlayTitle,
              formatTemplate(CHANNEL_REACTIVATE_UI_TEXT.overlayDesc, { count: selectedCount }),
            );
            return;
          }

          event.preventDefault();
          if (form.dataset.reactivateDialogOpen === "1") return;
          form.dataset.reactivateDialogOpen = "1";

          const maxWaitSeconds = (selectedCount * timeoutSeconds) + (Math.max(0, selectedCount - 1) * probeDelaySeconds);
          openChannelReactivateConfirmModal({
            title: CHANNEL_REACTIVATE_UI_TEXT.confirmTitle,
            description: formatTemplate(CHANNEL_REACTIVATE_UI_TEXT.confirmDesc, { count: selectedCount }),
            hint: formatTemplate(CHANNEL_REACTIVATE_UI_TEXT.confirmWaitHint, { seconds: maxWaitSeconds }),
            confirmLabel: CHANNEL_REACTIVATE_UI_TEXT.confirmSubmit,
            cancelLabel: CHANNEL_REACTIVATE_UI_TEXT.confirmCancel,
          }).then((confirmed) => {
            delete form.dataset.reactivateDialogOpen;
            if (!confirmed) return;
            form.dataset.reactivateConfirmed = "1";
            if (submitter instanceof HTMLButtonElement) {
              form.requestSubmit(submitter);
            } else {
              form.requestSubmit();
            }
          });
        });

        const settle = (event) => {
          if (event.target === form) {
            clearPendingState();
          }
        };
        form.addEventListener("htmx:afterRequest", settle);
        form.addEventListener("htmx:responseError", settle);
        form.addEventListener("htmx:sendError", settle);
        form.addEventListener("htmx:timeout", settle);
      }

      function bindChannelReactivateBulkForms(scope) {
        const root = scope instanceof Element ? scope : document;
        root.querySelectorAll("[data-channel-reactivate-bulk-form]").forEach(initChannelReactivateBulkForm);
      }

      function bindChannelReactivateToasts() {
        alertToasts?.bindChannelReactivateToasts();
      }

      function bindChannelMetadataToasts() {
        alertToasts?.bindChannelMetadataToasts();
      }

      function bindVideoDownloadBulkToasts() {
        alertToasts?.bindVideoDownloadBulkToasts();
      }

      function bindVideoArticleRequestToasts() {
        alertToasts?.bindVideoArticleRequestToasts();
      }

      function bindVideoTranscriptRequestToasts() {
        alertToasts?.bindVideoTranscriptRequestToasts();
      }

      function bindLlmRuntimeToasts() {
        alertToasts?.bindLlmRuntimeToasts();
      }

      function bindVideoDownloadButtons(scope) {
        downloadControls?.bindVideoDownloadButtons(scope);
      }

      function bindDownloadDetailButtons(scope) {
        downloadControls?.bindDownloadDetailButtons(scope);
      }

      function bindDownloadRetryButtons(scope) {
        downloadControls?.bindDownloadRetryButtons(scope);
      }

      function startDownloadProgressPolling() {
        downloadControls?.startDownloadProgressPolling();
      }

      function bindDownloadSettingsErrorHandlers() {
        downloadControls?.bindDownloadSettingsErrorHandlers();
      }

      function bindQueueRetryButtons(scope) {
        queueStatus?.bindQueueRetryButtons(scope);
      }

      function bindQueueClearButtons(scope) {
        queueStatus?.bindQueueClearButtons(scope);
      }

      function startQueuePolling() {
        queueStatus?.startQueuePolling();
      }

      function parseJsonSafe(text) {
        if (!text) return null;
        try {
          return JSON.parse(text);
        } catch (_err) {
          return null;
        }
      }

      function hydrateUiScope(scope) {
        bindThemeControls(scope);
        bindChannelCompose(scope);
        bindChannelComposeForms(scope);
        bindGlobalSearchForms(scope);
        bindSearchClearButtons(scope);
        bindChannelSearch(scope);
        bindChannelManageForms(scope);
        bindChannelMetaAccordion(scope);
        bindChannelAvatars(scope);
        bindChannelReactivateBulkForms(scope);
        bindPollTriggerButtons(scope);
        bindVideoManageForms(scope);
        bindThumbPreviews(scope);
        bindDigitsOnlyInputs(scope);
        bindAlertToasts(scope);
        bindRetentionForms(scope);
        bindRetentionNotices(scope);
        bindCopyButtons(scope);
        bindCollapsibles(scope);
        bindYouTubeEmbeds(scope);
        bindVideoDownloadButtons(scope);
        bindVideoArticleRequestButtons(scope);
        bindVideoTranscriptRequestButtons(scope);
        bindVideoTranscriptCopyButtons(scope);
        bindVideoArticlePreviewLoadButtons(scope);
        bindArticlePreviewModals(scope);
        bindDownloadDetailButtons(scope);
        bindDownloadRetryButtons(scope);
        bindCategorySortable(scope);
        bindChannelMoveCategory(scope);
        bindCategoryFilterReset(scope);
      }

      async function refreshVideoDetailFragmentNow() {
        await autoRefresh?.refreshVideoDetailFragmentNow?.();
      }

      function startVideoDetailAutoRefresh() {
        autoRefresh?.startVideoDetailAutoRefresh?.();
      }

      function startChannelListAutoRefresh() {
        autoRefresh?.startChannelListAutoRefresh?.();
      }

      function startLlmRuntimeAutoRefresh() {
        autoRefresh?.startLlmRuntimeAutoRefresh?.();
      }

      async function refreshDownloadHistoryFragment(forceReloadFallback = false) {
        if (typeof autoRefresh?.refreshDownloadHistoryFragment !== "function") return true;
        return await autoRefresh.refreshDownloadHistoryFragment(forceReloadFallback);
      }

      function flattenSavedValues(value, prefix = "") {
        if (value === null || value === undefined) return [];
        if (typeof value !== "object" || Array.isArray(value)) {
          return [`${prefix}=${String(value)}`];
        }

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

      function bindDigitsOnlyInputs(scope) {
        inputControls?.bindDigitsOnlyInputs(scope);
      }

      function bindAlertToasts(scope) {
        alertToasts?.bindAlertToasts(scope);
      }

      function bindRetentionForms(scope) {
        videoControls?.bindRetentionForms(scope);
      }

      function bindRetentionNotices(scope) {
        videoControls?.bindRetentionNotices(scope);
      }

      function bindVideoManageForms(scope) {
        videoControls?.bindVideoManageForms(scope);
      }

      function bindVideoArticleRequestButtons(scope) {
        videoControls?.bindVideoArticleRequestButtons(scope);
      }

      function bindVideoTranscriptRequestButtons(scope) {
        videoControls?.bindVideoTranscriptRequestButtons(scope);
      }

      function bindVideoTranscriptCopyButtons(scope) {
        videoControls?.bindVideoTranscriptCopyButtons(scope);
      }

      function bindVideoArticlePreviewLoadButtons(scope) {
        videoControls?.bindVideoArticlePreviewLoadButtons(scope);
      }

      function bindArticlePreviewModals(scope) {
        videoControls?.bindArticlePreviewModals(scope);
      }

      function bindCopyButtons(scope) {
        videoControls?.bindCopyButtons(scope);
      }

      function bindCollapsibles(scope) {
        videoControls?.bindCollapsibles(scope);
      }

      function bindThumbPreviews(scope) {
        videoControls?.bindThumbPreviews(scope);
      }

      function bindYouTubeEmbeds(scope) {
        youtubeEmbed?.bindYouTubeEmbeds(scope);
      }

      function revealPageShell() {
        if (navTransitionController && typeof navTransitionController.revealPageShell === "function") {
          navTransitionController.revealPageShell();
          return;
        }
        const shell = document.querySelector("[data-page-shell]");
        if (!(shell instanceof HTMLElement)) return;
        shell.classList.add("is-visible");
        shell.classList.remove("is-leaving");
      }

      function bindNavTransitions(scope) {
        if (navTransitionController && typeof navTransitionController.bindNavTransitions === "function") {
          navTransitionController.bindNavTransitions(scope);
        }
      }

      function bindCategorySortable(scope) {
        categoryControls?.bindCategorySortable(scope);
      }

      function bindChannelMoveCategory(scope) {
        categoryControls?.bindChannelMoveCategory(scope);
      }

      function bindCategoryFilterReset(scope) {
        categoryControls?.bindCategoryFilterReset(scope);
      }

      function bindCategoryRename() {
        categoryControls?.bindCategoryRename();
      }

      function initChannelImportForm() {
        const form = document.getElementById("channel-import-form");
        if (!form || form.dataset.importBound === "1") return;
        form.dataset.importBound = "1";
        form.addEventListener("submit", async (e) => {
          e.preventDefault();
          const fileInput = form.querySelector('input[name="import_file"]');
          if (!fileInput || !fileInput.files || fileInput.files.length === 0) {
            showUiToast(form.dataset.toastNoFile || "Please select a file.", "info");
            return;
          }
          const submitBtn = form.querySelector('button[type="submit"]');
          if (submitBtn) submitBtn.disabled = true;
          try {
            const fd = new FormData();
            fd.append("import_file", fileInput.files[0]);
            const resp = await fetch("/api/channels/import", { method: "POST", body: fd });
            const data = await resp.json();
            if (resp.ok && data.ok) {
              let msg;
              if (data.created_categories > 0) {
                msg = (form.dataset.toastSuccessCat || "")
                  .replace("{added}", data.added)
                  .replace("{duplicate}", data.duplicate)
                  .replace("{invalid}", data.invalid)
                  .replace("{created_categories}", data.created_categories);
              } else {
                msg = (form.dataset.toastSuccess || "")
                  .replace("{added}", data.added)
                  .replace("{duplicate}", data.duplicate)
                  .replace("{invalid}", data.invalid);
              }
              showUiToast(msg, "success");
              fileInput.value = "";
              const statusInput = document.querySelector('[data-channel-status-input]');
              const status = statusInput ? statusInput.value : "active";
              if (typeof htmx !== "undefined") {
                htmx.ajax("GET", "/views/channel-list?status=" + encodeURIComponent(status), {
                  target: "#channel-list-wrap",
                  swap: "innerHTML",
                });
              }
            } else {
              showUiToast(form.dataset.toastFailed || "Import failed.", "error");
            }
          } catch (_err) {
            showUiToast(form.dataset.toastFailed || "Import failed.", "error");
          } finally {
            if (submitBtn) submitBtn.disabled = false;
          }
        });
      }

      document.addEventListener("DOMContentLoaded", () => {
        const themeState = getThemeState();
        applyTheme(themeState.mode, themeState.tone, { persist: false });
        bindSystemThemeObserver();
        hydrateUiScope(document);
        bindChannelReactivateToasts();
        bindChannelMetadataToasts();
        bindVideoDownloadBulkToasts();
        bindVideoArticleRequestToasts();
        bindVideoTranscriptRequestToasts();
        bindLlmRuntimeToasts();
        startDownloadProgressPolling();
        bindDownloadSettingsErrorHandlers();
        bindQueueRetryButtons(document);
        bindQueueClearButtons(document);
        startQueuePolling();
        startVideoDetailAutoRefresh();
        startChannelListAutoRefresh();
        startLlmRuntimeAutoRefresh();
        bindNavTransitions(document);
        bindCategoryRename();
        initChannelImportForm();
        revealPageShell();
      });
      window.addEventListener("pageshow", () => {
        revealPageShell();
      });
      document.addEventListener("htmx:afterRequest", (event) => {
        if (!event.detail.successful) return;
        const requestElt = event.detail?.requestConfig?.elt;
        if (
          requestElt instanceof Element
          && requestElt.hasAttribute("data-skip-save-toast")
        ) {
          return;
        }
        const target = event.target instanceof Element ? event.target : null;
        if (target?.hasAttribute("data-skip-save-toast")) return;
        let source = null;
        if (requestElt instanceof Element) {
          source = requestElt.closest("[data-save-toast]");
        }
        if (!source && target instanceof Element) {
          source = target.closest("[data-save-toast]");
        }
        const baseMessage = source?.getAttribute("data-save-toast");
        if (baseMessage) {
          const payload = parseJsonSafe(event.detail?.xhr?.responseText || "");
          const message = buildSaveToastMessage(baseMessage, payload);
          showUiToast(message, "success");
        }
      });
      document.addEventListener("htmx:afterSwap", (event) => {
        hydrateUiScope(event.target);
        bindCategoryRename();
      });
    })();
