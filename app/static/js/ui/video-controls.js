(() => {
  let showToast = () => {};
  let refreshVideoDetail = () => {};
  let lastVideoListArticlePreviewKey = "";
  let lastVideoListArticlePreviewScrollTop = 0;

  function configure(options = {}) {
    if (typeof options.showUiToast === "function") {
      showToast = options.showUiToast;
    }
    if (typeof options.refreshVideoDetailFragmentNow === "function") {
      refreshVideoDetail = options.refreshVideoDetailFragmentNow;
    }
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

  function closeArticlePreviewModal(modal) {
    if (!(modal instanceof HTMLElement)) return;
    rememberVideoListArticlePreviewScroll(modal);
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

  function getArticlePreviewScroller(modal) {
    if (!(modal instanceof HTMLElement)) return null;
    const scroller = modal.querySelector("[data-article-preview-scroll]");
    return scroller instanceof HTMLElement ? scroller : null;
  }

  function rememberVideoListArticlePreviewScroll(modal) {
    const targetModal = modal || document.querySelector("[data-video-list-article-modal]");
    if (!(targetModal instanceof HTMLElement)) return;
    if (!targetModal.hasAttribute("data-video-list-article-modal")) return;
    if (targetModal.dataset.modalOpen === "0") return;
    lastVideoListArticlePreviewKey = targetModal.dataset.articlePreviewKey || "";
    lastVideoListArticlePreviewScrollTop = getArticlePreviewScroller(targetModal)?.scrollTop || 0;
  }

  function restoreVideoListArticlePreviewScroll(modal, previewKey) {
    const scroller = getArticlePreviewScroller(modal);
    if (!scroller) return;
    scroller.scrollTop = previewKey === lastVideoListArticlePreviewKey
      ? lastVideoListArticlePreviewScrollTop
      : 0;
  }

  function formatTemplate(template, values) {
    return String(template || "").replace(/\{(\w+)\}/g, (_, key) => (
      values[key] != null ? String(values[key]) : ""
    ));
  }

  function listArticlePreviewButtons() {
    const wrap = document.getElementById("video-list-wrap");
    if (!(wrap instanceof HTMLElement)) return [];
    return Array.from(wrap.querySelectorAll("[data-video-article-preview-load]"))
      .filter((node) => node instanceof HTMLButtonElement);
  }

  function syncListArticlePreviewNav(modal, previewKey) {
    if (!(modal instanceof HTMLElement) || !modal.hasAttribute("data-video-list-article-modal")) {
      return;
    }
    const buttons = listArticlePreviewButtons();
    const index = buttons.findIndex((btn) => (btn.dataset.articlePreviewUrl || "") === previewKey);
    const total = buttons.length;
    const position = modal.querySelector("[data-article-preview-position]");
    const prevBtn = modal.querySelector("[data-article-preview-prev]");
    const nextBtn = modal.querySelector("[data-article-preview-next]");
    const positionTemplate = position?.dataset.positionTemplate
      || "{current} / {total}";
    if (position instanceof HTMLElement) {
      if (total > 0 && index >= 0) {
        position.hidden = false;
        position.textContent = formatTemplate(positionTemplate, {
          current: index + 1,
          total,
        });
      } else {
        position.hidden = true;
      }
    }
    if (prevBtn instanceof HTMLButtonElement) {
      prevBtn.disabled = index <= 0 || total === 0;
      prevBtn.dataset.navIndex = String(index);
    }
    if (nextBtn instanceof HTMLButtonElement) {
      nextBtn.disabled = index < 0 || index >= total - 1;
      nextBtn.dataset.navIndex = String(index);
    }
  }

  async function loadVideoListArticlePreview(previewUrl, { resetScroll = false } = {}) {
    const response = await fetch(previewUrl || "", {
      headers: { "Accept": "text/html", "HX-Request": "true" },
    });
    if (!response.ok) throw new Error("article preview load failed");
    const html = await response.text();
    if (!resetScroll) {
      rememberVideoListArticlePreviewScroll();
    } else {
      lastVideoListArticlePreviewKey = "";
      lastVideoListArticlePreviewScrollTop = 0;
    }
    document.querySelectorAll("[data-video-list-article-modal]").forEach((node) => {
      node.remove();
    });
    const container = document.createElement("div");
    container.innerHTML = html.trim();
    const modal = container.querySelector("[data-article-preview-modal]");
    if (!(modal instanceof HTMLElement)) throw new Error("article preview modal missing");
    modal.dataset.articlePreviewKey = previewUrl;
    document.body.appendChild(modal);
    ensureArticlePreviewEscapeHandler();
    ensureListSwapClosesArticleModal();
    setupArticlePreviewModal(modal);
    bindCopyButtons(modal);
    openArticlePreviewModal(modal);
    restoreVideoListArticlePreviewScroll(modal, previewUrl);
    syncListArticlePreviewNav(modal, previewUrl);
    return modal;
  }

  function getOpenListArticlePreviewModal() {
    const modal = document.querySelector(
      "[data-video-list-article-modal][data-modal-open='1']",
    );
    return modal instanceof HTMLElement ? modal : null;
  }

  async function navigateOpenListArticlePreview(delta) {
    const modal = getOpenListArticlePreviewModal();
    if (!modal) return false;
    const previewKey = modal.dataset.articlePreviewKey || "";
    const buttons = listArticlePreviewButtons();
    const index = buttons.findIndex((btn) => (btn.dataset.articlePreviewUrl || "") === previewKey);
    const next = buttons[index + delta];
    if (!(next instanceof HTMLButtonElement)) return false;
    const nextUrl = next.dataset.articlePreviewUrl || "";
    if (!nextUrl) return false;
    try {
      await loadVideoListArticlePreview(nextUrl, { resetScroll: true });
      return true;
    } catch (_err) {
      showToast(next.dataset.loadFailed || "Could not load article.", "error");
      return false;
    }
  }

  function isEditableKeyTarget(target) {
    if (!(target instanceof HTMLElement)) return false;
    const tag = target.tagName;
    if (tag === "INPUT" || tag === "TEXTAREA" || tag === "SELECT") return true;
    if (target.isContentEditable) return true;
    return Boolean(target.closest("input, textarea, select, [contenteditable='true']"));
  }

  function setupArticlePreviewModal(modal) {
    if (!(modal instanceof HTMLElement) || modal.dataset.articlePreviewModalBound === "1") return;
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
    modal.querySelector("[data-article-preview-prev]")?.addEventListener("click", (event) => {
      event.preventDefault();
      event.stopPropagation();
      void navigateOpenListArticlePreview(-1);
    });
    modal.querySelector("[data-article-preview-next]")?.addEventListener("click", (event) => {
      event.preventDefault();
      event.stopPropagation();
      void navigateOpenListArticlePreview(1);
    });
  }

  function ensureArticlePreviewEscapeHandler() {
    const root = document.body;
    if (!root || root.dataset.articlePreviewEscapeBound === "1") return;
    root.dataset.articlePreviewEscapeBound = "1";
    document.addEventListener("keydown", (event) => {
      if (event.key === "Escape") {
        document.querySelectorAll("[data-article-preview-modal]").forEach((node) => {
          if (!(node instanceof HTMLElement)) return;
          if (node.dataset.modalOpen === "1") {
            closeArticlePreviewModal(node);
          }
        });
        return;
      }

      // List article modal: ←/j 이전, →/k 다음 (입력 포커스 중에는 무시)
      if (isEditableKeyTarget(event.target)) return;
      if (event.altKey || event.ctrlKey || event.metaKey) return;
      const openListModal = getOpenListArticlePreviewModal();
      if (!openListModal) return;

      const key = event.key;
      if (key === "ArrowLeft" || key === "j" || key === "J") {
        event.preventDefault();
        void navigateOpenListArticlePreview(-1);
        return;
      }
      if (key === "ArrowRight" || key === "k" || key === "K") {
        event.preventDefault();
        void navigateOpenListArticlePreview(1);
      }
    });
  }

  function ensureListSwapClosesArticleModal() {
    const root = document.body;
    if (!root || root.dataset.articlePreviewListSwapBound === "1") return;
    root.dataset.articlePreviewListSwapBound = "1";
    document.body.addEventListener("htmx:beforeSwap", (event) => {
      const target = event.detail?.target;
      if (!(target instanceof Element)) return;
      if (target.id !== "video-list-wrap" && !target.querySelector?.("#video-list-wrap")) return;
      document.querySelectorAll("[data-video-list-article-modal]").forEach((node) => {
        if (node instanceof HTMLElement) node.remove();
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
    setupArticlePreviewModal(modal);

    button.addEventListener("click", (event) => {
      event.preventDefault();
      event.stopPropagation();
      openArticlePreviewModal(modal);
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
      const failedMsg = btn.dataset.copyFailed || "Copy failed";
      if (!navigator.clipboard?.writeText) {
        showToast(failedMsg, "error");
        return;
      }
      navigator.clipboard.writeText(text.trim())
        .then(() => showToast(toastMsg, "success"))
        .catch(() => showToast(failedMsg, "error"));
    });
  }

  function bindCopyButtons(scope) {
    const root = scope instanceof Element ? scope : document;
    root.querySelectorAll("[data-copy-target]").forEach(initCopyButton);
  }

  function initVideoTranscriptCopyButton(button) {
    if (button.dataset.videoTranscriptCopyBound === "1") return;
    button.dataset.videoTranscriptCopyBound = "1";

    button.addEventListener("click", async () => {
      if (button.dataset.videoTranscriptCopyInFlight === "1") return;
      button.dataset.videoTranscriptCopyInFlight = "1";
      button.disabled = true;
      try {
        const response = await fetch(button.dataset.transcriptUrl || "", {
          headers: { "Accept": "application/json" },
        });
        if (!response.ok) throw new Error("transcript copy failed");
        const payload = await response.json();
        const rawText = typeof payload.raw_text === "string" ? payload.raw_text.trim() : "";
        if (!rawText) throw new Error("transcript copy failed");
        await navigator.clipboard.writeText(rawText);
        showToast(button.dataset.copyToast || "Copied", "success");
      } catch (_err) {
        showToast(button.dataset.copyFailed || "Copy failed", "error");
      } finally {
        delete button.dataset.videoTranscriptCopyInFlight;
        button.disabled = false;
      }
    });
  }

  function bindVideoTranscriptCopyButtons(scope) {
    const root = scope instanceof Element ? scope : document;
    root.querySelectorAll("[data-video-transcript-copy]").forEach((button) => {
      if (button instanceof HTMLButtonElement) {
        initVideoTranscriptCopyButton(button);
      }
    });
  }

  function initVideoArticlePreviewLoadButton(button) {
    if (button.dataset.videoArticlePreviewLoadBound === "1") return;
    button.dataset.videoArticlePreviewLoadBound = "1";

    button.addEventListener("click", async () => {
      if (button.dataset.videoArticlePreviewLoadInFlight === "1") return;
      button.dataset.videoArticlePreviewLoadInFlight = "1";
      button.disabled = true;
      const previewKey = button.dataset.articlePreviewUrl || "";
      try {
        await loadVideoListArticlePreview(previewKey);
      } catch (_err) {
        showToast(button.dataset.loadFailed || "Could not load article.", "error");
      } finally {
        delete button.dataset.videoArticlePreviewLoadInFlight;
        button.disabled = false;
      }
    });
  }

  function bindVideoArticlePreviewLoadButtons(scope) {
    const root = scope instanceof Element ? scope : document;
    ensureListSwapClosesArticleModal();
    root.querySelectorAll("[data-video-article-preview-load]").forEach((button) => {
      if (button instanceof HTMLButtonElement) {
        initVideoArticlePreviewLoadButton(button);
      }
    });
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
      const target = event.target instanceof Element ? event.target : event.target?.parentElement;
      if (target?.closest("button,a,input,select,textarea,label,form")) return;
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
    root.querySelectorAll("[data-thumb-hover]").forEach((el) => {
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

  function initVideoManageForm(form) {
    if (form.dataset.videoManageBound === "1") return;
    form.dataset.videoManageBound = "1";

    const selectAll = form.querySelector("[data-video-select-all]");
    const deleteBtn = form.querySelector("[data-video-delete-selected]");
    const downloadBtn = form.querySelector("[data-video-download-selected]");
    const articleBtn = form.querySelector("[data-video-article-request-selected]");
    const selectEligibleBtn = form.querySelector("[data-video-select-eligible]");
    const selectHasArticleBtn = form.querySelector("[data-video-select-has-article]");
    const selectNoneBtn = form.querySelector("[data-video-select-none]");
    const isDisplayed = (el) => {
      if (!(el instanceof Element)) return false;
      let node = el;
      while (node && node !== document.documentElement) {
        const st = window.getComputedStyle(node);
        if (st.display === "none" || st.visibility === "hidden") return false;
        node = node.parentElement;
      }
      return true;
    };
    const allItems = () => Array.from(form.querySelectorAll("[data-video-select-item]"));
    const items = () => allItems().filter(isDisplayed);
    const syncItemSubmitState = () => {
      allItems().forEach((cb) => {
        const visible = isDisplayed(cb);
        cb.disabled = !visible;
        if (!visible) cb.checked = false;
      });
    };
    if (!selectAll || !deleteBtn || !downloadBtn || !articleBtn) return;

    const downloadDefaultLabel = (downloadBtn.textContent || "").trim();
    const downloadBusyLabel = downloadBtn.dataset.busyLabel || downloadDefaultLabel;
    const articleDefaultLabel = articleBtn.dataset.labelDefault
      || articleBtn.querySelector("[data-video-bulk-article-text]")?.textContent?.trim()
      || (articleBtn.textContent || "").trim();
    const articleBusyLabel = articleBtn.dataset.busyLabel || articleDefaultLabel;
    const articleTextEl = articleBtn.querySelector("[data-video-bulk-article-text]");
    const articleCountEl = articleBtn.querySelector("[data-video-bulk-article-count]");

    function setArticleButtonLabel(checked, isArticleBusy) {
      if (articleTextEl instanceof HTMLElement) {
        articleTextEl.textContent = isArticleBusy ? articleBusyLabel : articleDefaultLabel;
      } else if (!isArticleBusy) {
        articleBtn.textContent = articleDefaultLabel;
      } else {
        articleBtn.textContent = articleBusyLabel;
      }
      if (articleCountEl instanceof HTMLElement) {
        if (isArticleBusy) {
          articleCountEl.classList.add("hidden");
        } else {
          articleCountEl.classList.remove("hidden");
          articleCountEl.textContent = `(${checked})`;
        }
      }
    }

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
      const checked = items().filter((cb) => cb.checked).length;
      setArticleButtonLabel(checked, isArticleBusy);
      sync();
    }

    function sync() {
      syncItemSubmitState();
      const all = items();
      const checked = all.filter((cb) => cb.checked).length;
      const isDownloadBusy = form.dataset.videoDownloadBulkInFlight === "1";
      const isArticleBusy = form.dataset.videoArticleRequestBulkInFlight === "1";
      const isAnyBusy = isDownloadBusy || isArticleBusy;
      deleteBtn.disabled = checked === 0;
      downloadBtn.disabled = checked === 0 || isAnyBusy;
      articleBtn.disabled = checked === 0 || isAnyBusy;
      setArticleButtonLabel(checked, isArticleBusy);
      selectAll.checked = all.length > 0 && checked === all.length;
      selectAll.indeterminate = checked > 0 && checked < all.length;
    }

    function selectByPredicate(predicate) {
      syncItemSubmitState();
      items().forEach((cb) => {
        cb.checked = predicate(cb);
      });
      sync();
    }

    selectAll.addEventListener("change", () => {
      syncItemSubmitState();
      items().forEach((cb) => {
        cb.checked = selectAll.checked;
      });
      sync();
    });
    selectEligibleBtn?.addEventListener("click", () => {
      selectByPredicate((cb) => cb.getAttribute("data-article-eligible") === "1");
    });
    selectHasArticleBtn?.addEventListener("click", () => {
      selectByPredicate((cb) => cb.getAttribute("data-has-article") === "1");
    });
    selectNoneBtn?.addEventListener("click", () => {
      selectByPredicate(() => false);
    });
    form.addEventListener("change", (event) => {
      if (event.target.matches("[data-video-select-item]")) sync();
    });
    form.addEventListener("submit", () => {
      syncItemSubmitState();
    });
    form.addEventListener("htmx:configRequest", () => {
      syncItemSubmitState();
    });
    window.addEventListener("resize", () => {
      sync();
    });
    downloadBtn.addEventListener("click", (event) => {
      const isBusy = form.dataset.videoDownloadBulkInFlight === "1"
        || form.dataset.videoArticleRequestBulkInFlight === "1";
      if (isBusy) {
        event.preventDefault();
        return;
      }
      syncItemSubmitState();
      setBulkBusy("download", true);
    });
    articleBtn.addEventListener("click", (event) => {
      const isBusy = form.dataset.videoDownloadBulkInFlight === "1"
        || form.dataset.videoArticleRequestBulkInFlight === "1";
      if (isBusy) {
        event.preventDefault();
        return;
      }
      syncItemSubmitState();
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

    form.querySelectorAll("[data-video-list-inline-article]").forEach((button) => {
      if (!(button instanceof HTMLButtonElement) || button.dataset.inlineArticleBound === "1") return;
      button.dataset.inlineArticleBound = "1";
      button.dataset.defaultLabel = (button.textContent || "").trim();
      button.addEventListener("click", (event) => {
        if (button.dataset.inlineInFlight === "1") {
          event.preventDefault();
          return;
        }
        button.dataset.inlineInFlight = "1";
        button.disabled = true;
        if (button.dataset.busyLabel) {
          button.textContent = button.dataset.busyLabel;
        }
      });
    });

    sync();
  }

  function ensureInlineArticleRequestSettle() {
    const root = document.body;
    if (!root || root.dataset.inlineArticleSettleBound === "1") return;
    root.dataset.inlineArticleSettleBound = "1";
    const settle = (event) => {
      const source = event.detail?.requestConfig?.elt;
      if (!(source instanceof HTMLButtonElement)) return;
      if (!source.hasAttribute("data-video-list-inline-article")) return;
      delete source.dataset.inlineInFlight;
      source.disabled = false;
      if (source.dataset.defaultLabel) {
        source.textContent = source.dataset.defaultLabel;
      }
    };
    document.body.addEventListener("htmx:afterRequest", settle);
    document.body.addEventListener("htmx:responseError", settle);
    document.body.addEventListener("htmx:sendError", settle);
    document.body.addEventListener("htmx:timeout", settle);
  }

  function bindVideoManageForms(scope) {
    const root = scope instanceof Element ? scope : document;
    ensureInlineArticleRequestSettle();
    root.querySelectorAll("[data-video-manage-form]").forEach(initVideoManageForm);
  }

  function initVideoRequestButton(button, datasetPrefix) {
    const boundKey = `${datasetPrefix}Bound`;
    const inFlightKey = `${datasetPrefix}InFlight`;
    if (button.dataset[boundKey] === "1") return;
    button.dataset[boundKey] = "1";

    const defaultLabel = button.textContent || "";
    const busyLabel = button.dataset.busyLabel || defaultLabel;
    const owner = button.closest("#video-detail-fragment") || document.body;

    function setBusy(isBusy) {
      if (isBusy) {
        button.dataset[inFlightKey] = "1";
      } else {
        delete button.dataset[inFlightKey];
      }
      button.disabled = isBusy;
      button.textContent = isBusy ? busyLabel : defaultLabel;
    }

    button.addEventListener("click", (event) => {
      if (button.dataset[inFlightKey] === "1") {
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
        void refreshVideoDetail();
      }
    };
    owner.addEventListener("htmx:afterRequest", settle);
    owner.addEventListener("htmx:responseError", settle);
    owner.addEventListener("htmx:sendError", settle);
    owner.addEventListener("htmx:timeout", settle);
  }

  function bindVideoArticleRequestButtons(scope) {
    const root = scope instanceof Element ? scope : document;
    root.querySelectorAll("[data-video-article-request-button]").forEach((button) => {
      initVideoRequestButton(button, "videoArticleRequest");
    });
  }

  function bindVideoTranscriptRequestButtons(scope) {
    const root = scope instanceof Element ? scope : document;
    root.querySelectorAll("[data-video-transcript-request-button]").forEach((button) => {
      initVideoRequestButton(button, "videoTranscriptRequest");
    });
  }

  window.BrieftubeVideoControls = {
    configure,
    bindRetentionForms,
    bindRetentionNotices,
    bindVideoManageForms,
    bindVideoArticleRequestButtons,
    bindVideoTranscriptRequestButtons,
    bindArticlePreviewModals,
    bindCopyButtons,
    bindVideoTranscriptCopyButtons,
    bindVideoArticlePreviewLoadButtons,
    bindCollapsibles,
    bindThumbPreviews,
  };
})();
