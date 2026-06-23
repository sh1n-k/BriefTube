(() => {
  function initDigitsOnlyInput(input) {
    if (input.dataset.digitsBound === "1") return;
    input.dataset.digitsBound = "1";

    const normalize = () => {
      const original = input.value || "";
      const next = original.replace(/\D+/g, "");
      if (next !== original) {
        input.value = next;
      }
    };
    input.addEventListener("input", normalize);
    normalize();
  }

  function bindDigitsOnlyInputs(scope) {
    const root = scope instanceof Element ? scope : document;
    root.querySelectorAll("input[data-digits-only]").forEach(initDigitsOnlyInput);
  }

  function replaceSelectWithFixedValue(select, value, label) {
    if (!select || select.dataset.codexOnlyReplaced === "1") return;
    select.dataset.codexOnlyReplaced = "1";
    const wrapper = document.createElement("div");
    wrapper.className = "flex items-center gap-2 rounded-md border border-slate-200 bg-white px-3 py-2 text-sm text-slate-700";
    const hidden = document.createElement("input");
    hidden.type = "hidden";
    hidden.name = select.name;
    hidden.value = value;
    const text = document.createElement("span");
    text.textContent = label;
    wrapper.append(hidden, text);
    select.replaceWith(wrapper);
  }

  function removeClosestGrid(control) {
    const row = control?.closest(".grid");
    if (row) row.remove();
  }

  function bindCodexOnlyLlmSettings(scope) {
    const root = scope instanceof Element ? scope : document;
    const form = root.querySelector("#llm-settings-form");
    if (!form || form.dataset.codexOnlyBound === "1") return;
    form.dataset.codexOnlyBound = "1";
    form.setAttribute(
      "hx-trigger",
      "change from:select[name='llm_max_concurrent'], change from:select[name='llm_model_codex'], change from:select[name='llm_reasoning_effort_codex'], input changed delay:1s from:textarea[name='llm_prompt_template']",
    );

    replaceSelectWithFixedValue(
      form.querySelector("select[name='llm_provider_primary']"),
      "codex",
      "Codex",
    );
    replaceSelectWithFixedValue(
      form.querySelector("select[name='llm_provider_fallback']"),
      "none",
      "None",
    );

    [
      "input[name='llm_model_claude']",
      "input[name='llm_model_gemini']",
      "select[name='llm_reasoning_effort_claude']",
      "select[name='llm_reasoning_effort_gemini']",
    ].forEach((selector) => removeClosestGrid(form.querySelector(selector)));
  }

  function bindInputControls(scope) {
    bindDigitsOnlyInputs(scope);
    bindCodexOnlyLlmSettings(scope);
  }

  document.addEventListener("DOMContentLoaded", () => bindCodexOnlyLlmSettings(document));
  document.body?.addEventListener("htmx:afterSwap", (event) => bindCodexOnlyLlmSettings(event.target));

  window.BrieftubeInputControls = {
    bindDigitsOnlyInputs,
    bindCodexOnlyLlmSettings,
    bindInputControls,
  };
})();
