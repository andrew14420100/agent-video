"use strict";
// Steam Focus Studio – interfaccia. Nessuna dipendenza esterna.
const $ = (s, r = document) => r.querySelector(s);
const $$ = (s, r = document) => [...r.querySelectorAll(s)];
const esc = (s) => String(s ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
const fmt = (t) => { t = Math.max(0, t || 0); const m = Math.floor(t / 60); return `${m}:${(t - m * 60).toFixed(1).padStart(4, "0")}`; };

const S = { project: null, tl: null, dirty: false, sel: null, pps: 60, runId: null, poll: null, settings: null };
const XFADES = ["cut", "fade", "dissolve", "fadeblack", "fadewhite", "wipeleft", "wiperight", "slideleft", "slideright",
  "slideup", "smoothleft", "smoothright", "circleopen", "circleclose", "radial", "zoomin", "hblur", "pixelize", "diagtl"];

async function api(path, opts = {}) {
  const o = { ...opts };
  if (o.body && !(o.body instanceof FormData)) { o.headers = { "Content-Type": "application/json" }; o.body = JSON.stringify(o.body); }
  const r = await fetch("/api" + path, o);
  const ct = r.headers.get("content-type") || "";
  const data = ct.includes("json") ? await r.json() : await r.text();
  if (!r.ok) throw new Error((data && data.detail) || r.statusText);
  return data;
}
function toast(msg, err = false) {
  const t = $("#toast"); t.textContent = msg; t.className = "toast show" + (err ? " err" : "");
  clearTimeout(toast._t); toast._t = setTimeout(() => (t.className = "toast"), err ? 6000 : 2800);
}
const fileUrl = (rel) => `/api/projects/${S.project.id}/files/${rel.split("/").map(encodeURIComponent).join("/")}`;

// ---------------------------------------------------------------- progetti
async function loadProjects(selectId) {
  let list = await api("/projects");
  if (!list.length) { await api("/projects", { method: "POST", body: { name: "Primo progetto" } }); list = await api("/projects"); }
  const sel = $("#projectSelect");
  sel.innerHTML = list.map((p) => `<option value="${p.id}">${esc(p.name)}</option>`).join("");
  const id = selectId || localStorage.getItem("sfs.project") || list[0].id;
  sel.value = list.some((p) => p.id === id) ? id : list[0].id;
  await openProject(sel.value);
}
async function openProject(id) {
  localStorage.setItem("sfs.project", id);
  setProject(await api(`/projects/${id}`));
  const act = await api("/active");
  if (act && act.project_id === id) watchRun(act.id);
}
function setProject(p, keepTimeline = false) {
  S.project = p;
  if (!keepTimeline || !S.dirty) { S.tl = structuredClone(p.timeline); S.dirty = false; }
  $("#projectName").value = p.name;
  if (p.brief && p.brief.prompt && !$("#prompt").value) $("#prompt").value = p.brief.prompt;
  renderAssets(); renderTimeline(); renderRenders(); renderExport(); renderInspector();
  if (p.runs.length && !S.runId) showRun(p.runs[p.runs.length - 1]);
}

// ---------------------------------------------------------------- asset
function assetById(id) { return S.project.assets.find((a) => a.id === id); }
function assetCard(a) {
  const i = a.info || {}, s = a.analysis_summary || {};
  const thumb = a.thumb ? fileUrl(a.thumb) : "";
  const desc = a.kind === "audio" ? `${fmt(i.duration)}${s.bpm ? ` · ${s.bpm} BPM` : ""}`
    : a.kind === "image" ? `${i.width}×${i.height}` : `${i.width}×${i.height} · ${fmt(i.duration)}${i.fps ? ` · ${i.fps} fps` : ""}`;
  const q = s.mean_score != null ? ` · qualità ${(s.mean_score * 100).toFixed(0)}` : "";
  return `<div class="asset" data-id="${a.id}" draggable="${a.kind !== "audio"}">
    ${thumb ? `<img src="${thumb}" alt="">` : `<img alt="">`}
    <div class="meta"><div class="title" title="${esc(a.name)}">${esc(a.name)}</div>
      <div class="muted">${a.kind} · ${desc}${q}</div>
      <div><span class="tag ${a.license_ok ? "lic-ok" : "lic-bad"}" title="${esc(a.license.notes)}">${esc(a.license.name)}</span>${a.role ? `<span class="tag role">${a.role}</span>` : ""}${s.scene_count ? `<span class="tag">${s.scene_count} scene</span>` : ""}</div>
      <div class="actions">
        ${a.kind !== "audio" && !a.role ? `<button class="small" data-act="add">＋ Timeline</button>` : ""}
        ${a.kind === "audio" && a.role !== "voice" ? `<button class="small" data-act="addaudio">＋ Musica</button>` : ""}
        ${a.kind === "image" && a.role !== "logo" ? `<button class="small" data-act="logo">Usa come logo</button>` : ""}
        <button class="small ghost danger" data-act="del">Elimina</button></div></div></div>`;
}
function renderAssets() {
  const list = S.project.assets.filter((a) => a.role !== "voice");
  $("#assetList").innerHTML = list.length ? list.map(assetCard).join("") : `<p class="hint">Nessun file. Importa le tue clip oppure chiedi agli agenti di cercare materiale a licenza libera.</p>`;
}
$("#assetList").addEventListener("click", async (e) => {
  const b = e.target.closest("button[data-act]"); if (!b) return;
  const id = b.closest(".asset").dataset.id, a = assetById(id);
  if (b.dataset.act === "add") addClip(a);
  if (b.dataset.act === "addaudio") { S.tl.audio.push({ asset_id: a.id, role: "music", start: 0, src_in: 0, duration: Math.max(1, tlDuration() || a.info.duration), volume_db: -2, fade_in: 0.5, fade_out: 2, loop: (a.info.duration || 0) < tlDuration(), text: "" }); markDirty(); }
  if (b.dataset.act === "logo") { S.tl.logo = { asset_id: a.id, position: "top-right", scale: 0.09, opacity: 0.9 }; markDirty(); toast("Logo impostato: salva per applicarlo"); }
  if (b.dataset.act === "del" && confirm(`Eliminare ${a.name} dal progetto?`)) {
    if (S.dirty) await saveTimeline();
    setProject(await api(`/projects/${S.project.id}/assets/${id}`, { method: "DELETE" }));
  }
});
$("#assetList").addEventListener("dragstart", (e) => { const c = e.target.closest(".asset"); if (c) e.dataTransfer.setData("text/asset", c.dataset.id); });
function addClip(a, index = S.tl.video.length) {
  const d = a.kind === "image" ? 4 : Math.min(5, Math.max(0.5, (a.info.duration || 5) - 0.1));
  S.tl.video.splice(index, 0, { asset_id: a.id, src_in: 0, duration: +d.toFixed(2), transition: { type: "cut", duration: 0 },
    fit: "cover", motion: a.kind === "image" ? "zoom_in" : "none", volume: 1, mute: false, speed: 1, look: "none", note: "aggiunta manualmente" });
  markDirty(); select("video", index);
}

async function upload(files, role = "") {
  if (S.dirty) await saveTimeline();
  for (const f of files) {
    const fd = new FormData(); fd.append("file", f); fd.append("role", role); fd.append("analyze", "true");
    toast(`Importo e analizzo ${f.name}…`);
    try { setProject(await api(`/projects/${S.project.id}/assets`, { method: "POST", body: fd })); }
    catch (err) { toast(`${f.name}: ${err.message}`, true); }
  }
  toast("Importazione completata");
}
$("#fileInput").onchange = (e) => upload([...e.target.files]);
$("#logoInput").onchange = (e) => upload([...e.target.files], "logo");
$("#musicInput").onchange = (e) => upload([...e.target.files], "music");
const dz = $("#dropZone");
dz.ondragover = (e) => { e.preventDefault(); dz.classList.add("over"); };
dz.ondragleave = () => dz.classList.remove("over");
dz.ondrop = (e) => { e.preventDefault(); dz.classList.remove("over"); upload([...e.dataTransfer.files]); };
$("#pathBtn").onclick = async () => {
  const path = $("#pathInput").value.trim(); if (!path) return;
  try { toast("Importo…"); setProject(await api(`/projects/${S.project.id}/assets/import-path`, { method: "POST", body: { path } })); $("#pathInput").value = ""; }
  catch (e) { toast(e.message, true); }
};

// ---------------------------------------------------------------- ricerca
let lastResults = [];
$("#searchBtn").onclick = async () => {
  const [provider, kind] = $("#searchProvider").value.split(":"), q = $("#searchQuery").value.trim();
  if (!q) return;
  $("#searchResults").innerHTML = `<p class="hint">Ricerca in corso…</p>`;
  try {
    lastResults = await api(`/research?provider=${provider}&kind=${kind}&q=${encodeURIComponent(q)}`);
    const ok = new Set(S.settings.allowed_licenses);
    $("#searchResults").innerHTML = lastResults.length ? lastResults.map((r, i) => `<div class="asset">
      <img src="${esc(r.thumb)}" alt="" loading="lazy"><div class="meta"><div class="title" title="${esc(r.title)}">${esc(r.title)}</div>
      <div class="muted">${r.kind}${r.duration ? " · " + fmt(r.duration) : ""}${r.width ? ` · ${r.width}×${r.height}` : ""}</div>
      <div><span class="tag ${ok.has(r.license.code) ? "lic-ok" : "lic-bad"}" title="${esc(r.license.notes)}">${esc(r.license.name)}</span></div>
      <div class="actions"><a class="btn small ghost" href="${esc(r.landing_url)}" target="_blank" rel="noopener">Fonte</a>
      ${ok.has(r.license.code) ? `<button class="small" data-i="${i}">Scarica nel progetto</button>` : `<span class="muted">non scaricabile</span>`}</div></div></div>`).join("")
      : `<p class="hint">Nessun risultato.</p>`;
  } catch (e) { $("#searchResults").innerHTML = `<p class="hint">Errore: ${esc(e.message)}</p>`; }
};
$("#searchResults").addEventListener("click", async (e) => {
  const b = e.target.closest("button[data-i]"); if (!b) return;
  b.disabled = true; b.textContent = "Scarico…";
  try { if (S.dirty) await saveTimeline(); setProject(await api(`/projects/${S.project.id}/research/import`, { method: "POST", body: lastResults[+b.dataset.i] })); toast("Aggiunto alla libreria con licenza registrata"); b.textContent = "Aggiunto"; }
  catch (err) { toast(err.message, true); b.disabled = false; b.textContent = "Riprova"; }
});

// ---------------------------------------------------------------- timeline
function tlStarts() { const out = []; let t = 0; S.tl.video.forEach((c, i) => { const tr = i > 0 && c.transition.type !== "cut" ? c.transition.duration : 0; t -= tr; out.push(Math.max(0, t)); t += c.duration; }); return out; }
function tlDuration() { let t = 0; S.tl.video.forEach((c, i) => { t += c.duration - (i > 0 && c.transition.type !== "cut" ? c.transition.duration : 0); }); return Math.max(0, t); }
function markDirty() { S.dirty = true; $("#saveBtn").disabled = false; renderTimeline(); renderInspector(); }
function renderTimeline() {
  const pps = S.pps, T = tlDuration(), width = Math.max((T + 5) * pps, $("#tracksScroll").clientWidth);
  $("#tracks").style.width = width + "px";
  let r = ""; const step = pps >= 100 ? 1 : pps >= 40 ? 2 : 5;
  for (let t = 0; t <= T + 5; t += step) r += `<span style="left:${t * pps}px">${fmt(t)}</span>`;
  $("#ruler").innerHTML = r;
  const starts = tlStarts();
  $("#trackVideo").innerHTML = S.tl.video.map((c, i) => {
    const a = assetById(c.asset_id) || { name: "?", kind: "video" };
    const bg = a.thumb ? `background-image:linear-gradient(#0006,#0006),url('${fileUrl(a.thumb)}')` : "";
    const tr = i > 0 && c.transition.type !== "cut" ? `<div class="trans" style="left:${starts[i] * pps}px;width:${c.transition.duration * pps}px"></div>` : "";
    return `${tr}<div class="item ${a.kind === "image" ? "img" : ""} ${isSel("video", i) ? "sel" : ""}" draggable="true" data-track="video" data-i="${i}"
      style="left:${starts[i] * pps}px;width:${Math.max(4, c.duration * pps - 2)}px;${bg}" title="${esc(c.note || a.name)}"><span class="lbl">${esc(a.name)} · ${c.duration.toFixed(2)}s</span></div>`;
  }).join("");
  $("#trackText").innerHTML = S.tl.overlays.map((o, i) => `<div class="item text ${isSel("text", i) ? "sel" : ""}" data-track="text" data-i="${i}" style="left:${o.start * pps}px;width:${Math.max(4, o.duration * pps)}px">${esc(o.kind)}: ${esc(o.text)}</div>`).join("");
  $("#trackAudio").innerHTML = S.tl.audio.map((c, i) => { const a = assetById(c.asset_id) || { name: "?" }; return `<div class="item audio ${c.role} ${isSel("audio", i) ? "sel" : ""}" data-track="audio" data-i="${i}" style="left:${c.start * pps}px;width:${Math.max(4, c.duration * pps)}px" title="${esc(c.text || a.name)}">${c.role === "voice" ? "🎙 " + esc(c.text) : "♪ " + esc(a.name)}</div>`; }).join("");
  $("#trackSubs").innerHTML = S.tl.subtitles.map((c, i) => `<div class="item sub ${isSel("sub", i) ? "sel" : ""}" data-track="sub" data-i="${i}" style="left:${c.start * pps}px;width:${Math.max(4, (c.end - c.start) * pps)}px">${esc(c.text)}</div>`).join("");
  $("#tlInfo").textContent = `${S.tl.video.length} clip · ${fmt(T)} (${T.toFixed(3)} s)${S.dirty ? " · modifiche non salvate" : ""}`;
  movePlayhead();
}
const isSel = (track, i) => S.sel && S.sel.track === track && S.sel.i === i;
function select(track, i) { S.sel = { track, i }; renderTimeline(); renderInspector(); switchRTab("inspector"); }
$("#tracks").addEventListener("click", (e) => {
  const it = e.target.closest(".item");
  if (it) { select(it.dataset.track, +it.dataset.i); const st = it.dataset.track === "video" ? tlStarts()[+it.dataset.i] : null; if (st != null) seek(st + 0.05); return; }
  if (e.target.closest("#ruler")) { const x = e.clientX - $("#tracks").getBoundingClientRect().left; seek(x / S.pps); }
});
// trascinamento: riordino clip e drop di asset dalla libreria
$("#trackVideo").addEventListener("dragstart", (e) => { const it = e.target.closest(".item"); if (it) e.dataTransfer.setData("text/clip", it.dataset.i); });
$("#trackVideo").addEventListener("dragover", (e) => { e.preventDefault(); $$(".item.dragover").forEach((x) => x.classList.remove("dragover")); const it = e.target.closest(".item"); if (it) it.classList.add("dragover"); });
$("#trackVideo").addEventListener("drop", (e) => {
  e.preventDefault(); const it = e.target.closest(".item"); $$(".item.dragover").forEach((x) => x.classList.remove("dragover"));
  const to = it ? +it.dataset.i : S.tl.video.length;
  const from = e.dataTransfer.getData("text/clip"), aid = e.dataTransfer.getData("text/asset");
  if (from !== "") { const [c] = S.tl.video.splice(+from, 1); S.tl.video.splice(to > +from ? to : to, 0, c); fixFirstTransition(); markDirty(); select("video", S.tl.video.indexOf(c)); }
  else if (aid) addClip(assetById(aid), to);
});
function fixFirstTransition() { if (S.tl.video[0]) S.tl.video[0].transition = { type: "cut", duration: 0 }; }
$("#zoom").oninput = (e) => { S.pps = +e.target.value; renderTimeline(); };
document.addEventListener("keydown", (e) => {
  if (["INPUT", "TEXTAREA", "SELECT"].includes(document.activeElement.tagName)) return;
  if ((e.key === "Delete" || e.key === "Backspace") && S.sel) { removeSelected(); e.preventDefault(); }
  if (e.key === " ") { const p = $("#player"); p.paused ? p.play() : p.pause(); e.preventDefault(); }
  if ((e.ctrlKey || e.metaKey) && e.key === "s") { e.preventDefault(); saveTimeline(); }
});
function removeSelected() {
  const { track, i } = S.sel; const arr = { video: S.tl.video, text: S.tl.overlays, audio: S.tl.audio, sub: S.tl.subtitles }[track];
  arr.splice(i, 1); if (track === "video") fixFirstTransition(); S.sel = null; markDirty();
}
$("#addTitleBtn").onclick = () => { S.tl.overlays.push({ kind: "title", text: "Nuovo titolo", subtext: "", start: +($("#player").currentTime || 0).toFixed(2), duration: 3 }); markDirty(); select("text", S.tl.overlays.length - 1); };
$("#addSubBtn").onclick = () => { const t = +($("#player").currentTime || 0).toFixed(2); S.tl.subtitles.push({ start: t, end: t + 2, text: "Nuovo sottotitolo" }); S.tl.subtitles.sort((a, b) => a.start - b.start); markDirty(); };

async function saveTimeline() {
  try { const p = await api(`/projects/${S.project.id}`, { method: "PUT", body: { timeline: S.tl, name: $("#projectName").value } }); S.dirty = false; $("#saveBtn").disabled = true; setProject(p); toast("Timeline salvata"); return true; }
  catch (e) { toast("Salvataggio rifiutato: " + e.message, true); return false; }
}
$("#saveBtn").onclick = saveTimeline;
$("#projectName").onchange = async () => { setProject(await api(`/projects/${S.project.id}`, { method: "PUT", body: { name: $("#projectName").value } }), true); loadProjects(S.project.id); };

// ---------------------------------------------------------------- inspector
function field(label, html) { return `<label>${label}</label>${html}`; }
function num(key, val, step = 0.05, min = 0) { return `<input type="number" data-k="${key}" value="${val}" step="${step}" min="${min}">`; }
function sel(key, val, opts) { return `<select data-k="${key}">${opts.map((o) => `<option ${o === val ? "selected" : ""}>${o}</option>`).join("")}</select>`; }
function renderInspector() {
  const box = $("#inspector");
  if (!S.sel) { box.innerHTML = `<p class="hint">Seleziona un elemento nella timeline. Trascina le clip per riordinarle, trascina dalla libreria per inserirle. Canc elimina, Spazio riproduce, Ctrl+S salva.</p>`; return; }
  const { track, i } = S.sel;
  if (track === "video") {
    const c = S.tl.video[i]; if (!c) { S.sel = null; return renderInspector(); }
    const a = assetById(c.asset_id) || { info: {}, name: "?" };
    box.innerHTML = `<h4>Clip ${i + 1}: ${esc(a.name)}</h4>${c.note ? `<p class="hint">${esc(c.note)}</p>` : ""}
      <div class="grid2">${field("Punto di ingresso (s)", num("src_in", c.src_in))}${field("Durata (s)", num("duration", c.duration, 0.05, 0.1))}</div>
      <p class="hint">Sorgente: ${a.info.duration ? a.info.duration.toFixed(2) + " s" : "immagine"}</p>
      <div class="grid2">${field("Transizione in entrata", sel("transition.type", c.transition.type, XFADES))}${field("Durata transizione", num("transition.duration", c.transition.duration))}</div>
      <div class="grid2">${field("Adattamento", sel("fit", c.fit, ["cover", "contain"]))}${field("Movimento", sel("motion", c.motion, ["none", "zoom_in", "zoom_out", "pan_left", "pan_right"]))}</div>
      <div class="grid2">${field("Look", sel("look", c.look, ["none", "cinematic", "vivid", "warm", "mono"]))}${field("Velocità", num("speed", c.speed, 0.05, 0.5))}</div>
      <div class="grid2">${field("Volume audio clip", num("volume", c.volume, 0.05))}${field("&nbsp;", `<span class="check"><input type="checkbox" data-k="mute" ${c.mute ? "checked" : ""}> Muto</span>`)}</div>
      <div class="row gap"><button class="small" data-op="left">◀ Sposta</button><button class="small" data-op="right">Sposta ▶</button><button class="small" data-op="dup">Duplica</button><button class="small" data-op="split">Dividi al cursore</button><button class="small danger" data-op="del">Elimina</button></div>`;
  } else if (track === "text") {
    const o = S.tl.overlays[i];
    box.innerHTML = `<h4>Testo animato</h4>${field("Tipo", sel("kind", o.kind, ["title", "lower_third", "caption", "end_card"]))}
      ${field("Testo", `<input data-k="text" value="${esc(o.text)}">`)}${field("Sottotesto", `<input data-k="subtext" value="${esc(o.subtext)}">`)}
      <div class="grid2">${field("Inizio (s)", num("start", o.start))}${field("Durata (s)", num("duration", o.duration))}</div>
      <div class="row"><button class="small danger" data-op="del">Elimina</button></div>`;
  } else if (track === "audio") {
    const c = S.tl.audio[i]; const a = assetById(c.asset_id) || { name: "?" };
    box.innerHTML = `<h4>${c.role === "voice" ? "Voce" : "Audio"}: ${esc(a.name)}</h4>${c.text ? `<p class="hint">«${esc(c.text)}»</p>` : ""}
      <div class="grid2">${field("Inizio (s)", num("start", c.start))}${field("Durata (s)", num("duration", c.duration))}</div>
      <div class="grid2">${field("Ingresso sorgente (s)", num("src_in", c.src_in))}${field("Volume (dB)", num("volume_db", c.volume_db, 0.5, -60))}</div>
      <div class="grid2">${field("Fade in (s)", num("fade_in", c.fade_in))}${field("Fade out (s)", num("fade_out", c.fade_out))}</div>
      <span class="check"><input type="checkbox" data-k="loop" ${c.loop ? "checked" : ""}> Ripeti in loop</span>
      <div class="row"><button class="small danger" data-op="del">Elimina</button></div>`;
  } else if (track === "sub") {
    const c = S.tl.subtitles[i];
    box.innerHTML = `<h4>Sottotitolo ${i + 1}</h4>${field("Testo", `<textarea data-k="text" rows="2">${esc(c.text)}</textarea>`)}
      <div class="grid2">${field("Inizio (s)", num("start", c.start))}${field("Fine (s)", num("end", c.end))}</div>
      <div class="row"><button class="small danger" data-op="del">Elimina</button></div>`;
  }
}
function selObj() { const { track, i } = S.sel; return { video: S.tl.video, text: S.tl.overlays, audio: S.tl.audio, sub: S.tl.subtitles }[track][i]; }
$("#inspector").addEventListener("change", (e) => {
  const k = e.target.dataset.k; if (!k || !S.sel) return;
  let v = e.target.type === "checkbox" ? e.target.checked : e.target.type === "number" ? parseFloat(e.target.value) : e.target.value;
  if (typeof v === "number" && isNaN(v)) return;
  const o = selObj(); const parts = k.split(".");
  if (parts.length === 2) o[parts[0]][parts[1]] = v; else o[k] = v;
  if (k === "transition.type") o.transition.duration = v === "cut" ? 0 : (o.transition.duration || 0.5);
  if (S.sel.track === "video") fixFirstTransition();
  markDirty();
});
$("#inspector").addEventListener("click", (e) => {
  const b = e.target.closest("button[data-op]"); if (!b) return;
  const { track, i } = S.sel, v = S.tl.video;
  if (b.dataset.op === "del") return removeSelected();
  if (b.dataset.op === "left" && i > 0) { [v[i - 1], v[i]] = [v[i], v[i - 1]]; S.sel.i--; }
  if (b.dataset.op === "right" && i < v.length - 1) { [v[i + 1], v[i]] = [v[i], v[i + 1]]; S.sel.i++; }
  if (b.dataset.op === "dup") v.splice(i + 1, 0, structuredClone(v[i]));
  if (b.dataset.op === "split") {
    const t = $("#player").currentTime - tlStarts()[i];
    if (t > 0.2 && t < v[i].duration - 0.2) { const c2 = structuredClone(v[i]); c2.src_in = +(v[i].src_in + t * v[i].speed).toFixed(3); c2.duration = +(v[i].duration - t).toFixed(3); c2.transition = { type: "cut", duration: 0 }; v[i].duration = +t.toFixed(3); v.splice(i + 1, 0, c2); }
    else return toast("Posiziona il cursore dentro la clip", true);
  }
  fixFirstTransition(); markDirty();
});

// ---------------------------------------------------------------- export
function renderExport() {
  const e = S.project.export;
  $("#exportForm").innerHTML = `<h4>Formato di uscita</h4>
    ${field("Risoluzione", `<select data-e="res">${[["1920x1080", "1080p 16:9"], ["3840x2160", "4K UHD 16:9"], ["1280x720", "720p"], ["1080x1920", "Verticale 1080×1920"], ["2160x3840", "Verticale 4K"], ["1080x1080", "Quadrato"]].map(([v, l]) => `<option value="${v}" ${v === `${e.width}x${e.height}` ? "selected" : ""}>${l}</option>`).join("")}</select>`)}
    <div class="grid2">${field("Frame rate", `<select data-e="fps">${[24, 25, 30, 50, 60].map((f) => `<option ${f === e.fps ? "selected" : ""}>${f}</option>`).join("")}</select>`)}
    ${field("Codec", `<select data-e="codec"><option value="h264" ${e.codec === "h264" ? "selected" : ""}>H.264 (MP4)</option><option value="hevc" ${e.codec === "hevc" ? "selected" : ""}>HEVC / H.265 (MP4)</option></select>`)}</div>
    ${field("Encoder", `<select data-e="encoder">${[["auto", "Automatico (NVENC se verificato)"], ["gpu", "Preferisci GPU NVIDIA"], ["cpu", "Solo CPU (x264/x265)"]].map(([v, l]) => `<option value="${v}" ${v === e.encoder ? "selected" : ""}>${l}</option>`).join("")}</select>`)}
    <div class="grid2">${field("Loudness (LUFS)", `<input type="number" data-e="loudness_target" value="${e.loudness_target}" step="1">`)}${field("Bitrate audio", `<select data-e="audio_bitrate">${["192k", "256k", "320k"].map((b) => `<option ${b === e.audio_bitrate ? "selected" : ""}>${b}</option>`).join("")}</select>`)}</div>
    <label class="check"><input type="checkbox" data-e="burn_subtitles" ${e.burn_subtitles ? "checked" : ""}> Sottotitoli impressi nel video</label>
    <label class="check"><input type="checkbox" data-e="soft_subtitles" ${e.soft_subtitles ? "checked" : ""}> Traccia sottotitoli nel file MP4 + SRT/VTT</label>
    <p class="hint">Il file finale viene sempre verificato dal controllo qualità (ffprobe, decodifica completa, loudness EBU R128, nero/freeze/silenzi).</p>`;
}
$("#exportForm").addEventListener("change", async (ev) => {
  const e = structuredClone(S.project.export), k = ev.target.dataset.e;
  if (k === "res") { const [w, h] = ev.target.value.split("x").map(Number); e.width = w; e.height = h; }
  else if (ev.target.type === "checkbox") e[k] = ev.target.checked;
  else e[k] = ["fps", "loudness_target"].includes(k) ? Number(ev.target.value) : ev.target.value;
  try { setProject(await api(`/projects/${S.project.id}`, { method: "PUT", body: { export: e } }), true); toast("Impostazioni di esportazione salvate"); } catch (err) { toast(err.message, true); }
});

// ---------------------------------------------------------------- render e anteprima
function renderRenders() {
  const rs = [...S.project.renders].reverse(), sel = $("#renderSelect");
  sel.innerHTML = rs.map((r) => `<option value="${esc(r.path)}">${r.mode === "final" ? "Finale" : "Anteprima"} · ${esc(r.size)} · ${r.duration.toFixed(2)}s · ${esc(r.encoder)} · ${esc(r.path.split("/").pop())}</option>`).join("");
  if (rs.length) loadVideo(rs[0].path); else { $("#player").removeAttribute("src"); $("#viewerEmpty").hidden = false; }
}
$("#renderSelect").onchange = (e) => loadVideo(e.target.value);
function loadVideo(rel) {
  const p = $("#player"); const url = fileUrl(rel);
  if (p.dataset.src === rel) return;
  p.dataset.src = rel; p.src = url; $("#viewerEmpty").hidden = true; $("#openRender").href = url;
  $$("track", p).forEach((t) => t.remove());
  const vtt = rel.replace(/\.mp4$/, ".vtt");
  const tr = document.createElement("track"); tr.kind = "subtitles"; tr.srclang = "it"; tr.label = "Italiano"; tr.src = fileUrl(vtt);
  if (!S.project.export.burn_subtitles) tr.default = true;
  p.appendChild(tr);
}
function seek(t) { const p = $("#player"); if (p.src) p.currentTime = t; movePlayhead(t); }
function movePlayhead(t) { t = t ?? $("#player").currentTime ?? 0; $("#playhead").style.left = t * S.pps + "px"; }
$("#player").addEventListener("timeupdate", () => movePlayhead());

async function startRun(kind) {
  if (S.dirty && !(await saveTimeline())) return;
  try {
    const run = kind === "generate"
      ? await api(`/projects/${S.project.id}/generate`, { method: "POST", body: { prompt: $("#prompt").value } })
      : await api(`/projects/${S.project.id}/render`, { method: "POST", body: { mode: kind } });
    switchRTab("agents"); watchRun(run.id);
  } catch (e) { toast(e.message, true); }
}
$("#generateBtn").onclick = () => { if (!$("#prompt").value.trim()) return toast("Scrivi una richiesta", true); startRun("generate"); };
$("#previewBtn").onclick = () => startRun("preview");
$("#exportBtn").onclick = () => startRun("final");
$("#cancelBtn").onclick = () => S.runId && api(`/runs/${S.runId}/cancel`, { method: "POST" });

function setBusy(b) { ["#generateBtn", "#previewBtn", "#exportBtn"].forEach((s) => ($(s).disabled = b)); $("#cancelBtn").hidden = !b; }
function watchRun(id) {
  S.runId = id; setBusy(true); clearInterval(S.poll);
  const tick = async () => {
    try {
      const r = await api(`/projects/${S.project.id}/runs/${id}`); drawRun(r);
      if (!["queued", "running"].includes(r.status)) { clearInterval(S.poll); setBusy(false); S.dirty = false; setProject(await api(`/projects/${S.project.id}`)); drawRun(r); toast(`Elaborazione terminata: ${statusLabel(r.status)}`, r.status === "failed" || r.status === "blocked"); }
    } catch (e) { clearInterval(S.poll); setBusy(false); }
  };
  tick(); S.poll = setInterval(tick, 900);
}
async function showRun(id) { try { drawRun(await api(`/projects/${S.project.id}/runs/${id}`)); } catch { } }
const statusLabel = (s) => ({ completed: "completato", completed_with_issues: "completato con avvisi", failed: "non riuscito", blocked: "bloccato", cancelled: "annullato", running: "in corso", queued: "in coda", pending: "in attesa", skipped: "saltato" }[s] || s);
function drawRun(r) {
  const pill = { completed: "ok", completed_with_issues: "warn", failed: "bad", blocked: "bad", cancelled: "warn" }[r.status] || "";
  $("#agents").innerHTML = `<div class="run-head"><span>${r.kind === "generate" ? "Produzione" : "Rendering"} · ${esc(r.created_at.replace("T", " ").slice(0, 19))}</span><span class="pill ${pill}">${statusLabel(r.status)}</span></div>` +
    r.tasks.map((t) => `<div class="agent st-${t.status}"><div class="head"><span class="dot"></span>${esc(t.title)}<span class="spacer"></span><span class="muted">${statusLabel(t.status)}${t.seconds ? " · " + t.seconds.toFixed(1) + "s" : ""}</span></div>
      ${t.status === "running" ? `<div class="bar"><i style="width:${(t.progress * 100).toFixed(0)}%"></i></div>` : ""}
      ${t.summary ? `<div class="sum">${esc(t.summary)}</div>` : ""}${t.warnings.map((w) => `<div class="warn">⚠ ${esc(w)}</div>`).join("")}
      ${(t.log.length || Object.keys(t.details).length || t.error) ? `<details><summary class="muted">Dettagli verificabili</summary>${t.log.length ? `<pre>${esc(t.log.slice(-30).join("\n"))}</pre>` : ""}${Object.keys(t.details).length ? `<pre>${esc(JSON.stringify(t.details, null, 1)).slice(0, 6000)}</pre>` : ""}${t.error ? `<pre>${esc(t.error)}</pre>` : ""}</details>` : ""}</div>`).join("");
  const qa = r.tasks.find((t) => t.agent === "qa");
  const b = $("#qaBadge");
  if (qa && qa.details && qa.details.status) { b.textContent = "QA: " + qa.details.status.toUpperCase(); b.className = "pill " + ({ pass: "ok", warn: "warn", fail: "bad" }[qa.details.status]); } else { b.textContent = ""; b.className = "pill"; }
}

// ---------------------------------------------------------------- tab e impostazioni
$$(".panel.left .tabs button").forEach((b) => (b.onclick = () => { $$(".panel.left .tabs button").forEach((x) => x.classList.toggle("active", x === b)); $$(".panel.left .tab").forEach((t) => t.classList.toggle("active", t.id === "tab-" + b.dataset.tab)); }));
function switchRTab(name) { $$(".panel.right .tabs button").forEach((x) => x.classList.toggle("active", x.dataset.rtab === name)); $$(".panel.right .tab").forEach((t) => t.classList.toggle("active", t.id === "rtab-" + name)); }
$$(".panel.right .tabs button").forEach((b) => (b.onclick = () => switchRTab(b.dataset.rtab)));
$("#projectSelect").onchange = (e) => { if (S.dirty && !confirm("Modifiche non salvate: continuare?")) return; S.runId = null; openProject(e.target.value); };
$("#newProjectBtn").onclick = async () => { const n = prompt("Nome del progetto", "Nuovo progetto"); if (!n) return; const p = await api("/projects", { method: "POST", body: { name: n } }); S.runId = null; $("#prompt").value = ""; loadProjects(p.id); };

let SYS = null;
async function loadSystem() {
  SYS = await api("/system");
  const nv = SYS.encoders && (SYS.encoders.h264_nvenc || {}).ok;
  const ok = SYS.ffmpeg && SYS.ffmpeg.ok;
  $("#sysPill").textContent = !ok ? "FFmpeg mancante" : `FFmpeg ${SYS.ffmpeg.version.split("-")[0]} · ${nv ? "NVENC attivo" : "CPU"}`;
  $("#sysPill").className = "pill " + (!ok ? "bad" : nv ? "ok" : "warn");
}
const yes = (b, t = "sì", f = "no") => `<span class="${b ? "lic-ok tag" : "lic-bad tag"}">${b ? t : f}</span>`;
$("#settingsBtn").onclick = async () => {
  await loadSystem(); S.settings = await api("/settings");
  const s = SYS, enc = s.encoders || {};
  $("#sysDetails").innerHTML = `<div>FFmpeg</div><div>${yes(s.ffmpeg.ok, s.ffmpeg.version || "ok", s.ffmpeg.error || "mancante")}</div>
    <div>GPU</div><div>${esc(s.gpu || "nessuna GPU NVIDIA rilevata")}</div>
    ${Object.entries(enc).map(([k, v]) => `<div>${k}</div><div>${yes(v.ok, "verificato", "non disponibile")} <span class="muted">${esc(v.detail)}</span></div>`).join("")}
    <div>Voce (Piper)</div><div>${yes(s.tts.ready, s.tts.voice, s.tts.installed ? "nessuna voce" : "non installato")}</div>
    <div>Trascrizione (Whisper)</div><div>${yes(s.asr.installed, "installato", "non installato")}</div>
    <div>LLM locale (Ollama)</div><div>${yes(s.llm.reachable && s.llm.model_present, s.llm.model, s.llm.reachable ? "modello assente" : "non attivo (uso regole)")}</div>
    <div>Cartella dati</div><div class="muted">${esc(s.data_dir)}</div>`;
  const st = S.settings;
  $("#settingsFields").innerHTML = `<div class="grid2">${field("Encoder GPU", `<select data-s="gpu_encoding"><option value="auto" ${st.gpu_encoding === "auto" ? "selected" : ""}>Auto (verificato)</option><option value="off" ${st.gpu_encoding === "off" ? "selected" : ""}>Disattivato</option></select>`)}
    ${field("Font titoli/sottotitoli", `<input data-s="font_family" value="${esc(st.font_family)}">`)}</div>
    <div class="grid2">${field("Modello Ollama", `<input data-s="ollama_model" value="${esc(st.ollama_model)}">`)}${field("URL Ollama", `<input data-s="ollama_url" value="${esc(st.ollama_url)}">`)}</div>
    <div class="grid2">${field("Modello Whisper", `<select data-s="whisper_model">${["tiny", "base", "small", "medium", "large-v3"].map((m) => `<option ${m === st.whisper_model ? "selected" : ""}>${m}</option>`).join("")}</select>`)}${field("Voce Piper (.onnx, vuoto = auto)", `<input data-s="piper_voice" value="${esc(st.piper_voice)}">`)}</div>
    <label class="check"><input type="checkbox" data-s="llm_enabled" ${st.llm_enabled ? "checked" : ""}> Usa LLM locale se disponibile</label>
    <label class="check"><input type="checkbox" data-s="online_research" ${st.online_research ? "checked" : ""}> Consenti ricerca online di materiale a licenza libera</label>
    ${field("Licenze consentite", `<input data-s="allowed_licenses" value="${esc(st.allowed_licenses.join(", "))}">`)}
    <p class="hint">Codici: cc0, pdm, public-domain, public-domain-us-gov, by, by-sa. Il materiale importato dall'utente è considerato di sua proprietà.</p>`;
  $("#settingsDlg").showModal();
};
$("#saveSettings").onclick = async (e) => {
  e.preventDefault(); const body = {};
  $$("[data-s]").forEach((el) => { const k = el.dataset.s; body[k] = el.type === "checkbox" ? el.checked : k === "allowed_licenses" ? el.value.split(",").map((x) => x.trim()).filter(Boolean) : el.value; });
  S.settings = await api("/settings", { method: "PUT", body }); $("#settingsDlg").close(); toast("Impostazioni salvate"); loadSystem();
};

(async function init() {
  S.settings = await api("/settings");
  loadSystem().catch(() => { $("#sysPill").textContent = "sistema non verificato"; });
  await loadProjects();
  window.addEventListener("resize", renderTimeline);
  window.addEventListener("beforeunload", (e) => { if (S.dirty) { e.preventDefault(); e.returnValue = ""; } });
})();
