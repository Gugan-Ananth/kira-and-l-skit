const state = {
  meta: null,
  skits: [],
  skit: null,
  view: "shelf",
  busy: false,
  speakingId: null,
  playingId: null,
  menuAnchor: null,
};

const timers = new Map();
const tails = new Map();
const clip = new Audio();

const $ = (id) => document.getElementById(id);

clip.addEventListener("ended", () => {
  state.playingId = null;
  paintPlayIcons();
});

function textLimit() {
  return state.meta && state.meta.text_limit ? state.meta.text_limit : 2000;
}

function speakerName(id) {
  const found = state.meta && state.meta.speakers.find((item) => item.id === id);
  return found ? found.label : id;
}

function who(line) {
  return speakerName(line.speaker);
}

function faceFor(speaker) {
  return speaker === "kira" ? "/static/art/kira-icon.png" : "/static/art/l-icon.png";
}

function bannerFor(skit) {
  const speakers = new Set(skit.lines.map((line) => line.speaker));
  if (speakers.has("l") && speakers.has("kira")) return "/static/art/pair.png";
  if (speakers.has("kira")) return "/static/art/kira.png";
  if (speakers.has("l")) return "/static/art/l-lean.png";
  return "/static/art/l-sit.png";
}

function excerpt(skit) {
  const lines = skit.lines.map((line) => line.text.trim()).filter(Boolean);
  if (!lines.length) return "No lines yet";
  return lines.slice(0, 2).join("  ");
}

function mixReady(skit) {
  if (!skit || !skit.lines.length) return false;
  return skit.lines.every((line) => {
    if (!line.text.trim() && line.mode !== "saved") return false;
    if (line.mode === "saved") return true;
    return line.status === "ready" || line.status === "fallback";
  });
}

function canPlay(line) {
  if (line.mode === "saved") return true;
  return line.status === "ready" || line.status === "fallback" || line.status === "stale";
}

function noteFor(line) {
  if (state.speakingId === line.id) return "Speaking…";
  if (line.status === "error") return line.error || "Could not generate.";
  if (line.status === "stale") return "Out of date. Regenerate this line.";
  if (line.status === "fallback") return "Used the neutral clip.";
  return "";
}

function mixVersion(skit) {
  return skit.lines.map((line) => line.audio_key || line.status).join("-");
}

async function api(path, options = {}) {
  const headers = { ...(options.headers || {}) };
  if (options.body && !(options.body instanceof FormData)) {
    headers["Content-Type"] = "application/json";
  }
  const response = await fetch(path, {
    credentials: "same-origin",
    ...options,
    headers,
  });
  if (response.status === 401 && path !== "/api/login") {
    showGate();
    throw new Error("Sign in required.");
  }
  if (!response.ok) {
    const raw = await response.text();
    let detail = "";
    try {
      const payload = JSON.parse(raw);
      detail = payload.detail ?? "";
      if (Array.isArray(detail)) {
        detail = detail.map((item) => item.msg || String(item)).join(" ");
      } else if (detail && typeof detail === "object") {
        detail = detail.message || JSON.stringify(detail);
      }
    } catch (_error) {
      detail = raw.trim();
    }
    detail = String(detail || "").trim();
    if (!detail || detail === "Internal Server Error") {
      detail = "The speech engine failed, and the server did not say why.";
    }
    throw new Error(detail);
  }
  if (response.status === 204) return null;
  const type = response.headers.get("content-type") || "";
  if (type.includes("application/json")) return response.json();
  return response;
}

function showGate() {
  $("login").hidden = false;
  $("app").hidden = true;
}

function showApp() {
  $("login").hidden = true;
  $("app").hidden = false;
}

function toast(message) {
  const text = message || "";
  const shelf = $("shelf-toast");
  const editor = $("toast");
  if (shelf) shelf.textContent = state.view === "shelf" ? text : "";
  if (editor) editor.textContent = state.view === "editor" ? text : "";
}

async function keepSpokenFailure(lineId, message) {
  try {
    await reloadSkit();
  } catch (_reloadError) {
    /* keep the generate error */
  }
  const line = state.skit && state.skit.lines.find((item) => item.id === lineId);
  if (!line || !message) return;
  if (line.status !== "error" || !line.error) {
    line.status = "error";
    line.error = message;
  }
}

