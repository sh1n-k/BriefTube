(() => {
  const bootstrap = window.BRIEFTUBE_UI_BOOTSTRAP || {};
  const text = {
    pathErrorEmpty: "다운로드 저장 경로를 입력해 주세요.",
    pathErrorMustBeAbsolute: "다운로드 저장 경로는 절대 경로여야 합니다.",
    pathErrorInvalid: "다운로드 저장 경로 형식이 올바르지 않습니다.",
    pathErrorNotFound: "다운로드 저장 경로가 존재하지 않습니다.",
    pathErrorNotDirectory: "다운로드 저장 경로는 디렉터리여야 합니다.",
    pathErrorNotWritable: "다운로드 저장 경로에 쓰기 권한이 없습니다.",
    pathErrorGeneric: "다운로드 저장 경로를 확인해 주세요.",
    ...(bootstrap.downloadSettings || {}),
  };
  let showToast = () => {};
  let handlersBound = false;

  function configure(options = {}) {
    if (typeof options.showUiToast === "function") showToast = options.showUiToast;
  }

  function parseJsonSafe(value) {
    try {
      return JSON.parse(value || "");
    } catch (_err) {
      return null;
    }
  }

  function resolvePathErrorMessage(detail) {
    const code = String(detail || "").trim().toLowerCase();
    const messages = {
      download_path_empty: text.pathErrorEmpty,
      download_path_must_be_absolute: text.pathErrorMustBeAbsolute,
      download_path_invalid: text.pathErrorInvalid,
      download_path_not_found: text.pathErrorNotFound,
      download_path_not_directory: text.pathErrorNotDirectory,
      download_path_not_writable: text.pathErrorNotWritable,
    };
    if (messages[code]) return messages[code];
    if (code.length > 0 && code.includes(" ")) return code;
    return text.pathErrorGeneric;
  }

  function setOutputDirError(form, message) {
    if (!(form instanceof HTMLFormElement)) return;
    const input = form.querySelector("input[name='download_output_dir']");
    const errorNode = form.querySelector("[data-download-output-dir-error]");
    if (input instanceof HTMLInputElement) {
      input.toggleAttribute("aria-invalid", Boolean(message));
      input.classList.toggle("border-rose-400", Boolean(message));
      input.classList.toggle("focus:border-rose-500", Boolean(message));
      input.classList.toggle("focus:ring-rose-500", Boolean(message));
    }
    if (errorNode instanceof HTMLElement) {
      errorNode.textContent = message || "";
      errorNode.classList.toggle("hidden", !message);
    }
  }

  function bindErrorHandlers() {
    if (handlersBound) return;
    handlersBound = true;
    document.addEventListener("htmx:afterRequest", (event) => {
      if (!event.detail.successful) return;
      const requestElement = event.detail?.requestConfig?.elt;
      const form = requestElement instanceof Element
        ? requestElement.closest("[data-download-settings-form]")
        : null;
      if (form instanceof HTMLFormElement) setOutputDirError(form, "");
    });
    document.addEventListener("htmx:responseError", (event) => {
      const requestElement = event.detail?.requestConfig?.elt;
      const form = requestElement instanceof Element
        ? requestElement.closest("[data-download-settings-form]")
        : null;
      if (!(form instanceof HTMLFormElement)) return;
      const payload = parseJsonSafe(event.detail?.xhr?.responseText);
      const message = resolvePathErrorMessage(payload?.detail);
      setOutputDirError(form, message);
      showToast(message, "error");
    });
    document.addEventListener("input", (event) => {
      const input = event.target;
      if (!(input instanceof HTMLInputElement) || input.name !== "download_output_dir") return;
      const form = input.closest("[data-download-settings-form]");
      if (form instanceof HTMLFormElement && input.hasAttribute("aria-invalid")) {
        setOutputDirError(form, "");
      }
    });
  }

  window.BrieftubeDownloadSettings = { configure, resolvePathErrorMessage, bindErrorHandlers };
})();
