(() => {
  const UI_BOOTSTRAP = window.BRIEFTUBE_UI_BOOTSTRAP || {};
  const downloadSettings = window.BrieftubeDownloadSettings || null;
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
    detailSize: "파일 크기",
    detailErrorCode: "오류 코드",
    detailOutputPath: "출력 파일",
    detailCopyOutput: "파일 경로 복사",
    detailErrorMessage: "오류 메시지",
    detailCopyError: "오류 메시지 복사",
    toastCopiedOutput: "출력 파일 경로를 복사했습니다.",
    toastCopiedError: "오류 메시지를 복사했습니다.",
    ...(UI_BOOTSTRAP.download || {}),
  };
  let showToast = () => {};
  let refreshDownloadHistory = async () => true;

  function configure(options = {}) {
    if (typeof options.showUiToast === "function") {
      showToast = options.showUiToast;
    }
    if (typeof options.refreshDownloadHistoryFragment === "function") {
      refreshDownloadHistory = options.refreshDownloadHistoryFragment;
    }
  }

  function parseJsonSafe(text) {
    if (!text) return null;
    try {
      return JSON.parse(text);
    } catch (_err) {
      return null;
    }
  }

  function formatTemplate(template, values = {}) {
    const base = String(template || "");
    return Object.entries(values).reduce((acc, [key, value]) => {
      return acc.replaceAll(`{${key}}`, String(value));
    }, base);
  }

  function formatFileSizeMb(value) {
    const raw = String(value == null ? "" : value).trim();
    if (!raw) return DOWNLOAD_UI_TEXT.detailNone;
    const bytes = Number(raw);
    if (!Number.isFinite(bytes) || bytes < 0) return raw;
    return `${(bytes / (1024 * 1024)).toFixed(1)} MB`;
  }

  function resolveDownloadRequestErrorMessage(payload) {
    const code = String(payload?.code || "").trim().toLowerCase();
    if (code === "ffmpeg_missing") return DOWNLOAD_UI_TEXT.toastFfmpegMissing;
    if (code.startsWith("download_path_")) {
      return downloadSettings?.resolvePathErrorMessage?.(code) || DOWNLOAD_UI_TEXT.toastRequestFailed;
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
          showToast(DOWNLOAD_UI_TEXT.toastQueued, "success");
          close(true);
          void window.BrieftubeDownloadProgress?.pollNow?.();
          return;
        }
        if (payload?.duplicate === true) {
          showToast(DOWNLOAD_UI_TEXT.toastDuplicate, "success");
          close(true);
          void pollDownloadProgress();
          return;
        }
        showToast(resolveDownloadRequestErrorMessage(payload), "error");
      } catch (_error) {
        showToast(DOWNLOAD_UI_TEXT.toastRequestFailed, "error");
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
        showToast(DOWNLOAD_UI_TEXT.toastFfmpegMissing, "error");
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
          showToast(DOWNLOAD_UI_TEXT.toastCopiedOutput, "success");
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
          showToast(DOWNLOAD_UI_TEXT.toastCopiedError, "success");
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
          showToast(
            resolveDownloadOutputOpenErrorMessage(payload, response.status),
            "error",
          );
        } catch (_error) {
          showToast(DOWNLOAD_UI_TEXT.toastOutputOpenFailed, "error");
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
    const fileSize = formatFileSizeMb(options.fileSize);
    const outputPath = String(options.outputPath || "").trim();
    const outputFullPath = String(options.outputFullPath || "").trim();
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

    root.dataset.currentOutputPath = outputFullPath || outputPath;
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
        outputFullPath: button.dataset.outputFullPath,
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
          showToast(DOWNLOAD_UI_TEXT.toastRetryQueued, "success");
          void pollDownloadProgress();
          if (window.location.pathname === "/downloads") {
            window.setTimeout(() => {
              void refreshDownloadHistory(true);
            }, 250);
          }
          return;
        }
        if (payload?.code === "ffmpeg_missing") {
          showToast(DOWNLOAD_UI_TEXT.toastFfmpegMissing, "error");
          return;
        }
        showToast(DOWNLOAD_UI_TEXT.toastRetryFailed, "error");
      } catch (_error) {
        showToast(DOWNLOAD_UI_TEXT.toastRetryFailed, "error");
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

  window.BrieftubeDownloadControls = {
    configure,
    bindVideoDownloadButtons,
    bindDownloadDetailButtons,
    bindDownloadRetryButtons,
  };
})();