function remember(line) {
  line._saved = {
    text: line.text,
    emotion: line.emotion,
    speaker: line.speaker,
  };
}

function dirtyBody(line) {
  const body = {};
  if (!line._saved) return body;
  if (line.text !== line._saved.text) body.text = line.text;
  if (line.emotion !== line._saved.emotion) body.emotion = line.emotion;
  if (line.speaker !== line._saved.speaker) body.speaker = line.speaker;
  return body;
}

function enqueue(lineId, task) {
  const prev = tails.get(lineId) || Promise.resolve();
  const run = prev.then(task, task);
  tails.set(lineId, run);
  return run;
}

async function persist(line) {
  if (!state.skit || !state.skit.lines.some((item) => item.id === line.id)) return;
  let body = dirtyBody(line);
  if (!Object.keys(body).length) return;
  if (line.mode === "saved") {
    const switched = await api(`/api/lines/${line.id}/synth`, { method: "POST" });
    const keep = { text: line.text, emotion: line.emotion, speaker: line.speaker };
    Object.assign(line, switched, keep);
    body = dirtyBody(line);
    if (!Object.keys(body).length) {
      remember(line);
      return;
    }
  }
  const sendingText = body.text;
  const updated = await api(`/api/lines/${line.id}`, {
    method: "PATCH",
    body: JSON.stringify(body),
  });
  const newest = line.text;
  Object.assign(line, updated);
  if (sendingText !== undefined && newest !== sendingText) line.text = newest;
  line._saved = {
    text: updated.text,
    emotion: updated.emotion,
    speaker: updated.speaker,
  };
  if (Object.keys(dirtyBody(line)).length) await persist(line);
}

function scheduleSave(line) {
  clearTimeout(timers.get(line.id));
  timers.set(line.id, setTimeout(() => {
    timers.delete(line.id);
    enqueue(line.id, () => persist(line))
      .then(() => paintAfterSave(line))
      .catch((error) => toast(error.message));
  }, 350));
}

async function flushSaves() {
  for (const [id, timer] of [...timers]) {
    clearTimeout(timer);
    timers.delete(id);
  }
  if (!state.skit) return;
  for (const line of state.skit.lines) {
    await enqueue(line.id, () => persist(line));
  }
}

async function reloadSkit() {
  const skit = await api(`/api/skits/${state.skit.id}`);
  skit.lines.forEach(remember);
  state.skit = skit;
}

async function loadShelf() {
  const list = await api("/api/skits");
  state.skits = await Promise.all(list.map((item) => api(`/api/skits/${item.id}`)));
}

function closeMenu() {
  $("popover").hidden = true;
  $("popover").replaceChildren();
  state.menuAnchor = null;
}

function toggleMenu(anchor, build) {
  const pop = $("popover");
  if (!pop.hidden && state.menuAnchor === anchor) {
    closeMenu();
    return;
  }
  state.menuAnchor = anchor;
  pop.replaceChildren();
  build(pop);
  pop.hidden = false;
  const rect = anchor.getBoundingClientRect();
  const width = pop.offsetWidth;
  const height = pop.offsetHeight;
  const left = Math.max(12, Math.min(rect.left, window.innerWidth - width - 12));
  let top = rect.bottom + 8;
  if (top + height > window.innerHeight - 12) top = Math.max(12, rect.top - height - 8);
  pop.style.left = `${left}px`;
  pop.style.top = `${top}px`;
}

const FORMAT_LABELS = { mp3: "MP3", wav: "WAV", flac: "FLAC", m4a: "M4A", ogg: "OGG" };

function downloadFormats() {
  const listed = state.meta && state.meta.download_formats;
  return listed && listed.length ? listed : ["mp3", "wav", "flac"];
}

function desktopDownload() {
  const api = window.pywebview && window.pywebview.api;
  return api && typeof api.save_download === "function" ? api : null;
}

