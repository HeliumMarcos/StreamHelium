(() => {
  "use strict";

  const feedback = document.querySelector("#admin-feedback");
  let feedbackTimer;

  function announce(message, isError = false) {
    if (!feedback) return;
    window.clearTimeout(feedbackTimer);
    feedback.textContent = message;
    feedback.classList.toggle("error", isError);
    feedback.hidden = false;
    feedbackTimer = window.setTimeout(() => {
      feedback.hidden = true;
    }, 4000);
  }

  document.addEventListener("submit", (event) => {
    const form = event.target;
    if (!(form instanceof HTMLFormElement)) return;

    const confirmation = form.dataset.confirm;
    if (confirmation && !window.confirm(confirmation)) {
      event.preventDefault();
      return;
    }

    if (!form.checkValidity()) return;
    const submitter = event.submitter;
    if (submitter instanceof HTMLButtonElement) {
      submitter.dataset.originalLabel = submitter.textContent;
      submitter.textContent = submitter.dataset.loadingLabel || "Processando…";
      submitter.disabled = true;
      submitter.setAttribute("aria-busy", "true");
    }
  });

  document.addEventListener("click", async (event) => {
    const button = event.target.closest("[data-copy-url]");
    if (!(button instanceof HTMLButtonElement)) return;

    const url = new URL(button.dataset.copyUrl, window.location.origin).href;
    try {
      await navigator.clipboard.writeText(url);
      button.textContent = "Copiado";
      button.dataset.copyState = "success";
      announce("Link de convite copiado.");
    } catch (_error) {
      button.textContent = "Não copiou";
      button.dataset.copyState = "error";
      announce("Não foi possível copiar. Abra o convite e copie o endereço do navegador.", true);
    }

    window.setTimeout(() => {
      button.textContent = "Copiar link";
      delete button.dataset.copyState;
    }, 3000);
  });
})();
