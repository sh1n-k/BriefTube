(() => {
  const MATCH_CLASSES = ["bg-amber-50", "ring-1", "ring-inset", "ring-amber-200"];
  const ACTIVE_CLASSES = ["bg-indigo-100", "ring-indigo-300"];

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

  const BULK_DELETE_CONFIRM_NAME_LIMIT = 10;
  let channelDeleteConfirmBound = false;

  function formatText(template, values) {
    return String(template || "").replace(/\{(\w+)\}/g, (match, key) => (
      Object.prototype.hasOwnProperty.call(values, key) ? String(values[key]) : match
    ));
  }

  function isBulkDeleteRequest(detail) {
    const form = detail?.elt;
    if (!(form instanceof HTMLFormElement) || !form.matches("[data-channel-manage-form]")) return false;
    if (String(detail?.path || "").includes("/channels/delete-selected")) return true;
    const submitter = detail?.triggeringEvent?.submitter;
    return submitter instanceof HTMLButtonElement
      && submitter.name === "bulk_action"
      && submitter.value === "delete";
  }

  function buildBulkDeleteConfirmMessage(form) {
    const names = Array.from(form.querySelectorAll("[data-channel-select-item]"))
      .filter((item) => item instanceof HTMLInputElement && item.checked)
      .map((item) => item.dataset.channelName || item.value);
    if (!names.length) return "";
    const shown = names.slice(0, BULK_DELETE_CONFIRM_NAME_LIMIT).map((name) => `- ${name}`);
    const hiddenCount = names.length - shown.length;
    if (hiddenCount > 0) {
      shown.push(formatText(form.dataset.channelDeleteConfirmMore, { count: hiddenCount }));
    }
    return formatText(form.dataset.channelDeleteConfirmBulk, {
      count: names.length,
      channels: shown.join("\n"),
    });
  }

  function bindChannelDeleteConfirm() {
    if (channelDeleteConfirmBound) return;
    channelDeleteConfirmBound = true;
    document.addEventListener("htmx:confirm", (event) => {
      const detail = event.detail;
      if (!isBulkDeleteRequest(detail)) return;
      const message = buildBulkDeleteConfirmMessage(detail.elt);
      if (!message) return;
      event.preventDefault();
      if (window.confirm(message)) {
        detail.issueRequest(true);
      }
    });
  }

  function captureChannelListScroll(wrap) {
    if (!(wrap instanceof Element)) return null;
    const fragment = wrap.querySelector("[data-channel-list-fragment]");
    const scroller = wrap.querySelector("[data-channel-scroll]");
    if (!(fragment instanceof HTMLElement) || !(scroller instanceof HTMLElement)) return null;
    return {
      key: fragment.dataset.channelListRefreshUrl || "",
      top: scroller.scrollTop,
    };
  }

  function restoreChannelListScroll(wrap, snapshot) {
    if (!snapshot || !(wrap instanceof Element)) return;
    const fragment = wrap.querySelector("[data-channel-list-fragment]");
    const scroller = wrap.querySelector("[data-channel-scroll]");
    if (!(fragment instanceof HTMLElement) || !(scroller instanceof HTMLElement)) return;
    if ((fragment.dataset.channelListRefreshUrl || "") !== snapshot.key) return;
    scroller.scrollTop = snapshot.top;
  }

  window.BrieftubeChannelListControls = {
    bindChannelSearch,
    bindChannelManageForms,
    bindChannelMetaAccordion,
    bindChannelAvatars,
    bindChannelDeleteConfirm,
    captureChannelListScroll,
    restoreChannelListScroll,
  };
})();