async function saveDownload(url) {
  const desktop = desktopDownload();
  if (desktop) {
    // The window's browser cancels a blob click, so the mp3 never lands in Downloads.
    const result = await desktop.save_download(url);
    if (!result || result.ok === false) {
      throw new Error((result && result.error) || "Could not save that file.");
    }
    return;
  }
  const response = await fetch(url, { credentials: "same-origin" });
  if (!response.ok) {
    let detail = response.statusText;
    try {
      const payload = await response.json();
      detail = payload.detail || detail;
    } catch (_error) {
      /* keep the status text */
    }
    throw new Error(detail);
  }
  const blob = await response.blob();
  const header = response.headers.get("content-disposition") || "";
  const match = /filename="?([^"]+)"?/i.exec(header);
  const link = document.createElement("a");
  link.href = URL.createObjectURL(blob);
  link.download = match ? match[1] : "audio";
  document.body.append(link);
  link.click();
  link.remove();
  URL.revokeObjectURL(link.href);
}

function showDownloadMenu(anchor, urlFor) {
  if (state.busy) return;
  toggleMenu(anchor, (pop) => {
    const title = document.createElement("p");
    title.className = "pop-title";
    title.textContent = "Format";
    const grid = document.createElement("div");
    grid.className = "pop-grid";
    for (const fmt of downloadFormats()) {
      const button = document.createElement("button");
      button.type = "button";
      button.textContent = FORMAT_LABELS[fmt] || fmt.toUpperCase();
      button.addEventListener("click", () => {
        closeMenu();
        saveDownload(urlFor(fmt)).catch((error) => toast(error.message));
      });
      grid.append(button);
    }
    pop.append(title, grid);
  });
}

function showTypeMenu(anchor, line) {
  if (state.busy) return;
  toggleMenu(anchor, (pop) => {
    const title = document.createElement("p");
    title.className = "pop-title";
    title.textContent = "Speaker";
    const grid = document.createElement("div");
    grid.className = "pop-grid";
    for (const speaker of state.meta.speakers) {
      const button = document.createElement("button");
      button.type = "button";
      button.textContent = speaker.label;
      if (line.speaker === speaker.id) button.className = "is-on";
      button.addEventListener("click", () => changeType(line, speaker.id));
      grid.append(button);
    }
    pop.append(title, grid);
  });
}

function icon(name) {
  const paths = {
    play: "M8 5.5v13l11-6.5-11-6.5z",
    pause: "M6 5h4.2v14H6zm7.8 0H18v14h-4.2z",
    reload: "M12 6V3L7 8l5 5V9a5 5 0 1 1-4.9 6H7.1A7 7 0 1 0 12 6z",
    type: "M4 7h12.2l-2.6-2.6L15 3l5 5-5 5-1.4-1.4L16.2 9H4V7zm16 10H7.8l2.6 2.6L9 21l-5-5 5-5 1.4 1.4L7.8 15H20v2z",
    download: "M12 3v12.2l4.3-4.2 1.4 1.4L12 18.3 6.3 12.4l1.4-1.4 4.3 4.2V3zM5 20h14v2H5z",
  };
  const wrap = document.createElement("span");
  wrap.className = "icon";
  wrap.innerHTML = `<svg viewBox="0 0 24 24" aria-hidden="true"><path fill="currentColor" d="${paths[name]}"/></svg>`;
  return wrap;
}

function stopSounds() {
  clip.pause();
  state.playingId = null;
  for (const audio of document.querySelectorAll("audio")) audio.pause();
}

function playLine(line) {
  if (!canPlay(line)) return;
  const mix = $("mix-audio");
  if (mix && !mix.paused) mix.pause();
  for (const audio of document.querySelectorAll("audio")) {
    if (audio !== mix) audio.pause();
  }
  const src = `/api/lines/${line.id}/audio?v=${encodeURIComponent(line.audio_key || line.status)}`;
  if (state.playingId === line.id && !clip.paused) {
    clip.pause();
    state.playingId = null;
    paintPlayIcons();
    return;
  }
  state.playingId = line.id;
  const next = new URL(src, window.location.origin).href;
  if (clip.src !== next) clip.src = src;
  else if (clip.ended) clip.currentTime = 0;
  clip.play();
  paintPlayIcons();
}

