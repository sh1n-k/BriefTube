(() => {
  let showToast = () => {};

  function configure(options = {}) {
    if (typeof options.showUiToast === "function") {
      showToast = options.showUiToast;
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
              showToast(reorderFailedToast, "error");
              return;
            }
            syncChannelMoveTargetsOrder(ordered_ids, defaultCategoryId);
          } catch (_err) {
            showToast(reorderFailedToast, "error");
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
      showToast(moveNoneSelectedToast, "info");
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
        showToast(moveSuccessToast, "success");
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
        showToast(
          detailMessage ? `${moveFailedToast} (${detailMessage})` : moveFailedToast,
          "error",
        );
      }
    } catch (_err) {
      showToast(moveFailedToast, "error");
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

  function bindChannelMoveCategory() {
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
        showToast(renameEmptyToast, "error");
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
          showToast(
            detailMessage ? `${renameFailedToast} (${detailMessage})` : renameFailedToast,
            "error",
          );
          return;
        }

        showToast(renameSuccessToast, "success");
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
        showToast(renameFailedToast, "error");
      } finally {
        delete btn.dataset.renameInFlight;
        btn.disabled = false;
      }
    });
  }

  function bindCategoryRename() {
    initCategoryRename();
  }

  window.BrieftubeCategoryControls = {
    configure,
    bindCategorySortable,
    bindChannelMoveCategory,
    bindCategoryFilterReset,
    bindCategoryRename,
  };
})();
