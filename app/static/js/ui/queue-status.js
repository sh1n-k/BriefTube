(() => {
  const QUEUE_POLL_INTERVAL_MS = 2000;

  let showToast = () => {};
  let queuePollingStarted = false;
  let queuePollInFlight = false;
  let queueIntervalId = null;

  function configure(options = {}) {
    if (typeof options.showUiToast === "function") {
      showToast = options.showUiToast;
    }
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

  function captureQueueCollapsibleStates(content) {
    return Array.from(content.querySelectorAll("[data-collapsible]")).map((section) => {
      const body = section.querySelector("[data-collapsible-body]");
      return body?.classList.contains("hidden") === true;
    });
  }

  function restoreQueueCollapsibleStates(content, states) {
    content.querySelectorAll("[data-collapsible]").forEach((section, index) => {
      const body = section.querySelector("[data-collapsible-body]");
      const icon = section.querySelector("[data-collapsible-icon]");
      if (!body || states[index] === undefined) return;
      const hidden = states[index] === true;
      body.classList.toggle("hidden", hidden);
      if (icon instanceof HTMLElement || icon instanceof SVGElement) {
        icon.style.transform = hidden ? "" : "rotate(180deg)";
      }
    });
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
      el.classList.remove(
        "bg-emerald-50",
        "text-emerald-700",
        "bg-slate-100",
        "text-slate-500",
      );
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
        "bg-emerald-50",
        "text-emerald-700",
        "bg-rose-50",
        "text-rose-700",
        "bg-amber-50",
        "text-amber-700",
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

      const content = document.querySelector("[data-queue-content]");
      if (content && typeof payload.queue_html === "string") {
        const collapsibleStates = captureQueueCollapsibleStates(content);
        content.innerHTML = payload.queue_html;
        bindQueueRetryButtons(content);
        bindQueueRetryAllButtons(content);
        bindQueueClearButtons(content);
        window.BrieftubeVideoControls?.bindCollapsibles?.(content);
        restoreQueueCollapsibleStates(content, collapsibleStates);
      }
      updateWorkerStatusDots(payload);
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
            showToast(page?.dataset.retrySuccessText || "Retry submitted.", "success");
            void pollQueueStatus();
            return;
          }
          showToast(page?.dataset.retryFailedText || "Retry failed.", "error");
        } catch (err) {
          if (typeof console !== "undefined") console.warn("[BriefTube] Queue retry error:", err);
          showToast(page?.dataset.retryFailedText || "Retry failed.", "error");
        }
        btn.disabled = false;
      });
    });
  }

  function bindQueueRetryButtons(scope) {
    if (!scope) return;
    bindQueueRetryHandler(
      scope,
      "[data-queue-retry-transcript]",
      "queueRetryTranscript",
      (id) => `/api/videos/${encodeURIComponent(id)}/transcript/retry`,
    );
    bindQueueRetryHandler(
      scope,
      "[data-queue-retry-llm]",
      "queueRetryLlm",
      (id) => `/api/videos/${encodeURIComponent(id)}/retry`,
    );
  }

  function bindQueueRetryAllButtons(scope) {
    if (!scope) return;
    scope.querySelectorAll("[data-queue-retry-section]").forEach((btn) => {
      if (btn.dataset.queueRetryAllBound === "1") return;
      btn.dataset.queueRetryAllBound = "1";
      btn.addEventListener("click", async (event) => {
        event.stopPropagation();
        const section = btn.dataset.queueRetrySection;
        if (!section || btn.disabled) return;
        btn.disabled = true;
        const page = document.querySelector("[data-queue-page]");
        try {
          const resp = await fetch(`/api/queue/${encodeURIComponent(section)}/retry-failed`, {
            method: "POST",
            headers: { Accept: "application/json" },
          });
          const payload = await resp.json().catch(() => null);
          if (resp.ok && payload?.ok) {
            const count = Number.parseInt(String(payload.retried_count || "0"), 10) || 0;
            const template = count > 0
              ? (page?.dataset.retryAllSuccessText || "Retried {count} failed item(s).")
              : (page?.dataset.retryAllEmptyText || "No failed items to retry.");
            showToast(template.replace("{count}", String(count)), count > 0 ? "success" : "info");
            void pollQueueStatus();
            return;
          }
          showToast(page?.dataset.retryAllFailedText || "Failed to retry queue items.", "error");
        } catch (err) {
          if (typeof console !== "undefined") console.warn("[BriefTube] Queue retry-all error:", err);
          showToast(page?.dataset.retryAllFailedText || "Failed to retry queue items.", "error");
        }
        btn.disabled = false;
      });
    });
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
            showToast(template.replace("{count}", String(count)), count > 0 ? "success" : "info");
            void pollQueueStatus();
            return;
          }
          showToast(page?.dataset.clearFailedText || "Failed to clear queue items.", "error");
        } catch (err) {
          if (typeof console !== "undefined") console.warn("[BriefTube] Queue clear error:", err);
          showToast(page?.dataset.clearFailedText || "Failed to clear queue items.", "error");
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

  window.BrieftubeQueueStatus = {
    configure,
    bindQueueRetryButtons,
    bindQueueRetryAllButtons,
    bindQueueClearButtons,
    pollQueueStatus,
    startQueuePolling,
  };
})();