function paintPlayIcons() {
  for (const button of document.querySelectorAll("[data-act='play']")) {
    const playing = button.dataset.line === state.playingId && !clip.paused;
    const glyph = button.querySelector(".icon");
    if (!glyph) continue;
    glyph.replaceWith(icon(playing ? "pause" : "play"));
    const caption = button.querySelector(".caption");
    if (caption) caption.textContent = playing ? "Pause" : "Listen";
    button.setAttribute("aria-label", playing ? "Pause" : "Listen");
  }
}

function skitCard(skit) {
  const card = document.createElement("article");
  card.className = "skit-card";
  const ready = mixReady(skit);
  const art = document.createElement(ready ? "button" : "div");
  art.className = "card-art";
  if (ready) {
    art.type = "button";
    art.setAttribute("aria-label", "Play skit");
  }
  const img = document.createElement("img");
  img.src = bannerFor(skit);
  img.alt = "";
  art.append(img);

  const blurb = document.createElement("p");
  blurb.className = "blurb";
  blurb.textContent = excerpt(skit);

  const actions = document.createElement("div");
  actions.className = "card-actions";
  const edit = document.createElement("button");
  edit.type = "button";
  edit.className = "text-btn";
  edit.textContent = "Edit";
  edit.addEventListener("click", () => {
    editSkit(skit.id).catch((error) => toast(error.message));
  });
  const remove = document.createElement("button");
  remove.type = "button";
  remove.className = "text-btn danger";
  remove.textContent = "Delete";
  remove.addEventListener("click", () => {
    deleteSkit(skit).catch((error) => toast(error.message));
  });
  actions.append(edit, remove);
  card.append(art, blurb);

  if (ready) {
    const audio = document.createElement("audio");
    audio.controls = true;
    audio.preload = "none";
    const version = mixVersion(skit);
    audio.src = `/api/skits/${skit.id}/export.wav?v=${encodeURIComponent(version)}`;
    art.addEventListener("click", () => {
      if (!audio.paused) {
        audio.pause();
        return;
      }
      stopSounds();
      audio.play();
    });
    audio.addEventListener("play", () => {
      clip.pause();
      state.playingId = null;
      for (const other of document.querySelectorAll("audio")) {
        if (other !== audio) other.pause();
      }
    });
    card.append(audio);
  } else {
    const hint = document.createElement("p");
    hint.className = "hint";
    hint.textContent = skit.lines.length ? "Generate it to listen." : "Empty";
    card.append(hint);
  }
  card.append(actions);
  return card;
}

function renderShelf() {
  const left = $("side-left");
  const right = $("side-right");
  const mid = Math.floor(state.skits.length / 2);
  left.replaceChildren();
  right.replaceChildren();
  state.skits.slice(0, mid).forEach((skit) => left.append(skitCard(skit)));
  state.skits.slice(mid).forEach((skit) => right.append(skitCard(skit)));
  $("shelf").hidden = false;
  $("editor").hidden = true;
}

function optionButton(line, act, label, glyph) {
  const button = document.createElement("button");
  button.type = "button";
  button.dataset.act = act;
  button.dataset.line = line.id;
  button.setAttribute("aria-label", label);
  if (act === "type") button.dataset.opensMenu = "true";
  const caption = document.createElement("span");
  caption.className = "caption";
  caption.textContent = label;
  button.append(icon(glyph), caption);
  return button;
}

function paintFoot(card, line) {
  const count = card.querySelector(".count");
  const limit = textLimit();
  const length = line.text.trim().length;
  count.textContent = `${length} / ${limit}`;
  count.classList.toggle("hot", length > limit * 0.9);
  const note = card.querySelector(".note");
  note.textContent = noteFor(line);
  note.classList.toggle("bad", line.status === "error" || line.status === "stale");
  card.classList.toggle("is-speaking", state.speakingId === line.id);
  const options = card.querySelector(".options");
  options.replaceChildren();
  const change = optionButton(line, "type", "Speaker", "type");
  change.setAttribute("aria-label", "Change speaker");
  change.disabled = state.busy;
  change.addEventListener("click", () => showTypeMenu(change, line));
  options.append(change);
  if (canPlay(line)) {
    const listen = optionButton(line, "play", "Listen", "play");
    listen.disabled = state.busy && state.speakingId === line.id;
    listen.addEventListener("click", () => playLine(line));
    const regen = optionButton(line, "reload", "Regenerate", "reload");
    regen.disabled = state.busy;
    regen.addEventListener("click", () => {
      regenerate(line).catch((error) => toast(error.message));
    });
    const download = optionButton(line, "download", "Download", "download");
    download.dataset.opensMenu = "true";
    download.disabled = state.busy;
    download.addEventListener("click", () => {
      showDownloadMenu(download, (fmt) => `/api/lines/${line.id}/download?format=${encodeURIComponent(fmt)}`);
    });
    options.append(listen, regen, download);
  }
  paintPlayIcons();
}

