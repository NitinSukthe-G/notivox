/*
  app.js - what the NOTIVOX page DOES.

  1. Gives this browser an anonymous id (so the server knows which cases are yours)
  2. Starts a fresh case every time the page opens, and shows the conversation
  3. Language buttons, typing, and the MIC (records, stops by itself when you go quiet)
  4. Plays the agent's voice (Ishita, from Sarvam) - one voice only, one reply after another; you can interrupt by speaking
  5. When everything is confirmed: asks the server to write the notice, shows it,
     lets you edit it, download the PDF, and shows where to sign
*/
"use strict";

// ====================================================================== small helpers
const $ = (id) => document.getElementById(id);

/** Make an HTML element. Text is always set with textContent, so nothing typed can break the page. */
function el(tag, className, text) {
  const node = document.createElement(tag);
  if (className) node.className = className;
  if (text !== undefined && text !== null) node.textContent = text;
  return node;
}

// Language names in their own script, for the language buttons
const NATIVE = {
  "hi-IN": "हिंदी", "en-IN": "English", "ta-IN": "தமிழ்", "te-IN": "తెలుగు", "kn-IN": "ಕನ್ನಡ",
  "ml-IN": "മലയാളം", "mr-IN": "मराठी", "gu-IN": "ગુજરાતી", "bn-IN": "বাংলা", "pa-IN": "ਪੰਜਾਬੀ", "od-IN": "ଓଡ଼ିଆ",
};

// ====================================================================== 1. anonymous device id
function getDeviceId() {
  try {
    let id = localStorage.getItem("notivox_device");
    if (!id) {
      id = "dev-" + (crypto.randomUUID ? crypto.randomUUID() : Math.random().toString(36).slice(2) + Date.now().toString(36));
      localStorage.setItem("notivox_device", id);
    }
    return id;
  } catch (e) {
    // storage blocked (private window): use an id just for this visit
    return "dev-" + Math.random().toString(36).slice(2) + Date.now().toString(36);
  }
}
const DEVICE = getDeviceId();

// Everything the page needs to remember while it is open
const state = {
  caseId: null,     // the current case
  info: null,       // the case summary from the server (stage, facts, missing...)
  draft: null,      // the written notice
  tab: "local",     // which copy of the notice is shown: "local" (translated) or "notice" (English)
  dirty: false,     // has the user edited the notice without saving?
  languages: {},
  signGuide: [],
  voiceOK: true,    // false = Sarvam voice not available: replies are shown as text only
  audio: null,      // the voice clip that is playing now
  busy: false,
  speechId: 0,       // changes whenever the user interrupts, which cancels queued voices
  audioDone: null,  // lets stopSpeaking() release the queue
  drafting: false,
  started: true,   // the agent starts talking by itself when the page opens
  handsFree: true,  // turn the mic on automatically after the agent finishes speaking
};

// ====================================================================== talking to our server
/** Call one of our /api routes. Returns the JSON answer, or throws an Error with a readable message. */
async function api(method, path, body) {
  const options = { method, headers: { "X-User-Id": DEVICE } };
  if (body instanceof FormData) {
    options.body = body;                                 // a file upload (voice)
  } else if (body !== undefined) {
    options.headers["Content-Type"] = "application/json";
    options.body = JSON.stringify(body);
  }
  const resp = await fetch(path, options);
  let data = null;
  try { data = await resp.json(); } catch (e) { /* not JSON */ }
  if (!resp.ok) {
    const err = new Error((data && data.detail && typeof data.detail === "string") ? data.detail : `Something went wrong (${resp.status})`);
    err.status = resp.status;
    err.data = data;
    throw err;
  }
  return data;
}

// ====================================================================== status + chat
// What the big mic button says in each state
const MIC_LABEL = {
  idle: "Tap to talk",
  listening: "Listening… tap when done",
  thinking: "Thinking…",
  speaking: "Speaking… tap to interrupt",
};

function setStatus(mode, text, sub) {
  $("orb").dataset.state = mode;                         // idle / listening / thinking / speaking
  $("status").textContent = text;
  if (sub !== undefined) $("subStatus").textContent = sub;
  $("micLabel").textContent = MIC_LABEL[mode] || "";
  $("mic").classList.toggle("speaking", mode === "speaking");
}

