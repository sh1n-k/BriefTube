(() => {
  const THEME_MODE_KEY = "brieftube.theme.mode";
  const THEME_TONE_KEY = "brieftube.theme.tone";
  const THEME_MODE_DEFAULT = "system";
  const THEME_TONE_DEFAULT = "neutral";
  const SYSTEM_DARK_QUERY = "(prefers-color-scheme: dark)";
  const THEME_MODES = new Set(["light", "dark", "system"]);
  const THEME_TONES = new Set(["brand", "neutral", "high-contrast"]);

  function getStoredValue(key) {
    try {
      return localStorage.getItem(key);
    } catch (_err) {
      return null;
    }
  }

  function setStoredValue(key, value) {
    try {
      localStorage.setItem(key, value);
    } catch (_err) {
      // ignore storage write errors and keep runtime-only state.
    }
  }

  function normalizeThemeMode(value) {
    const normalized = String(value || "").trim().toLowerCase();
    return THEME_MODES.has(normalized) ? normalized : THEME_MODE_DEFAULT;
  }

  function normalizeTone(value) {
    const normalized = String(value || "").trim().toLowerCase();
    return THEME_TONES.has(normalized) ? normalized : THEME_TONE_DEFAULT;
  }

  function resolveEffectiveTheme(mode) {
    if (mode !== "system") return mode;
    const prefersDark = window.matchMedia && window.matchMedia(SYSTEM_DARK_QUERY).matches;
    return prefersDark ? "dark" : "light";
  }

  function getThemeState() {
    return {
      mode: normalizeThemeMode(getStoredValue(THEME_MODE_KEY)),
      tone: normalizeTone(getStoredValue(THEME_TONE_KEY)),
    };
  }

  function syncThemeControls(scope) {
    const root = scope instanceof Element ? scope : document;
    const html = document.documentElement;
    const mode = normalizeThemeMode(html.dataset.themeMode || getStoredValue(THEME_MODE_KEY));
    const tone = normalizeTone(html.dataset.tone || getStoredValue(THEME_TONE_KEY));
    const effective = resolveEffectiveTheme(mode);
    root.querySelectorAll("[data-theme-mode-select]").forEach((node) => {
      if (node instanceof HTMLSelectElement) {
        node.value = mode;
      }
    });
    root.querySelectorAll("[data-theme-tone-select]").forEach((node) => {
      if (node instanceof HTMLSelectElement) {
        node.value = tone;
      }
    });
    root.querySelectorAll("[data-theme-toggle-label]").forEach((node) => {
      const darkLabel = node.getAttribute("data-theme-label-dark") || "Dark";
      const lightLabel = node.getAttribute("data-theme-label-light") || "Light";
      node.textContent = effective === "dark" ? lightLabel : darkLabel;
    });
  }

  function applyTheme(modeInput, toneInput, options = {}) {
    const mode = normalizeThemeMode(modeInput);
    const tone = normalizeTone(toneInput);
    const persist = options.persist !== false;
    if (persist) {
      setStoredValue(THEME_MODE_KEY, mode);
      setStoredValue(THEME_TONE_KEY, tone);
    }
    const effective = resolveEffectiveTheme(mode);
    const html = document.documentElement;
    html.dataset.themeMode = mode;
    html.dataset.tone = tone;
    html.dataset.theme = effective;
    syncThemeControls(document);
  }

  function bindThemeModeSelects(scope) {
    const root = scope instanceof Element ? scope : document;
    root.querySelectorAll("[data-theme-mode-select]").forEach((node) => {
      if (!(node instanceof HTMLSelectElement)) return;
      if (node.dataset.themeBound === "1") return;
      node.dataset.themeBound = "1";
      node.addEventListener("change", () => {
        const html = document.documentElement;
        const tone = normalizeTone(html.dataset.tone || getStoredValue(THEME_TONE_KEY));
        applyTheme(node.value, tone, { persist: true });
      });
    });
  }

  function bindThemeToneSelects(scope) {
    const root = scope instanceof Element ? scope : document;
    root.querySelectorAll("[data-theme-tone-select]").forEach((node) => {
      if (!(node instanceof HTMLSelectElement)) return;
      if (node.dataset.themeBound === "1") return;
      node.dataset.themeBound = "1";
      node.addEventListener("change", () => {
        const html = document.documentElement;
        const mode = normalizeThemeMode(html.dataset.themeMode || getStoredValue(THEME_MODE_KEY));
        applyTheme(mode, node.value, { persist: true });
      });
    });
  }

  function bindThemeToggles(scope) {
    const root = scope instanceof Element ? scope : document;
    root.querySelectorAll("[data-theme-toggle]").forEach((node) => {
      if (!(node instanceof HTMLButtonElement)) return;
      if (node.dataset.themeBound === "1") return;
      node.dataset.themeBound = "1";
      node.addEventListener("click", () => {
        const html = document.documentElement;
        const currentMode = normalizeThemeMode(html.dataset.themeMode || getStoredValue(THEME_MODE_KEY));
        const currentTone = normalizeTone(html.dataset.tone || getStoredValue(THEME_TONE_KEY));
        const effective = resolveEffectiveTheme(currentMode);
        const nextMode = effective === "dark" ? "light" : "dark";
        applyTheme(nextMode, currentTone, { persist: true });
      });
    });
  }

  function bindSystemThemeObserver() {
    if (window.__themeSystemObserverBound === true) return;
    window.__themeSystemObserverBound = true;
    if (!window.matchMedia) return;
    const media = window.matchMedia(SYSTEM_DARK_QUERY);
    const onChange = () => {
      const html = document.documentElement;
      const mode = normalizeThemeMode(html.dataset.themeMode || getStoredValue(THEME_MODE_KEY));
      if (mode !== "system") return;
      const tone = normalizeTone(html.dataset.tone || getStoredValue(THEME_TONE_KEY));
      applyTheme(mode, tone, { persist: false });
    };
    if (typeof media.addEventListener === "function") {
      media.addEventListener("change", onChange);
      return;
    }
    if (typeof media.addListener === "function") {
      media.addListener(onChange);
    }
  }

  function bindThemeControls(scope) {
    bindThemeModeSelects(scope);
    bindThemeToneSelects(scope);
    bindThemeToggles(scope);
    syncThemeControls(scope);
  }

  window.BrieftubeTheme = {
    getThemeState,
    applyTheme,
    bindSystemThemeObserver,
    bindThemeControls,
  };
})();