function paintAfterSave(line) {
  const card = document.querySelector(`[data-line="${line.id}"]`);
  if (!card) return;
  const area = card.querySelector("textarea");
  if (area && area.value !== line.text && document.activeElement !== area) {
    area.value = line.text;
    fitLine(area);
  }
  paintFoot(card, line);
  paintMix();
}

function fitLine(area) {
  area.style.height = "auto";
  const next = Math.min(Math.max(area.scrollHeight, 68), 176);
  area.style.height = `${next}px`;
}

function dialogueCard(line) {
  const card = document.createElement("article");
  card.className = `dialogue ${line.speaker === "kira" ? "from-right" : "from-left"}`;
  card.dataset.line = line.id;

  const face = document.createElement("img");
  face.className = "face";
  face.src = faceFor(line.speaker);
  face.alt = "";
  const name = document.createElement("p");
  name.className = "who";
  name.textContent = who(line);

  const field = document.createElement("div");
  field.className = "line-text";
  const text = document.createElement("textarea");
  text.rows = 1;
  text.maxLength = textLimit();
  text.value = line.text;
  text.placeholder = "Write the line";
  text.readOnly = state.busy;
  text.addEventListener("input", () => {
    line.text = text.value;
    fitLine(text);
    paintFoot(card, line);
    scheduleSave(line);
  });
  text.addEventListener("change", () => {
    enqueue(line.id, () => persist(line))
      .then(() => paintAfterSave(line))
      .catch((error) => toast(error.message));
  });
  const meta = document.createElement("div");
  meta.className = "meta-row";
  const count = document.createElement("span");
  count.className = "count";
  const note = document.createElement("span");
  note.className = "note";
  meta.append(count, note);
  field.append(text, meta);

  const options = document.createElement("div");
  options.className = "options";

  const remove = document.createElement("button");
  remove.type = "button";
  remove.className = "remove";
  remove.setAttribute("aria-label", "Remove dialogue");
  remove.textContent = "×";
  remove.disabled = state.busy;
  remove.addEventListener("click", () => {
    removeLine(line).catch((error) => toast(error.message));
  });

  card.append(face, name, field, options, remove);
  paintFoot(card, line);
  return card;
}

function paintMix() {
  const mix = $("mix");
  const audio = $("mix-audio");
  if (!state.skit || !mixReady(state.skit)) {
    audio.removeAttribute("src");
    audio.load();
    mix.hidden = true;
    return;
  }
  const src = `/api/skits/${state.skit.id}/export.wav?v=${encodeURIComponent(mixVersion(state.skit))}`;
  if (audio.getAttribute("src") !== src) audio.src = src;
  mix.hidden = false;
}

function renderEditor() {
  $("shelf").hidden = true;
  $("editor").hidden = false;
  const root = $("dialogues");
  root.replaceChildren();
  if (!state.skit || !state.skit.lines.length) {
    const empty = document.createElement("p");
    empty.className = "empty-lines";
    empty.textContent = "Choose L or Kira.";
    root.append(empty);
  } else {
    for (const line of state.skit.lines) root.append(dialogueCard(line));
    for (const area of root.querySelectorAll("textarea")) fitLine(area);
  }
  const generate = $("generate");
  generate.disabled = state.busy || !state.skit || !state.skit.lines.length;
  generate.textContent = state.busy ? "Speaking…" : "Generate audio";
  $("add-l").disabled = state.busy;
  $("add-kira").disabled = state.busy;
  $("back").disabled = state.busy;
  $("mix-download").disabled = state.busy;
  paintMix();
}

function render() {
  closeMenu();
  if (state.view === "editor" && state.skit) renderEditor();
  else {
    state.view = "shelf";
    renderShelf();
  }
}

