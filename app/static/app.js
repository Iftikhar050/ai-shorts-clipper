// Shorts Clipper — single-file front end (no build step).
// All dynamic text goes through h()/textContent, never innerHTML, so titles
// and captions from videos or the AI can't inject markup.

const $ = (sel, root = document) => root.querySelector(sel);
const view = $("#view");
const OPTS_KEY = "asc.options.v1";
const THEME_KEY = "asc.theme";
const STAGES = [
  ["download", "Get video"], ["transcribe", "Transcribe"], ["signals", "Signals"],
  ["analyze", "Find moments"], ["visual", "Visual review"], ["render", "Render"],
];
const DEFAULT_OPTS = {
  clips: 3, clip_length: "medium", ratio: "9:16", font_style: "DEFAULT", words_per_sub: 5,
  output_language: "English", topic: "", time_range: "", visual_rerank: false, use_comments: false,
  karaoke: true, hook: true, broll: false, bgm: false, loudnorm: true,
};
let timers = [];
let health = null;
let pendingUpload = null; // {upload_id, name, size}

// ------------------------------------------------------------------ utils
function h(tag, attrs = {}, ...kids) {
  const el = document.createElement(tag);
  for (const [k, v] of Object.entries(attrs || {})) {
    if (v === null || v === undefined || v === false) continue;
    if (k === "class") el.className = v;
    else if (k === "style" && typeof v === "object") Object.assign(el.style, v);
    else if (k.startsWith("on")) el.addEventListener(k.slice(2), v);
    else if (k === "html") throw new Error("innerHTML is not allowed");
    else el.setAttribute(k, v === true ? "" : v);
  }
  for (const kid of kids.flat(Infinity)) {
    if (kid === null || kid === undefined || kid === false) continue;
    el.append(kid instanceof Node ? kid : document.createTextNode(String(kid)));
  }
  return el;
}
function icon(path, extra = "") {
  const ns = "http://www.w3.org/2000/svg";
  const svg = document.createElementNS(ns, "svg");
  svg.setAttribute("viewBox", "0 0 24 24");
  if (extra) svg.setAttribute("class", extra);
  for (const d of [].concat(path)) {
    const p = document.createElementNS(ns, "path");
    p.setAttribute("d", d);
    svg.append(p);
  }
  return svg;
}
const I = {
  link: "M10 14a4 4 0 005.7 0l3-3a4 4 0 00-5.7-5.7l-1 1M14 10a4 4 0 00-5.7 0l-3 3a4 4 0 005.7 5.7l1-1",
  upload: ["M12 16V4", "M7 9l5-5 5 5", "M4 16v3a1 1 0 001 1h14a1 1 0 001-1v-3"],
  download: ["M12 4v12", "M7 11l5 5 5-5", "M4 20h16"],
  back: "M15 6l-6 6 6 6",
  play: "M8 5l11 7-11 7z",
  copy: ["M9 9h10v10H9z", "M5 15V5h10"],
  close: ["M6 6l12 12", "M18 6L6 18"],
  film: ["M4 5h16v14H4z", "M8 5v14M16 5v14M4 9h4M4 15h4M16 9h4M16 15h4"],
};
async function api(path, opts = {}) {
  const init = { method: opts.method || "GET", headers: {} };
  if (opts.body !== undefined) {
    init.headers["Content-Type"] = "application/json";
    init.body = JSON.stringify(opts.body);
  }
  const res = await fetch(path, init);
  let data = null;
  try { data = await res.json(); } catch { /* empty body */ }
  if (!res.ok) throw new Error((data && data.detail) || `${res.status} ${res.statusText}`);
  return data;
}
function toast(msg, bad = false) {
  const t = h("div", { class: "toast" + (bad ? " bad" : "") }, msg);
  $("#toasts").append(t);
  setTimeout(() => t.remove(), bad ? 6000 : 3200);
}
function every(ms, fn) { const id = setInterval(fn, ms); timers.push(id); return id; }
function clearTimers() { timers.forEach(clearInterval); timers = []; }
function ago(iso) {
  if (!iso) return "";
  const s = (Date.now() - new Date(iso).getTime()) / 1000;
  if (s < 60) return "just now";
  if (s < 3600) return `${Math.floor(s / 60)} min ago`;
  if (s < 86400) return `${Math.floor(s / 3600)} h ago`;
  return new Date(iso).toLocaleDateString();
}
function mmss(sec) {
  sec = Math.max(0, Math.round(Number(sec) || 0));
  return `${Math.floor(sec / 60)}:${String(sec % 60).padStart(2, "0")}`;
}
function loadOpts() {
  try { return { ...DEFAULT_OPTS, ...JSON.parse(localStorage.getItem(OPTS_KEY) || "{}") }; }
  catch { return { ...DEFAULT_OPTS }; }
}
function saveOpts(o) { try { localStorage.setItem(OPTS_KEY, JSON.stringify(o)); } catch { /* private mode */ } }
async function copyText(text, label = "Copied") {
  try { await navigator.clipboard.writeText(text); toast(label); }
  catch { toast("Couldn't copy — select the text manually", true); }
}
const statusLabel = (s) => ({ success: "Done", partial: "Partly done", failed: "Failed", running: "Working",
  queued: "Queued", cancelled: "Cancelled", incomplete: "Incomplete" }[s] || s);