/**
 * Hands-free: after the agent finishes speaking, turn the mic on by itself,
 * so the user can simply answer - like a phone call.
 */
function listenAgain() {
  setStatus("idle", "Your turn");
  bargeBaseline = null;
  // wait a moment so the mic does not catch the last echo of the agent's own voice
  setTimeout(() => {
    if (state.handsFree && state.started && !state.busy && !state.drafting && !rec && !state.audio && speechPending === 0) startRecording();
  }, 400);
}

function setBusy(on) {
  state.busy = on;
  $("send").disabled = on;
  $("text").disabled = on;
}

/** Add one message bubble. role = "agent", "user" or "system". */
function addMessage(role, text) {
  const bubble = el("div", "msg " + role, text);
  if (role === "agent") {
    const replay = el("button", "play", "▶");
    replay.type = "button";
    replay.title = "Play again";
    replay.addEventListener("click", () => { stopSpeaking(); speak(text); });
    bubble.appendChild(replay);
  }
  $("chat").appendChild(bubble);
  $("chat").scrollTop = $("chat").scrollHeight;
}

function showError(err) {
  addMessage("system", err.message || String(err));
  setStatus("idle", "Your turn");
}

const STAGE_HINT = {
  choose_language: "Choose a language to begin",
  listening: "Tell me what happened",
  clarifying: "A few more details",
  language: "Which language for the document?",
  confirm: "Check the summary and say yes",
  ready: "Writing your document…",
  drafted: "Your document is ready",
};

// ====================================================================== the agent's voice
/*
 * ONE voice queue. Every reply is spoken in Ishita's voice, ONE AFTER ANOTHER - two voices can never
 * play at the same time (that was the overlap bug: a new reply started while an older voice was still
 * being downloaded, so both played).
 *
 *  speak(text)     adds a reply to the end of the queue. It starts downloading its voice IMMEDIATELY (in
 *                  parallel) but only plays when everything before it has finished.
 *  stopSpeaking()  cancels everything queued and stops the sound now (used when the user taps or types).
 *
 * To start talking sooner, a long reply is cut into a first sentence and the rest: the first sentence
 * is short, so its voice arrives quickly and starts playing while the rest is still downloading.
 */
let speechChain = Promise.resolve();   // the queue
let speechPending = 0;                 // how many replies are queued or playing

function stopSpeaking() {
  stopBargeIn();
  state.speechId++;                                    // cancels every queued reply
  if (state.audio) {
    const resolve = state.audioDone;
    state.audio.pause();
    state.audio = null;
    state.audioDone = null;
    if (resolve) resolve();                            // let the queue move on (it will see it was cancelled)
  }
}

/** Cut a reply into [first sentence, the rest] so speech starts sooner. */
function splitForSpeech(text) {
  const m = text.match(/^(.{25,}?[.!?।])\s+(.+)$/s);
  return m ? [m[1], m[2]] : [text];
}

/** Download the voice for one piece of text. Returns a sound URL, or null if there is no voice. */
async function fetchVoice(text, lang) {
  if (!state.voiceOK) return null;
  try {
    const resp = await fetch("/api/tts", {
      method: "POST",
      headers: { "X-User-Id": DEVICE, "Content-Type": "application/json" },
      body: JSON.stringify({ text: text.slice(0, 1500), language: lang }),
    });
    if (resp.status === 402 || resp.status === 409) { textOnly(resp.status); return null; }
    if (!resp.ok) return null;
    return URL.createObjectURL(await resp.blob());
  } catch (e) { return null; }
}

/** Play one sound and wait until it has finished (or was stopped). */
function playUrl(url, id) {
  return new Promise((resolve) => {
    if (id !== state.speechId) { URL.revokeObjectURL(url); return resolve(); }
    const audio = new Audio(url);
    state.audio = audio;
    state.audioDone = resolve;
    const finish = () => {
      URL.revokeObjectURL(url);
      if (state.audio === audio) { state.audio = null; state.audioDone = null; }
      resolve();
    };
    audio.onended = () => { stopBargeIn(); finish(); };
    audio.onerror = () => { stopBargeIn(); finish(); };
    setStatus("speaking", "Speaking…");
    audio.play().then(() => startBargeIn(audio)).catch((e) => {
      if (e && e.name === "NotAllowedError") waitForTap(audio, finish);   // browser blocks sound until a click
      else finish();
    });
  });
}

