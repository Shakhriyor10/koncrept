(() => {
  const page = document.querySelector("[data-ticket-scanner]");
  if (!page) return;

  const video = document.querySelector("#ticket-camera");
  const result = document.querySelector("#scan-result");
  const startButton = document.querySelector("#scan-start");
  const stopButton = document.querySelector("#scan-stop");
  const form = document.querySelector("#manual-scan-form");
  const tokenInput = document.querySelector("#scan-token");
  let stream = null;
  let detector = null;
  let cameraActive = false;
  let lastCode = "";
  let codeLostAt = 0;

  function showMessage(message, state = "idle") {
    result.dataset.state = state;
    result.textContent = message;
  }

  function showScan(data) {
    result.replaceChildren();
    result.dataset.state = data.valid ? (data.already_used ? "used" : "valid") : "invalid";

    const title = document.createElement("strong");
    title.textContent = data.valid ? "Билет действителен" : "Билет недействителен";
    result.append(title);
    const detail = document.createElement("span");
    detail.textContent = data.valid
      ? `${data.artist} · ${data.event_date} · ${data.ticket_type} · билет №${String(data.ticket_id).padStart(2, "0")}`
      : data.message;
    result.append(detail);

    if (data.valid) {
      const count = document.createElement("span");
      count.textContent = `Сканирован администратором: ${data.admin_scan_count}`;
      result.append(count);
      if (data.already_used) {
        const warning = document.createElement("span");
        warning.textContent = "Вход по этому билету уже отмечен ранее.";
        result.append(warning);
      } else {
        const reference = document.createElement("span");
        reference.textContent = `Заказ ${data.reference} · ${data.venue}`;
        result.append(reference);
      }
    }
  }

  async function scanTicket(value) {
    const token = (value || "").trim();
    if (!token) return;
    const payload = new FormData(form);
    payload.set("token", token);
    showMessage("Проверяем билет…");
    const submit = form.querySelector("button[type='submit']");
    if (submit) submit.disabled = true;
    try {
      const response = await fetch(page.dataset.scanUrl, {
        method: "POST",
        body: payload,
        credentials: "same-origin",
        headers: { "X-Requested-With": "XMLHttpRequest" },
      });
      const data = await response.json();
      showScan(data);
    } catch (_) {
      showMessage("Не удалось проверить билет. Проверьте подключение и попробуйте ещё раз.", "invalid");
    } finally {
      if (submit) submit.disabled = false;
    }
  }

  async function detectFrame() {
    if (!cameraActive) return;
    try {
      const codes = await detector.detect(video);
      const code = codes.find((item) => item.rawValue)?.rawValue || "";
      if (code) {
        codeLostAt = 0;
        if (code !== lastCode) {
          lastCode = code;
          tokenInput.value = code;
          await scanTicket(code);
        }
      } else if (lastCode) {
        if (!codeLostAt) codeLostAt = Date.now();
        if (Date.now() - codeLostAt > 900) {
          lastCode = "";
          codeLostAt = 0;
        }
      }
    } catch (_) {
      showMessage("Не удалось прочитать QR-код. Попробуйте поднести камеру ближе.", "invalid");
    }
    if (cameraActive) window.requestAnimationFrame(detectFrame);
  }

  async function startCamera() {
    if (!navigator.mediaDevices?.getUserMedia) {
      showMessage("Браузер не поддерживает доступ к камере. Используйте ручной ввод QR-ссылки.", "invalid");
      return;
    }
    if (!("BarcodeDetector" in window)) {
      showMessage("Сканирование камерой не поддерживается в этом браузере. Используйте ручной ввод или QR-сканер клавиатурного типа.", "invalid");
      return;
    }
    try {
      const formats = await BarcodeDetector.getSupportedFormats();
      if (!formats.includes("qr_code")) {
        showMessage("В этом браузере не поддерживается распознавание QR. Используйте ручной ввод.", "invalid");
        return;
      }
      stream = await navigator.mediaDevices.getUserMedia({ video: { facingMode: { ideal: "environment" } }, audio: false });
      video.srcObject = stream;
      video.hidden = false;
      await video.play();
      detector = new BarcodeDetector({ formats: ["qr_code"] });
      cameraActive = true;
      lastCode = "";
      codeLostAt = 0;
      startButton.hidden = true;
      stopButton.hidden = false;
      showMessage("Камера включена. Наведите её на QR-код билета.");
      detectFrame();
    } catch (_) {
      showMessage("Не удалось включить камеру. Разрешите доступ к камере или используйте ручной ввод.", "invalid");
      stopCamera();
    }
  }

  function stopCamera() {
    cameraActive = false;
    if (stream) stream.getTracks().forEach((track) => track.stop());
    stream = null;
    video.srcObject = null;
    video.hidden = true;
    startButton.hidden = false;
    stopButton.hidden = true;
  }

  startButton.addEventListener("click", startCamera);
  stopButton.addEventListener("click", () => {
    stopCamera();
    showMessage("Камера выключена. Вы можете продолжить сканировать вручную.");
  });
  form.addEventListener("submit", (event) => {
    event.preventDefault();
    scanTicket(tokenInput.value);
  });
  window.addEventListener("pagehide", stopCamera);
})();