const ytThumb = (id) => `https://i.ytimg.com/vi/${encodeURIComponent(id)}/hqdefault.jpg`;

// ------------------------------------------------------------------ theme
function applyTheme(t) {
  if (t === "light" || t === "dark") document.documentElement.dataset.theme = t;
  else delete document.documentElement.dataset.theme;
}
(function initTheme() {
  let saved = null;
  try { saved = localStorage.getItem(THEME_KEY); } catch { /* ignore */ }
  const sys = matchMedia("(prefers-color-scheme: light)").matches ? "light" : "dark";
  applyTheme(saved || sys);
  $("#themeToggle").addEventListener("click", () => {
    const next = document.documentElement.dataset.theme === "light" ? "dark" : "light";
    applyTheme(next);
    try { localStorage.setItem(THEME_KEY, next); } catch { /* ignore */ }
  });
})();

// ----------------------------------------------------------------- health
async function refreshHealth() {
  try { health = await api("/api/settings"); } catch { health = null; }
  const box = $("#health");
  box.replaceChildren();
  if (!health) { box.append(h("span", {}, h("i", { class: "dot bad" }), "Server unreachable")); return; }
  const row = (ok, text, warn) => h("span", {}, h("i", { class: "dot " + (ok ? "ok" : warn ? "warn" : "bad") }), text);
  box.append(
    row(health.keys.GOOGLE_API_KEY.set, health.keys.GOOGLE_API_KEY.set ? "Gemini key set" : "Gemini key missing"),
    row(health.ffmpeg, health.ffmpeg ? "FFmpeg found" : "FFmpeg missing"),
    row(health.accounts_configured, health.accounts_configured ? "Accounts set" : "Default account", true),
  );
}

