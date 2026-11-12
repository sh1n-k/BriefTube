"""Drive the in-app confirm/prompt modal (`#app-dialog`) from E2E tests."""

from __future__ import annotations

from playwright.sync_api import Locator, Page, expect

_OBSERVER_SCRIPT = """
(() => {
  if (window.__e2eAppDialogObserver) return;
  window.__e2eAppDialogObserver = true;
  const handle = (root) => {
    if (root.dataset.e2eHandled === "1") return;
    root.dataset.e2eHandled = "1";
    const title = root.querySelector("[data-app-dialog-title]")?.textContent || "";
    const message = root.querySelector("[data-app-dialog-message]")?.textContent || "";
    window.__e2eAppDialogRecord({ title, message }).then((answer) => {
      if (answer && typeof answer.value === "string") {
        root.querySelector("[data-app-dialog-input]").value = answer.value;
      }
      const selector = answer && answer.accept ? "[data-app-dialog-confirm]" : "[data-app-dialog-cancel]";
      root.querySelector(selector)?.click();
    });
  };
  const check = () => {
    const root = document.getElementById("app-dialog");
    if (!root) return;
    if (root.classList.contains("hidden")) {
      delete root.dataset.e2eHandled;
    } else {
      handle(root);
    }
  };
  const start = () => {
    new MutationObserver(check).observe(document.body, {
      subtree: true,
      childList: true,
      attributes: true,
      attributeFilter: ["class"],
    });
  };
  if (document.body) start(); else document.addEventListener("DOMContentLoaded", start);
})();
"""


class AppDialogRecorder:
    """Records every app modal and answers it automatically.

    `accept` decides confirm vs cancel; `prompt_value` fills the prompt input before confirming.
    Must be created before the page navigates so the observer is installed on every load.
    """

    def __init__(self, page: Page) -> None:
        self.titles: list[str] = []
        self.messages: list[str] = []
        self.accept = False
        self.prompt_value: str | None = None
        page.expose_function("__e2eAppDialogRecord", self._record)
        page.add_init_script(_OBSERVER_SCRIPT)

    def _record(self, payload: dict[str, str]) -> dict[str, object]:
        self.titles.append(payload.get("title", ""))
        self.messages.append(payload.get("message", ""))
        answer: dict[str, object] = {"accept": self.accept}
        if self.prompt_value is not None:
            answer["value"] = self.prompt_value
        return answer


def app_dialog(page: Page) -> Locator:
    return page.locator("#app-dialog")


def expect_app_dialog_open(page: Page, *, text: str | None = None) -> Locator:
    dialog = app_dialog(page)
    expect(dialog).to_be_visible(timeout=5_000)
    if text is not None:
        expect(dialog.locator("[data-app-dialog-panel]")).to_contain_text(text)
    return dialog


def cancel_app_dialog(page: Page) -> None:
    dialog = app_dialog(page)
    dialog.locator("[data-app-dialog-cancel]").click()
    expect(dialog).to_be_hidden()


def confirm_app_dialog(page: Page, *, value: str | None = None) -> None:
    dialog = app_dialog(page)
    if value is not None:
        dialog.locator("[data-app-dialog-input]").fill(value)
    dialog.locator("[data-app-dialog-confirm]").click()
    expect(dialog).to_be_hidden()