/**
 * Browsers refuse to play sound until the person has interacted with the page once. When that happens we
 * quietly start the voice on the very first click, key press or touch ANYWHERE on the page (nothing is shown).
 */
function waitForTap(audio, finish) {
  const events = ["pointerdown", "keydown", "touchstart"];
  const go = () => {
    events.forEach((ev) => document.removeEventListener(ev, go, true));
    if (state.audio !== audio) return finish();        // it was cancelled while waiting
    setStatus("speaking", "Speaking…");
    audio.play().then(() => startBargeIn(audio)).catch(finish);
  };
  events.forEach((ev) => document.addEventListener(ev, go, true));
}

/** Speak a reply (queued after anything already speaking). */
function speak(text) {
  const id = state.speechId;
  const lang = (state.info && state.info.talk_language) || "en-IN";
  const downloads = splitForSpeech(text).map((part) => fetchVoice(part, lang));   // start all downloads now
  speechPending++;
  speechChain = speechChain.then(async () => {
    try {
      for (const download of downloads) {
        if (id !== state.speechId) {                   // cancelled: don't wait, just clean up
          download.then((u) => { if (u) URL.revokeObjectURL(u); });
          continue;
        }
        const url = await download;
        if (id !== state.speechId) { if (url) URL.revokeObjectURL(url); continue; }   // cancelled while waiting
        if (url) await playUrl(url, id);
      }
    } finally {
      speechPending--;
      if (id === state.speechId && speechPending === 0) listenAgain();   // all done: your turn
    }
  });
  return speechChain;
}

/** Ishita can't speak right now: show a short note (once) and keep the conversation going in text. */
let textOnlyNoted = false;
function textOnly(status) {
  if (status === 402) state.voiceOK = false;           // credits used up: stop asking for voice
  if (!textOnlyNoted) {
    addMessage("system", status === 402
      ? "Voice is paused (Sarvam credits). Replies will be shown as text."
      : "Ishita's voice isn't available for this language, so replies will be shown as text.");
    textOnlyNoted = true;
  }
}

// ====================================================================== case summary panel
function applyInfo(info) {
  state.info = info;
  $("langs").hidden = info.stage !== "choose_language";
  $("noticeLang").value = info.language || "";             // keep the dropdown in sync with the case
  $("subStatus").textContent = STAGE_HINT[info.stage] || "";
  renderCaseInfo(info);
  if (info.stage === "ready" && !state.drafting) makeDraft();    // confirmed -> write the notice
}

function renderCaseInfo(info) {
  const box = $("caseInfo");
  box.replaceChildren();
  const facts = info.facts || {};
  const rows = [
    ["Talking in", info.talk_language_name],
    [info.kind === "letter" ? "Letter" : "Notice", info.notice_label],
    ["Your name", (facts.sender || {}).name],
    ["Against", (facts.recipient || {}).name],
    ["You want", (facts.demand || {}).what],
    ["Deadline", (facts.demand || {}).deadline_days ? `${facts.demand.deadline_days} days` : null],
  ].filter(([, v]) => v);

  if (!rows.length) {
    box.appendChild(el("p", "muted", "Details appear here as you talk."));
    return;
  }
  const dl = el("dl", "kv");
  for (const [k, v] of rows) { dl.appendChild(el("dt", null, k)); dl.appendChild(el("dd", null, v)); }
  box.appendChild(dl);

  const chips = el("div", "chips");
  if (info.blanks && info.blanks.length) {
    // the user chose to fill these in by hand: they become blank lines on the document
    info.blanks.forEach((m) => chips.appendChild(el("span", "chip", "Fill by hand: " + m)));
  } else if (info.missing && info.missing.length) {
    info.missing.forEach((m) => chips.appendChild(el("span", "chip", "Needed: " + m)));
  } else if (info.notice_type) {
    chips.appendChild(el("span", "chip ok", "All details collected"));
  }
  box.appendChild(chips);
  (info.warnings || []).forEach((w) => box.appendChild(el("div", "alert", w)));
}