async function addLine(speaker) {
  closeMenu();
  if (!state.skit || state.busy) return;
  try {
    await flushSaves();
    await api(`/api/skits/${state.skit.id}/lines`, {
      method: "POST",
      body: JSON.stringify({ speaker, text: "", emotion: "neutral", pause_ms: 400 }),
    });
    await reloadSkit();
    renderEditor();
    const areas = document.querySelectorAll(".dialogue textarea");
    const last = areas[areas.length - 1];
    if (last) last.focus();
  } catch (error) {
    toast(error.message);
  }
}

async function changeType(line, speaker) {
  closeMenu();
  if (line.speaker === speaker && line.emotion === "neutral") return;
  line.speaker = speaker;
  line.emotion = "neutral";
  clearTimeout(timers.get(line.id));
  timers.delete(line.id);
  try {
    await enqueue(line.id, () => persist(line));
    renderEditor();
  } catch (error) {
    toast(error.message);
    await reloadSkit();
    renderEditor();
  }
}

async function removeLine(line) {
  if (state.busy) return;
  clearTimeout(timers.get(line.id));
  timers.delete(line.id);
  await enqueue(line.id, async () => {});
  await api(`/api/lines/${line.id}`, { method: "DELETE" });
  if (state.playingId === line.id) {
    clip.pause();
    state.playingId = null;
  }
  await reloadSkit();
  renderEditor();
}

async function editSkit(id) {
  stopSounds();
  state.skit = await api(`/api/skits/${id}`);
  state.skit.lines.forEach(remember);
  state.view = "editor";
  toast("");
  render();
}

async function deleteSkit(skit) {
  const preview = excerpt(skit);
  if (!window.confirm(`Delete this skit?\n${preview}`)) return;
  stopSounds();
  await api(`/api/skits/${skit.id}`, { method: "DELETE" });
  await loadShelf();
  state.view = "shelf";
  render();
}

async function createSkit() {
  if (state.busy || state.creating) return;
  state.creating = true;
  $("new-skit").disabled = true;
  toast("");
  try {
    const skit = await api("/api/skits", {
      method: "POST",
      body: JSON.stringify({ title: "Skit" }),
    });
    skit.lines.forEach(remember);
    state.skit = skit;
    state.view = "editor";
    render();
  } catch (error) {
    toast(error.message);
  } finally {
    state.creating = false;
    if ($("new-skit")) $("new-skit").disabled = false;
  }
}

let progressTimer = null;
let progressReveal = null;

function showProgress(line, index, count) {
  const face = $("progress-face");
  face.src = faceFor(line.speaker);
  face.alt = "";
  $("progress-kicker").textContent = `${who(line)} · line ${index} of ${count}`;
  $("progress-quote").textContent = line.text.trim();
  $("progress-detail").textContent = "Starting…";
  const bar = $("progress-bar");
  bar.classList.add("is-wait");
  bar.style.width = "35%";
  $("progress").hidden = true;
  if (progressTimer) clearInterval(progressTimer);
  if (progressReveal) clearTimeout(progressReveal);
  progressReveal = setTimeout(() => {
    $("progress").hidden = false;
  }, 350);
  refreshProgress().catch(() => {});
  progressTimer = setInterval(() => {
    refreshProgress().catch(() => {});
  }, 400);
}

async function refreshProgress() {
  const data = await api("/api/progress");
  if (!data || !data.active) return;
  $("progress").hidden = false;
  $("progress-detail").textContent = data.detail || "Speaking…";
  const bar = $("progress-bar");
  if (data.determinate) {
    bar.classList.remove("is-wait");
    const pct = Math.max(4, Math.min(100, Math.round((data.fraction || 0) * 100)));
    bar.style.width = `${pct}%`;
  } else {
    bar.classList.add("is-wait");
    bar.style.width = "35%";
  }
}

function hideProgress() {
  if (progressTimer) clearInterval(progressTimer);
  if (progressReveal) clearTimeout(progressReveal);
  progressTimer = null;
  progressReveal = null;
  const screen = $("progress");
  if (screen) screen.hidden = true;
}

