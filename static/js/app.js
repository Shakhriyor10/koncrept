(() => {
  const money = new Intl.NumberFormat("ru-RU");
  let orderPoll = null;
  let countdownTick = null;

  async function navigate(url, addHistory = true) {
    try {
      const response = await fetch(url, { credentials: "same-origin" });
      const html = await response.text();
      const nextDocument = new DOMParser().parseFromString(html, "text/html");
      if (!nextDocument.querySelector(".site-shell")) {
        window.location.assign(url);
        return;
      }
      document.title = nextDocument.title;
      document.body.innerHTML = nextDocument.body.innerHTML;
      if (addHistory) window.history.pushState({}, "", response.url);
      updateTotal();
      syncPaymentProvider();
      startReviewWatch();
      window.scrollTo({ top: 0, behavior: "smooth" });
    } catch (_) {
      window.location.assign(url);
    }
  }

  function updateTotal() {
    const inputs = document.querySelectorAll(".quantity-input[data-price]");
    const total = [...inputs].reduce((sum, input) => {
      const quantity = Math.max(0, Number.parseInt(input.value || "0", 10) || 0);
      return sum + quantity * Number(input.dataset.price || 0);
    }, 0);
    const output = document.querySelector("#order-total-preview");
    if (output) output.textContent = `${money.format(total)} сум`;
  }

  function formatUzbekPhone(input) {
    const raw = input.value || "";
    let digits = raw.replace(/\D/g, "");
    if (/^\s*\+?998/.test(raw) || (digits.startsWith("998") && digits.length === 12)) digits = digits.slice(3);
    digits = digits.slice(0, 9);
    input.value = `+998${digits}`;
    try { input.setSelectionRange(input.value.length, input.value.length); } catch (_) { /* Some mobile browsers do not expose selection for tel fields. */ }
  }

  function updateProofSelection(input) {
    const fileName = document.querySelector("#proof-file-name");
    const submit = document.querySelector("#proof-confirm");
    if (fileName) fileName.textContent = input.files?.[0]?.name || "Файл не выбран";
    if (submit) {
      const hasProvider = document.querySelector("[data-payment-provider]:checked") !== null;
      const hasCard = document.querySelector("input[name='payment_card']:checked") !== null;
      submit.disabled = !hasProvider || !hasCard;
    }
  }

  function syncPaymentProvider() {
    const choices = document.querySelectorAll("[data-payment-provider]");
    if (!choices.length) return;
    const selected = document.querySelector("[data-payment-provider]:checked");
    const provider = selected?.value || "";
    const picker = document.querySelector("[data-provider-picker]");
    const locked = document.querySelector("[data-payment-provider-locked]");
    const proofControls = document.querySelector("[data-proof-controls]");
    if (picker) picker.hidden = Boolean(provider);
    if (locked) locked.hidden = !provider;
    if (proofControls) proofControls.hidden = !provider;
    const selectedName = document.querySelector("[data-selected-provider-name]");
    if (selectedName) selectedName.textContent = selected?.dataset.providerLabel || "";
    document.querySelectorAll("[data-provider-cards]").forEach((group) => {
      const visible = group.dataset.providerCards === provider;
      group.hidden = !visible;
      const cards = group.querySelectorAll("[data-payment-card]");
      cards.forEach((card) => {
        card.disabled = !visible;
        if (!visible) card.checked = false;
      });
      if (visible && !group.querySelector("[data-payment-card]:checked") && cards.length) cards[0].checked = true;
    });
    const submit = document.querySelector("#proof-confirm");
    const file = document.querySelector("#proof-file");
    if (file) file.disabled = !provider;
    const uploadBox = document.querySelector("[data-proof-upload-box]");
    if (uploadBox) {
      uploadBox.classList.toggle("upload-box-disabled", !provider);
      uploadBox.setAttribute("aria-disabled", String(!provider));
    }
    const uploadHint = document.querySelector("[data-proof-upload-hint]");
    if (uploadHint) uploadHint.textContent = provider
      ? "JPG, PNG, WebP, PDF и другие фото · до 10 МБ"
      : "Сначала выберите HUMO или UZCARD";
    if (submit) submit.disabled = !provider || !document.querySelector("[data-payment-card]:checked");
  }

  function startReviewWatch() {
    if (orderPoll) window.clearInterval(orderPoll);
    if (countdownTick) window.clearInterval(countdownTick);
    const panel = document.querySelector("[data-review-watch]");
    if (!panel) return;
    const deadline = new Date(panel.dataset.deadline).getTime();
    const countdown = document.querySelector("#review-countdown");
    const tick = () => {
      const seconds = Math.max(0, Math.floor((deadline - Date.now()) / 1000));
      if (countdown) countdown.textContent = `${String(Math.floor(seconds / 60)).padStart(2, "0")}:${String(seconds % 60).padStart(2, "0")}`;
    };
    tick();
    countdownTick = window.setInterval(tick, 1000);
    const statusUrl = panel.dataset.statusUrl;
    orderPoll = window.setInterval(async () => {
      try {
        const response = await fetch(statusUrl, { headers: { "X-Requested-With": "XMLHttpRequest" } });
        if (!response.ok) return;
        const data = await response.json();
        if (!data.terminal) return;
        window.clearInterval(orderPoll);
        orderPoll = null;
        const target = document.querySelector("#checkout-flow");
        if (target) target.innerHTML = data.html;
        startReviewWatch();
      } catch (_) {
        // Keep the countdown visible and retry the next status poll.
      }
    }, 4000);
  }

  function closeTicketInfoModal(modal) {
    if (!modal?.open) return;
    modal.classList.add("is-closing");
    window.setTimeout(() => {
      if (modal.classList.contains("is-closing")) {
        modal.close();
        modal.classList.remove("is-closing");
      }
    }, 180);
  }

  document.addEventListener("input", (event) => {
    if (event.target.matches(".quantity-input")) updateTotal();
    if (event.target.matches("[data-uz-phone]")) formatUzbekPhone(event.target);
  });

  document.addEventListener("change", (event) => {
    if (event.target.matches("#proof-file")) updateProofSelection(event.target);
    if (event.target.matches("[data-payment-provider]")) syncPaymentProvider();
    if (event.target.matches("[data-payment-card]")) {
      const file = document.querySelector("#proof-file");
      if (file) updateProofSelection(file);
    }
  });

  document.addEventListener("click", async (event) => {
    const infoButton = event.target.closest("[data-ticket-info]");
    if (infoButton) {
      event.preventDefault();
      const modal = document.querySelector("[data-ticket-modal]");
      if (!modal) return;
      modal.querySelector("[data-ticket-modal-title]").textContent = infoButton.dataset.title || "Тип билета";
      modal.querySelector("[data-ticket-modal-description]").textContent = infoButton.dataset.description || "Дополнительная информация скоро появится.";
      modal.classList.remove("is-closing");
      if (!modal.open) {
        modal.showModal();
        modal.classList.add("is-opening");
        modal.animate(
          [
            { opacity: 0, transform: "translateY(14px) scale(.97)" },
            { opacity: 1, transform: "translateY(0) scale(1)" },
          ],
          { duration: 240, easing: "cubic-bezier(.2,.75,.25,1)" },
        ).onfinish = () => modal.classList.remove("is-opening");
      }
      return;
    }
    const modalClose = event.target.closest("[data-ticket-modal-close]");
    if (modalClose) {
      event.preventDefault();
      closeTicketInfoModal(modalClose.closest("[data-ticket-modal]"));
      return;
    }
    const modalBackdrop = event.target.closest("[data-ticket-modal]");
    if (modalBackdrop && event.target === modalBackdrop) {
      closeTicketInfoModal(modalBackdrop);
      return;
    }
    const paymentBack = event.target.closest("[data-payment-provider-back]");
    if (paymentBack) {
      event.preventDefault();
      document.querySelectorAll("[data-payment-provider]").forEach((choice) => { choice.checked = false; });
      const file = document.querySelector("#proof-file");
      if (file) file.value = "";
      const fileName = document.querySelector("#proof-file-name");
      if (fileName) fileName.textContent = "Файл не выбран";
      syncPaymentProvider();
      return;
    }
    const link = event.target.closest("a[data-spa-nav]");
    if (link && !event.defaultPrevented && !event.metaKey && !event.ctrlKey && !event.shiftKey && !event.altKey && !link.target && !link.hasAttribute("download")) {
      const destination = new URL(link.href, window.location.href);
      if (destination.origin === window.location.origin) {
        event.preventDefault();
        await navigate(destination.href);
        return;
      }
    }
    const button = event.target.closest("[data-copy]");
    if (!button) return;
    event.preventDefault();
    try {
      await navigator.clipboard.writeText(button.dataset.copy);
      const oldText = button.textContent;
      button.textContent = "Скопировано";
      window.setTimeout(() => { button.textContent = oldText; }, 1400);
    } catch (_) {
      button.textContent = button.dataset.copy;
    }
  });

  document.addEventListener("submit", async (event) => {
    const form = event.target.closest("form[data-async-form]");
    if (!form) return;
    event.preventDefault();
    if (form.dataset.confirm && !window.confirm(form.dataset.confirm)) return;
    const submit = form.querySelector("button[type='submit']");
    if (submit?.disabled) return;
    const oldText = submit?.textContent;
    if (submit) { submit.disabled = true; submit.dataset.oldText = oldText; submit.textContent = "Обрабатываем…"; }
    try {
      const response = await fetch(form.getAttribute("action") || window.location.href, {
        method: form.method || "POST",
        body: new FormData(form),
        headers: { "X-Requested-With": "XMLHttpRequest" },
        credentials: "same-origin",
      });
      const data = await response.json();
      if (data.redirect) {
        await navigate(data.redirect);
        return;
      }
      if (data.html) {
        const target = document.querySelector(form.dataset.fragment || "#checkout-flow");
        if (target) {
          target.innerHTML = data.html;
          syncPaymentProvider();
        }
        if (data.url) window.history.pushState({}, "", data.url);
        updateTotal();
        startReviewWatch();
      }
      if (!response.ok && !data.html && submit) {
        submit.disabled = false;
        submit.textContent = oldText;
      }
    } catch (_) {
      if (submit) { submit.disabled = false; submit.textContent = oldText; }
      const errors = form.querySelector(".form-error");
      if (errors) errors.textContent = "Не удалось отправить данные. Проверьте соединение и попробуйте ещё раз.";
    }
  });

  window.addEventListener("popstate", () => navigate(window.location.href, false));

  document.querySelectorAll("[data-uz-phone]").forEach(formatUzbekPhone);
  syncPaymentProvider();
  updateTotal();
  startReviewWatch();
})();
