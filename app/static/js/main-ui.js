    (() => {
      const PAGE_FADE_ENABLE_KEY = "brieftube.enableNextPageFade";
      const NAV_FADE_DURATION_MS = 120;
      const DOWNLOAD_EVENT_CURSOR_KEY = "brieftube.download.lastEventId";
      const DOWNLOAD_PROGRESS_POLL_INTERVAL_MS = 5000;
      const QUEUE_POLL_INTERVAL_MS = 2000;
      const VIDEO_DETAIL_POLL_INTERVAL_MS = 3000;
      const CHANNEL_LIST_POLL_INTERVAL_MS = 15000;
      const LLM_RUNTIME_POLL_INTERVAL_MS = 10000;
      const MATCH_CLASSES = ["bg-amber-50", "ring-1", "ring-inset", "ring-amber-200"];
      const ACTIVE_CLASSES = ["bg-indigo-100", "ring-indigo-300"];
      const themeController = window.BrieftubeTheme || null;
      const navTransitionController = window.BrieftubeNavTransition || null;
      if (navTransitionController && typeof navTransitionController.configure === "function") {
        navTransitionController.configure({
          pageFadeKey: PAGE_FADE_ENABLE_KEY,
          navFadeDurationMs: NAV_FADE_DURATION_MS,
        });
      }

      function getStoredValue(key) {
        try {
          return localStorage.getItem(key);
        } catch (_err) {
          return null;
        }
      }

      function setStoredValue(key, value) {
        try {
          localStorage.setItem(key, value);
        } catch (_err) {
          // ignore storage write errors and keep runtime-only state.
        }
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

      function isDocumentVisible() {
        return document.hidden !== true;
      }

      function parseHtmlFragment(html) {
        const template = document.createElement("template");
        template.innerHTML = String(html || "").trim();
        return template.content;
      }

      function clearMatchStyles(row) {
        row.classList.remove(...MATCH_CLASSES, ...ACTIVE_CLASSES);
      }

      function updateCount(countNode, current, total) {
        if (!countNode) return;
        if (total <= 0) {
          countNode.textContent = "0 / 0";
          return;
        }
        countNode.textContent = `${current + 1} / ${total}`;
      }

      function initChannelSearch(root) {
        if (root.dataset.channelSearchBound === "1") return;
        root.dataset.channelSearchBound = "1";

        const input = root.querySelector("[data-channel-search-input]");
        const prevButton = root.querySelector("[data-channel-search-prev]");
        const nextButton = root.querySelector("[data-channel-search-next]");
        const countNode = root.querySelector("[data-channel-search-count]");
        const rows = Array.from(root.querySelectorAll("[data-channel-row]"));

        let matches = [];
        let activeIndex = -1;

        function applyActive() {
          matches.forEach((row, idx) => {
            row.classList.remove(...ACTIVE_CLASSES);
            if (idx === activeIndex) {
              row.classList.add(...ACTIVE_CLASSES);
              row.scrollIntoView({ behavior: "smooth", block: "nearest" });
            }
          });
          updateCount(countNode, activeIndex, matches.length);
        }

        function recalculateMatches() {
          const query = (input?.value || "").trim().toLowerCase();
          matches = [];
          activeIndex = -1;

          rows.forEach((row) => {
            clearMatchStyles(row);
            if (!query) return;
            const haystack = (row.getAttribute("data-search-text") || "").toLowerCase();
            if (haystack.includes(query)) {
              row.classList.add(...MATCH_CLASSES);
              matches.push(row);
            }
          });

          if (matches.length > 0) {
            activeIndex = 0;
            applyActive();
          } else {
            updateCount(countNode, -1, 0);
          }
        }

        function step(direction) {
          if (!matches.length) return;
          activeIndex = (activeIndex + direction + matches.length) % matches.length;
          applyActive();
        }

        input?.addEventListener("input", recalculateMatches);
        input?.addEventListener("keydown", (event) => {
          if (event.key === "Enter") {
            event.preventDefault();
            step(event.shiftKey ? -1 : 1);
          }
        });
        prevButton?.addEventListener("click", () => step(-1));
        nextButton?.addEventListener("click", () => step(1));

        recalculateMatches();
      }

      function bindChannelSearch(scope) {
        const root = scope instanceof Element ? scope : document;
        root.querySelectorAll("[data-channel-search]").forEach(initChannelSearch);
      }

      function initChannelManageForm(form) {
        if (form.dataset.channelManageBound === "1") return;
        form.dataset.channelManageBound = "1";

        const selectAll = form.querySelector("[data-channel-select-all]");
        const items = Array.from(form.querySelectorAll("[data-channel-select-item]"));
        const bulkButtons = Array.from(form.querySelectorAll("[data-channel-bulk-submit]"));
        if (!selectAll || !items.length) return;

        const sync = () => {
          const checkedCount = items.filter((item) => item.checked).length;
          selectAll.checked = checkedCount > 0 && checkedCount === items.length;
          selectAll.indeterminate = checkedCount > 0 && checkedCount < items.length;
          bulkButtons.forEach((button) => {
            button.disabled = checkedCount === 0;
          });
        };

        selectAll.addEventListener("change", () => {
          items.forEach((item) => {
            item.checked = selectAll.checked;
          });
          sync();
        });
        items.forEach((item) => item.addEventListener("change", sync));
        sync();
      }

      function bindChannelManageForms(scope) {
        const root = scope instanceof Element ? scope : document;
        root.querySelectorAll("[data-channel-manage-form]").forEach(initChannelManageForm);
      }

      function applyChannelMetaToggleState(toggle, panel, isOpen) {
        if (!(toggle instanceof HTMLButtonElement) || !(panel instanceof HTMLElement)) return;
        const openLabel = toggle.dataset.labelOpen || "Details";
        const closeLabel = toggle.dataset.labelClose || "Collapse";
        toggle.setAttribute("aria-expanded", isOpen ? "true" : "false");
        toggle.textContent = isOpen ? closeLabel : openLabel;
        panel.classList.toggle("hidden", !isOpen);
      }

      function initChannelMetaAccordion(root) {
        if (!(root instanceof HTMLElement)) return;
        if (root.dataset.channelMetaAccordionBound === "1") return;
        root.dataset.channelMetaAccordionBound = "1";

        root.querySelectorAll("[data-channel-meta-item]").forEach((item) => {
          const toggle = item.querySelector("[data-channel-meta-toggle]");
          const panel = item.querySelector("[data-channel-meta-panel]");
          applyChannelMetaToggleState(toggle, panel, false);
        });

        root.addEventListener("click", (event) => {
          const target = event.target;
          if (!(target instanceof Element)) return;
          const toggle = target.closest("[data-channel-meta-toggle]");
          if (!(toggle instanceof HTMLButtonElement)) return;
          if (!root.contains(toggle)) return;
          event.preventDefault();

          const item = toggle.closest("[data-channel-meta-item]");
          if (!(item instanceof HTMLElement)) return;
          const panel = item.querySelector("[data-channel-meta-panel]");
          if (!(panel instanceof HTMLElement)) return;
          const shouldOpen = toggle.getAttribute("aria-expanded") !== "true";

          root.querySelectorAll("[data-channel-meta-item]").forEach((node) => {
            const rowToggle = node.querySelector("[data-channel-meta-toggle]");
            const rowPanel = node.querySelector("[data-channel-meta-panel]");
            applyChannelMetaToggleState(rowToggle, rowPanel, false);
          });

          if (shouldOpen) {
            applyChannelMetaToggleState(toggle, panel, true);
          }
        });
      }

      function bindChannelMetaAccordion(scope) {
        const root = scope instanceof Element ? scope : document;
        root.querySelectorAll("[data-channel-meta-root]").forEach((node) => {
          initChannelMetaAccordion(node);
        });
      }

      function initChannelAvatarImage(img) {
        if (!(img instanceof HTMLImageElement)) return;
        if (img.dataset.avatarBound === "1") return;
        img.dataset.avatarBound = "1";
        const fallback = img.parentElement?.querySelector("[data-channel-avatar-fallback]");
        if (!(fallback instanceof HTMLElement)) return;
        const revealFallback = () => {
          img.classList.add("hidden");
          fallback.classList.remove("hidden");
          fallback.classList.add("flex");
        };
        if (!img.complete) {
          img.addEventListener("error", revealFallback);
          return;
        }
        if (img.naturalWidth <= 0) {
          revealFallback();
        }
      }

      function bindChannelAvatars(scope) {
        const root = scope instanceof Element ? scope : document;
        root.querySelectorAll("[data-channel-avatar-img]").forEach((img) => {
          initChannelAvatarImage(img);
        });
      }

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
      const DOWNLOAD_UI_TEXT = {
        modalTitle: "영상 다운로드",
        modalDesc: "{title} 영상을 백그라운드로 다운로드합니다.",
        modalQuality: "화질 상한",
        modalOverwrite: "기존 파일 덮어쓰기",
        modalCancel: "취소",
        modalSubmit: "다운로드 시작",
        toastQueued: "다운로드 대기열에 추가되었습니다.",
        toastDuplicate: "이미 다운로드가 진행 중입니다.",
        toastFfmpegMissing: "ffmpeg가 설치되지 않아 다운로드를 시작할 수 없습니다.",
        toastRequestFailed: "다운로드 요청에 실패했습니다.",
        toastOutputFileMissing: "출력 파일을 찾을 수 없습니다.",
        toastOutputJobMissing: "다운로드 이력을 찾을 수 없습니다.",
        toastOutputDirMissing: "저장 폴더를 찾을 수 없습니다.",
        toastOutputOpenFailed: "파일 열기에 실패했습니다.",
        toastRetryQueued: "다운로드 재시도를 등록했습니다.",
        toastRetryFailed: "재시도 요청에 실패했습니다.",
        toastCompleted: "다운로드 완료 {count}건",
        toastFailed: "다운로드 실패 {count}건 ({videos})",
        toastMixed: "다운로드 결과: 완료 {success}건, 실패 {failed}건",
        toastMore: "외 {count}건",
        badgeInProgress: "진행 {count}",
        detailTitle: "{title} 상세 정보",
        detailNone: "-",
        detailClose: "닫기",
        detailVideoId: "영상 ID",
        detailStatus: "상태",
        detailRequestedAt: "요청 시각",
        detailUpdatedAt: "갱신 시각",
        detailFinishedAt: "완료 시각",
        detailSize: "파일 크기(bytes)",
        detailErrorCode: "오류 코드",
        detailOutputPath: "출력 파일",
        detailCopyOutput: "파일 경로 복사",
        detailErrorMessage: "오류 메시지",
        detailCopyError: "오류 메시지 복사",
        toastCopiedOutput: "출력 파일 경로를 복사했습니다.",
        toastCopiedError: "오류 메시지를 복사했습니다.",
        ...(UI_BOOTSTRAP.download || {}),
      };
      const DOWNLOAD_SETTINGS_UI_TEXT = {
        pathErrorEmpty: "다운로드 저장 경로를 입력해 주세요.",
        pathErrorMustBeAbsolute: "다운로드 저장 경로는 절대 경로여야 합니다.",
        pathErrorInvalid: "다운로드 저장 경로 형식이 올바르지 않습니다.",
        pathErrorNotFound: "다운로드 저장 경로가 존재하지 않습니다.",
        pathErrorNotDirectory: "다운로드 저장 경로는 디렉터리여야 합니다.",
        pathErrorNotWritable: "다운로드 저장 경로에 쓰기 권한이 없습니다.",
        pathErrorGeneric: "다운로드 저장 경로를 확인해 주세요.",
        ...(UI_BOOTSTRAP.downloadSettings || {}),
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
        const root = document.body;
        if (!root || root.dataset.channelReactivateToastBound === "1") return;
        root.dataset.channelReactivateToastBound = "1";
        root.addEventListener("channel-reactivate-toast", (event) => {
          const detail = event.detail;
          const message = typeof detail === "string" ? detail : detail?.message;
          const tone = detail && typeof detail === "object" && detail.tone === "error"
            ? "error"
            : "success";
          showUiToast(message, tone);
        });
      }

      function bindChannelMetadataToasts() {
        const root = document.body;
        if (!root || root.dataset.channelMetadataToastBound === "1") return;
        root.dataset.channelMetadataToastBound = "1";
        root.addEventListener("channel-metadata-toast", (event) => {
          const detail = event.detail;
          const message = typeof detail === "string" ? detail : detail?.message;
          const tone = detail && typeof detail === "object" && detail.tone === "error"
            ? "error"
            : detail && typeof detail === "object" && detail.tone === "info"
              ? "info"
              : "success";
          showUiToast(message, tone);
        });
      }

      function bindVideoDownloadBulkToasts() {
        const root = document.body;
        if (!root || root.dataset.videoDownloadBulkToastBound === "1") return;
        root.dataset.videoDownloadBulkToastBound = "1";
        root.addEventListener("video-download-bulk-toast", (event) => {
          const detail = event.detail;
          const message = typeof detail === "string" ? detail : detail?.message;
          const tone = detail && typeof detail === "object" && detail.tone === "error"
            ? "error"
            : detail && typeof detail === "object" && detail.tone === "info"
              ? "info"
              : "success";
          showUiToast(message, tone);
        });
      }

      function bindVideoArticleRequestToasts() {
        const root = document.body;
        if (!root || root.dataset.videoArticleRequestToastBound === "1") return;
        root.dataset.videoArticleRequestToastBound = "1";
        root.addEventListener("video-article-request-toast", (event) => {
          const detail = event.detail;
          const message = typeof detail === "string" ? detail : detail?.message;
          const tone = detail && typeof detail === "object" && detail.tone === "error"
            ? "error"
            : detail && typeof detail === "object" && detail.tone === "info"
              ? "info"
              : "success";
          showUiToast(message, tone);
        });
      }

      function bindVideoTranscriptRequestToasts() {
        const root = document.body;
        if (!root || root.dataset.videoTranscriptRequestToastBound === "1") return;
        root.dataset.videoTranscriptRequestToastBound = "1";
        root.addEventListener("video-transcript-request-toast", (event) => {
          const detail = event.detail;
          const message = typeof detail === "string" ? detail : detail?.message;
          const tone = detail && typeof detail === "object" && detail.tone === "error"
            ? "error"
            : detail && typeof detail === "object" && detail.tone === "info"
              ? "info"
              : "success";
          showUiToast(message, tone);
        });
      }

      function bindLlmRuntimeToasts() {
        const root = document.body;
        if (!root || root.dataset.llmRuntimeToastBound === "1") return;
        root.dataset.llmRuntimeToastBound = "1";
        root.addEventListener("llm-runtime-toast", (event) => {
          const detail = event.detail;
          const message = typeof detail === "string" ? detail : detail?.message;
          const tone = detail && typeof detail === "object" && detail.tone === "error"
            ? "error"
            : detail && typeof detail === "object" && detail.tone === "info"
              ? "info"
              : "success";
          showUiToast(message, tone);
        });
      }

      function resolveDownloadPathErrorMessage(detail) {
        const code = String(detail || "").trim().toLowerCase();
        if (code === "download_path_empty") return DOWNLOAD_SETTINGS_UI_TEXT.pathErrorEmpty;
        if (code === "download_path_must_be_absolute") return DOWNLOAD_SETTINGS_UI_TEXT.pathErrorMustBeAbsolute;
        if (code === "download_path_invalid") return DOWNLOAD_SETTINGS_UI_TEXT.pathErrorInvalid;
        if (code === "download_path_not_found") return DOWNLOAD_SETTINGS_UI_TEXT.pathErrorNotFound;
        if (code === "download_path_not_directory") return DOWNLOAD_SETTINGS_UI_TEXT.pathErrorNotDirectory;
        if (code === "download_path_not_writable") return DOWNLOAD_SETTINGS_UI_TEXT.pathErrorNotWritable;
        if (code.length > 0 && !code.includes(" ")) return DOWNLOAD_SETTINGS_UI_TEXT.pathErrorGeneric;
        if (code.length > 0) return code;
        return DOWNLOAD_SETTINGS_UI_TEXT.pathErrorGeneric;
      }

      function resolveDownloadRequestErrorMessage(payload) {
        const code = String(payload?.code || "").trim().toLowerCase();
        if (code === "ffmpeg_missing") return DOWNLOAD_UI_TEXT.toastFfmpegMissing;
        if (code.startsWith("download_path_")) {
          return resolveDownloadPathErrorMessage(code);
        }
        const message = String(payload?.message || payload?.detail || "").trim();
        if (message) return message;
        return DOWNLOAD_UI_TEXT.toastRequestFailed;
      }

      function resolveDownloadOutputOpenErrorMessage(payload, statusCode = 0) {
        const code = String(payload?.code || "").trim().toLowerCase();
        if (code === "download_job_not_found") return DOWNLOAD_UI_TEXT.toastOutputJobMissing;
        if (code === "download_dir_not_found") return DOWNLOAD_UI_TEXT.toastOutputDirMissing;
        if (code === "download_file_not_found") return DOWNLOAD_UI_TEXT.toastOutputFileMissing;
        if (statusCode === 404) return DOWNLOAD_UI_TEXT.toastOutputFileMissing;
        const message = String(payload?.message || payload?.detail || "").trim();
        if (message) return message;
        return DOWNLOAD_UI_TEXT.toastOutputOpenFailed;
      }

      function setDownloadOutputDirError(form, message) {
        if (!(form instanceof HTMLFormElement)) return;
        const input = form.querySelector("input[name='download_output_dir']");
        const errorNode = form.querySelector("[data-download-output-dir-error]");
        if (input instanceof HTMLInputElement) {
          if (message) {
            input.setAttribute("aria-invalid", "true");
            input.classList.add("border-rose-400", "focus:border-rose-500", "focus:ring-rose-500");
          } else {
            input.removeAttribute("aria-invalid");
            input.classList.remove("border-rose-400", "focus:border-rose-500", "focus:ring-rose-500");
          }
        }
        if (errorNode instanceof HTMLElement) {
          errorNode.textContent = message || "";
          errorNode.classList.toggle("hidden", !message);
        }
      }

      function getDownloadEventCursor() {
        const raw = getStoredValue(DOWNLOAD_EVENT_CURSOR_KEY);
        const parsed = Number.parseInt(String(raw || ""), 10);
        if (Number.isNaN(parsed) || parsed < 0) return 0;
        return parsed;
      }

      function setDownloadEventCursor(value) {
        const safe = Math.max(0, Number.parseInt(String(value || "0"), 10) || 0);
        setStoredValue(DOWNLOAD_EVENT_CURSOR_KEY, String(safe));
      }

      function formatDownloadPreview(items) {
        const names = items
          .map((item) => String(item?.video_title || item?.video_id || "").trim())
          .filter((name) => name.length > 0);
        const preview = names.slice(0, 2);
        const remain = Math.max(0, names.length - preview.length);
        if (remain > 0) {
          preview.push(formatTemplate(DOWNLOAD_UI_TEXT.toastMore, { count: remain }));
        }
        return preview.join(", ");
      }

      function buildDownloadEventToast(events) {
        const succeeded = events.filter((event) => event?.event_type === "succeeded");
        const failed = events.filter((event) => event?.event_type === "failed");
        if (!succeeded.length && !failed.length) return null;
        if (succeeded.length && failed.length) {
          return {
            tone: "error",
            message: formatTemplate(DOWNLOAD_UI_TEXT.toastMixed, {
              success: succeeded.length,
              failed: failed.length,
            }),
          };
        }
        if (failed.length) {
          return {
            tone: "error",
            message: formatTemplate(DOWNLOAD_UI_TEXT.toastFailed, {
              count: failed.length,
              videos: formatDownloadPreview(failed),
            }),
          };
        }
        return {
          tone: "success",
          message: formatTemplate(DOWNLOAD_UI_TEXT.toastCompleted, {
            count: succeeded.length,
          }),
        };
      }

      function updateDownloadNavBadge(activeCount) {
        const nodes = document.querySelectorAll("[data-download-nav-badge]");
        const count = Math.max(0, Number.parseInt(String(activeCount || "0"), 10) || 0);
        nodes.forEach((node) => {
          if (!(node instanceof HTMLElement)) return;
          if (count <= 0) {
            node.classList.add("hidden");
            node.textContent = "0";
            return;
          }
          node.classList.remove("hidden");
          node.textContent = formatTemplate(DOWNLOAD_UI_TEXT.badgeInProgress, { count });
        });
      }

      function ensureVideoDownloadModal() {
        let root = document.getElementById("video-download-modal");
        if (root) return root;

        root = document.createElement("div");
        root.id = "video-download-modal";
        root.className = "fixed inset-0 z-[76] hidden items-center justify-center px-4";
        root.innerHTML = `
          <div class="absolute inset-0 bg-slate-900/45 backdrop-blur-sm" data-download-modal-backdrop></div>
          <div class="relative w-full max-w-md rounded-xl border border-slate-200 bg-white p-5 shadow-2xl">
            <h3 class="text-base font-semibold text-slate-900" data-download-modal-title></h3>
            <p class="mt-2 text-sm text-slate-600" data-download-modal-desc></p>
            <div class="mt-4 space-y-3">
              <label class="block text-sm text-slate-600">
                <span class="block">${DOWNLOAD_UI_TEXT.modalQuality}</span>
                <select data-download-modal-quality class="mt-1 w-full rounded-md border border-slate-300 px-3 py-2 text-sm">
                  <option value="2160">2160p</option>
                  <option value="1440">1440p</option>
                  <option value="1080">1080p</option>
                  <option value="720">720p</option>
                  <option value="480">480p</option>
                </select>
              </label>
              <label class="flex items-center gap-2 text-sm text-slate-700">
                <input type="checkbox" data-download-modal-overwrite />
                <span>${DOWNLOAD_UI_TEXT.modalOverwrite}</span>
              </label>
            </div>
            <div class="mt-4 flex justify-end gap-2">
              <button type="button" class="rounded-md border border-slate-300 bg-white px-3 py-2 text-sm font-medium text-slate-700 hover:bg-slate-50" data-download-modal-cancel></button>
              <button type="button" class="rounded-md bg-indigo-600 px-3 py-2 text-sm font-medium text-white hover:bg-indigo-700" data-download-modal-submit></button>
            </div>
          </div>
        `;
        document.body.appendChild(root);
        return root;
      }

      async function requestVideoDownload(videoId, quality, overwrite) {
        const response = await fetch(`/api/videos/${encodeURIComponent(videoId)}/downloads`, {
          method: "POST",
          headers: {
            "Content-Type": "application/json",
            "Accept": "application/json",
          },
          body: JSON.stringify({
            quality,
            overwrite,
          }),
        });
        const payload = await response.json().catch(() => ({}));
        return { response, payload };
      }

      function openVideoDownloadModal(options) {
        const root = ensureVideoDownloadModal();
        const titleNode = root.querySelector("[data-download-modal-title]");
        const descNode = root.querySelector("[data-download-modal-desc]");
        const qualitySelect = root.querySelector("[data-download-modal-quality]");
        const overwriteInput = root.querySelector("[data-download-modal-overwrite]");
        const cancelButton = root.querySelector("[data-download-modal-cancel]");
        const submitButton = root.querySelector("[data-download-modal-submit]");
        if (
          !(titleNode instanceof HTMLElement)
          || !(descNode instanceof HTMLElement)
          || !(qualitySelect instanceof HTMLSelectElement)
          || !(overwriteInput instanceof HTMLInputElement)
          || !(cancelButton instanceof HTMLButtonElement)
          || !(submitButton instanceof HTMLButtonElement)
        ) {
          return;
        }

        const videoId = String(options?.videoId || "").trim();
        const videoTitle = String(options?.videoTitle || "").trim() || videoId;
        const defaultQuality = String(options?.defaultQuality || "1080").trim();
        const defaultOverwrite = options?.defaultOverwrite === true;
        if (!videoId) return;
        if (root.dataset.modalOpen === "1") return;

        titleNode.textContent = DOWNLOAD_UI_TEXT.modalTitle;
        descNode.textContent = formatTemplate(DOWNLOAD_UI_TEXT.modalDesc, { title: videoTitle });
        cancelButton.textContent = DOWNLOAD_UI_TEXT.modalCancel;
        submitButton.textContent = DOWNLOAD_UI_TEXT.modalSubmit;
        qualitySelect.value = ["2160", "1440", "1080", "720", "480"].includes(defaultQuality)
          ? defaultQuality
          : "1080";
        overwriteInput.checked = defaultOverwrite;

        root.dataset.videoId = videoId;
        root.dataset.modalOpen = "1";
        root.classList.remove("hidden");
        root.classList.add("flex");

        const cleanup = () => {
          cancelButton.removeEventListener("click", onCancel);
          submitButton.removeEventListener("click", onSubmit);
          root.removeEventListener("click", onBackdrop);
          document.removeEventListener("keydown", onKeyDown);
        };
        const close = (force = false) => {
          if (!force && root.dataset.submitting === "1") return;
          root.classList.add("hidden");
          root.classList.remove("flex");
          delete root.dataset.videoId;
          delete root.dataset.modalOpen;
          cleanup();
        };

        const onBackdrop = (event) => {
          const target = event.target;
          if (
            target === root
            || (target instanceof Element && target.hasAttribute("data-download-modal-backdrop"))
          ) {
            close();
          }
        };
        const onKeyDown = (event) => {
          if (event.key === "Escape") {
            close();
          }
        };
        const onCancel = () => {
          close();
        };
        const onSubmit = async () => {
          if (root.dataset.submitting === "1") return;
          root.dataset.submitting = "1";
          submitButton.disabled = true;
          cancelButton.disabled = true;
          try {
            const { response, payload } = await requestVideoDownload(
              videoId,
              qualitySelect.value,
              overwriteInput.checked,
            );
            if (response.status === 202 && payload?.queued === true) {
              showUiToast(DOWNLOAD_UI_TEXT.toastQueued, "success");
              close(true);
              void pollDownloadProgress();
              return;
            }
            if (payload?.duplicate === true) {
              showUiToast(DOWNLOAD_UI_TEXT.toastDuplicate, "success");
              close(true);
              void pollDownloadProgress();
              return;
            }
            showUiToast(resolveDownloadRequestErrorMessage(payload), "error");
          } catch (_error) {
            showUiToast(DOWNLOAD_UI_TEXT.toastRequestFailed, "error");
          } finally {
            delete root.dataset.submitting;
            submitButton.disabled = false;
            cancelButton.disabled = false;
          }
        };

        cancelButton.addEventListener("click", onCancel);
        submitButton.addEventListener("click", onSubmit);
        root.addEventListener("click", onBackdrop);
        document.addEventListener("keydown", onKeyDown);
      }

      function initVideoDownloadButton(button) {
        if (button.dataset.downloadButtonBound === "1") return;
        button.dataset.downloadButtonBound = "1";
        button.addEventListener("click", () => {
          if (button.dataset.downloadFfmpeg === "0") {
            showUiToast(DOWNLOAD_UI_TEXT.toastFfmpegMissing, "error");
            return;
          }
          openVideoDownloadModal({
            videoId: button.dataset.videoId,
            videoTitle: button.dataset.videoTitle,
            defaultQuality: button.dataset.downloadDefaultQuality,
            defaultOverwrite: button.dataset.downloadDefaultOverwrite === "1",
          });
        });
      }

      function bindVideoDownloadButtons(scope) {
        const root = scope instanceof Element ? scope : document;
        root.querySelectorAll("[data-video-download-open]").forEach((node) => {
          if (node instanceof HTMLButtonElement) {
            initVideoDownloadButton(node);
          }
        });
      }

      function ensureDownloadDetailModal() {
        let root = document.getElementById("download-detail-modal");
        if (root) return root;

        root = document.createElement("div");
        root.id = "download-detail-modal";
        root.className = "fixed inset-0 z-[77] hidden items-center justify-center px-4";
        root.innerHTML = `
          <div class="absolute inset-0 bg-slate-900/45 backdrop-blur-sm" data-download-detail-backdrop></div>
          <div class="relative w-full max-w-2xl rounded-xl border border-slate-200 bg-white p-5 shadow-2xl">
            <div class="flex items-start justify-between gap-3">
              <h3 class="text-base font-semibold text-slate-900" data-download-detail-title></h3>
              <button type="button" class="shrink-0 whitespace-nowrap rounded-md border border-slate-300 bg-white px-2.5 py-1 text-xs font-medium text-slate-700 hover:bg-slate-50" data-download-detail-close>${DOWNLOAD_UI_TEXT.detailClose}</button>
            </div>
            <div class="mt-4 grid gap-3 text-xs text-slate-700 sm:grid-cols-2">
              <div><span class="font-semibold text-slate-900">${DOWNLOAD_UI_TEXT.detailVideoId}:</span> <span data-download-detail-video-id></span></div>
              <div><span class="font-semibold text-slate-900">${DOWNLOAD_UI_TEXT.detailStatus}:</span> <span data-download-detail-status></span></div>
              <div><span class="font-semibold text-slate-900">${DOWNLOAD_UI_TEXT.detailRequestedAt}:</span> <span data-download-detail-requested-at></span></div>
              <div><span class="font-semibold text-slate-900">${DOWNLOAD_UI_TEXT.detailUpdatedAt}:</span> <span data-download-detail-updated-at></span></div>
              <div><span class="font-semibold text-slate-900">${DOWNLOAD_UI_TEXT.detailFinishedAt}:</span> <span data-download-detail-finished-at></span></div>
              <div><span class="font-semibold text-slate-900">${DOWNLOAD_UI_TEXT.detailSize}:</span> <span data-download-detail-file-size></span></div>
              <div><span class="font-semibold text-slate-900">${DOWNLOAD_UI_TEXT.detailErrorCode}:</span> <span data-download-detail-error-code></span></div>
            </div>
            <div class="mt-4 rounded-lg border border-slate-200 bg-slate-50 p-3">
              <div class="mb-1 flex items-center justify-between gap-2">
                <span class="text-xs font-semibold text-slate-900">${DOWNLOAD_UI_TEXT.detailOutputPath}</span>
                <button type="button" class="rounded border border-slate-300 bg-white px-2 py-0.5 text-[11px] font-medium text-slate-700 hover:bg-slate-100" data-download-detail-copy-output>${DOWNLOAD_UI_TEXT.detailCopyOutput}</button>
              </div>
              <a data-download-detail-output-link class="hidden break-all text-xs text-indigo-700 hover:text-indigo-900" target="_blank" rel="noopener noreferrer"></a>
              <div data-download-detail-output-empty class="text-xs text-slate-600"></div>
            </div>
            <div class="mt-3 rounded-lg border border-slate-200 bg-slate-50 p-3">
              <div class="mb-1 flex items-center justify-between gap-2">
                <span class="text-xs font-semibold text-slate-900">${DOWNLOAD_UI_TEXT.detailErrorMessage}</span>
                <button type="button" class="rounded border border-slate-300 bg-white px-2 py-0.5 text-[11px] font-medium text-slate-700 hover:bg-slate-100" data-download-detail-copy-error>${DOWNLOAD_UI_TEXT.detailCopyError}</button>
              </div>
              <pre data-download-detail-error-message class="max-h-40 overflow-auto whitespace-pre-wrap break-all text-xs text-rose-700"></pre>
            </div>
          </div>
        `;
        document.body.appendChild(root);

        const close = () => {
          root.classList.add("hidden");
          root.classList.remove("flex");
          root.dataset.modalOpen = "0";
        };

        root.addEventListener("click", (event) => {
          const target = event.target;
          if (
            target === root
            || (target instanceof Element && target.hasAttribute("data-download-detail-backdrop"))
            || (target instanceof Element && target.hasAttribute("data-download-detail-close"))
          ) {
            close();
          }
        });

        document.addEventListener("keydown", (event) => {
          if (event.key === "Escape" && root.dataset.modalOpen === "1") {
            close();
          }
        });

        const copyOutputButton = root.querySelector("[data-download-detail-copy-output]");
        const copyErrorButton = root.querySelector("[data-download-detail-copy-error]");
        const outputLinkNode = root.querySelector("[data-download-detail-output-link]");
        if (copyOutputButton instanceof HTMLButtonElement) {
          copyOutputButton.addEventListener("click", async () => {
            const value = String(root.dataset.currentOutputPath || "").trim();
            if (!value) return;
            try {
              await navigator.clipboard.writeText(value);
              showUiToast(DOWNLOAD_UI_TEXT.toastCopiedOutput, "success");
            } catch (_error) {
              // ignore clipboard failures
            }
          });
        }
        if (copyErrorButton instanceof HTMLButtonElement) {
          copyErrorButton.addEventListener("click", async () => {
            const value = String(root.dataset.currentErrorMessage || "").trim();
            if (!value) return;
            try {
              await navigator.clipboard.writeText(value);
              showUiToast(DOWNLOAD_UI_TEXT.toastCopiedError, "success");
            } catch (_error) {
              // ignore clipboard failures
            }
          });
        }
        if (outputLinkNode instanceof HTMLAnchorElement) {
          outputLinkNode.addEventListener("click", async (event) => {
            const href = outputLinkNode.getAttribute("href");
            if (!href) return;
            event.preventDefault();
            if (outputLinkNode.dataset.probeInFlight === "1") return;
            outputLinkNode.dataset.probeInFlight = "1";
            outputLinkNode.classList.add("pointer-events-none", "opacity-60");
            const popup = window.open("about:blank", "_blank", "noopener,noreferrer");
            let opened = false;
            try {
              const probeUrl = new URL(href, window.location.origin);
              probeUrl.searchParams.set("probe", "1");
              const response = await fetch(probeUrl.toString(), {
                method: "GET",
                headers: {
                  "Accept": "application/json",
                },
              });
              if (response.ok) {
                opened = true;
                if (popup && !popup.closed) {
                  popup.location.replace(href);
                } else {
                  window.location.assign(href);
                }
                return;
              }
              const payload = await response.json().catch(() => null);
              showUiToast(
                resolveDownloadOutputOpenErrorMessage(payload, response.status),
                "error",
              );
            } catch (_error) {
              showUiToast(DOWNLOAD_UI_TEXT.toastOutputOpenFailed, "error");
            } finally {
              if (!opened && popup && !popup.closed) {
                popup.close();
              }
              delete outputLinkNode.dataset.probeInFlight;
              outputLinkNode.classList.remove("pointer-events-none", "opacity-60");
            }
          });
        }

        return root;
      }

      function openDownloadDetailModal(options = {}) {
        const root = ensureDownloadDetailModal();
        const textOrNone = (value) => {
          const normalized = String(value || "").trim();
          return normalized.length > 0 ? normalized : DOWNLOAD_UI_TEXT.detailNone;
        };

        const videoTitle = String(options.videoTitle || "").trim();
        const videoId = textOrNone(options.videoId);
        const statusLabel = textOrNone(options.statusLabel);
        const requestedAt = textOrNone(options.requestedAt);
        const updatedAt = textOrNone(options.updatedAt);
        const finishedAt = textOrNone(options.finishedAt);
        const fileSize = textOrNone(options.fileSize);
        const outputPath = String(options.outputPath || "").trim();
        const outputUrl = String(options.outputUrl || "").trim();
        const errorCode = textOrNone(options.errorCode);
        const errorMessage = String(options.errorMessage || "").trim();

        const titleNode = root.querySelector("[data-download-detail-title]");
        const videoIdNode = root.querySelector("[data-download-detail-video-id]");
        const statusNode = root.querySelector("[data-download-detail-status]");
        const requestedNode = root.querySelector("[data-download-detail-requested-at]");
        const updatedNode = root.querySelector("[data-download-detail-updated-at]");
        const finishedNode = root.querySelector("[data-download-detail-finished-at]");
        const sizeNode = root.querySelector("[data-download-detail-file-size]");
        const errorCodeNode = root.querySelector("[data-download-detail-error-code]");
        const outputLinkNode = root.querySelector("[data-download-detail-output-link]");
        const outputEmptyNode = root.querySelector("[data-download-detail-output-empty]");
        const errorMessageNode = root.querySelector("[data-download-detail-error-message]");
        const copyOutputButton = root.querySelector("[data-download-detail-copy-output]");
        const copyErrorButton = root.querySelector("[data-download-detail-copy-error]");
        if (
          !(titleNode instanceof HTMLElement)
          || !(videoIdNode instanceof HTMLElement)
          || !(statusNode instanceof HTMLElement)
          || !(requestedNode instanceof HTMLElement)
          || !(updatedNode instanceof HTMLElement)
          || !(finishedNode instanceof HTMLElement)
          || !(sizeNode instanceof HTMLElement)
          || !(errorCodeNode instanceof HTMLElement)
          || !(outputLinkNode instanceof HTMLAnchorElement)
          || !(outputEmptyNode instanceof HTMLElement)
          || !(errorMessageNode instanceof HTMLElement)
          || !(copyOutputButton instanceof HTMLButtonElement)
          || !(copyErrorButton instanceof HTMLButtonElement)
        ) {
          return;
        }

        titleNode.textContent = formatTemplate(DOWNLOAD_UI_TEXT.detailTitle, { title: videoTitle || videoId });
        videoIdNode.textContent = videoId;
        statusNode.textContent = statusLabel;
        requestedNode.textContent = requestedAt;
        updatedNode.textContent = updatedAt;
        finishedNode.textContent = finishedAt;
        sizeNode.textContent = fileSize;
        errorCodeNode.textContent = errorCode;

        if (outputPath && outputUrl) {
          outputLinkNode.textContent = outputPath;
          outputLinkNode.href = outputUrl;
          outputLinkNode.classList.remove("hidden");
          outputEmptyNode.classList.add("hidden");
          outputEmptyNode.textContent = "";
          copyOutputButton.disabled = false;
        } else {
          outputLinkNode.textContent = "";
          outputLinkNode.removeAttribute("href");
          outputLinkNode.classList.add("hidden");
          outputEmptyNode.textContent = DOWNLOAD_UI_TEXT.detailNone;
          outputEmptyNode.classList.remove("hidden");
          copyOutputButton.disabled = true;
        }

        if (errorMessage) {
          errorMessageNode.textContent = errorMessage;
          copyErrorButton.disabled = false;
        } else {
          errorMessageNode.textContent = DOWNLOAD_UI_TEXT.detailNone;
          copyErrorButton.disabled = true;
        }

        root.dataset.currentOutputPath = outputPath;
        root.dataset.currentErrorMessage = errorMessage;
        root.dataset.modalOpen = "1";
        root.classList.remove("hidden");
        root.classList.add("flex");
      }

      function initDownloadDetailButton(button) {
        if (button.dataset.downloadDetailBound === "1") return;
        button.dataset.downloadDetailBound = "1";
        button.addEventListener("click", () => {
          openDownloadDetailModal({
            videoTitle: button.dataset.videoTitle,
            videoId: button.dataset.videoId,
            statusLabel: button.dataset.statusLabel,
            requestedAt: button.dataset.requestedAt,
            updatedAt: button.dataset.updatedAt,
            finishedAt: button.dataset.finishedAt,
            fileSize: button.dataset.fileSize,
            outputPath: button.dataset.outputPath,
            outputUrl: button.dataset.outputUrl,
            errorCode: button.dataset.errorCode,
            errorMessage: button.dataset.errorMessage,
          });
        });
      }

      function bindDownloadDetailButtons(scope) {
        const root = scope instanceof Element ? scope : document;
        root.querySelectorAll("[data-download-detail-open]").forEach((node) => {
          if (node instanceof HTMLButtonElement) {
            initDownloadDetailButton(node);
          }
        });
      }

      async function requestDownloadRetry(jobId) {
        const response = await fetch(`/api/downloads/${encodeURIComponent(jobId)}/retry`, {
          method: "POST",
          headers: {
            "Accept": "application/json",
          },
        });
        const payload = await response.json().catch(() => ({}));
        return { response, payload };
      }

      function initDownloadRetryButton(button) {
        if (button.dataset.downloadRetryBound === "1") return;
        button.dataset.downloadRetryBound = "1";
        button.addEventListener("click", async () => {
          if (button.dataset.retryInFlight === "1") return;
          button.dataset.retryInFlight = "1";
          button.disabled = true;
          try {
            const { response, payload } = await requestDownloadRetry(button.dataset.jobId || "");
            if (response.ok && payload?.retried === true) {
              showUiToast(DOWNLOAD_UI_TEXT.toastRetryQueued, "success");
              void pollDownloadProgress();
              if (window.location.pathname === "/downloads") {
                window.setTimeout(() => {
                  void refreshDownloadHistoryFragment(true);
                }, 250);
              }
              return;
            }
            if (payload?.code === "ffmpeg_missing") {
              showUiToast(DOWNLOAD_UI_TEXT.toastFfmpegMissing, "error");
              return;
            }
            showUiToast(DOWNLOAD_UI_TEXT.toastRetryFailed, "error");
          } catch (_error) {
            showUiToast(DOWNLOAD_UI_TEXT.toastRetryFailed, "error");
          } finally {
            delete button.dataset.retryInFlight;
            button.disabled = false;
          }
        });
      }

      function bindDownloadRetryButtons(scope) {
        const root = scope instanceof Element ? scope : document;
        root.querySelectorAll("[data-download-retry-button]").forEach((node) => {
          if (node instanceof HTMLButtonElement) {
            initDownloadRetryButton(node);
          }
        });
      }

      let downloadProgressInFlight = false;
      let downloadProgressPollingStarted = false;

      async function pollDownloadProgress() {
        if (downloadProgressInFlight) return;
        downloadProgressInFlight = true;
        const storedCursorRaw = getStoredValue(DOWNLOAD_EVENT_CURSOR_KEY);
        const hasStoredCursor = storedCursorRaw !== null;
        const afterEventId = getDownloadEventCursor();
        try {
          const response = await fetch(
            `/api/downloads/progress?after_event_id=${encodeURIComponent(String(afterEventId))}`,
            {
              method: "GET",
              headers: { "Accept": "application/json" },
            },
          );
          if (!response.ok) return;
          const payload = await response.json().catch(() => null);
          if (!payload || typeof payload !== "object") return;

          updateDownloadNavBadge(payload.active_count);

          const events = Array.isArray(payload.events) ? payload.events : [];
          const latestFromPayload = Number.parseInt(String(payload.latest_event_id || "0"), 10) || 0;
          const maxEventId = events.reduce((acc, item) => {
            const current = Number.parseInt(String(item?.id || "0"), 10) || 0;
            return Math.max(acc, current);
          }, 0);
          const nextCursor = Math.max(afterEventId, latestFromPayload, maxEventId);

          if (!hasStoredCursor) {
            setDownloadEventCursor(nextCursor);
            return;
          }
          if (events.length > 0) {
            const toast = buildDownloadEventToast(events);
            if (toast && toast.message) {
              showUiToast(toast.message, toast.tone);
            }
            if (window.location.pathname === "/downloads") {
              void refreshDownloadHistoryFragment(false);
            }
          }
          setDownloadEventCursor(nextCursor);
        } catch (_error) {
          // ignore polling failures and retry on next interval.
        } finally {
          downloadProgressInFlight = false;
        }
      }

      function startDownloadProgressPolling() {
        if (downloadProgressPollingStarted) return;
        downloadProgressPollingStarted = true;
        void pollDownloadProgress();
        window.setInterval(() => {
          void pollDownloadProgress();
        }, DOWNLOAD_PROGRESS_POLL_INTERVAL_MS);
      }

      // ── Queue polling ──────────────────────────────────────────
      const QUEUE_STATUS_BADGE_MAP = {
        transcript_pending: "status-badge--transcript-pending",
        transcript_processing: "status-badge--transcript-processing",
        transcript_failed: "status-badge--transcript-failed",
        no_subtitle: "status-badge--no-subtitle",
        llm_pending: "status-badge--llm-pending",
        llm_processing: "status-badge--llm-processing",
        llm_failed: "status-badge--llm-failed",
        manual_review: "status-badge--manual-review",
      };
      let queuePollingStarted = false;
      let queuePollInFlight = false;
      let queueIntervalId = null;

      function getQueueStatusLabel(status) {
        const page = document.querySelector("[data-queue-page]");
        if (!page) return status.replace(/_/g, " ");
        const key = "label" + status.split("_").map((w) => w.charAt(0).toUpperCase() + w.slice(1)).join("");
        return page.dataset[key] || status.replace(/_/g, " ");
      }

      function updateQueueNavBadge(count) {
        const badges = document.querySelectorAll("[data-queue-nav-badge]");
        const n = Math.max(0, Number.parseInt(String(count || "0"), 10) || 0);
        badges.forEach((badge) => {
          if (!(badge instanceof HTMLElement)) return;
          if (n <= 0) {
            badge.classList.add("hidden");
            badge.textContent = "0";
            return;
          }
          badge.classList.remove("hidden");
          badge.textContent = String(n);
        });
      }

      function createQueueItemElement(item, section) {
        const vid = String(item.video_id || "");
        const title = String(item.title || vid);
        const channel = String(item.channel_name || item.channel_id || "");
        const status = String(item.pipeline_status || "");
        const badgeClass = QUEUE_STATUS_BADGE_MAP[status] || "status-badge--unknown";
        const label = getQueueStatusLabel(status);

        const failedStatuses = ["transcript_failed", "no_subtitle", "llm_failed", "manual_review"];
        const isFailed = failedStatuses.includes(status);
        const row = document.createElement("div");
        row.className = isFailed
          ? "flex items-center gap-3 py-3 -mx-2 rounded-md bg-rose-50/60 px-2"
          : "flex items-center gap-3 py-3";

        const thumbWrap = document.createElement("div");
        thumbWrap.className = "h-12 w-20 flex-shrink-0 overflow-hidden rounded";
        if (item.thumbnail_url) {
          const img = document.createElement("img");
          img.src = item.thumbnail_url;
          img.alt = "";
          img.className = "h-full w-full object-cover";
          img.loading = "lazy";
          thumbWrap.appendChild(img);
        } else {
          const placeholder = document.createElement("div");
          placeholder.className = "flex h-full w-full items-center justify-center bg-slate-100 text-xs text-slate-400";
          placeholder.textContent = "No img";
          thumbWrap.appendChild(placeholder);
        }
        row.appendChild(thumbWrap);

        const info = document.createElement("div");
        info.className = "min-w-0 flex-1";
        const link = document.createElement("a");
        link.href = `/videos/${encodeURIComponent(vid)}`;
        link.className = "block truncate text-sm font-medium text-slate-900 hover:text-indigo-600";
        link.textContent = title;
        info.appendChild(link);
        const channelP = document.createElement("p");
        channelP.className = "truncate text-xs text-slate-500";
        channelP.textContent = channel;
        info.appendChild(channelP);
        row.appendChild(info);

        const badgeWrap = document.createElement("div");
        badgeWrap.className = "flex-shrink-0";
        const badge = document.createElement("span");
        badge.className = `status-badge inline-flex items-center rounded-full px-2.5 py-0.5 text-xs font-medium ${badgeClass}`;
        badge.textContent = label;
        badgeWrap.appendChild(badge);
        row.appendChild(badgeWrap);

        const retryStatuses = section === "transcript"
          ? ["transcript_failed", "no_subtitle"]
          : ["llm_failed", "manual_review"];
        if (retryStatuses.includes(status)) {
          const btn = document.createElement("button");
          btn.type = "button";
          if (section === "transcript") {
            btn.dataset.queueRetryTranscript = vid;
          } else {
            btn.dataset.queueRetryLlm = vid;
          }
          btn.className = "flex-shrink-0 rounded-md border border-indigo-300 bg-indigo-50 px-2.5 py-1 text-xs font-medium text-indigo-700 hover:bg-indigo-100";
          const retryLabel = document.querySelector("[data-queue-page]")?.dataset.retryButtonText || "Retry";
          btn.textContent = retryLabel;
          row.appendChild(btn);
        }

        return row;
      }

      function updateQueueSection(listAttr, countAttr, items, section, emptyText, totalCount) {
        const listEl = document.querySelector(`[${listAttr}]`);
        const countEl = document.querySelector(`[${countAttr}]`);
        if (countEl) countEl.textContent = String(totalCount ?? items.length);
        if (!listEl) return;
        listEl.textContent = "";
        if (items.length === 0) {
          const p = document.createElement("p");
          p.className = "py-6 text-center text-sm text-slate-400";
          p.textContent = emptyText;
          listEl.appendChild(p);
        } else {
          const container = document.createElement("div");
          container.className = "divide-y divide-slate-100";
          items.forEach((item) => container.appendChild(createQueueItemElement(item, section)));
          listEl.appendChild(container);
        }
        bindQueueRetryButtons(listEl);
      }

      function updateQueueChips(counts) {
        const page = document.querySelector("[data-queue-page]");
        if (!page) return;
        const chipLabels = {
          pending: page.dataset.chipPending || "Pending",
          processing: page.dataset.chipProcessing || "Processing",
          failed: page.dataset.chipFailed || "Failed",
          no_subtitle: page.dataset.chipNoSubtitle || "No subtitle",
          manual_review: page.dataset.chipManualReview || "Manual review",
        };
        const chipStyles = {
          pending: "rounded-full bg-amber-100 px-1.5 py-0.5 text-[10px] font-medium text-amber-800",
          processing: "rounded-full bg-sky-100 px-1.5 py-0.5 text-[10px] font-medium text-sky-800",
          failed: "rounded-full bg-rose-100 px-1.5 py-0.5 text-[10px] font-medium text-rose-800",
          no_subtitle: "rounded-full bg-slate-200 px-1.5 py-0.5 text-[10px] font-medium text-slate-700",
          manual_review: "rounded-full bg-orange-100 px-1.5 py-0.5 text-[10px] font-medium text-orange-800",
        };

        function rebuildChips(containerAttr, statusKeys) {
          const container = document.querySelector(`[${containerAttr}]`);
          if (!container) return;
          container.textContent = "";
          statusKeys.forEach(({ key, chipKey }) => {
            const val = counts[key] || 0;
            if (val <= 0) return;
            const span = document.createElement("span");
            span.className = chipStyles[chipKey] || chipStyles.pending;
            span.textContent = `${chipLabels[chipKey] || chipKey} ${val}`;
            container.appendChild(span);
          });
        }

        rebuildChips("data-queue-transcript-chips", [
          { key: "transcript_pending", chipKey: "pending" },
          { key: "transcript_processing", chipKey: "processing" },
          { key: "transcript_failed", chipKey: "failed" },
          { key: "no_subtitle", chipKey: "no_subtitle" },
        ]);
        rebuildChips("data-queue-llm-chips", [
          { key: "llm_pending", chipKey: "pending" },
          { key: "llm_processing", chipKey: "processing" },
          { key: "llm_failed", chipKey: "failed" },
          { key: "manual_review", chipKey: "manual_review" },
        ]);
      }

      function updateWorkerStatusDots(payload) {
        const tInd = document.querySelector("[data-queue-transcript-worker-indicator]");
        const lInd = document.querySelector("[data-queue-llm-worker-indicator]");
        const page = document.querySelector("[data-queue-page]");
        const activeLabel = page?.dataset.labelWorkerActive || "Active";
        const inactiveLabel = page?.dataset.labelWorkerInactive || "Inactive";
        [
          { el: tInd, active: payload.workers?.transcript === true },
          { el: lInd, active: payload.workers?.llm === true },
        ].forEach(({ el, active }) => {
          if (!el) return;
          const dot = el.querySelector("span");
          const labelEl = el.childNodes[el.childNodes.length - 1];
          el.classList.remove("bg-emerald-50", "text-emerald-700", "bg-slate-100", "text-slate-500");
          if (active) {
            el.classList.add("bg-emerald-50", "text-emerald-700");
            if (dot) dot.className = "h-1.5 w-1.5 rounded-full bg-emerald-500";
          } else {
            el.classList.add("bg-slate-100", "text-slate-500");
            if (dot) dot.className = "h-1.5 w-1.5 rounded-full bg-slate-400";
          }
          if (labelEl && labelEl.nodeType === Node.TEXT_NODE) {
            labelEl.textContent = "\n          " + (active ? activeLabel : inactiveLabel) + "\n        ";
          }
        });

        const gInd = document.querySelector("[data-queue-guard-indicator]");
        if (gInd) {
          const state = payload.transcript_guard?.breaker_state || "closed";
          const dot = gInd.querySelector("span:not([data-guard-label])");
          gInd.classList.remove(
            "bg-emerald-50", "text-emerald-700",
            "bg-rose-50", "text-rose-700",
            "bg-amber-50", "text-amber-700",
          );
          const labelMap = {
            closed: gInd.dataset.guardLabelClosed || "Closed",
            open: gInd.dataset.guardLabelOpen || "Open",
            half_open: gInd.dataset.guardLabelHalfOpen || "Half-open",
          };
          const colorMap = {
            closed: { bg: "bg-emerald-50", text: "text-emerald-700", dot: "bg-emerald-500" },
            open: { bg: "bg-rose-50", text: "text-rose-700", dot: "bg-rose-500" },
            half_open: { bg: "bg-amber-50", text: "text-amber-700", dot: "bg-amber-500" },
          };
          const colors = colorMap[state] || colorMap.closed;
          gInd.classList.add(colors.bg, colors.text);
          if (dot) dot.className = `h-1.5 w-1.5 rounded-full ${colors.dot}`;
          const labelEl = gInd.querySelector("[data-guard-label]");
          if (labelEl) labelEl.textContent = labelMap[state] || state;
        }
      }

      async function pollQueueStatus() {
        if (queuePollInFlight) return;
        queuePollInFlight = true;
        const isQueuePage = !!document.querySelector("[data-queue-page]");
        const url = isQueuePage ? "/api/queue/poll" : "/api/status";
        try {
          const resp = await fetch(url, {
            method: "GET",
            headers: { "Accept": "application/json" },
          });
          if (!resp.ok) return;
          const payload = await resp.json().catch(() => null);
          if (!payload || typeof payload !== "object") return;

          const badgeCount = (
            (payload.badge_count ?? 0)
            || ((payload.transcript_pending || 0) + (payload.transcript_processing || 0)
              + (payload.llm_pending || 0) + (payload.llm_processing || 0))
          );
          updateQueueNavBadge(badgeCount);

          if (!isQueuePage) return;

          const queuePage = document.querySelector("[data-queue-page]");
          const tItems = Array.isArray(payload.transcript_items) ? payload.transcript_items : [];
          const lItems = Array.isArray(payload.llm_items) ? payload.llm_items : [];
          const counts = payload.counts || {};
          const tTotal = (counts.transcript_pending || 0) + (counts.transcript_processing || 0)
            + (counts.transcript_failed || 0) + (counts.no_subtitle || 0);
          const lTotal = (counts.llm_pending || 0) + (counts.llm_processing || 0)
            + (counts.llm_failed || 0) + (counts.manual_review || 0);
          updateQueueSection(
            "data-queue-transcript-list", "data-queue-transcript-count",
            tItems, "transcript",
            queuePage?.dataset.transcriptEmptyText || "Transcript queue is empty.",
            tTotal,
          );
          updateQueueSection(
            "data-queue-llm-list", "data-queue-llm-count",
            lItems, "llm",
            queuePage?.dataset.llmEmptyText || "LLM queue is empty.",
            lTotal,
          );
          updateWorkerStatusDots(payload);
          updateQueueChips(counts);
        } catch (err) {
          if (typeof console !== "undefined") console.warn("[BriefTube] Queue poll error:", err);
        } finally {
          queuePollInFlight = false;
        }
      }

      function bindQueueRetryHandler(scope, selector, datasetKey, urlBuilder) {
        scope.querySelectorAll(selector).forEach((btn) => {
          if (btn.dataset.queueRetryBound) return;
          btn.dataset.queueRetryBound = "1";
          btn.addEventListener("click", async () => {
            const videoId = btn.dataset[datasetKey];
            if (!videoId || btn.disabled) return;
            btn.disabled = true;
            const page = document.querySelector("[data-queue-page]");
            try {
              const resp = await fetch(urlBuilder(videoId), { method: "POST" });
              if (resp.ok) {
                showUiToast(page?.dataset.retrySuccessText || "Retry submitted.", "success");
                void pollQueueStatus();
                return;
              }
              showUiToast(page?.dataset.retryFailedText || "Retry failed.", "error");
            } catch (err) {
              if (typeof console !== "undefined") console.warn("[BriefTube] Queue retry error:", err);
              showUiToast(page?.dataset.retryFailedText || "Retry failed.", "error");
            }
            btn.disabled = false;
          });
        });
      }

      function bindQueueRetryButtons(scope) {
        if (!scope) return;
        bindQueueRetryHandler(
          scope, "[data-queue-retry-transcript]", "queueRetryTranscript",
          (id) => `/api/videos/${encodeURIComponent(id)}/transcript/retry`,
        );
        bindQueueRetryHandler(
          scope, "[data-queue-retry-llm]", "queueRetryLlm",
          (id) => `/api/videos/${encodeURIComponent(id)}/retry`,
        );
      }

      function bindQueueClearButtons(scope) {
        if (!scope) return;
        scope.querySelectorAll("[data-queue-clear-section]").forEach((btn) => {
          if (btn.dataset.queueClearBound === "1") return;
          btn.dataset.queueClearBound = "1";
          btn.addEventListener("click", async (event) => {
            event.stopPropagation();
            const section = btn.dataset.queueClearSection;
            if (!section || btn.disabled) return;
            const confirmMessage = btn.dataset.confirmMessage || "Clear queue items?";
            if (!window.confirm(confirmMessage)) return;
            btn.disabled = true;
            const page = document.querySelector("[data-queue-page]");
            try {
              const resp = await fetch(`/api/queue/${encodeURIComponent(section)}/clear`, {
                method: "POST",
                headers: { "Accept": "application/json" },
              });
              const payload = await resp.json().catch(() => null);
              if (resp.ok && payload?.ok) {
                const count = Number.parseInt(String(payload.cleared_count || "0"), 10) || 0;
                const template = count > 0
                  ? (page?.dataset.clearSuccessText || "Cleared {count} queue item(s).")
                  : (page?.dataset.clearEmptyText || "No queue items to clear.");
                showUiToast(template.replace("{count}", String(count)), count > 0 ? "success" : "info");
                void pollQueueStatus();
                return;
              }
              showUiToast(page?.dataset.clearFailedText || "Failed to clear queue items.", "error");
            } catch (err) {
              if (typeof console !== "undefined") console.warn("[BriefTube] Queue clear error:", err);
              showUiToast(page?.dataset.clearFailedText || "Failed to clear queue items.", "error");
            } finally {
              btn.disabled = false;
            }
          });
        });
      }

      function startQueuePolling() {
        if (queuePollingStarted) return;
        queuePollingStarted = true;
        void pollQueueStatus();
        queueIntervalId = window.setInterval(() => {
          void pollQueueStatus();
        }, QUEUE_POLL_INTERVAL_MS);
        document.addEventListener("visibilitychange", () => {
          if (document.hidden) {
            if (queueIntervalId) {
              clearInterval(queueIntervalId);
              queueIntervalId = null;
            }
          } else if (!queueIntervalId) {
            void pollQueueStatus();
            queueIntervalId = window.setInterval(() => {
              void pollQueueStatus();
            }, QUEUE_POLL_INTERVAL_MS);
          }
        });
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
        bindArticlePreviewModals(scope);
        bindDownloadDetailButtons(scope);
        bindDownloadRetryButtons(scope);
        bindCategorySortable(scope);
        bindChannelMoveCategory(scope);
        bindCategoryFilterReset(scope);
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
              currentNode instanceof Element &&
              nextNode instanceof Element &&
              typeof shouldSwap === "function" &&
              shouldSwap({ target: latestTarget, currentNode, nextNode }) === false
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
            hydrateUiScope(latestTarget);
            return true;
          }
          const fragment = parseHtmlFragment(html);
          const nextNode = fragment.firstElementChild;
          const latestTarget = document.querySelector(targetSelector);
          if (!(latestTarget instanceof Element) || !(nextNode instanceof Element)) return false;
          if (
            typeof shouldSwap === "function" &&
            shouldSwap({ target: latestTarget, currentNode: latestTarget, nextNode }) === false
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
          hydrateUiScope(nextNode);
          return true;
        } catch (_error) {
          return false;
        }
      }

      let videoDetailRefreshInFlight = false;
      let videoDetailRefreshStarted = false;
      let videoDetailRefreshIntervalId = null;

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
              if (!(currentNode instanceof HTMLElement) || !(nextNode instanceof HTMLElement)) return true;
              const currentFragment = currentNode.matches("[data-video-detail-dynamic-fragment]")
                ? currentNode
                : currentNode.querySelector("[data-video-detail-dynamic-fragment]");
              const nextFragment = nextNode.matches("[data-video-detail-dynamic-fragment]")
                ? nextNode
                : nextNode.querySelector("[data-video-detail-dynamic-fragment]");
              if (!(currentFragment instanceof HTMLElement) || !(nextFragment instanceof HTMLElement)) return true;
              return currentFragment.dataset.videoDetailRefreshKey !== nextFragment.dataset.videoDetailRefreshKey;
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
        videoDetailRefreshIntervalId = window.setInterval(() => {
          void pollVideoDetailFragment();
        }, VIDEO_DETAIL_POLL_INTERVAL_MS);
        document.addEventListener("visibilitychange", () => {
          if (!document.hidden) {
            void pollVideoDetailFragment();
          }
        });
      }

      let channelListRefreshInFlight = false;
      let channelListRefreshStarted = false;
      let channelListRefreshIntervalId = null;

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
        if (fragment.dataset.channelListAutoRefresh !== "1" || shouldSkipChannelListAutoRefresh()) return;
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
        channelListRefreshIntervalId = window.setInterval(() => {
          void pollChannelListFragment();
        }, CHANNEL_LIST_POLL_INTERVAL_MS);
        document.addEventListener("visibilitychange", () => {
          if (!document.hidden) {
            void pollChannelListFragment();
          }
        });
      }

      let llmRuntimeRefreshInFlight = false;
      let llmRuntimeRefreshStarted = false;
      let llmRuntimeRefreshIntervalId = null;

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
        llmRuntimeRefreshIntervalId = window.setInterval(() => {
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

      function initDigitsOnlyInput(input) {
        if (input.dataset.digitsBound === "1") return;
        input.dataset.digitsBound = "1";

        const normalize = () => {
          const original = input.value || "";
          const next = original.replace(/\D+/g, "");
          if (next !== original) {
            input.value = next;
          }
        };
        input.addEventListener("input", normalize);
        normalize();
      }

      function bindDigitsOnlyInputs(scope) {
        const root = scope instanceof Element ? scope : document;
        root.querySelectorAll("input[data-digits-only]").forEach(initDigitsOnlyInput);
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

      function initRetentionForm(form) {
        if (form.dataset.retentionFormBound === "1") return;
        form.dataset.retentionFormBound = "1";

        const selectToggle = form.querySelector("[data-retention-select-toggle]");
        const items = Array.from(form.querySelectorAll("[data-retention-select-item]"));
        const deleteButton = form.querySelector("[data-retention-delete-button]");
        if (!items.length) return;

        const sync = () => {
          const checkedCount = items.filter((item) => item.checked).length;
          if (selectToggle) {
            selectToggle.dataset.checkedCount = String(checkedCount);
            selectToggle.dataset.totalCount = String(items.length);
          }
          if (deleteButton) {
            deleteButton.disabled = checkedCount === 0;
          }
        };

        selectToggle?.addEventListener("click", () => {
          const allChecked = items.every((item) => item.checked);
          items.forEach((item) => {
            item.checked = !allChecked;
          });
          sync();
        });
        items.forEach((item) => item.addEventListener("change", sync));
        sync();
      }

      function bindRetentionForms(scope) {
        const root = scope instanceof Element ? scope : document;
        root.querySelectorAll("[data-retention-form]").forEach(initRetentionForm);
      }

      function initRetentionNotice(node) {
        if (node.dataset.retentionNoticeBound === "1") return;
        node.dataset.retentionNoticeBound = "1";

        const key = `retentionNoticeDismissed:${node.dataset.retentionNoticeKey || "default"}`;
        const dismissButton = node.querySelector("[data-retention-notice-dismiss]");
        if (sessionStorage.getItem(key) === "1") {
          node.remove();
          return;
        }

        const dismiss = () => {
          node.classList.add("opacity-0", "translate-y-1");
          setTimeout(() => node.remove(), 250);
          sessionStorage.setItem(key, "1");
        };

        setTimeout(dismiss, 7000);
        dismissButton?.addEventListener("click", (event) => {
          event.preventDefault();
          event.stopPropagation();
          dismiss();
        });
        node.addEventListener("click", () => {
          sessionStorage.setItem(key, "1");
        });
      }

      function bindRetentionNotices(scope) {
        const root = scope instanceof Element ? scope : document;
        root.querySelectorAll("[data-retention-notice]").forEach(initRetentionNotice);
      }

      function initVideoManageForm(form) {
        if (form.dataset.videoManageBound === "1") return;
        form.dataset.videoManageBound = "1";

        const selectAll = form.querySelector("[data-video-select-all]");
        const deleteBtn = form.querySelector("[data-video-delete-selected]");
        const downloadBtn = form.querySelector("[data-video-download-selected]");
        const articleBtn = form.querySelector("[data-video-article-request-selected]");
        const items = () => form.querySelectorAll("[data-video-select-item]");
        if (!selectAll || !deleteBtn || !downloadBtn || !articleBtn) return;
        const downloadDefaultLabel = downloadBtn.textContent || "";
        const downloadBusyLabel = downloadBtn.dataset.busyLabel || downloadDefaultLabel;
        const articleDefaultLabel = articleBtn.textContent || "";
        const articleBusyLabel = articleBtn.dataset.busyLabel || articleDefaultLabel;

        function setBulkBusy(kind, isBusy) {
          if (kind === "download") {
            form.dataset.videoDownloadBulkInFlight = isBusy ? "1" : "0";
          } else if (kind === "article") {
            form.dataset.videoArticleRequestBulkInFlight = isBusy ? "1" : "0";
          }
          const isDownloadBusy = form.dataset.videoDownloadBulkInFlight === "1";
          const isArticleBusy = form.dataset.videoArticleRequestBulkInFlight === "1";
          const isAnyBusy = isDownloadBusy || isArticleBusy;
          form.setAttribute("aria-busy", isAnyBusy ? "true" : "false");
          downloadBtn.textContent = isDownloadBusy ? downloadBusyLabel : downloadDefaultLabel;
          articleBtn.textContent = isArticleBusy ? articleBusyLabel : articleDefaultLabel;
          sync();
        }

        function sync() {
          const checked = form.querySelectorAll("[data-video-select-item]:checked").length;
          const all = items();
          const isDownloadBusy = form.dataset.videoDownloadBulkInFlight === "1";
          const isArticleBusy = form.dataset.videoArticleRequestBulkInFlight === "1";
          const isAnyBusy = isDownloadBusy || isArticleBusy;
          deleteBtn.disabled = checked === 0;
          downloadBtn.disabled = checked === 0 || isAnyBusy;
          articleBtn.disabled = checked === 0 || isAnyBusy;
          selectAll.checked = all.length > 0 && checked === all.length;
          selectAll.indeterminate = checked > 0 && checked < all.length;
        }

        selectAll.addEventListener("change", () => {
          items().forEach(cb => { cb.checked = selectAll.checked; });
          sync();
        });
        form.addEventListener("change", (e) => {
          if (e.target.matches("[data-video-select-item]")) sync();
        });
        downloadBtn.addEventListener("click", (event) => {
          const isBusy = form.dataset.videoDownloadBulkInFlight === "1"
            || form.dataset.videoArticleRequestBulkInFlight === "1";
          if (isBusy) {
            event.preventDefault();
            return;
          }
          setBulkBusy("download", true);
        });
        articleBtn.addEventListener("click", (event) => {
          const isBusy = form.dataset.videoDownloadBulkInFlight === "1"
            || form.dataset.videoArticleRequestBulkInFlight === "1";
          if (isBusy) {
            event.preventDefault();
            return;
          }
          setBulkBusy("article", true);
        });
        const settleDownloadBulk = (event) => {
          const source = event.detail?.requestConfig?.elt;
          if (source !== downloadBtn) return;
          setBulkBusy("download", false);
        };
        const settleArticleBulk = (event) => {
          const source = event.detail?.requestConfig?.elt;
          if (source !== articleBtn) return;
          setBulkBusy("article", false);
        };
        form.addEventListener("htmx:afterRequest", settleDownloadBulk);
        form.addEventListener("htmx:afterRequest", settleArticleBulk);
        form.addEventListener("htmx:responseError", settleDownloadBulk);
        form.addEventListener("htmx:responseError", settleArticleBulk);
        form.addEventListener("htmx:sendError", settleDownloadBulk);
        form.addEventListener("htmx:sendError", settleArticleBulk);
        form.addEventListener("htmx:timeout", settleDownloadBulk);
        form.addEventListener("htmx:timeout", settleArticleBulk);
        sync();
      }

      function bindVideoManageForms(scope) {
        const root = scope instanceof Element ? scope : document;
        root.querySelectorAll("[data-video-manage-form]").forEach(initVideoManageForm);
      }

      function initVideoArticleRequestButton(button) {
        if (button.dataset.videoArticleRequestBound === "1") return;
        button.dataset.videoArticleRequestBound = "1";

        const defaultLabel = button.textContent || "";
        const busyLabel = button.dataset.busyLabel || defaultLabel;
        const owner = button.closest("#video-detail-fragment") || document.body;

        function setBusy(isBusy) {
          if (isBusy) {
            button.dataset.videoArticleRequestInFlight = "1";
          } else {
            delete button.dataset.videoArticleRequestInFlight;
          }
          button.disabled = isBusy;
          button.textContent = isBusy ? busyLabel : defaultLabel;
        }

        button.addEventListener("click", (event) => {
          if (button.dataset.videoArticleRequestInFlight === "1") {
            event.preventDefault();
            return;
          }
          setBusy(true);
        });

        const settle = (event) => {
          const source = event.detail?.requestConfig?.elt;
          if (source !== button) return;
          setBusy(false);
          if (event.type === "htmx:afterRequest") {
            void refreshVideoDetailFragmentNow();
          }
        };
        owner.addEventListener("htmx:afterRequest", settle);
        owner.addEventListener("htmx:responseError", settle);
        owner.addEventListener("htmx:sendError", settle);
        owner.addEventListener("htmx:timeout", settle);
      }

      function bindVideoArticleRequestButtons(scope) {
        const root = scope instanceof Element ? scope : document;
        root.querySelectorAll("[data-video-article-request-button]").forEach(initVideoArticleRequestButton);
      }

      function initVideoTranscriptRequestButton(button) {
        if (button.dataset.videoTranscriptRequestBound === "1") return;
        button.dataset.videoTranscriptRequestBound = "1";

        const defaultLabel = button.textContent || "";
        const busyLabel = button.dataset.busyLabel || defaultLabel;
        const owner = button.closest("#video-detail-fragment") || document.body;

        function setBusy(isBusy) {
          if (isBusy) {
            button.dataset.videoTranscriptRequestInFlight = "1";
          } else {
            delete button.dataset.videoTranscriptRequestInFlight;
          }
          button.disabled = isBusy;
          button.textContent = isBusy ? busyLabel : defaultLabel;
        }

        button.addEventListener("click", (event) => {
          if (button.dataset.videoTranscriptRequestInFlight === "1") {
            event.preventDefault();
            return;
          }
          setBusy(true);
        });

        const settle = (event) => {
          const source = event.detail?.requestConfig?.elt;
          if (source !== button) return;
          setBusy(false);
          if (event.type === "htmx:afterRequest") {
            void refreshVideoDetailFragmentNow();
          }
        };
        owner.addEventListener("htmx:afterRequest", settle);
        owner.addEventListener("htmx:responseError", settle);
        owner.addEventListener("htmx:sendError", settle);
        owner.addEventListener("htmx:timeout", settle);
      }

      function bindVideoTranscriptRequestButtons(scope) {
        const root = scope instanceof Element ? scope : document;
        root.querySelectorAll("[data-video-transcript-request-button]").forEach(initVideoTranscriptRequestButton);
      }

      function closeArticlePreviewModal(modal) {
        if (!(modal instanceof HTMLElement)) return;
        modal.classList.add("hidden");
        modal.classList.remove("flex");
        modal.dataset.modalOpen = "0";
      }

      function openArticlePreviewModal(modal) {
        if (!(modal instanceof HTMLElement)) return;
        modal.classList.remove("hidden");
        modal.classList.add("flex");
        modal.dataset.modalOpen = "1";
      }

      function ensureArticlePreviewEscapeHandler() {
        const root = document.body;
        if (!root || root.dataset.articlePreviewEscapeBound === "1") return;
        root.dataset.articlePreviewEscapeBound = "1";
        document.addEventListener("keydown", (event) => {
          if (event.key !== "Escape") return;
          document.querySelectorAll("[data-article-preview-modal]").forEach((node) => {
            if (!(node instanceof HTMLElement)) return;
            if (node.dataset.modalOpen === "1") {
              closeArticlePreviewModal(node);
            }
          });
        });
      }

      function initArticlePreviewButton(button) {
        if (button.dataset.articlePreviewBound === "1") return;
        const root = button.closest("#video-detail-fragment") || document;
        const modalSelector = button.dataset.articlePreviewTarget || "#article-preview-modal";
        const modal = root.querySelector(modalSelector);
        if (!(modal instanceof HTMLElement)) return;
        button.dataset.articlePreviewBound = "1";
        ensureArticlePreviewEscapeHandler();

        button.addEventListener("click", (event) => {
          event.preventDefault();
          event.stopPropagation();
          openArticlePreviewModal(modal);
        });

        if (modal.dataset.articlePreviewModalBound === "1") return;
        modal.dataset.articlePreviewModalBound = "1";
        modal.addEventListener("click", (event) => {
          const target = event.target;
          if (
            target === modal
            || (target instanceof Element && target.hasAttribute("data-article-preview-backdrop"))
            || (target instanceof Element && target.hasAttribute("data-article-preview-close"))
          ) {
            closeArticlePreviewModal(modal);
          }
        });
      }

      function bindArticlePreviewModals(scope) {
        const root = scope instanceof Element ? scope : document;
        root.querySelectorAll("[data-article-preview-open]").forEach((node) => {
          if (node instanceof HTMLButtonElement) {
            initArticlePreviewButton(node);
          }
        });
      }

      function initCopyButton(btn) {
        if (btn.dataset.copyBound === "1") return;
        btn.dataset.copyBound = "1";
        btn.addEventListener("click", () => {
          const targetId = btn.getAttribute("data-copy-target");
          const toastMsg = btn.getAttribute("data-copy-toast") || "Copied";
          const target = document.getElementById(targetId);
          if (!target) return;
          const text = target.textContent || "";
          navigator.clipboard.writeText(text.trim()).then(() => {
            showUiToast(toastMsg, "success");
          });
        });
      }

      function bindCopyButtons(scope) {
        const root = scope instanceof Element ? scope : document;
        root.querySelectorAll("[data-copy-target]").forEach(initCopyButton);
      }

      function initCollapsible(section) {
        if (section.dataset.collapsibleBound === "1") return;
        section.dataset.collapsibleBound = "1";
        const toggle = section.querySelector("[data-collapsible-toggle]");
        const body = section.querySelector("[data-collapsible-body]");
        const icon = toggle?.querySelector("[data-collapsible-icon]");
        if (!toggle || !body) return;
        const startOpen = section.hasAttribute("data-collapsible-open");
        if (startOpen) {
          if (icon) icon.style.transform = "rotate(180deg)";
        } else {
          body.classList.add("hidden");
        }
        toggle.addEventListener("click", (event) => {
          if (event.target?.closest?.("button,a,input,select,textarea,label,form")) return;
          const isHidden = body.classList.toggle("hidden");
          if (icon) icon.style.transform = isHidden ? "" : "rotate(180deg)";
        });
      }

      function bindCollapsibles(scope) {
        const root = scope instanceof Element ? scope : document;
        root.querySelectorAll("[data-collapsible]").forEach(initCollapsible);
      }

      function bindThumbPreviews(scope) {
        const root = scope instanceof Element ? scope : document;
        root.querySelectorAll("[data-thumb-hover]").forEach(el => {
          if (el.dataset.thumbBound === "1") return;
          el.dataset.thumbBound = "1";
          el.addEventListener("mouseenter", () => {
            const preview = el.querySelector("[data-thumb-preview]");
            if (!preview) return;
            const rect = el.getBoundingClientRect();
            if (rect.bottom + 278 > window.innerHeight) {
              preview.style.bottom = (el.offsetHeight + 4) + "px";
              preview.style.top = "auto";
            } else {
              preview.style.top = (el.offsetHeight + 4) + "px";
              preview.style.bottom = "auto";
            }
          });
        });
      }

      function loadYouTubeIframeApi() {
        if (window.YT && typeof window.YT.Player === "function") {
          return Promise.resolve(window.YT);
        }
        if (window.__ytIframeApiPromise) {
          return window.__ytIframeApiPromise;
        }

        window.__ytIframeApiPromise = new Promise((resolve, reject) => {
          let settled = false;
          let timeoutId = null;

          const resolveOnce = (value) => {
            if (settled) return;
            settled = true;
            if (timeoutId !== null) {
              window.clearTimeout(timeoutId);
            }
            resolve(value);
          };
          const rejectOnce = (error) => {
            if (settled) return;
            settled = true;
            if (timeoutId !== null) {
              window.clearTimeout(timeoutId);
            }
            reject(error);
          };

          const ready = () => {
            if (window.YT && typeof window.YT.Player === "function") {
              resolveOnce(window.YT);
            } else {
              rejectOnce(new Error("youtube_iframe_api_not_ready"));
            }
          };

          const previousReady = window.onYouTubeIframeAPIReady;
          window.onYouTubeIframeAPIReady = () => {
            if (typeof previousReady === "function") {
              previousReady();
            }
            ready();
          };

          timeoutId = window.setTimeout(() => {
            rejectOnce(new Error("youtube_iframe_api_timeout"));
          }, 12000);

          const existingScript = document.querySelector("script[data-youtube-iframe-api='1']");
          if (!existingScript) {
            const script = document.createElement("script");
            script.src = "https://www.youtube.com/iframe_api";
            script.async = true;
            script.dataset.youtubeIframeApi = "1";
            script.onerror = () => rejectOnce(new Error("youtube_iframe_api_load_failed"));
            document.head.appendChild(script);
            return;
          }

          if (window.YT && typeof window.YT.Player === "function") {
            ready();
          }
        });

        return window.__ytIframeApiPromise;
      }

      function setYouTubeEmbedState(section, state) {
        section.dataset.youtubeState = state;
        const loading = section.querySelector("[data-youtube-loading]");
        const player = section.querySelector("[data-youtube-player-slot]");
        const blocked = section.querySelector("[data-youtube-fallback-blocked]");
        const error = section.querySelector("[data-youtube-fallback-error]");
        if (!loading || !player || !blocked || !error) return;

        loading.classList.toggle("hidden", state !== "loading");
        player.classList.toggle("hidden", state !== "player");
        blocked.classList.toggle("hidden", state !== "blocked");
        error.classList.toggle("hidden", state !== "error");
      }

      function initYouTubeEmbed(section) {
        if (section.dataset.youtubeBound === "1") return;
        section.dataset.youtubeBound = "1";

        const videoId = (section.dataset.youtubeVideoId || "").trim();
        const slot = section.querySelector("[data-youtube-player-slot]");
        if (!videoId || !slot) return;

        setYouTubeEmbedState(section, "loading");

        const mount = async () => {
          try {
            await loadYouTubeIframeApi();
            const host = document.createElement("div");
            host.className = "h-full w-full";
            host.id = `yt-player-${videoId}-${Math.random().toString(36).slice(2, 8)}`;
            slot.replaceChildren(host);

            new window.YT.Player(host, {
              host: "https://www.youtube-nocookie.com",
              videoId,
              playerVars: {
                autoplay: 0,
                rel: 0,
                modestbranding: 1,
                playsinline: 1,
                origin: window.location.origin,
              },
              events: {
                onReady: () => {
                  setYouTubeEmbedState(section, "player");
                },
                onError: (event) => {
                  const code = Number(event?.data);
                  if (code === 101 || code === 150) {
                    setYouTubeEmbedState(section, "blocked");
                    return;
                  }
                  setYouTubeEmbedState(section, "error");
                },
              },
            });
          } catch (_err) {
            setYouTubeEmbedState(section, "error");
          }
        };

        const startMount = () => {
          if (section.dataset.youtubeStarted === "1") return;
          section.dataset.youtubeStarted = "1";
          void mount();
        };

        if (!("IntersectionObserver" in window)) {
          startMount();
          return;
        }

        const observer = new IntersectionObserver(
          (entries) => {
            const isVisible = entries.some((entry) => entry.isIntersecting);
            if (!isVisible) return;
            observer.disconnect();
            startMount();
          },
          { rootMargin: "120px 0px" },
        );
        observer.observe(section);
      }

      function bindYouTubeEmbeds(scope) {
        const root = scope instanceof Element ? scope : document;
        root.querySelectorAll("[data-youtube-embed]").forEach(initYouTubeEmbed);
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

      function syncChannelMoveTargetsOrder(orderedIds, defaultCategoryId) {
        const orderedValues = [];
        const defaultValue = String(defaultCategoryId || "").trim();
        if (defaultValue) orderedValues.push(defaultValue);
        orderedIds.forEach((id) => {
          const value = String(id || "").trim();
          if (!value || orderedValues.includes(value)) return;
          orderedValues.push(value);
        });

        document.querySelectorAll("[data-channel-move-target]").forEach((target) => {
          if (!(target instanceof HTMLSelectElement)) return;
          const selectedValue = target.value;
          const options = Array.from(target.options);
          const optionTextByValue = new Map(options.map((opt) => [opt.value, opt.textContent || opt.value]));
          options.forEach((opt) => {
            if (!orderedValues.includes(opt.value)) {
              orderedValues.push(opt.value);
            }
          });

          const nextValues = [...orderedValues];
          target.innerHTML = "";
          nextValues.forEach((value) => {
            if (!optionTextByValue.has(value)) return;
            const option = document.createElement("option");
            option.value = value;
            option.textContent = optionTextByValue.get(value) || value;
            target.appendChild(option);
          });
          if (optionTextByValue.has(selectedValue)) {
            target.value = selectedValue;
          }
        });
      }

      function initCategorySortable(scope) {
        const root = scope instanceof Element ? scope : document;
        root.querySelectorAll("[data-category-list]").forEach((list) => {
          if (list.dataset.sortableBound === "1") return;
          if (typeof Sortable === "undefined") return;
          list.dataset.sortableBound = "1";
          const sidebar = list.closest("#category-sidebar");
          const defaultCategoryId = String(sidebar?.dataset.defaultCategoryId || "").trim();
          const reorderFailedToast = sidebar?.dataset.reorderFailedToast || "Reorder failed";
          Sortable.create(list, {
            handle: "[data-drag-handle]",
            animation: 150,
            onEnd: async () => {
              const items = list.querySelectorAll("[data-category-id]");
              const ordered_ids = [];
              const parsedDefaultId = Number.parseInt(defaultCategoryId, 10);
              if (Number.isInteger(parsedDefaultId) && parsedDefaultId > 0) {
                ordered_ids.push(parsedDefaultId);
              }
              items.forEach((el) => {
                const id = el.getAttribute("data-category-id");
                if (!id) return;
                const parsedId = Number.parseInt(id, 10);
                if (!Number.isInteger(parsedId) || parsedId <= 0) return;
                if (!ordered_ids.includes(parsedId)) {
                  ordered_ids.push(parsedId);
                }
              });
              if (ordered_ids.length === 0) return;
              try {
                const resp = await fetch("/api/categories/reorder", {
                  method: "PUT",
                  headers: { "Content-Type": "application/json" },
                  body: JSON.stringify({ ordered_ids }),
                });
                if (!resp.ok) {
                  showUiToast(reorderFailedToast, "error");
                  return;
                }
                syncChannelMoveTargetsOrder(ordered_ids, defaultCategoryId);
              } catch (_err) {
                showUiToast(reorderFailedToast, "error");
              }
            },
          });
        });
      }

      function bindCategorySortable(scope) {
        initCategorySortable(scope);
      }

      async function handleChannelMoveButtonClick(btn) {
        const wrap = btn.closest("[data-channel-move-category]");
        if (!wrap) return;
        const moveSuccessToast = wrap.dataset.moveToast || "Moved";
        const moveNoneSelectedToast = wrap.dataset.moveNoneSelectedToast || "Select channels to move";
        const moveFailedToast = wrap.dataset.moveFailedToast || "Move failed";
        const select = wrap.querySelector("[data-channel-move-target]");
        if (!select) return;
        const categoryId = select.value;
        if (!categoryId) return;
        const form = btn.closest("form") || btn.closest("[data-channel-manage-form]");
        if (!form) return;
        const checked = form.querySelectorAll("[data-channel-select-item]:checked");
        const channelIds = [];
        checked.forEach((cb) => {
          if (cb.value) channelIds.push(cb.value);
        });
        if (channelIds.length === 0) {
          showUiToast(moveNoneSelectedToast, "info");
          return;
        }
        if (btn.dataset.moveInFlight === "1") return;
        btn.dataset.moveInFlight = "1";
        btn.disabled = true;
        try {
          const resp = await fetch(`/api/categories/${categoryId}/channels`, {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({ channel_ids: channelIds }),
          });
          if (resp.ok) {
            showUiToast(moveSuccessToast, "success");
            const params = new URLSearchParams(window.location.search);
            htmx.ajax("GET", "/views/channel-list?" + params.toString(), {
              target: "#channel-list-wrap",
              swap: "innerHTML",
            });
            htmx.ajax("GET", "/views/category-sidebar?" + params.toString(), {
              target: "#category-sidebar",
              swap: "outerHTML",
            });
          } else {
            let detailMessage = "";
            try {
              const payload = await resp.json();
              if (payload && typeof payload.detail === "string") {
                detailMessage = payload.detail;
              }
            } catch (_ignored) {
              detailMessage = "";
            }
            showUiToast(
              detailMessage ? `${moveFailedToast} (${detailMessage})` : moveFailedToast,
              "error",
            );
          }
        } catch (_err) {
          showUiToast(moveFailedToast, "error");
        } finally {
          delete btn.dataset.moveInFlight;
          btn.disabled = false;
        }
      }

      function initChannelMoveCategory() {
        const root = document.body;
        if (!root || root.dataset.channelMoveDelegatedBound === "1") return;
        root.dataset.channelMoveDelegatedBound = "1";
        root.addEventListener("click", (event) => {
          const target = event.target;
          if (!(target instanceof Element)) return;
          const btn = target.closest("[data-channel-move-submit]");
          if (!(btn instanceof HTMLButtonElement)) return;
          event.preventDefault();
          void handleChannelMoveButtonClick(btn);
        });
      }

      function bindChannelMoveCategory(scope) {
        initChannelMoveCategory();
      }

      function bindCategoryFilterReset(scope) {
        const root = scope instanceof Element ? scope : document;
        root.querySelectorAll("[data-category-filter]").forEach((sel) => {
          if (sel.dataset.filterResetBound === "1") return;
          sel.dataset.filterResetBound = "1";
          sel.addEventListener("change", () => {
            const form = sel.closest("form");
            if (!form) return;
            const channelSel = form.querySelector("[name='channel_id']");
            if (channelSel) channelSel.value = "";
          });
        });
      }

      function initCategoryRename() {
        const root = document.body;
        if (!root || root.dataset.categoryRenameDelegatedBound === "1") return;
        root.dataset.categoryRenameDelegatedBound = "1";
        root.addEventListener("click", async (event) => {
          const target = event.target;
          if (!(target instanceof Element)) return;
          const btn = target.closest("[data-category-rename-trigger]");
          if (!(btn instanceof HTMLButtonElement)) return;
          event.preventDefault();

          const sidebar = document.getElementById("category-sidebar");
          if (!(sidebar instanceof HTMLElement)) return;

          const categoryId = btn.dataset.categoryId;
          const currentName = btn.dataset.categoryName || "";
          if (!categoryId) return;
          if (btn.dataset.renameInFlight === "1") return;

          const renameTitle = sidebar.dataset.renameTitle || "Rename category";
          const renameSuccessToast = sidebar.dataset.renameSuccessToast || "Renamed";
          const renameFailedToast = sidebar.dataset.renameFailedToast || "Rename failed";
          const renameEmptyToast = sidebar.dataset.renameEmptyToast || "Name is required";

          const nextNameRaw = window.prompt(renameTitle, currentName);
          if (nextNameRaw === null) return;
          const nextName = nextNameRaw.trim();
          if (!nextName) {
            showUiToast(renameEmptyToast, "error");
            return;
          }
          if (nextName === currentName.trim()) {
            return;
          }

          btn.dataset.renameInFlight = "1";
          btn.disabled = true;
          try {
            const resp = await fetch(`/api/categories/${encodeURIComponent(categoryId)}`, {
              method: "PUT",
              headers: { "Content-Type": "application/json" },
              body: JSON.stringify({ name: nextName }),
            });
            if (!resp.ok) {
              let detailMessage = "";
              try {
                const payload = await resp.json();
                if (payload && typeof payload.detail === "string") {
                  detailMessage = payload.detail;
                }
              } catch (_ignored) {
                detailMessage = "";
              }
              showUiToast(
                detailMessage ? `${renameFailedToast} (${detailMessage})` : renameFailedToast,
                "error",
              );
              return;
            }

            showUiToast(renameSuccessToast, "success");
            const params = new URLSearchParams(window.location.search);
            const statusFromSidebar = String(sidebar.dataset.currentStatus || "").trim();
            const selectedCategoryId = String(sidebar.dataset.selectedCategoryId || "").trim();
            if (!params.get("status")) {
              params.set("status", statusFromSidebar || "active");
            }
            if (!params.get("category_id") && selectedCategoryId) {
              params.set("category_id", selectedCategoryId);
            }
            htmx.ajax("GET", "/views/channel-list?" + params.toString(), {
              target: "#channel-list-wrap",
              swap: "innerHTML",
            });
            htmx.ajax("GET", "/views/category-sidebar?" + params.toString(), {
              target: "#category-sidebar",
              swap: "outerHTML",
            });
          } catch (_err) {
            showUiToast(renameFailedToast, "error");
          } finally {
            delete btn.dataset.renameInFlight;
            btn.disabled = false;
          }
        });
      }

      function bindCategoryRename() {
        initCategoryRename();
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
        if (requestElt instanceof Element) {
          const downloadSettingsForm = requestElt.closest("[data-download-settings-form]");
          if (downloadSettingsForm instanceof HTMLFormElement) {
            setDownloadOutputDirError(downloadSettingsForm, "");
          }
        }
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
      document.addEventListener("htmx:responseError", (event) => {
        const requestElt = event.detail?.requestConfig?.elt;
        if (!(requestElt instanceof Element)) return;
        const form = requestElt.closest("[data-download-settings-form]");
        if (!(form instanceof HTMLFormElement)) return;
        const payload = parseJsonSafe(event.detail?.xhr?.responseText || "");
        const detail = payload && typeof payload === "object" ? payload.detail : "";
        const message = resolveDownloadPathErrorMessage(detail);
        setDownloadOutputDirError(form, message);
        showUiToast(message, "error");
      });
      document.addEventListener("input", (event) => {
        const target = event.target;
        if (!(target instanceof HTMLInputElement)) return;
        if (target.name !== "download_output_dir") return;
        const form = target.closest("[data-download-settings-form]");
        if (!(form instanceof HTMLFormElement)) return;
        if (target.hasAttribute("aria-invalid")) {
          setDownloadOutputDirError(form, "");
        }
      });
      document.addEventListener("htmx:afterSwap", (event) => {
        hydrateUiScope(event.target);
        bindCategoryRename();
      });
    })();
