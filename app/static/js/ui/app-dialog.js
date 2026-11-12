(() => {
  // Shared in-app confirm/prompt modal. The service never relies on native browser dialogs;
  // window.confirm/prompt below are only a safety net when rendering the modal throws.
  const UI_TEXT = {
    title: "확인",
    confirm: "확인",
    cancel: "취소",
    ...((window.BRIEFTUBE_UI_BOOTSTRAP || {}).dialog || {}),
  };
  const FOCUSABLE_SELECTOR = "button:not([disabled]), input:not([disabled])";
  const CONFIRM_BUTTON_CLASSES = {
    danger: "bg-rose-600 hover:bg-rose-700",
    default: "bg-indigo-600 hover:bg-indigo-700",
  };

  let activeDialog = null;

  function ensureRoot() {
    let root = document.getElementById("app-dialog");
    if (root) return root;
    root = document.createElement("div");
    root.id = "app-dialog";
    root.setAttribute("data-app-dialog", "");
    root.className = "fixed inset-0 z-[80] hidden items-center justify-center px-4";
    root.innerHTML = `
      <div class="absolute inset-0 bg-slate-900/45" data-app-dialog-backdrop></div>
      <div
        class="relative w-full max-w-md rounded-xl border border-slate-200 bg-white p-5 shadow-2xl"
        role="dialog"
        aria-modal="true"
        aria-labelledby="app-dialog-title"
        aria-describedby="app-dialog-message"
        data-app-dialog-panel
      >
        <h3 id="app-dialog-title" class="text-base font-semibold text-slate-900" data-app-dialog-title></h3>
        <p
          id="app-dialog-message"
          class="mt-2 max-h-[50vh] overflow-y-auto whitespace-pre-line break-words text-sm text-slate-600"
          data-app-dialog-message
        ></p>
        <input
          type="text"
          class="mt-3 hidden w-full rounded-md border border-slate-300 bg-white px-3 py-2 text-sm text-slate-900"
          data-app-dialog-input
        />
        <div class="mt-4 flex justify-end gap-2">
          <button
            type="button"
            class="rounded-md border border-slate-300 bg-white px-3 py-2 text-sm font-medium text-slate-700 hover:bg-slate-50"
            data-app-dialog-cancel
          ></button>
          <button
            type="button"
            class="rounded-md px-3 py-2 text-sm font-medium text-white"
            data-app-dialog-confirm
          ></button>
        </div>
      </div>`;
    document.body.appendChild(root);
    return root;
  }

  function open(options, mode) {
    if (activeDialog) return Promise.resolve(mode === "prompt" ? null : false);
    const root = ensureRoot();
    const titleNode = root.querySelector("[data-app-dialog-title]");
    const messageNode = root.querySelector("[data-app-dialog-message]");
    const input = root.querySelector("[data-app-dialog-input]");
    const cancelButton = root.querySelector("[data-app-dialog-cancel]");
    const confirmButton = root.querySelector("[data-app-dialog-confirm]");
    const panel = root.querySelector("[data-app-dialog-panel]");
    if (!titleNode || !messageNode || !input || !cancelButton || !confirmButton || !panel) {
      throw new Error("app_dialog_markup_missing");
    }

    const tone = options?.tone === "danger" ? "danger" : "default";
    titleNode.textContent = options?.title || UI_TEXT.title;
    messageNode.textContent = options?.message || "";
    messageNode.classList.toggle("hidden", !options?.message);
    cancelButton.textContent = options?.cancelLabel || UI_TEXT.cancel;
    confirmButton.textContent = options?.confirmLabel || UI_TEXT.confirm;
    confirmButton.classList.remove(...Object.values(CONFIRM_BUTTON_CLASSES).join(" ").split(" "));
    confirmButton.classList.add(...CONFIRM_BUTTON_CLASSES[tone].split(" "));
    root.dataset.tone = tone;
    root.dataset.mode = mode;
    input.classList.toggle("hidden", mode !== "prompt");
    input.value = mode === "prompt" ? String(options?.value ?? "") : "";

    const previousFocus = document.activeElement instanceof HTMLElement ? document.activeElement : null;
    root.classList.remove("hidden");
    root.classList.add("flex");

    return new Promise((resolve) => {
      const finalize = (confirmed) => {
        if (activeDialog !== state) return;
        activeDialog = null;
        root.classList.add("hidden");
        root.classList.remove("flex");
        document.removeEventListener("keydown", onKeydown, true);
        cancelButton.removeEventListener("click", onCancel);
        confirmButton.removeEventListener("click", onConfirm);
        root.removeEventListener("click", onBackdropClick);
        if (previousFocus?.isConnected) previousFocus.focus();
        if (mode === "prompt") {
          resolve(confirmed ? input.value : null);
        } else {
          resolve(confirmed);
        }
      };
      const onCancel = () => finalize(false);
      const onConfirm = () => finalize(true);
      const onBackdropClick = (event) => {
        if (event.target instanceof Element && event.target.hasAttribute("data-app-dialog-backdrop")) {
          finalize(false);
        }
      };
      const onKeydown = (event) => {
        if (event.key === "Escape") {
          event.preventDefault();
          event.stopPropagation();
          finalize(false);
          return;
        }
        if (event.key === "Enter" && event.target === input) {
          event.preventDefault();
          finalize(true);
          return;
        }
        if (event.key === "Tab") {
          const focusables = Array.from(panel.querySelectorAll(FOCUSABLE_SELECTOR))
            .filter((node) => node instanceof HTMLElement && !node.classList.contains("hidden"));
          if (!focusables.length) return;
          const first = focusables[0];
          const last = focusables[focusables.length - 1];
          if (!panel.contains(document.activeElement)) {
            event.preventDefault();
            first.focus();
          } else if (event.shiftKey && document.activeElement === first) {
            event.preventDefault();
            last.focus();
          } else if (!event.shiftKey && document.activeElement === last) {
            event.preventDefault();
            first.focus();
          }
        }
      };
      const state = { finalize };
      activeDialog = state;
      cancelButton.addEventListener("click", onCancel);
      confirmButton.addEventListener("click", onConfirm);
      root.addEventListener("click", onBackdropClick);
      document.addEventListener("keydown", onKeydown, true);

      if (mode === "prompt") {
        input.focus();
        input.select();
      } else if (tone === "danger") {
        cancelButton.focus();
      } else {
        confirmButton.focus();
      }
    });
  }

  function confirm(options = {}) {
    try {
      return open(options, "confirm");
    } catch (_error) {
      return Promise.resolve(window.confirm(options.message || options.title || ""));
    }
  }

  function prompt(options = {}) {
    try {
      return open(options, "prompt");
    } catch (_error) {
      return Promise.resolve(window.prompt(options.title || options.message || "", options.value ?? ""));
    }
  }

  function confirmOptionsFromElement(elt, question) {
    const source = elt instanceof Element ? elt.closest("[data-confirm-title], [hx-confirm]") || elt : null;
    return {
      title: source?.getAttribute("data-confirm-title") || "",
      message: question,
      confirmLabel: source?.getAttribute("data-confirm-label") || "",
      tone: source?.getAttribute("data-confirm-tone") || "default",
    };
  }

  // htmx fires htmx:confirm before applying hx-sync, so without this check a modal would open
  // for a request that hx-sync="...:drop" will discard right after the user confirms.
  function isDroppedBySync(elt) {
    if (!(elt instanceof Element)) return false;
    const spec = elt.getAttribute("hx-sync");
    if (!spec) return false;
    const [rawSelector, rawStrategy] = spec.split(":");
    if ((rawStrategy || "drop").trim() !== "drop") return false;
    const selector = (rawSelector || "").trim();
    if (selector === "this") return elt.classList.contains("htmx-request");
    // Only requests that sync on the same element make htmx drop this one.
    return Array.from(document.querySelectorAll(".htmx-request[hx-sync]")).some((node) => (
      String(node.getAttribute("hx-sync") || "").split(":")[0].trim() === selector
    ));
  }

  // hx-confirm stays the source of the question; this shows it in the app modal instead of
  // the browser dialog. Element/form-level listeners that already handled the event win.
  function bindHtmxConfirm() {
    if (document.documentElement.dataset.appDialogHtmxBound === "1") return;
    document.documentElement.dataset.appDialogHtmxBound = "1";
    document.addEventListener("htmx:confirm", (event) => {
      if (event.defaultPrevented) return;
      const detail = event.detail;
      const question = detail?.question;
      if (!question) return;
      event.preventDefault();
      if (isDroppedBySync(detail.elt)) return;
      confirm(confirmOptionsFromElement(detail.elt, question)).then((confirmed) => {
        if (confirmed) detail.issueRequest(true);
      });
    });
  }

  window.BrieftubeDialog = {
    confirm,
    prompt,
    bindHtmxConfirm,
    isDroppedBySync,
    isOpen: () => activeDialog !== null,
  };
  bindHtmxConfirm();
})();
