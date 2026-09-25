(() => {
  const dropzone = document.getElementById("dropzone");
  const dzPlaceholder = document.getElementById("dz-placeholder");
  const preview = document.getElementById("preview");
  const fileInput = document.getElementById("fileInput");
  const providerList = document.getElementById("providerList");
  const styleList = document.getElementById("styleList");
  const durationEl = document.getElementById("duration");
  const durationVal = document.getElementById("durationVal");
  const intensityEl = document.getElementById("intensity");
  const intensityVal = document.getElementById("intensityVal");
  const colorGradeEl = document.getElementById("colorGrade");
  const generateBtn = document.getElementById("generateBtn");
  const statusEl = document.getElementById("status");
  const resultEl = document.getElementById("result");
  const resultVideo = document.getElementById("resultVideo");
  const downloadLink = document.getElementById("downloadLink");
  const fallbackNote = document.getElementById("fallbackNote");

  let selectedFile = null;
  let selectedStyle = "parallax_dolly";
  let selectedProvider = "parallax_2_5d";

  function setPreview(file) {
    const url = URL.createObjectURL(file);
    preview.src = url;
    preview.style.display = "block";
    dzPlaceholder.style.display = "none";
  }

  dropzone.addEventListener("click", () => fileInput.click());
  dropzone.addEventListener("dragover", (e) => { e.preventDefault(); dropzone.classList.add("drag"); });
  dropzone.addEventListener("dragleave", () => dropzone.classList.remove("drag"));
  dropzone.addEventListener("drop", (e) => {
    e.preventDefault();
    dropzone.classList.remove("drag");
    if (e.dataTransfer.files && e.dataTransfer.files[0]) {
      selectedFile = e.dataTransfer.files[0];
      setPreview(selectedFile);
    }
  });
  fileInput.addEventListener("change", () => {
    if (fileInput.files && fileInput.files[0]) {
      selectedFile = fileInput.files[0];
      setPreview(selectedFile);
    }
  });

  durationEl.addEventListener("input", () => { durationVal.textContent = `${parseFloat(durationEl.value).toFixed(1)}s`; });
  intensityEl.addEventListener("input", () => { intensityVal.textContent = `${parseFloat(intensityEl.value).toFixed(1)}x`; });

  async function loadProviders() {
    const res = await fetch("/api/providers");
    const data = await res.json();
    providerList.innerHTML = "";
    selectedProvider = data.default;
    data.providers.forEach((p, idx) => {
      const card = document.createElement("div");
      card.className = "style-card provider-card" + (idx === 0 ? " active" : "") + (p.available ? "" : " unavailable");
      card.dataset.id = p.id;
      const badge = p.available
        ? `<span class="badge available">verfuegbar</span>`
        : `<span class="badge unavailable">nicht verfuegbar</span>`;
      card.innerHTML = `
        <div>
          <div class="label">${p.label}</div>
          <div class="desc">${p.description}</div>
          <div class="status-note">${p.status}</div>
        </div>
        ${badge}
      `;
      card.addEventListener("click", () => {
        document.querySelectorAll(".provider-card").forEach((c) => c.classList.remove("active"));
        card.classList.add("active");
        selectedProvider = p.id;
      });
      providerList.appendChild(card);
    });
  }

  async function loadStyles() {
    const res = await fetch("/api/styles");
    const data = await res.json();
    styleList.innerHTML = "";
    data.styles.forEach((s, idx) => {
      const card = document.createElement("div");
      card.className = "style-card" + (idx === 0 ? " active" : "");
      card.dataset.id = s.id;
      card.innerHTML = `<div class="label">${s.label}</div><div class="desc">${s.description}</div>`;
      card.addEventListener("click", () => {
        document.querySelectorAll(".style-card").forEach((c) => c.classList.remove("active"));
        card.classList.add("active");
        selectedStyle = s.id;
      });
      styleList.appendChild(card);
    });
    if (data.styles.length) selectedStyle = data.styles[0].id;
  }

  function setBusy(busy, message) {
    generateBtn.disabled = busy;
    generateBtn.innerHTML = busy ? `<span class="spinner"></span> Rendert...` : "Cinematische Szene erzeugen";
    if (message) statusEl.textContent = message;
  }

  generateBtn.addEventListener("click", async () => {
    if (!selectedFile) {
      statusEl.textContent = "Bitte zuerst ein Bild auswaehlen.";
      return;
    }
    setBusy(true, "Tiefenkarte wird berechnet, Kamera fliegt los...");
    resultEl.style.display = "none";

    const form = new FormData();
    form.append("file", selectedFile);
    form.append("provider", selectedProvider);
    form.append("style", selectedStyle);
    form.append("duration", durationEl.value);
    form.append("fps", 24);
    form.append("resolution", 1280);
    form.append("intensity", intensityEl.value);
    form.append("color_grade", colorGradeEl.value);
    form.append("ai_depth", document.getElementById("aiDepth").checked);
    form.append("vignette", document.getElementById("vignette").checked);
    form.append("grain", document.getElementById("grain").checked);
    form.append("bloom", document.getElementById("bloom").checked);
    form.append("chromatic_aberration", document.getElementById("chroma").checked);
    form.append("letterbox", document.getElementById("letterbox").checked);
    form.append("motion_blur", document.getElementById("motionblur").checked);

    try {
      const res = await fetch("/api/generate", { method: "POST", body: form });
      if (!res.ok) {
        const err = await res.json().catch(() => ({ detail: res.statusText }));
        throw new Error(err.detail || "Unbekannter Fehler");
      }
      const data = await res.json();
      resultVideo.src = data.video_url;
      downloadLink.href = data.video_url;
      resultEl.style.display = "block";
      const m = data.meta;

      if (m.fallback_reason) {
        fallbackNote.style.display = "block";
        fallbackNote.textContent = `Hinweis: "${m.requested_provider}" war nicht verfuegbar (${m.fallback_reason}) - stattdessen wurde automatisch "${m.used_provider}" verwendet.`;
      } else {
        fallbackNote.style.display = "none";
      }

      const parts = [`Fertig in ${m.elapsed_seconds}s`];
      if (m.resolution) parts.push(`${m.resolution[0]}x${m.resolution[1]}`);
      if (m.frames) parts.push(`${m.frames} Frames @ ${m.fps}fps`);
      if (m.depth_backend) parts.push(`Tiefe: ${m.depth_backend}`);
      statusEl.textContent = parts.join(" - ");
    } catch (e) {
      statusEl.textContent = `Fehler: ${e.message}`;
    } finally {
      setBusy(false);
    }
  });

  loadProviders();
  loadStyles();
})();
