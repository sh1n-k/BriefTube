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

  window.BrieftubeInputControls = {
    bindDigitsOnlyInputs,
  };
})();