// ====================================================================== language buttons
function renderLanguageButtons() {
  const box = $("langs");
  box.replaceChildren();
  for (const [code, name] of Object.entries(state.languages)) {
    const b = el("button", "btn", NATIVE[code] && NATIVE[code] !== name ? `${NATIVE[code]} · ${name}` : name);
    b.type = "button";
    b.addEventListener("click", () => chooseLanguage(code));
    box.appendChild(b);
  }
}

// ====================================================================== notice language dropdown
function fillNoticeLanguages() {
  const select = $("noticeLang");
  for (const [code, name] of Object.entries(state.languages)) {
    const option = el("option", null, NATIVE[code] && NATIVE[code] !== name ? `${name} · ${NATIVE[code]}` : name);
    option.value = code;
    select.appendChild(option);
  }
}

async function chooseNoticeLanguage(code) {
  if (!code || !state.caseId || state.busy) return;
  const name = state.languages[code];
  // If the agent has just asked "which language for the notice?", answer it in the conversation,
  // so the agent continues naturally with the summary.
  if (state.info && state.info.stage === "language") {
    sendText(name);
    return;
  }
  setBusy(true);
  setStatus("thinking", state.draft ? "Translating your document…" : "Saving…");
  try {
    const result = await api("POST", `/api/cases/${state.caseId}/notice-language`, { code });
    applyInfo(result);
    addMessage("system", `Document language: ${name}`);
    if (result.draft) {                                    // the notice was re-translated
      state.draft = { ...state.draft, ...result.draft };
      state.tab = state.draft.local ? "local" : "notice";
      renderNotice();
    }
    setStatus("idle", "Your turn");
  } catch (e) { showError(e); }
  finally { setBusy(false); }
}

async function chooseLanguage(code) {
  if (state.busy) return;
  stopSpeaking();
  addMessage("user", NATIVE[code] || state.languages[code]);
  setBusy(true);
  setStatus("thinking", "Thinking…");
  try {
    const result = await api("POST", `/api/cases/${state.caseId}/language`, { code });
    addMessage("agent", result.reply);
    applyInfo(result);
    speak(result.reply);
  } catch (e) { showError(e); }
  finally { setBusy(false); }
}

// ====================================================================== typing
async function sendText(text) {
  if (!text.trim() || state.busy) return;
  stopSpeaking();
  addMessage("user", text);
  setBusy(true);
  setStatus("thinking", "Thinking…");
  try {
    const result = await api("POST", `/api/cases/${state.caseId}/message`, { text });
    addMessage("agent", result.reply);
    applyInfo(result);
    speak(result.reply);
  } catch (e) { showError(e); }
  finally { setBusy(false); }
}

// ====================================================================== the mic
// Recording starts by itself (hands-free), by tapping the big button, or when you speak over the agent.
// It stops by itself after ~1.5 s of silence (or 29 s max). Tap again to stop early.
let rec = null;            // the current recording session
let recStarting = false;   // true while the microphone is being opened (prevents starting twice)

// ONE microphone stream is kept open while hands-free is on. It is used for recording AND for listening
// while the agent speaks (barge-in), so the browser asks for permission only once.
let micStream = null;
let micDenied = false;

async function getMic() {
  if (micStream && micStream.getTracks().some((t) => t.readyState === "live")) return micStream;
  // echoCancellation: the browser removes the agent's own voice from the mic sound
  micStream = await navigator.mediaDevices.getUserMedia({
    audio: { echoCancellation: true, noiseSuppression: true, autoGainControl: true },
  });
  return micStream;
}

function releaseMic() {
  if (micStream) { micStream.getTracks().forEach((t) => t.stop()); micStream = null; }
}

/** How loud is the sound right now? (0 = silence, 1 = very loud) */
function loudnessOf(analyser, buffer) {
  analyser.getByteTimeDomainData(buffer);
  let sum = 0;
  for (const v of buffer) sum += ((v - 128) / 128) ** 2;
  return Math.sqrt(sum / buffer.length);
}

