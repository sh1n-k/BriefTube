(() => {
  const EVENT_CURSOR_KEY = "brieftube.download.lastEventId";
  const POLL_INTERVAL_MS = 5000;
  const bootstrap = window.BRIEFTUBE_UI_BOOTSTRAP || {};
  const text = {
    toastCompleted: "다운로드 완료 {count}건",
    toastFailed: "다운로드 실패 {count}건 ({videos})",
    toastMixed: "다운로드 결과: 완료 {success}건, 실패 {failed}건",
    toastMore: "외 {count}건",
    badgeInProgress: "진행 {count}",
    ...(bootstrap.download || {}),
  };
  let showToast = () => {};
  let refreshHistory = async () => true;
  let inFlight = false;
  let pollingStarted = false;
  let intervalId = null;

  function configure(options = {}) {
    if (typeof options.showUiToast === "function") showToast = options.showUiToast;
    if (typeof options.refreshDownloadHistoryFragment === "function") {
      refreshHistory = options.refreshDownloadHistoryFragment;
    }
  }

  function formatTemplate(template, values = {}) {
    return Object.entries(values).reduce(
      (result, [key, value]) => result.replaceAll(`{${key}}`, String(value)),
      String(template || ""),
    );
  }

  function getStoredCursor() {
    try {
      const raw = localStorage.getItem(EVENT_CURSOR_KEY);
      const parsed = Number.parseInt(String(raw || ""), 10);
      return { exists: raw !== null, value: Number.isNaN(parsed) || parsed < 0 ? 0 : parsed };
    } catch (_err) {
      return { exists: false, value: 0 };
    }
  }

  function setStoredCursor(value) {
    try {
      const safe = Math.max(0, Number.parseInt(String(value || "0"), 10) || 0);
      localStorage.setItem(EVENT_CURSOR_KEY, String(safe));
    } catch (_err) {
      // Keep polling with runtime-only state when storage is unavailable.
    }
  }

  function eventToast(events) {
    const succeeded = events.filter((event) => event?.event_type === "succeeded");
    const failed = events.filter((event) => event?.event_type === "failed");
    if (!succeeded.length && !failed.length) return null;
    if (succeeded.length && failed.length) {
      return { tone: "error", message: formatTemplate(text.toastMixed, { success: succeeded.length, failed: failed.length }) };
    }
    if (failed.length) {
      const names = failed
        .map((item) => String(item?.video_title || item?.video_id || "").trim())
        .filter(Boolean);
      const preview = names.slice(0, 2);
      if (names.length > preview.length) {
        preview.push(formatTemplate(text.toastMore, { count: names.length - preview.length }));
      }
      return { tone: "error", message: formatTemplate(text.toastFailed, { count: failed.length, videos: preview.join(", ") }) };
    }
    return { tone: "success", message: formatTemplate(text.toastCompleted, { count: succeeded.length }) };
  }

  function updateNavBadge(activeCount) {
    const count = Math.max(0, Number.parseInt(String(activeCount || "0"), 10) || 0);
    document.querySelectorAll("[data-download-nav-badge]").forEach((node) => {
      if (!(node instanceof HTMLElement)) return;
      node.classList.toggle("hidden", count <= 0);
      node.textContent = count <= 0 ? "0" : formatTemplate(text.badgeInProgress, { count });
    });
  }

  async function poll() {
    if (inFlight) return;
    inFlight = true;
    const cursor = getStoredCursor();
    try {
      const response = await fetch(`/api/downloads/progress?after_event_id=${encodeURIComponent(String(cursor.value))}`, {
        method: "GET",
        headers: { "Accept": "application/json" },
      });
      if (!response.ok) return;
      const payload = await response.json().catch(() => null);
      if (!payload || typeof payload !== "object") return;
      updateNavBadge(payload.active_count);
      const events = Array.isArray(payload.events) ? payload.events : [];
      const maxEventId = events.reduce((maximum, item) => {
        return Math.max(maximum, Number.parseInt(String(item?.id || "0"), 10) || 0);
      }, 0);
      const nextCursor = Math.max(
        cursor.value,
        Number.parseInt(String(payload.latest_event_id || "0"), 10) || 0,
        maxEventId,
      );
      if (!cursor.exists) {
        setStoredCursor(nextCursor);
        return;
      }
      if (events.length > 0) {
        const toast = eventToast(events);
        if (toast?.message) showToast(toast.message, toast.tone);
        if (window.location.pathname === "/downloads") void refreshHistory(false);
      }
      setStoredCursor(nextCursor);
    } catch (_err) {
      // Retry transient polling failures on the next interval.
    } finally {
      inFlight = false;
    }
  }

  function startPolling() {
    if (pollingStarted) return;
    pollingStarted = true;
    const startInterval = () => {
      intervalId = window.setInterval(() => void poll(), POLL_INTERVAL_MS);
    };
    void poll();
    startInterval();
    document.addEventListener("visibilitychange", () => {
      if (document.hidden && intervalId) {
        clearInterval(intervalId);
        intervalId = null;
      } else if (!document.hidden && !intervalId) {
        void poll();
        startInterval();
      }
    });
  }

  window.BrieftubeDownloadProgress = { configure, startPolling, pollNow: poll };
})();