async function generateAll() {
  if (!state.skit || state.busy) return;
  state.busy = true;
  stopSounds();
  toast("");
  renderEditor();
  let message = "";
  let failedId = null;
  try {
    await flushSaves();
    await reloadSkit();
    const pending = state.skit.lines.filter((line) => {
      if (!line.text.trim()) return false;
      if (line.mode === "saved") return false;
      return line.status !== "ready" && line.status !== "fallback";
    });
    if (!pending.length) {
      const unwritten = state.skit.lines.some((line) => !line.text.trim() && line.mode !== "saved");
      message = unwritten
        ? "Write each line, then generate."
        : "Every line already has a voice. Use Reload to say one again.";
      return;
    }
    for (let index = 0; index < pending.length; index += 1) {
      const line = state.skit.lines.find((item) => item.id === pending[index].id);
      state.speakingId = line.id;
      failedId = line.id;
      toast("");
      renderEditor();
      showProgress(line, index + 1, pending.length);
      const updated = await api(`/api/lines/${line.id}/generate`, { method: "POST" });
      Object.assign(line, updated);
      remember(line);
      state.speakingId = null;
      renderEditor();
    }
    await reloadSkit();
    if (state.skit.lines.some((line) => !line.text.trim() && line.mode !== "saved")) {
      message = "Write the empty lines, or remove them, to hear the full audio.";
    }
  } catch (error) {
    message = error.message;
    if (failedId) await keepSpokenFailure(failedId, message);
  } finally {
    hideProgress();
    state.busy = false;
    state.speakingId = null;
    renderEditor();
    toast(message);
  }
}

async function regenerate(line) {
  if (state.busy) return;
  await flushSaves();
  if (!line.text.trim()) {
    toast("Write the line first.");
    return;
  }
  state.busy = true;
  state.speakingId = line.id;
  stopSounds();
  toast("");
  renderEditor();
  showProgress(line, 1, 1);
  try {
    const updated = await api(`/api/lines/${line.id}/generate`, { method: "POST" });
    const current = state.skit.lines.find((item) => item.id === line.id);
    if (current) {
      Object.assign(current, updated);
      remember(current);
    }
    toast("");
    await reloadSkit();
  } catch (error) {
    await keepSpokenFailure(line.id, error.message);
    toast(error.message);
  } finally {
    hideProgress();
    state.busy = false;
    state.speakingId = null;
    renderEditor();
  }
}

$("login-form").addEventListener("submit", async (event) => {
  event.preventDefault();
  $("login-error").textContent = "";
  try {
    await api("/api/login", {
      method: "POST",
      body: JSON.stringify({ password: $("login-password").value }),
    });
    await boot();
  } catch (error) {
    $("login-error").textContent = error.message;
  }
});

$("new-skit").addEventListener("click", () => {
  createSkit();
});

$("add-l").addEventListener("click", () => addLine("l"));
$("add-kira").addEventListener("click", () => addLine("kira"));

$("back").addEventListener("click", async () => {
  if (state.busy) return;
  try {
    await flushSaves();
  } catch (error) {
    toast(error.message);
  }
  stopSounds();
  state.skit = null;
  state.view = "shelf";
  try {
    await loadShelf();
    toast("");
    render();
  } catch (error) {
    toast(error.message);
  }
});

$("generate").addEventListener("click", () => {
  generateAll().catch((error) => toast(error.message));
});

$("mix-download").addEventListener("click", () => {
  if (!state.skit || state.busy) return;
  showDownloadMenu(
    $("mix-download"),
    (fmt) => `/api/skits/${state.skit.id}/download?format=${encodeURIComponent(fmt)}`,
  );
});

$("mix-audio").addEventListener("play", () => {
  clip.pause();
  state.playingId = null;
  paintPlayIcons();
});

document.addEventListener("click", (event) => {
  const pop = $("popover");
  if (pop.hidden) return;
  if (pop.contains(event.target)) return;
  if (event.target.closest("[data-opens-menu]")) return;
  closeMenu();
});

window.addEventListener("resize", () => {
  if (!$("popover").hidden) closeMenu();
});

async function boot() {
  try {
    state.meta = await api("/api/meta");
  } catch (error) {
    showGate();
    return;
  }
  showApp();
  try {
    await loadShelf();
    state.view = "shelf";
    render();
  } catch (error) {
    toast(error.message);
  }
}

boot();
