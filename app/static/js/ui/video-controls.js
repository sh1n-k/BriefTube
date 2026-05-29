(() => {
  let showToast = () => {};
  let refreshVideoDetail = () => {};

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
        showToast(toastMsg, "success");
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
      items().forEach((cb) => {
        cb.checked = selectAll.checked;
      });
      sync();
    });
    form.addEventListener("change", (event) => {
      if (event.target.matches("[data-video-select-item]")) sync();
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
    bindCollapsibles,
    bindThumbPreviews,
  };
})();