/*
 * BARGE-IN: while NOTIVOX is speaking, listen to the microphone. As soon as the user starts talking
 * (loud enough for about 0.15 seconds), stop her voice at once and start listening - like a phone call.
 * Her own voice leaking into the mic is learned during the first 0.35 s and ignored.
 */
let bargeTimer = null;
let bargeCtx = null;
let bargeBaseline = null;   // how loud her own voice sounds in the mic (learned once per answer)

function stopBargeIn() {
  clearInterval(bargeTimer);
  bargeTimer = null;
  if (bargeCtx) { bargeCtx.close().catch(() => {}); bargeCtx = null; }
}

async function startBargeIn(audio) {
  if (!state.handsFree || micDenied || bargeTimer || rec || recStarting) return;
  let stream;
  try { stream = await getMic(); } catch (e) { micDenied = true; return; }
  if (state.audio !== audio || bargeTimer) return;      // her sentence already ended
  const ctx = new (window.AudioContext || window.webkitAudioContext)();
  const analyser = ctx.createAnalyser();
  analyser.fftSize = 1024;
  ctx.createMediaStreamSource(stream).connect(analyser);
  bargeCtx = ctx;
  const buffer = new Uint8Array(analyser.fftSize);
  const began = Date.now();
  let samples = 0, loudCount = 0;
  bargeTimer = setInterval(() => {
    const level = loudnessOf(analyser, buffer);
    if (bargeBaseline === null && Date.now() - began < 350) {          // learning phase
      samples++;
      bargeLearn = ((bargeLearn || 0) * (samples - 1) + level) / samples;
      return;
    }
    if (bargeBaseline === null) bargeBaseline = bargeLearn || 0;
    const threshold = Math.max(0.08, bargeBaseline * 3);               // louder than her own voice leaking in
    loudCount = level > threshold ? loudCount + 1 : 0;
    if (loudCount >= 3) {                                              // ~150 ms of the user's voice
      stopBargeIn();
      stopSpeaking();                                                  // cut her off immediately
      startRecording();                                                // and listen
    }
  }, 50);
}
let bargeLearn = 0;

async function startRecording() {
  if (rec || recStarting) return;
  recStarting = true;
  let stream;
  try {
    stream = await getMic();
  } catch (e) {
    micDenied = true;
    recStarting = false;
    addMessage("system", "Microphone not available. Please allow microphone access, or type instead.");
    return;
  }
  recStarting = false;
  bargeBaseline = null;                                  // the next answer learns her voice level again
  const recorder = new MediaRecorder(stream);
  const chunks = [];
  const ctx = new (window.AudioContext || window.webkitAudioContext)();
  const analyser = ctx.createAnalyser();
  analyser.fftSize = 1024;
  ctx.createMediaStreamSource(stream).connect(analyser);
  const samples = new Uint8Array(analyser.fftSize);

  rec = { recorder, stream, ctx, started: Date.now(), heardSpeech: false, lastLoud: Date.now(), timer: null };
  recorder.ondataavailable = (e) => { if (e.data.size) chunks.push(e.data); };
  recorder.onstop = () => finishRecording(new Blob(chunks, { type: recorder.mimeType || "audio/webm" }));

  // Every 100 ms: how loud is it? (a simple "voice activity" check)
  rec.timer = setInterval(() => {
    analyser.getByteTimeDomainData(samples);
    let sum = 0;
    for (const s of samples) sum += ((s - 128) / 128) ** 2;
    const loudness = Math.sqrt(sum / samples.length);
    const now = Date.now();
    if (loudness > 0.04) { rec.heardSpeech = true; rec.lastLoud = now; }
    const quietFor = now - rec.lastLoud;
    if ((rec.heardSpeech && quietFor > 1500) || now - rec.started > 29000 || (!rec.heardSpeech && now - rec.started > 8000)) {
      stopRecording();
    }
  }, 100);

  recorder.start();
  $("mic").classList.add("recording");
  setStatus("listening", "Listening…", "Speak now. I'll stop when you pause.");
}

function stopRecording() {
  if (!rec) return;
  clearInterval(rec.timer);
  if (rec.recorder.state !== "inactive") rec.recorder.stop();
  if (!state.handsFree) releaseMic();                  // hands-free keeps the mic open so it can hear you interrupt
  $("mic").classList.remove("recording");
}