// ----------------------------------------------------------------- router
async function route() {
  clearTimers();
  closeDrawer();
  const hash = location.hash || "#/";
  const [, page, arg] = hash.match(/^#\/?([^/]*)\/?(.*)$/) || [];
  document.querySelectorAll("[data-nav]").forEach((a) =>
    a.classList.toggle("active", a.dataset.nav === (page === "job" || !page ? "home" : page)));
  view.replaceChildren(h("div", { class: "skeleton", style: { height: "160px" } }));
  try {
    if (page === "job" && arg) await renderJob(decodeURIComponent(arg));
    else if (page === "accounts") await renderAccounts();
    else if (page === "settings") await renderSettings();
    else await renderHome();
  } catch (e) {
    view.replaceChildren(h("div", { class: "empty" }, "Something went wrong: ", e.message));
  }
  view.focus({ preventScroll: true });
}
window.addEventListener("hashchange", route);

// ------------------------------------------------------------------- home
async function renderHome() {
  const opts = loadOpts();
  const set = (k, v) => { opts[k] = v; saveOpts(opts); };

  const srcInput = h("input", { type: "text", id: "src", placeholder: "Paste a YouTube, TikTok, Instagram or Google Drive link…",
    autocomplete: "off", spellcheck: "false" });
  const uploadSlot = h("div");
  const fileInput = h("input", { type: "file", accept: "video/*,.mkv", hidden: true,
    onchange: (e) => e.target.files[0] && doUpload(e.target.files[0]) });
  const goBtn = h("button", { class: "btn", type: "submit" }, "Get clips");

  function showUpload() {
    uploadSlot.replaceChildren();
    if (!pendingUpload) { srcInput.disabled = false; return; }
    srcInput.disabled = true;
    srcInput.value = "";
    uploadSlot.append(h("div", { class: "upload-chip", style: { marginTop: "10px" } }, icon(I.film), pendingUpload.name,
      h("span", { class: "faint" }, ` · ${(pendingUpload.size / 1048576).toFixed(0)} MB`),
      h("button", { type: "button", title: "Remove", onclick: () => { pendingUpload = null; showUpload(); } }, "×")));
  }
  async function doUpload(file) {
    goBtn.disabled = true;
    uploadSlot.replaceChildren(h("div", { class: "upload-chip", style: { marginTop: "10px" } }, h("i", { class: "spin" }), `Uploading ${file.name}… `,
      h("b", { id: "upPct" }, "0%")));
    try {
      pendingUpload = await uploadFile(file, (p) => { const el = $("#upPct"); if (el) el.textContent = `${p}%`; });
      toast("Video uploaded");
    } catch (e) { toast(e.message, true); pendingUpload = null; }
    goBtn.disabled = false;
    showUpload();
  }
  window.__asc_upload = doUpload; // used by the global drag & drop handler

  const seg = (key, choices) => {
    const wrap = h("div", { class: "seg", role: "group" });
    for (const [val, label] of choices) {
      const b = h("button", { type: "button", "aria-pressed": String(opts[key] === val), onclick: () => {
        set(key, val);
        wrap.querySelectorAll("button").forEach((x) => x.setAttribute("aria-pressed", String(x === b)));
      } }, label);
      wrap.append(b);
    }
    return wrap;
  };
  const out = h("output", {}, opts.clips);
  const stepper = h("div", { class: "stepper" },
    h("button", { type: "button", "aria-label": "Fewer clips", onclick: () => { set("clips", Math.max(1, opts.clips - 1)); out.textContent = opts.clips; } }, "−"),
    out,
    h("button", { type: "button", "aria-label": "More clips", onclick: () => { set("clips", Math.min(10, opts.clips + 1)); out.textContent = opts.clips; } }, "+"));
  const select = (key, choices) => h("select", { onchange: (e) => set(key, e.target.value) },
    choices.map(([v, l]) => h("option", { value: v, selected: opts[key] === v }, l)));
  const toggle = (key, title, sub, disabled) => h("label", { class: "toggle", title: disabled || "" },
    h("input", { type: "checkbox", checked: opts[key] && !disabled, disabled: !!disabled,
      onchange: (e) => set(key, e.target.checked) }),
    h("div", {}, h("b", {}, title), h("small", {}, disabled || sub)));
  const [trFrom, trTo] = (opts.time_range || "").split("-");
  const from = h("input", { type: "text", placeholder: "from 0:00", value: trFrom || "" });
  const to = h("input", { type: "text", placeholder: "to 12:30", value: trTo || "" });
  const syncRange = () => set("time_range", from.value.trim() && to.value.trim() ? `${from.value.trim()}-${to.value.trim()}` : "");
  from.addEventListener("input", syncRange); to.addEventListener("input", syncRange);
  const pexels = health && health.keys.PEXELS_API_KEY.set;

  const form = h("form", { class: "composer", onsubmit: async (e) => {
    e.preventDefault();
    const body = { options: { ...opts } };
    if (pendingUpload) body.upload_id = pendingUpload.upload_id, body.title = pendingUpload.name;
    else body.source = srcInput.value.trim();
    if (!body.upload_id && !body.source) { srcInput.focus(); toast("Paste a link or drop a video first", true); return; }
    if (!pexels) body.options.broll = false;
    goBtn.disabled = true;
    try {
      const job = await api("/api/jobs", { method: "POST", body });
      pendingUpload = null;
      location.hash = `#/job/${encodeURIComponent(job.id)}`;
    } catch (err) { toast(err.message, true); goBtn.disabled = false; }
  } },
    h("div", { class: "composer-bar" },
      h("div", { class: "src" }, icon(I.link), srcInput),
      h("button", { class: "ghost", type: "button", onclick: () => fileInput.click() }, icon(I.upload), "Upload"),
      goBtn, fileInput),
    uploadSlot,
    h("div", { class: "opts" },
      h("div", { class: "opt" }, h("span", {}, "Clips"), stepper),
      h("div", { class: "opt" }, h("span", {}, "Length"), seg("clip_length", [["short", "15–30s"], ["medium", "30–60s"], ["long", "60–90s"], ["any", "Any"]])),
      h("div", { class: "opt" }, h("span", {}, "Format"), seg("ratio", [["9:16", "9:16"], ["1:1", "1:1"], ["4:5", "4:5"], ["16:9", "16:9"]])),
      h("div", { class: "opt" }, h("span", {}, "Captions"), select("font_style", [["DEFAULT", "Default"], ["HORMOZI", "Hormozi"], ["STORYTELLER", "Storyteller"], ["CINEMATIC", "Cinematic"]]))),
    h("details", { class: "more" },
      h("summary", {}, "More options"),
      h("div", { class: "more-grid" },
        h("label", { class: "field" }, "Topic (optional)",
          h("input", { type: "text", placeholder: "e.g. sleep and dopamine", value: opts.topic, maxlength: 200, oninput: (e) => set("topic", e.target.value) }),
          h("span", { class: "hint" }, "Only clip moments about this.")),
        h("label", { class: "field" }, "Time range (optional)", h("div", { class: "range-row" }, from, "–", to),
          h("span", { class: "hint" }, "Clip only this part of the video.")),
        h("label", { class: "field" }, "Titles & captions language",
          h("input", { type: "text", value: opts.output_language, maxlength: 40, oninput: (e) => set("output_language", e.target.value) }),
          h("span", { class: "hint" }, "English, Urdu, Spanish…")),
        h("label", { class: "field" }, "Words per caption line", selectNum()),
        toggle("visual_rerank", "Visual review", "Gemini looks at frames to judge each clip (uses more AI)."),
        toggle("use_comments", "Viewer comments", "Use timestamps viewers mention in YouTube comments."),
        toggle("karaoke", "Karaoke captions", "Highlight each word as it's spoken."),
        toggle("hook", "Hook teaser", "Open each clip with its strongest line."),
        toggle("broll", "B-roll", "Insert stock footage.", pexels ? "" : "Add a Pexels key in Settings to enable."),
        toggle("loudnorm", "Even loudness", "Normalize audio to −14 LUFS."))));

  function selectNum() {
    return h("select", { onchange: (e) => set("words_per_sub", Number(e.target.value)) },
      [1, 2, 3, 4, 5, 6, 7, 8].map((n) => h("option", { value: n, selected: Number(opts.words_per_sub) === n }, n)));
  }

  const search = h("input", { type: "search", placeholder: "Search projects" });
  const gridSlot = h("div");
  view.replaceChildren(
    h("div", { class: "hero" }, h("h1", {}, "Turn long videos into shorts"),
      h("p", {}, "Paste a link or drop a video. The AI finds the best moments, frames them vertically and writes the captions.")),
    form,
    h("div", { class: "section-head" }, h("h2", {}, "Projects"), h("div", { class: "filters" }, search)),
    gridSlot);
  showUpload();

  let jobs = [];
  const draw = () => {
    const q = search.value.trim().toLowerCase();
    const list = jobs.filter((j) => !q || `${j.title} ${j.source}`.toLowerCase().includes(q));
    if (!list.length) {
      gridSlot.replaceChildren(h("div", { class: "empty" }, jobs.length ? "No projects match your search." :
        "No projects yet. Paste a link above to make your first clips."));
      return;
    }
    gridSlot.replaceChildren(h("div", { class: "grid" }, list.map(projectCard)));
  };
  search.addEventListener("input", draw);
  const load = async () => {
    try { jobs = (await api("/api/jobs")).jobs; draw(); } catch (e) { gridSlot.replaceChildren(h("div", { class: "empty" }, e.message)); }
  };
  await load();
  every(3000, () => { if (jobs.some((j) => j.status === "running" || j.status === "queued")) load(); });
}

function projectCard(j) {
  const bg = j.thumbnail || (j.youtube_id ? ytThumb(j.youtube_id) : null);
  const active = j.status === "running" || j.status === "queued";
  return h("a", { class: "pcard", href: `#/job/${encodeURIComponent(j.id)}` },
    h("div", { class: "pthumb", style: bg ? { backgroundImage: `url("${bg}")` } : {} },
      bg ? null : icon(I.film),
      h("span", { class: `status ${j.status}` }, statusLabel(j.status))),
    h("div", { class: "pbody" },
      h("div", { class: "ptitle" }, j.title || j.source || j.id),
      active ? h("div", { class: "bar" }, h("i", { style: { width: `${j.percent || 0}%` } })) : null,
      h("div", { class: "pmeta" },
        h("span", {}, active ? (j.queue_position ? `#${j.queue_position} in queue` : j.stage_label) :
          j.status === "failed" ? "Failed" : j.status === "incomplete" ? "Not finished" :
          `${j.rendered || 0} clip${j.rendered === 1 ? "" : "s"}`),
        h("span", {}, j.readonly ? "CLI run" : ago(j.created_at)))));
}

function uploadFile(file, onProgress) {
  return new Promise((resolve, reject) => {
    const xhr = new XMLHttpRequest();
    const fd = new FormData();
    fd.append("file", file);
    xhr.open("POST", "/api/uploads");
    xhr.upload.onprogress = (e) => e.lengthComputable && onProgress(Math.round((e.loaded / e.total) * 100));
    xhr.onload = () => {
      let data = {};
      try { data = JSON.parse(xhr.responseText); } catch { /* ignore */ }
      xhr.status < 300 ? resolve(data) : reject(new Error(data.detail || `Upload failed (${xhr.status})`));
    };
    xhr.onerror = () => reject(new Error("Upload failed — is the app still running?"));
    xhr.send(fd);
  });
}

// global drag & drop
(function dragDrop() {
  let depth = 0;
  const overlay = $("#dropOverlay");
  window.addEventListener("dragenter", (e) => { if (e.dataTransfer?.types?.includes("Files")) { depth++; overlay.hidden = false; } });
  window.addEventListener("dragleave", () => { depth = Math.max(0, depth - 1); if (!depth) overlay.hidden = true; });
  window.addEventListener("dragover", (e) => e.preventDefault());
  window.addEventListener("drop", (e) => {
    e.preventDefault(); depth = 0; overlay.hidden = true;
    const file = e.dataTransfer?.files?.[0];
    if (!file) return;
    if (!location.hash || location.hash === "#/") window.__asc_upload?.(file);
    else { location.hash = "#/"; setTimeout(() => window.__asc_upload?.(file), 300); }
  });
})();

// -------------------------------------------------------------------- job
async function renderJob(id) {
  let job = await api(`/api/jobs/${encodeURIComponent(id)}`).catch(() => null);
  const readonly = id.startsWith("cli-");
  if (!job && readonly) {
    const all = (await api("/api/jobs")).jobs;
    job = all.find((j) => j.id === id);
  }
  if (!job) { view.replaceChildren(h("a", { class: "back", href: "#/" }, icon(I.back), "Projects"), h("div", { class: "empty" }, "Project not found.")); return; }

  const head = h("div");
  const prog = h("div");
  const clipsSlot = h("div");
  view.replaceChildren(h("a", { class: "back", href: "#/" }, icon(I.back), "Projects"), head, prog,
    h("div", { class: "section-head" }, h("h2", {}, "Clips")), clipsSlot);

  let logOffset = 0;
  const logEl = h("pre", { class: "log" });
  const logBox = h("details", { class: "logbox", ontoggle: () => logBox.open && pullLog() }, h("summary", {}, "Show log"), logEl);
  async function pullLog() {
    if (readonly) return;
    try {
      const r = await api(`/api/jobs/${encodeURIComponent(id)}/log?offset=${logOffset}`);
      if (r.text) {
        const stick = logEl.scrollTop + logEl.clientHeight >= logEl.scrollHeight - 30;
        logEl.append(r.text.replace(/\r/g, "\n"));
        if (logEl.textContent.length > 400000) logEl.textContent = logEl.textContent.slice(-300000);
        if (stick) logEl.scrollTop = logEl.scrollHeight;
      }
      logOffset = r.offset;
    } catch { /* transient */ }
  }

  function drawHead() {
    const o = job.options || {};
    const chips = [];
    if (o.clips) chips.push(`${o.clips} clips`);
    if (o.clip_length) chips.push({ short: "15–30s", medium: "30–60s", long: "60–90s", any: "Any length" }[o.clip_length]);
    if (o.ratio) chips.push(o.ratio);
    if (o.topic) chips.push(`Topic: ${o.topic}`);
    if (o.time_range) chips.push(`Range ${o.time_range}`);
    if (o.visual_rerank) chips.push("Visual review");
    if (o.output_language && o.output_language !== "English") chips.push(o.output_language);
    const active = job.status === "running" || job.status === "queued";
    head.replaceChildren(h("div", { class: "phead" },
      h("div", {},
        h("h1", {}, job.title || job.source),
        job.source && job.source.startsWith("http") ? h("a", { class: "src", href: job.source, target: "_blank", rel: "noopener noreferrer" }, job.source)
          : h("div", { class: "src" }, job.source || (readonly ? "Earlier command-line run" : "")),
        h("div", { class: "chips" }, h("span", { class: `status ${job.status}` }, statusLabel(job.status)),
          chips.map((c) => h("span", { class: "chip" }, c)))),
      readonly ? null : h("div", { class: "actions" },
        active ? h("button", { class: "ghost danger", onclick: async () => {
          if (!confirm("Stop this job?")) return;
          try { await api(`/api/jobs/${encodeURIComponent(id)}/cancel`, { method: "POST" }); toast("Stopping…"); } catch (e) { toast(e.message, true); }
        } }, "Cancel") : null,
        !active ? h("button", { class: "ghost", onclick: async () => {
          try { const j = await api(`/api/jobs/${encodeURIComponent(id)}/rerun`, { method: "POST" }); location.hash = `#/job/${encodeURIComponent(j.id)}`; }
          catch (e) { toast(e.message, true); }
        } }, "Run again") : null,
        !active ? h("button", { class: "ghost danger", onclick: async () => {
          const withFiles = confirm("Delete this project?\n\nOK = also delete its clip files\nCancel = keep everything");
          if (!withFiles && !confirm("Remove the project from the list but keep the files?")) return;
          try { await api(`/api/jobs/${encodeURIComponent(id)}?files=${withFiles}`, { method: "DELETE" }); location.hash = "#/"; }
          catch (e) { toast(e.message, true); }
        } }, "Delete") : null)));
  }

  function drawProgress() {
    if (readonly) { prog.replaceChildren(); return; }
    const active = job.status === "running" || job.status === "queued";
    const idx = STAGES.findIndex(([k]) => k === job.stage);
    const finished = job.status === "success" || job.status === "partial";
    const skipVisual = !(job.options || {}).visual_rerank;
    const stages = h("div", { class: "stages" }, STAGES.map(([k, label], i) => {
      let cls = "stage";
      if (finished || i < idx) cls += " done";
      else if (i === idx && active) cls += " now";
      if (k === "visual" && skipVisual) cls += " skip";
      return h("div", { class: cls }, h("i"), label);
    }));
    const right = active ? (job.status === "queued" ? (job.queue_position ? `#${job.queue_position} in queue` : "Waiting…") : `${job.percent || 0}%`)
      : job.finished_at ? `Finished ${ago(job.finished_at)}` : "";
    prog.replaceChildren(h("div", { class: "progress" },
      h("div", { class: "progress-top" }, h("b", {}, job.stage_label || statusLabel(job.status)), h("span", {}, right)),
      h("div", { class: "bar" }, h("i", { style: { width: `${finished ? 100 : job.percent || 0}%` } })),
      stages,
      job.error ? h("div", { class: "errbox" }, job.error) : null,
      logBox));
  }

  let lastClipKey = "";
  async function drawClips(force = false) {
    let clips = [];
    try { clips = (await api(`/api/jobs/${encodeURIComponent(id)}/clips`)).clips; } catch { /* not ready */ }
    const key = JSON.stringify(clips.map((c) => [c.rank, c.status, !!c.video_url, c.viral_score]));
    if (!force && key === lastClipKey) return;
    lastClipKey = key;
    if (!clips.length) {
      const active = job.status === "running" || job.status === "queued";
      clipsSlot.replaceChildren(h("div", { class: "empty" }, active ? "Clips will appear here as soon as the AI has picked them." :
        "No clips were produced. Open the log above to see why."));
      return;
    }
    clipsSlot.replaceChildren(h("div", { class: "clips" }, clips.map((c) => clipCard(c, job))));
  }

  drawHead(); drawProgress(); await drawClips(true);
  if (!readonly) {
    every(2000, async () => {
      if (!(job.status === "running" || job.status === "queued")) return;
      try {
        const prev = job.status;
        job = await api(`/api/jobs/${encodeURIComponent(id)}`);
        drawProgress();
        if (job.stage === "render" || prev !== job.status) await drawClips();
        if (prev !== job.status) { drawHead(); await drawClips(true); refreshHealth();
          if (job.status === "success") toast("Your clips are ready");
          else if (job.status === "partial") toast("Some clips failed — see the log", true);
          else if (job.status === "failed") toast("The job failed — see the error above", true); }
        if (logBox.open) pullLog();
      } catch { /* keep polling */ }
    });
  }
}

function ratioClass(r) { return { "16:9": "r169", "1:1": "r11", "4:5": "r45" }[r] || ""; }

function clipCard(c, job) {
  const player = h("div", { class: `player ${ratioClass((job.options || {}).ratio)}` });
  const showPoster = () => {
    player.replaceChildren(
      c.thumbnail_url ? h("img", { src: c.thumbnail_url, alt: "", loading: "lazy" }) : null,
      c.video_url ? h("button", { class: "play", type: "button", "aria-label": `Play clip ${c.rank}`, onclick: () => {
        const v = h("video", { src: c.video_url, controls: true, autoplay: true, playsinline: true, preload: "metadata" });
        player.replaceChildren(v, badges());
      } }, h("span", {}, icon(I.play))) : null,
      badges());
  };
  const badges = () => h("div", { class: "badges" }, h("span", { class: "pill" }, `#${c.rank}`),
    h("span", { class: "pill" }, mmss(c.duration)));
  showPoster();
  const failed = c.status !== "success";
  const score = Number(c.viral_score) || 0;
  return h("div", { class: "clip" + (failed ? " failed" : "") }, player,
    h("div", { class: "cbody" },
      h("div", { class: "ctop" },
        h("div", { class: "score", style: { "--v": Math.min(100, score) }, title: "Score" }, h("b", {}, score || "–")),
        h("div", { class: "ctitle" }, c.title)),
      c.hook ? h("div", { class: "chook" }, `“${c.hook}”`) : null,
      failed ? h("div", { class: "errbox", style: { marginTop: 0 } }, c.status === "missing" ? "Not rendered yet" :
        `Render failed${c.error ? `: ${String(c.error).split("\n").pop()}` : ""}`) : null,
      h("div", { class: "chips", style: { marginTop: 0 } },
        c.account ? h("span", { class: "chip accent" }, c.account) : null,
        c.visual_score != null ? h("span", { class: "chip" }, `Visual ${c.visual_score}`) : null),
      h("div", { class: "crow" },
        c.video_url ? h("a", { class: "ghost small", href: c.video_url, download: `clip_${c.rank}.mp4` }, icon(I.download), "Download") : null,
        h("button", { class: "btn small", type: "button", onclick: () => openClip(c, job) }, "Details"))));
}

// ----------------------------------------------------------------- drawer
function closeDrawer() {
  const d = $("#drawer");
  if (d.hidden) return;
  d.querySelectorAll("video").forEach((v) => v.pause());
  d.hidden = true;
}
$("#drawer").addEventListener("click", (e) => { if (e.target.dataset.close !== undefined) closeDrawer(); });
window.addEventListener("keydown", (e) => { if (e.key === "Escape") closeDrawer(); });

function openClip(c, job) {
  const panel = $("#drawer .drawer-panel");
  const copyRow = (label, text) => text ? h("div", { class: "copy" },
    h("div", { class: "copy-head" }, label, h("button", { class: "ghost small", type: "button", onclick: () => copyText(text, `${label} copied`) }, icon(I.copy), "Copy")),
    h("div", { class: "copy-text" }, text)) : null;
  const sb = c.score_breakdown || {};
  const names = { hook: "Hook", emotion: "Emotion", shareability: "Shareability", clarity: "Clarity", payoff: "Payoff" };
  const breakdown = Object.keys(sb).length ? h("div", { class: "block" }, h("h3", {}, "Why it scores " + (c.viral_score ?? "")),
    Object.entries(names).filter(([k]) => k in sb).map(([k, l]) => h("div", { class: "brk" }, h("span", {}, l),
      h("div", { class: "bar" }, h("i", { style: { width: `${(sb[k] / 20) * 100}%` } })), h("b", {}, `${sb[k]}/20`)))) : null;
  const comps = c.score_components || {};
  const compNames = { ai: "AI", heatmap: "Most replayed", comments: "Comments", audio: "Audio", visual: "Visual" };
  const sig = c.audience_signals || {};
  const signals = h("div", { class: "block" }, h("h3", {}, "Signals"),
    h("dl", { class: "kv" },
      c.ai_viral_score != null && c.ai_viral_score !== c.viral_score ? [h("dt", {}, "AI score"), h("dd", {}, c.ai_viral_score)] : null,
      Object.keys(comps).length ? [h("dt", {}, "Combined from"), h("dd", {}, Object.entries(comps).map(([k, v]) => `${compNames[k] || k} ${Math.round(v * 100)}`).join(" · "))] : null,
      c.visual_score != null ? [h("dt", {}, "Visual"), h("dd", {}, `${c.visual_score}/100 — ${c.visual_notes || ""}`)] : null,
      sig.heatmap_peak != null ? [h("dt", {}, "Most replayed"), h("dd", {}, `${Math.round(sig.heatmap_peak * 100)}%`)] : null,
      sig.comment_mentions ? [h("dt", {}, "Comment mentions"), h("dd", {}, sig.comment_mentions)] : null,
      (sig.event_tags || []).length ? [h("dt", {}, "Events"), h("dd", {}, sig.event_tags.join(", "))] : null,
      [h("dt", {}, "From source"), h("dd", {}, `${mmss(c.start_time)} – ${mmss(c.end_time)}`)],
      c.account ? [h("dt", {}, "Account"), h("dd", {}, `${c.account}${c.account_handle && c.account_handle !== c.account ? ` (${c.account_handle})` : ""}`)] : null,
      c.loudness_normalized ? [h("dt", {}, "Audio"), h("dd", {}, "Normalized to −14 LUFS")] : null));
  const tags = (c.youtube_tags || []).join(", ");
  panel.replaceChildren(
    h("div", { class: "dhead" }, h("div", {}, h("div", { class: "faint" }, `Clip #${c.rank} · ${mmss(c.duration)}`), h("h2", { id: "drawerTitle" }, c.title)),
      h("button", { class: "ghost icon-btn", type: "button", "aria-label": "Close", onclick: closeDrawer }, icon(I.close))),
    h("div", { class: "dgrid" },
      h("div", {},
        h("div", { class: `player ${ratioClass((job.options || {}).ratio)}`, style: { borderRadius: "14px", overflow: "hidden" } },
          c.video_url ? h("video", { src: c.video_url, controls: true, playsinline: true, preload: "metadata", poster: c.thumbnail_url || undefined })
            : h("div", { class: "empty", style: { position: "absolute", inset: 0, display: "grid", placeItems: "center" } }, "No video")),
        h("div", { class: "crow", style: { marginTop: "10px" } },
          c.video_url ? h("a", { class: "btn small", href: c.video_url, download: `clip_${c.rank}.mp4` }, icon(I.download), "Video") : null,
          c.thumbnail_url ? h("a", { class: "ghost small", href: c.thumbnail_url, download: `thumbnail_${c.rank}.jpg` }, icon(I.download), "Thumbnail") : null)),
      h("div", {},
        c.reason ? h("div", { class: "block" }, h("h3", {}, "Why the AI picked it"), h("div", {}, c.reason),
          c.hook ? h("p", { class: "muted", style: { margin: "10px 0 0" } }, "Hook: ", h("i", {}, `“${c.hook}”`)) : null) : null,
        breakdown,
        signals,
        h("div", { class: "block" }, h("h3", {}, "Ready to post"),
          copyRow("YouTube title", c.youtube_title),
          copyRow("YouTube description", c.youtube_description),
          copyRow("YouTube tags", tags),
          copyRow("TikTok / Reels caption", c.tiktok_caption),
          copyRow("Long TikTok title", c.tiktok_title),
          copyRow("Hashtags", c.hashtags)))));
  $("#drawer").hidden = false;
  panel.focus?.();
}

// --------------------------------------------------------------- accounts
async function renderAccounts() {
  const res = await api("/api/accounts");
  const data = JSON.parse(JSON.stringify(res.data || { accounts: {}, rules: [] }));
  let rows = Object.entries(data.accounts || {}).map(([name, a]) => ({ name, handle: a.handle || a.akun_tujuan || "",
    angle_desc: a.angle_desc || "", bio: a.bio || "" }));
  let rules = [...(data.rules || [])];
  const list = h("div");
  const ruleList = h("div");

  const field = (label, val, on, attrs = {}) => h("label", { class: "field" + (attrs.full ? " full" : "") }, label,
    attrs.area ? h("textarea", { oninput: (e) => on(e.target.value) }, val)
      : h("input", { type: "text", value: val, oninput: (e) => on(e.target.value) }));
  function draw() {
    list.replaceChildren(...rows.map((r, i) => h("div", { class: "acct" },
      h("div", { class: "acct-head" }, h("b", {}, r.name || `Account ${i + 1}`),
        h("button", { class: "ghost small danger", type: "button", onclick: () => { rows.splice(i, 1); draw(); } }, "Remove")),
      field("Name", r.name, (v) => { r.name = v; }),
      field("Handle", r.handle, (v) => { r.handle = v; }),
      field("When a clip belongs here", r.angle_desc, (v) => { r.angle_desc = v; }, { full: true, area: true }),
      field("Bio (optional)", r.bio, (v) => { r.bio = v; }, { full: true }))),
    h("button", { class: "ghost", type: "button", onclick: () => { rows.push({ name: "", handle: "", angle_desc: "", bio: "" }); draw(); } }, "+ Add account"));
    ruleList.replaceChildren(...rules.map((r, i) => h("div", { class: "rule" },
      h("input", { type: "text", value: r, oninput: (e) => { rules[i] = e.target.value; } }),
      h("button", { class: "ghost small", type: "button", onclick: () => { rules.splice(i, 1); draw(); } }, "×"))),
    h("button", { class: "ghost small", type: "button", onclick: () => { rules.push(""); draw(); } }, "+ Add rule"));
  }
  draw();
  const save = async () => {
    const accounts = {};
    for (const r of rows) {
      const name = r.name.trim();
      if (!name) { toast("Every account needs a name", true); return; }
      if (accounts[name]) { toast(`Duplicate account name: ${name}`, true); return; }
      accounts[name] = { handle: r.handle.trim() || name, angle_desc: r.angle_desc.trim(), bio: r.bio.trim() };
    }
    try {
      await api("/api/accounts", { method: "PUT", body: { accounts, rules: rules.map((x) => x.trim()).filter(Boolean) } });
      toast("Accounts saved — new projects will use them"); refreshHealth();
    } catch (e) { toast(e.message, true); }
  };
  view.replaceChildren(
    h("h1", { class: "page-title" }, "Accounts"),
    h("p", { class: "page-sub" }, "Where your clips get posted. The AI routes every clip to the account whose angle fits best.",
      res.configured ? "" : " (Showing the example — save to start using your own.)"),
    h("div", { class: "panel" }, h("h2", {}, "Accounts"), h("p", {}, "Describe the angle, not just the topic. Example: “Health, brain and psychology explainers with a practical takeaway.”"), list),
    h("div", { class: "panel" }, h("h2", {}, "Tie-break rules"), h("p", {}, "Optional. For clips that could fit more than one account."), ruleList),
    h("div", { class: "savebar" },
      res.configured ? h("button", { class: "ghost danger", type: "button", onclick: async () => {
        if (!confirm("Delete accounts.json and go back to a single General account?")) return;
        await api("/api/accounts", { method: "DELETE" }); toast("Accounts reset"); refreshHealth(); route();
      } }, "Reset") : null,
      h("button", { class: "btn", type: "button", onclick: save }, "Save accounts")));
}

// --------------------------------------------------------------- settings
async function renderSettings() {
  const s = await api("/api/settings");
  const keyInputs = {};
  const keyRow = (key, label, help) => {
    const st = s.keys[key];
    const inp = h("input", { type: "password", placeholder: st.set ? `Saved (${st.hint || "set"}) — type to replace` : "Paste key", autocomplete: "off" });
    keyInputs[key] = inp;
    return h("div", { class: "key-row" },
      h("div", {}, h("b", {}, label), h("div", { class: "hint" }, help)),
      inp,
      h("span", { class: "chip" + (st.set ? " accent" : "") }, st.set ? "Set" : "Missing"));
  };
  const setInputs = {};
  const setField = (key, label, choices) => {
    const el = choices ? h("select", {}, choices.map((c) => h("option", { value: c, selected: s.settings[key] === c }, c)))
      : h("input", { type: "text", value: s.settings[key] });
    setInputs[key] = el;
    return h("label", { class: "field" }, label, el);
  };
  view.replaceChildren(
    h("h1", { class: "page-title" }, "Settings"),
    h("p", { class: "page-sub" }, "Stored on this computer only. Keys are saved to .env and never shown again in full."),
    h("div", { class: "panel" }, h("h2", {}, "API keys"),
      h("p", {}, "Gemini is required. Pexels is optional (stock B-roll)."),
      keyRow("GOOGLE_API_KEY", "Gemini", "aistudio.google.com/apikey"),
      keyRow("PEXELS_API_KEY", "Pexels", "pexels.com/api — optional")),
    h("div", { class: "panel" }, h("h2", {}, "Models & speed"),
      h("p", {}, "Defaults suit a laptop without a GPU. Larger Whisper models are more accurate but much slower on CPU."),
      h("div", { class: "form-grid" },
        setField("gemini_model", "Gemini model"),
        setField("gemini_fallback_model", "Fallback model"),
        setField("whisper_model", "Whisper model", ["tiny", "base", "small", "medium", "large-v3"]),
        setField("whisper_device", "Whisper device", ["cpu", "cuda", "auto"]),
        setField("whisper_compute_type", "Compute type", ["int8", "float16", "float32"]),
        setField("source_height", "Download quality", ["720", "1080", "1440", "max"]))),
    h("div", { class: "panel" }, h("h2", {}, "System"),
      h("dl", { class: "kv" },
        h("dt", {}, "FFmpeg"), h("dd", {}, s.ffmpeg ? "Found" : "Not found — install FFmpeg and add it to PATH"),
        h("dt", {}, "Accounts"), h("dd", {}, s.accounts_configured ? "accounts.json" : "Default (General)"))),
    h("div", { class: "savebar" }, h("button", { class: "btn", type: "button", onclick: async () => {
      const keys = {};
      for (const [k, inp] of Object.entries(keyInputs)) if (inp.value.trim()) keys[k] = inp.value.trim();
      const settings = {};
      for (const [k, el] of Object.entries(setInputs)) settings[k] = el.value.trim();
      try { await api("/api/settings", { method: "PUT", body: { keys, settings } }); toast("Settings saved"); refreshHealth(); route(); }
      catch (e) { toast(e.message, true); }
    } }, "Save settings")));
}

// ------------------------------------------------------------------- boot
refreshHealth().then(route);
setInterval(refreshHealth, 30000);
