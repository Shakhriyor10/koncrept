(() => {
  const money = new Intl.NumberFormat("ru-RU");
  let orderPoll = null;
  let countdownTick = null;
  let wheelCountdownTick = null;

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
      initWheel();
      startReviewWatch();
      window.scrollTo({ top: 0, behavior: "smooth" });
    } catch (_) {
      window.location.assign(url);
    }
  }

  function syncCouponPicker() {
    const groups = [...document.querySelectorAll("[data-coupon-group]")];
    const selected = new Map();
    groups.forEach((group) => {
      group.querySelectorAll("[data-coupon-id]:checked").forEach((input) => selected.set(input.dataset.couponId, group));
    });
    groups.forEach((group) => {
      const quantityInput = document.getElementById(group.dataset.quantityInput);
      const quantity = Math.max(0, Number.parseInt(quantityInput?.value || "0", 10) || 0);
      const chosenCount = group.querySelectorAll("[data-coupon-id]:checked").length;
      group.querySelectorAll("[data-coupon-id]").forEach((input) => {
        const chosenInOtherGroup = selected.has(input.dataset.couponId) && selected.get(input.dataset.couponId) !== group;
        input.disabled = !input.checked && (quantity === 0 || chosenCount >= quantity || chosenInOtherGroup);
      });
    });
  }

  function updateTotal() {
    const inputs = [...document.querySelectorAll(".quantity-input[data-price]")];
    const quantities = inputs.map((input) => ({
      input,
      quantity: Math.max(0, Number.parseInt(input.value || "0", 10) || 0),
      price: Number(input.dataset.price || 0),
    }));
    const gross = quantities.reduce((sum, item) => sum + item.quantity * item.price, 0);
    syncCouponPicker();
    const discount = [...document.querySelectorAll("[data-coupon-group]")].reduce((sum, group) => {
      const quantityInput = document.getElementById(group.dataset.quantityInput);
      const quantity = Math.max(0, Number.parseInt(quantityInput?.value || "0", 10) || 0);
      const price = Number(quantityInput?.dataset.price || 0);
      return sum + [...group.querySelectorAll("[data-coupon-id]:checked")]
        .slice(0, quantity)
        .reduce((ticketSum, coupon) => ticketSum + Math.floor(price * Number(coupon.dataset.discountPercent || 0) / 100), 0);
    }, 0);
    const total = Math.max(0, gross - discount);
    const output = document.querySelector("#order-total-preview");
    if (output) output.textContent = `${money.format(total)} сум`;
    const discountOutput = document.querySelector("#order-discount-preview");
    if (discountOutput) discountOutput.textContent = discount
      ? `−${money.format(discount)} сум по выбранным купонам`
      : "Выберите скидку для билета, если хотите её использовать";
  }

  function wheelStoreGuest(status) {
    if (status?.authenticated) return;
    try {
      if (status?.reward) localStorage.setItem("koncrept-wheel-guest-state", JSON.stringify(status));
      else localStorage.removeItem("koncrept-wheel-guest-state");
    } catch (_) { /* Storage may be unavailable in private browsing. */ }
  }

  function formatWheelCountdown(dateValue, output) {
    if (wheelCountdownTick) window.clearInterval(wheelCountdownTick);
    if (!dateValue || !output) { if (output) output.hidden = true; return; }
    const tick = () => {
      const seconds = Math.max(0, Math.ceil((new Date(dateValue).getTime() - Date.now()) / 1000));
      output.hidden = seconds <= 0;
      output.textContent = seconds > 0
        ? `Следующая попытка через ${Math.floor(seconds / 3600)} ч ${String(Math.floor((seconds % 3600) / 60)).padStart(2, "0")} мин`
        : "Новое вращение уже доступно";
      if (seconds <= 0) {
        window.clearInterval(wheelCountdownTick);
        const modal = output.closest("[data-wheel-modal]");
        const spin = modal?.querySelector("[data-wheel-spin]");
        if (spin) { spin.hidden = false; spin.disabled = false; spin.textContent = "Крутить снова ↻"; }
      }
    };
    tick();
    wheelCountdownTick = window.setInterval(tick, 30000);
  }

  function renderWheelStatus(modal, status) {
    const spin = modal.querySelector("[data-wheel-spin]");
    const result = modal.querySelector("[data-wheel-result]");
    const countdown = modal.querySelector("[data-wheel-countdown]");
    const cta = modal.querySelector("[data-wheel-auth-cta]");
    const expired = status.reward?.expires_at && new Date(status.reward.expires_at).getTime() <= Date.now();
    if (result && status.reward) result.textContent = expired
      ? `Ваш прошлый приз: ${status.reward.label}. Его срок действия закончился.`
      : `Ваш приз: ${status.reward.label}!`;
    if (spin) {
      spin.hidden = !status.can_spin;
      spin.disabled = !status.can_spin;
      spin.textContent = status.reward ? "Крутить снова ↻" : "Крутить барабан ↻";
    }
    formatWheelCountdown(status.can_spin ? null : status.next_spin_at, countdown);
    if (cta) cta.hidden = !(status.reward && !status.authenticated && !status.reward.claimed && !expired);
    wheelStoreGuest(status);
  }

  async function initWheel() {
    const modal = document.querySelector("[data-wheel-modal]");
    if (!modal) return;
    try {
      const response = await fetch(modal.dataset.wheelStatusUrl, { credentials: "same-origin" });
      if (!response.ok) return;
      const status = await response.json();
      renderWheelStatus(modal, status);
      const autoKey = `koncrept-wheel-auto-shown:${modal.dataset.wheelIdentity || "guest"}`;
      let lastAutoShown = 0;
      try { lastAutoShown = Number(localStorage.getItem(autoKey) || 0); } catch (_) { /* Storage may be unavailable. */ }
      if (modal.dataset.wheelAuto === "1" && (status.can_spin || status.reward) && Date.now() - lastAutoShown >= 24 * 60 * 60 * 1000) {
        try { localStorage.setItem(autoKey, String(Date.now())); } catch (_) { /* Storage may be unavailable. */ }
        if (!modal.open) modal.showModal();
      }
    } catch (_) { /* The visitor can still open the wheel manually. */ }
  }

  async function spinWheel(modal, button) {
    const error = modal.querySelector("[data-wheel-error]");
    const result = modal.querySelector("[data-wheel-result]");
    const disc = modal.querySelector("[data-wheel-disc]");
    if (error) { error.hidden = true; error.textContent = ""; }
    button.disabled = true;
    button.textContent = "Барабан вращается…";
    try {
      const token = modal.querySelector("[name=csrfmiddlewaretoken]")?.value;
      const response = await fetch(modal.dataset.wheelSpinUrl, {
        method: "POST",
        credentials: "same-origin",
        headers: { "X-CSRFToken": token || "", "X-Requested-With": "XMLHttpRequest" },
      });
      const data = await response.json();
      if (!response.ok) throw new Error(data.error || "Не удалось запустить барабан.");
      const prize = data.reward;
      const codes = ["discount_10", "discount_20", "discount_30", "discount_60", "gift_standard", "gift_vip"];
      const index = codes.indexOf(prize.code);
      if (disc && index >= 0) {
        const current = Number(disc.dataset.rotation || 0);
        const base = Math.ceil(current / 360) * 360;
        const finalRotation = base + 6 * 360 - index * 60;
        disc.dataset.rotation = String(finalRotation);
        disc.style.transform = `rotate(${finalRotation}deg)`;
      }
      if (result) result.textContent = `Ваш приз: ${prize.label}!`;
      if (data.gift_order_reference && result) result.textContent += ` Билет уже добавлен в профиль: ${data.gift_order_reference}.`;
      const statusResponse = await fetch(modal.dataset.wheelStatusUrl, { credentials: "same-origin" });
      const status = statusResponse.ok ? await statusResponse.json() : null;
      if (status) {
        renderWheelStatus(modal, status);
        if (!status.authenticated) {
          const cta = modal.querySelector("[data-wheel-auth-cta]");
          if (cta) cta.hidden = false;
        }
      } else {
        button.hidden = true;
      }
    } catch (exception) {
      if (error) { error.textContent = exception.message; error.hidden = false; }
      button.disabled = false;
      button.textContent = "Попробовать ещё раз ↻";
    }
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
    if (event.target.matches("[data-coupon-id]")) updateTotal();
  });

  document.addEventListener("click", async (event) => {
    const wheelOpen = event.target.closest("[data-wheel-open]");
    if (wheelOpen) {
      event.preventDefault();
      const modal = document.querySelector("[data-wheel-modal]");
      if (modal && !modal.open) modal.showModal();
      return;
    }
    const wheelClose = event.target.closest("[data-wheel-close]");
    if (wheelClose) { document.querySelector("[data-wheel-modal]")?.close(); return; }
    const wheelSpin = event.target.closest("[data-wheel-spin]");
    if (wheelSpin) { event.preventDefault(); await spinWheel(wheelSpin.closest("[data-wheel-modal]"), wheelSpin); return; }
    const wheelBackdrop = event.target.closest("[data-wheel-modal]");
    if (wheelBackdrop && event.target === wheelBackdrop) wheelBackdrop.close();
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
  initWheel();
  startReviewWatch();
})();