/**
 * Convert the browser's recording (WebM) into a WAV file, which Sarvam always accepts.
 * Steps: decode the sound -> resample to 16,000 samples per second, mono -> write a WAV file.
 * (16 kHz mono is what speech recognition uses, and it keeps the file small.)
 */
async function toWav(blob) {
  const ctx = new (window.AudioContext || window.webkitAudioContext)();
  const decoded = await ctx.decodeAudioData(await blob.arrayBuffer());   // the raw sound waves
  ctx.close();

  const rate = 16000;
  const offline = new OfflineAudioContext(1, Math.ceil(decoded.duration * rate), rate);
  const source = offline.createBufferSource();
  source.buffer = decoded;
  source.connect(offline.destination);
  source.start();
  const samples = (await offline.startRendering()).getChannelData(0);    // numbers between -1 and 1

  // A WAV file = a 44-byte header describing the sound + the sound as 16-bit numbers
  const view = new DataView(new ArrayBuffer(44 + samples.length * 2));
  const write = (pos, text) => { for (let i = 0; i < text.length; i++) view.setUint8(pos + i, text.charCodeAt(i)); };
  write(0, "RIFF"); view.setUint32(4, 36 + samples.length * 2, true); write(8, "WAVE");
  write(12, "fmt "); view.setUint32(16, 16, true); view.setUint16(20, 1, true); view.setUint16(22, 1, true);
  view.setUint32(24, rate, true); view.setUint32(28, rate * 2, true); view.setUint16(32, 2, true); view.setUint16(34, 16, true);
  write(36, "data"); view.setUint32(40, samples.length * 2, true);
  for (let i = 0; i < samples.length; i++) {
    const s = Math.max(-1, Math.min(1, samples[i]));
    view.setInt16(44 + i * 2, s < 0 ? s * 0x8000 : s * 0x7fff, true);
  }
  return new Blob([view], { type: "audio/wav" });
}

async function finishRecording(blob) {
  const session = rec;
  rec = null;
  try { session.ctx.close(); } catch (e) {}
  if (!session.heardSpeech || blob.size < 2000) {
    // Nothing heard: stop here (don't keep re-opening the mic). The user taps when ready.
    setStatus("idle", "I didn't hear anything", "Tap the big mic when you're ready to talk");
    return;
  }
  setBusy(true);
  setStatus("thinking", "Thinking…");
  try {
    const wav = await toWav(blob);                       // WebM -> WAV (Sarvam does not accept WebM)
    const form = new FormData();
    form.append("file", wav, "voice.wav");
    form.append("duration", String((Date.now() - session.started) / 1000));
    const result = await api("POST", `/api/cases/${state.caseId}/voice`, form);
    if (!result.transcript) { setStatus("idle", result.detail || "I didn't catch that"); return; }
    addMessage("user", result.transcript);
    addMessage("agent", result.reply);
    applyInfo(result);
    speak(result.reply);
  } catch (e) { showError(e); }
  finally { setBusy(false); }
}

// ====================================================================== the notice
async function makeDraft() {
  state.drafting = true;
  addMessage("system", "Writing your document… this takes about 20–40 seconds.");
  setStatus("thinking", "Writing your document…");
  try {
    const draft = await api("POST", `/api/cases/${state.caseId}/draft`);
    state.draft = draft;
    state.info.stage = "drafted";
    state.tab = draft.local ? "local" : "notice";
    renderNotice();
    renderSignSteps();
    addMessage("agent", draft.spoken);
    speak(draft.spoken);
    $("noticePanel").scrollIntoView({ behavior: "smooth", block: "start" });
  } catch (e) {
    showError(e);
  } finally {
    state.drafting = false;
  }
}

/** The copy of the notice currently shown (translated or English). */
function currentNotice() {
  return state.tab === "local" && state.draft.local ? state.draft.local : state.draft.notice;
}

/** An editable piece of text inside the notice. */
function editable(tag, text, field, index) {
  const node = el(tag, null, text || "");
  node.contentEditable = "true";
  node.dataset.field = field;
  if (index !== undefined) node.dataset.index = String(index);
  return node;
}

