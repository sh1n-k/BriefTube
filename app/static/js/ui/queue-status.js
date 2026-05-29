(() => {
  const QUEUE_POLL_INTERVAL_MS = 2000;
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

  let showToast = () => {};
  let queuePollingStarted = false;
  let queuePollInFlight = false;
  let queueIntervalId = null;

  function configure(options = {}) {
    if (typeof options.showUiToast === "function") {
      showToast = options.showUiToast;
    }
  }

  function getQueueStatusLabel(status) {
    const page = document.querySelector("[data-queue-page]");
    if (!page) return status.replace(/_/g, " ");
    const key = "label"
      + status
        .split("_")
        .map((w) => w.charAt(0).toUpperCase() + w.slice(1))
        .join("");
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
      placeholder.className =
        "flex h-full w-full items-center justify-center bg-slate-100 text-xs text-slate-400";
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
    badge.className =
      `status-badge inline-flex items-center rounded-full px-2.5 py-0.5 text-xs font-medium ${badgeClass}`;
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
      btn.className =
        "flex-shrink-0 rounded-md border border-indigo-300 bg-indigo-50 px-2.5 py-1 text-xs font-medium text-indigo-700 hover:bg-indigo-100";
      const retryLabel = document.querySelector("[data-queue-page]")?.dataset.retryButtonText
        || "Retry";
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
      no_subtitle:
        "rounded-full bg-slate-200 px-1.5 py-0.5 text-[10px] font-medium text-slate-700",
      manual_review:
        "rounded-full bg-orange-100 px-1.5 py-0.5 text-[10px] font-medium text-orange-800",
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

      const queuePage = document.querySelector("[data-queue-page]");
      const tItems = Array.isArray(payload.transcript_items) ? payload.transcript_items : [];
      const lItems = Array.isArray(payload.llm_items) ? payload.llm_items : [];
      const counts = payload.counts || {};
      const tTotal = (counts.transcript_pending || 0) + (counts.transcript_processing || 0)
        + (counts.transcript_failed || 0) + (counts.no_subtitle || 0);
      const lTotal = (counts.llm_pending || 0) + (counts.llm_processing || 0)
        + (counts.llm_failed || 0) + (counts.manual_review || 0);
      updateQueueSection(
        "data-queue-transcript-list",
        "data-queue-transcript-count",
        tItems,
        "transcript",
        queuePage?.dataset.transcriptEmptyText || "Transcript queue is empty.",
        tTotal,
      );
      updateQueueSection(
        "data-queue-llm-list",
        "data-queue-llm-count",
        lItems,
        "llm",
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
    bindQueueClearButtons,
    pollQueueStatus,
    startQueuePolling,
  };
})();