function marker(n) { return el("span", "marker", String(n)); }

function renderNotice() {
  const d = state.draft;
  $("noticePanel").hidden = false;

  // tabs: translated copy + English copy
  const tabs = $("tabs");
  tabs.replaceChildren();
  if (d.local) {
    for (const [key, label] of [["local", state.languages[d.language] || "Translated"], ["notice", "English"]]) {
      const b = el("button", state.tab === key ? "on" : "", label);
      b.type = "button";
      b.addEventListener("click", async () => {
        if (state.dirty) await saveEdits();
        state.tab = key;
        renderNotice();
      });
      tabs.appendChild(b);
    }
  }

  // things the user should double-check
  const probs = $("problems");
  probs.replaceChildren();
  (d.problems || []).forEach((p) => probs.appendChild(el("div", "alert warn", p)));

  // the document itself
  const n = currentNotice();
  const L = n.labels || {};
  const doc = $("doc");
  doc.replaceChildren();
  $("docTitle").textContent = n.kind === "letter" ? "Your letter" : "Your notice";
  if (L.title) doc.appendChild(el("h3", null, L.title));     // e.g. LEGAL NOTICE / COMPLAINT / RTI APPLICATION
  if (n.mode) doc.appendChild(el("p", "center", n.mode));
  const dateLine = el("p", "right", `${L.date || "Date"}: `);
  dateLine.appendChild(editable("span", n.date, "date"));
  doc.appendChild(dateLine);

  doc.appendChild(el("p", "label", `${L.from || "From"}:`));
  doc.appendChild(editable("p", n.from_name, "from_name"));
  doc.appendChild(editable("p", n.from_address, "from_address"));
  doc.appendChild(el("p", "label", `${L.to || "To"}:`));
  doc.appendChild(editable("p", n.to_name, "to_name"));
  if (n.to_role) doc.appendChild(editable("p", n.to_role, "to_role"));
  doc.appendChild(editable("p", n.to_address, "to_address"));

  const subject = el("p", "label", `${L.subject || "Subject"}: `);
  subject.appendChild(editable("span", n.subject, "subject"));
  doc.appendChild(subject);
  doc.appendChild(el("p", null, L.salutation || "Sir/Madam,"));
  doc.appendChild(editable("p", n.opening, "opening"));

  const ol = el("ol");
  (n.paragraphs || []).forEach((p, i) => ol.appendChild(editable("li", p, "paragraphs", i)));
  if (n.legal_paragraph) ol.appendChild(editable("li", n.legal_paragraph, "legal_paragraph"));
  doc.appendChild(ol);
  doc.appendChild(editable("p", n.demand, "demand"));
  if (n.consequence) doc.appendChild(editable("p", n.consequence, "consequence"));   // letters have none

  if ((n.annexures || []).length) {
    const head = el("p", "label");
    head.append(marker(4), `${L.annexures || "Annexures"}:`);
    doc.appendChild(head);
    const al = el("ol");
    n.annexures.forEach((a, i) => al.appendChild(editable("li", a, "annexures", i)));
    doc.appendChild(al);
  }

  doc.appendChild(el("p", null, L.closing || "Yours faithfully,"));
  const box = el("div", "signbox");
  box.append(marker(1), "Sign here (blue ink)");
  doc.appendChild(box);
  doc.appendChild(el("p", "label", n.from_name));
  const place = el("p");
  place.append(marker(3), `${L.place || "Place"}: `);
  place.appendChild(editable("span", n.place || "", "place"));
  doc.appendChild(place);
  doc.appendChild(el("p", null, `${L.date || "Date"}: ________________`));
  const ini = el("div", "initials");
  ini.append(marker(2), "In the PDF, every page has a small “Initials” box at the bottom. Put your short signature there.");
  doc.appendChild(ini);

  state.dirty = false;
  $("download").href = `/api/cases/${state.caseId}/notice.pdf?u=${encodeURIComponent(DEVICE)}`;
}

/** Read the edited text back out of the page. */
function collectEdits() {
  const n = JSON.parse(JSON.stringify(currentNotice()));          // a copy we can change
  const lists = { paragraphs: [], annexures: [] };
  document.querySelectorAll("#doc [data-field]").forEach((node) => {
    const field = node.dataset.field;
    const text = node.textContent.trim();
    if (field in lists) lists[field][Number(node.dataset.index)] = text;
    else n[field] = text;
  });
  n.paragraphs = lists.paragraphs.filter(Boolean);
  if ((currentNotice().annexures || []).length) n.annexures = lists.annexures.filter(Boolean);
  return n;
}

async function saveEdits() {
  if (!state.draft) return;
  const which = state.tab === "local" && state.draft.local ? "local" : "notice";
  try {
    const updated = await api("PUT", `/api/cases/${state.caseId}/draft`, { which, notice: collectEdits() });
    state.draft = { ...state.draft, ...updated };
    state.dirty = false;
    $("saveEdits").textContent = "Saved ✓";
    setTimeout(() => { $("saveEdits").textContent = "Save changes"; }, 1500);
  } catch (e) { showError(e); }
}

function renderSignSteps() {
  const list = $("signSteps");
  list.replaceChildren();
  // SIGN_GUIDE order: print, SIGN (1), INITIALS (2), PLACE/DATE (3), ATTACH (4), send
  // (the draft carries steps with the right "how to send" for this document type)
  const markers = [null, 1, 2, 3, 4, null];
  const steps = (state.draft && state.draft.sign_guide) || state.signGuide;
  steps.forEach((step, i) => {
    const li = el("li");
    if (markers[i]) li.appendChild(marker(markers[i]));
    else li.appendChild(el("span", "marker", "•"));
    li.appendChild(el("span", null, step));
    list.appendChild(li);
  });
  $("signPanel").hidden = false;
}

// ====================================================================== start a case
function resetScreen() {
  $("chat").replaceChildren();
  $("noticePanel").hidden = true;
  $("signPanel").hidden = true;
  state.draft = null;
  state.dirty = false;
}

async function newCase() {
  stopSpeaking();
  if (rec) stopRecording();
  resetScreen();
  setStatus("thinking", "Starting…");
  try {
    const result = await api("POST", "/api/cases");
    state.caseId = result.case_id;
    addMessage("agent", result.reply);
    applyInfo(result);
    setStatus("idle", "Your turn");
    speak(result.reply);                              // NOTIVOX greets you the moment the page opens
  } catch (e) { showError(e); }
}

async function init() {
  try {
    const cfg = await api("GET", "/api/config");
    state.languages = cfg.languages;
    state.signGuide = cfg.sign_guide;
    state.voiceOK = cfg.voice;
  } catch (e) { showError(e); return; }
  renderLanguageButtons();
  fillNoticeLanguages();
  await newCase();          // every time the page opens: a fresh session
}

// ====================================================================== wire up the buttons
$("composer").addEventListener("submit", (e) => {
  e.preventDefault();                       // don't reload the page
  const text = $("text").value;
  $("text").value = "";
  sendText(text);
});
$("mic").addEventListener("click", () => {
  if (rec) { stopRecording(); return; }     // second tap = stop now
  if (state.busy) return;
  stopSpeaking();                           // tapping the mic interrupts the agent
  startRecording();
});
$("newCase").addEventListener("click", newCase);
$("handsFree").addEventListener("change", (e) => {
  state.handsFree = e.target.checked;
  if (!state.handsFree) { stopBargeIn(); if (!rec) releaseMic(); }   // hands-free off: turn the microphone off
});
$("typeToggle").addEventListener("click", () => {
  const form = $("composer");
  form.hidden = !form.hidden;
  $("typeToggle").textContent = form.hidden ? "⌨ Type instead" : "Hide typing";
  if (!form.hidden) $("text").focus();
});
$("noticeLang").addEventListener("change", (e) => chooseNoticeLanguage(e.target.value));
$("saveEdits").addEventListener("click", saveEdits);
$("doc").addEventListener("input", () => { state.dirty = true; });
$("download").addEventListener("click", async (e) => {
  if (!state.dirty) return;                 // nothing unsaved: download straight away
  e.preventDefault();
  await saveEdits();                        // save edits first, so the PDF includes them
  window.location.href = $("download").href;
});

init();
