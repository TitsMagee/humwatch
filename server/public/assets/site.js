import { HumPlayer, humOnlyLevels, mixLevels, toneAmplitudes, tonesExplainLevel } from "./audio.js";
import { beatPairs, envelope, wobbleSentence } from "./wobble.js";
import { averageExcess, capitalise, currentRun, dateOnly, dateTime, duration, figures, longestRun, loudestExcess, runClock, runningShare, signedDb, status } from "./format.js";

const LIVE_REFRESH_MS = 10000;
const HISTORY_REFRESH_MS = 300000;
const FREQ_LOW = 30;
const FREQ_HIGH = 260;

const params = new URLSearchParams(location.search);
const demo = params.get("demo");
const $ = (id) => document.getElementById(id);
const reducedMotion = matchMedia("(prefers-reduced-motion: reduce)");

let site = { title: "Live hum monitor", source_label: "the source", source_code_url: "" };
let live = null;
let history = null;
const player = new HumPlayer();
let playingFrom = null;
let chartKey = null;
let clockKey = null;
let currentKind = null;
let humSecondsWatched = 0;

async function getJson(path) {
  try {
    const response = await fetch(`${path}?t=${Date.now()}`, { cache: "no-store" });
    return response.ok ? await response.json() : null;
  } catch {
    return null;
  }
}

function backgroundDb(reading) {
  if (!reading || reading.l90 === null || reading.l90 === undefined) return null;
  if (reading.excess === null || reading.excess === undefined) return null;
  return reading.l90 - reading.excess;
}

// Live view

// The run the headline counts: only when the live reading and the history agree it's on
function runningNow(current) {
  const run = history && history.current_run;
  return current.kind === "on" && run && run.state === "on" ? run : null;
}

function renderStatus() {
  const now = Date.now() / 1000;
  const current = status(live, site.source_label, now, Boolean(history && history.last_t));
  currentKind = current.kind;
  const run = runningNow(current);
  $("status-sub").hidden = !run;
  $("status-headline").setAttribute("aria-live", run ? "off" : "polite");
  if (run) {
    renderClock();
    const about = currentRun(history, site.source_label, now);
    $("run-tip").textContent = `Counting since ${dateTime(run.start_t)}. ${about ? about.note : ""}`.trim();
    $("status-detail").textContent = typeof live.excess === "number" && live.excess >= 0.5
      ? `Right now it's ${signedDb(live.excess)} above the background.`
      : current.detail;
  } else {
    clockKey = null;
    $("status-headline").textContent = current.headline;
    $("status-detail").textContent = current.detail;
    document.title = `${current.headline}: ${site.title}`;
  }
  $("status-time").textContent =
    live && current.kind !== "stale" ? `Updated ${duration(Math.max(0, now - live.received_at))} ago` : "";
  document.body.dataset.state = current.kind;
  wave.setTones(current.kind === "on" || current.kind === "off" || current.kind === "unknown" ? live.tones : [],
    current.kind === "fault" || current.kind === "stale" || current.kind === "none");
  renderTones(current);
  renderListen(current);
}

// "4 days, 3 hours and 12 minutes", ticking over each minute
function renderClock() {
  const run = history && history.current_run;
  if (!run) return;
  const clock = runClock(Math.max(Date.now() / 1000, run.end_t) - run.start_t);
  if (clock.text === clockKey) return;
  const changed = clockKey !== null;
  clockKey = clock.text;
  const nodes = [];
  clock.parts.forEach((part, index) => {
    if (index > 0) nodes.push(index === clock.parts.length - 1 ? " and " : ", ");
    const number = document.createElement("span");
    number.className = "clock-number";
    number.textContent = part.count;
    // A small flourish on the number that just ticked over
    if (changed && index === clock.parts.length - 1 && !reducedMotion.matches) number.classList.add("tick");
    const unit = document.createElement("span");
    unit.className = "clock-unit";
    unit.textContent = ` ${part.unit}`;
    const group = document.createElement("span");
    group.className = "clock-part";
    group.append(number, unit);
    nodes.push(group);
  });
  $("status-headline").replaceChildren(...nodes);
  document.title = `${clock.text} of continuous hum: ${site.title}`;
}

// Every second the visitor spends here while the source runs
function renderSinceOpen() {
  if (currentKind === "on" && !document.hidden) humSecondsWatched += 1;
  const shown = humSecondsWatched >= 5;
  $("since-open").hidden = !shown;
  if (!shown) return;
  const minutes = Math.floor(humSecondsWatched / 60);
  const seconds = humSecondsWatched % 60;
  const spent = minutes ? `${minutes} min ${String(seconds).padStart(2, "0")} s` : `${seconds} seconds`;
  $("since-open").textContent = `You've been here ${spent}. ${capitalise(site.source_label)} hummed through every second of it.`;
}

function renderTones(current) {
  const tones = current.numbers && live ? live.tones || [] : [];
  $("tones-section").hidden = tones.length === 0;
  if (!tones.length) return;
  const present = tones.filter((tone) => tone.present).length;
  $("tone-legend").textContent =
    `Filled marks are tones in this reading (${present} of ${tones.length}). Open marks are tones known from past readings but not found now.`;
  // Readings refresh often but change once a minute. Redrawing only on a real change
  // keeps a keyboard user's place in the chart.
  const key = `${$("tone-chart").clientWidth}:${JSON.stringify(tones)}`;
  if (key === chartKey) return;
  chartKey = key;
  drawToneChart(tones);
  $("tone-table").replaceChildren(...tones.map((tone) => {
    const row = document.createElement("tr");
    for (const text of [`${tone.hz.toFixed(1)} Hz`, tone.excess === null ? "Not known" : signedDb(tone.excess), tone.present ? "Yes" : "No"]) {
      const cell = document.createElement("td");
      cell.textContent = text;
      row.append(cell);
    }
    return row;
  }));
}

function svg(name, attributes = {}, text) {
  const node = document.createElementNS("http://www.w3.org/2000/svg", name);
  for (const [key, value] of Object.entries(attributes)) node.setAttribute(key, value);
  if (text !== undefined) node.textContent = text;
  return node;
}

function drawToneChart(tones) {
  const box = $("tone-chart");
  const width = Math.max(320, box.clientWidth || 800);
  const height = width < 560 ? 240 : 280;
  const margin = { top: 24, right: 12, bottom: 34, left: 44 };
  const plotWidth = width - margin.left - margin.right;
  const plotHeight = height - margin.top - margin.bottom;
  const known = tones.filter((tone) => tone.excess !== null);
  $("tones-summary").textContent = known.length
    ? "Each mark is one tone in the hum. The taller it is, the further it stands above the background."
    : "Each mark is one tone in the hum. Heights can't be shown until the monitor has recorded a stop to compare them with.";
  const top = Math.max(10, Math.ceil(Math.max(0, ...known.map((tone) => tone.excess)) / 10) * 10);
  const x = (hz) => margin.left + (plotWidth * Math.log(hz / FREQ_LOW)) / Math.log(FREQ_HIGH / FREQ_LOW);
  const y = (db) => margin.top + plotHeight * (1 - Math.max(0, db) / top);

  const chart = svg("svg", {
    viewBox: `0 0 ${width} ${height}`,
    role: "img",
    "aria-label": `Chart of ${tones.length} tones by frequency, showing how far each stands above the background. The same figures are in the table below.`,
  });
  for (let db = 0; db <= top; db += top <= 20 ? 5 : 10) {
    chart.append(svg("line", { class: db === 0 ? "base-line" : "grid-line", x1: margin.left, x2: width - margin.right, y1: y(db), y2: y(db) }));
    chart.append(svg("text", { class: "axis-text", x: margin.left - 8, y: y(db) + 4, "text-anchor": "end" }, db === 0 ? "0 dB" : `+${db}`));
  }
  for (const hz of [30, 50, 100, 200]) {
    chart.append(svg("text", { class: "axis-text", x: x(hz), y: height - 12, "text-anchor": "middle" }, `${hz} Hz`));
  }

  const labelled = new Set([...known].filter((tone) => tone.present).sort((a, b) => b.excess - a.excess).slice(0, 3));
  const tip = $("tone-tip");
  for (const tone of tones) {
    const excess = tone.excess ?? 0;
    const cx = x(tone.hz);
    const cy = y(excess);
    if (tone.present) {
      chart.append(svg("line", { class: "stem", x1: cx, x2: cx, y1: y(0), y2: cy }));
    }
    chart.append(svg("circle", { class: tone.present ? "dot-on" : "dot-off", cx, cy, r: 5 }));
    if (labelled.has(tone)) {
      chart.append(svg("text", { class: "value-text", x: cx, y: cy - 11, "text-anchor": "middle" }, `${tone.hz.toFixed(0)} Hz`));
    }
    const hit = svg("rect", {
      class: "hit", x: cx - 14, width: 28, y: margin.top, height: plotHeight + 6, tabindex: 0, role: "img",
      "aria-label": toneText(tone),
    });
    const show = () => {
      tip.textContent = toneText(tone);
      tip.hidden = false;
      const scale = box.getBoundingClientRect().width / width;
      const room = tip.offsetWidth / 2 + 4;
      tip.style.left = `${Math.min(Math.max(cx * scale, room), box.clientWidth - room)}px`;
      tip.style.top = `${cy * scale}px`;
    };
    const hide = () => { tip.hidden = true; };
    hit.addEventListener("pointerenter", show);
    hit.addEventListener("pointerleave", hide);
    hit.addEventListener("focus", show);
    hit.addEventListener("blur", hide);
    chart.append(hit);
  }
  box.replaceChildren(chart);
}

function toneText(tone) {
  const above = tone.excess === null ? "level not known" : `${signedDb(tone.excess)} against the background`;
  return `${tone.hz.toFixed(1)} Hz, ${above}${tone.present ? "" : ", not found in this reading"}`;
}

// The waveform: the real sum of the tones present, slowed right down to be seen

class Wave {
  constructor(canvas) {
    this.canvas = canvas;
    this.context = canvas.getContext("2d");
    this.parts = [];
    this.beats = { power: 0, pairs: [] };
    this.phases = [];
    this.key = null;
    this.broken = true;
    this.phase = 0;
    this.last = null;
    new ResizeObserver(() => this.draw()).observe(canvas);
    reducedMotion.addEventListener("change", () => this.start());
    this.start();
  }

  setTones(tones, broken) {
    this.broken = broken;
    const key = JSON.stringify((tones || []).map((tone) => [tone.hz, tone.db, tone.present]));
    if (key !== this.key) {
      this.key = key;
      this.beats = beatPairs(tones);
      // Real phases aren't measured, so each pair starts at a random point in its cycle
      this.phases = this.beats.pairs.map(() => Math.random() * 2 * Math.PI);
      this.parts = toneAmplitudes(tones);
    }
    const sentence = broken ? null : wobbleSentence(this.beats);
    $("wobble-note").hidden = !sentence;
    $("wobble-note").textContent = sentence || "";
    this.draw();
  }

  start() {
    cancelAnimationFrame(this.frame);
    if (reducedMotion.matches) {
      this.draw();
      return;
    }
    const step = (time) => {
      if (this.last !== null && !document.hidden) this.phase += (time - this.last) / 1000;
      this.last = time;
      this.draw();
      this.frame = requestAnimationFrame(step);
    };
    this.frame = requestAnimationFrame(step);
  }

  draw() {
    const { canvas, context } = this;
    const ratio = window.devicePixelRatio || 1;
    const width = canvas.clientWidth;
    const height = canvas.clientHeight;
    if (!width || !height) return;
    if (canvas.width !== Math.round(width * ratio) || canvas.height !== Math.round(height * ratio)) {
      canvas.width = Math.round(width * ratio);
      canvas.height = Math.round(height * ratio);
    }
    context.setTransform(ratio, 0, 0, ratio, 0, 0);
    context.clearRect(0, 0, width, height);
    const middle = height / 2;
    const styles = getComputedStyle(document.documentElement);
    context.lineWidth = 2;
    context.lineJoin = "round";

    if (this.broken || !this.parts.length) {
      context.strokeStyle = styles.getPropertyValue("--mist-muted");
      context.setLineDash(this.broken ? [3, 12] : []);
      context.beginPath();
      context.moveTo(0, middle);
      context.lineTo(width, middle);
      context.stroke();
      context.setLineDash([]);
      return;
    }

    // The hum's loudness over the last few seconds, scrolling in real time, newest on the
    // right. Its swelling and fading is the beating between the measured tones, drawn at
    // its true rate and depth.
    const span = 4;
    const half = height * 0.46;
    // Drawn as bars, like an audio waveform, scrolling smoothly with time
    const pitch = 6;
    const offset = ((this.phase / span) * width) % pitch;
    context.fillStyle = styles.getPropertyValue("--sodium");
    for (let px = width - offset; px >= -pitch; px -= pitch) {
      const t = this.phase - (1 - px / width) * span;
      const size = Math.max(1, envelope(this.beats, this.phases, t) * half);
      context.fillRect(px, middle - size, 3, size * 2);
    }
  }
}

// Listening

// What the listening buttons play: the live reading when it can be trusted, otherwise a
// typical reading from the history, clearly labelled as such.
function listenSource(current) {
  const shown = figures(history);
  const typical = shown && shown.typical;
  // A reading with no background to compare with can only play half of what's offered
  if (current.numbers && live && (backgroundDb(live) !== null || !typical)) return { reading: live, typical: false };
  return typical ? { reading: typical, typical: true, liveNotComparable: Boolean(current.numbers && live) } : null;
}

let source = null;

function renderListen(current) {
  if (!playingFrom) source = listenSource(current);
  // With no trustworthy reading and no fair example there's nothing honest to play
  $("listen-now").hidden = !source && !playingFrom;
  if (!source) return;
  const reading = source.reading;
  const hum = toneAmplitudes(reading.tones);
  const background = backgroundDb(reading);
  const fair = !source.typical || tonesExplainLevel(reading.tones, background, reading.l90);
  $("play-now").disabled = hum.length === 0 && playingFrom !== "now";
  $("play-background").disabled = (background === null || !fair) && playingFrom !== "background";
  $("listen-source").hidden = !source.typical;
  if (source.typical) {
    $("listen-source").textContent =
      `${source.liveNotComparable ? "The live reading can't be compared with the background yet" : "There's no live reading"}, so these play a typical reading instead, from ${dateTime(reading.t)}. ` +
      `${capitalise(site.source_label)} was running about ${reading.usual_excess.toFixed(1)} dB above the background at the time, its usual level.`;
  }
  if (!playingFrom) {
    $("play-now").textContent = source.typical ? "Play a typical reading" : "Play the hum";
    $("listen-status").textContent =
      hum.length === 0 ? "There are no tones in the latest reading to play." : "";
  }
}

function resetListenButtons() {
  $("play-now").textContent = source && source.typical ? "Play a typical reading" : "Play the hum";
  $("play-background").textContent = "Play the background";
  $("play-record").textContent = "Play it against the background";
  $("toggle-source").hidden = true;
  $("toggle-record").hidden = true;
  $("listen-status").textContent = "";
  $("record-status").textContent = "";
  playingFrom = null;
}

function stopAll() {
  player.stop();
  resetListenButtons();
  renderStatus();
}

async function startPlaying(from, action) {
  if (playingFrom === from) {
    stopAll();
    return;
  }
  player.stop();
  resetListenButtons();
  playingFrom = from;
  try {
    await action();
  } catch {
    resetListenButtons();
    $("listen-status").textContent = "This browser couldn't start the sound.";
  }
}

function setSourceToggle(toggleId, statusId, on) {
  const toggle = $(toggleId);
  toggle.hidden = false;
  toggle.textContent = on ? `Take away ${site.source_label}` : `Add ${site.source_label}`;
  toggle.setAttribute("aria-pressed", String(on));
  $(statusId).textContent = on ? `Playing: background plus ${site.source_label}` : "Playing: background only";
}

function wireToggle(toggleId, statusId) {
  $(toggleId).addEventListener("click", (event) => {
    const on = event.currentTarget.getAttribute("aria-pressed") !== "true";
    player.setHum(on);
    setSourceToggle(toggleId, statusId, on);
  });
}

function wireAudio() {
  $("volume").addEventListener("input", (event) => player.setVolume(event.target.value / 100));
  player.setVolume($("volume").value / 100);

  $("play-now").addEventListener("click", () => startPlaying("now", async () => {
    await player.play(humOnlyLevels(source.reading.tones));
    $("play-now").textContent = "Stop";
    $("listen-status").textContent = source.typical
      ? "Playing: an estimate of the hum in a typical reading"
      : "Playing: an estimate of the hum in the latest reading";
  }));

  $("play-background").addEventListener("click", () => startPlaying("background", async () => {
    const reading = source.reading;
    await player.play(mixLevels(reading.tones, backgroundDb(reading)), { birds: true, humOn: false });
    $("play-background").textContent = "Stop";
    if (toneAmplitudes(reading.tones).length) {
      setSourceToggle("toggle-source", "listen-status", false);
    } else {
      $("listen-status").textContent = "Playing: background only. There are no tones in the latest reading to add.";
    }
  }));

  wireToggle("toggle-source", "listen-status");
  wireToggle("toggle-record", "record-status");

  $("play-record").addEventListener("click", () => startPlaying("record", async () => {
    const record = figures(history).record;
    const base = record.excess === null ? null : record.l90 - record.excess;
    await player.play(mixLevels(record.tones, base), { birds: true, humOn: true });
    $("play-record").textContent = "Stop";
    setSourceToggle("toggle-record", "record-status", true);
  }));
}

// History view

// Numbers count up to their value when they first appear or change
function countTo(element, target, format) {
  const from = Number(element.dataset.value ?? 0);
  element.dataset.value = String(target);
  if (reducedMotion.matches || from === target) {
    element.textContent = format(target);
    return;
  }
  const started = performance.now();
  const step = (time) => {
    const progress = Math.min(1, (time - started) / 1100);
    const eased = 1 - (1 - progress) ** 3;
    element.textContent = format(from + (target - from) * eased);
    if (progress < 1 && element.dataset.value === String(target)) requestAnimationFrame(step);
  };
  requestAnimationFrame(step);
}

const plusDb = (value) => `+${value.toFixed(1)} dB`;

function renderHistory() {
  const shown = figures(history);
  const past = Boolean(history && history.earlier);
  const average = averageExcess(shown);
  const loudest = loudestExcess(shown, site.source_label);
  const longest = longestRun(shown);
  const share = runningShare(shown);
  const notes = [];

  fill("stat-average", average, () => {
    countTo($("average-value"), average.db, plusDb);
    $("tip-average").textContent = average.note;
    notes.push(["Average above background", average.note]);
  });
  fill("stat-loudest", loudest, () => {
    countTo($("loudest-value"), loudest.db, plusDb);
    $("tip-loudest").textContent = loudest.note;
    notes.push(["Loudest above background", loudest.note]);
  });
  fill("stat-longest", longest, () => {
    const run = shown.longest_run;
    const atLeast = longest.value.startsWith("At least");
    countTo($("longest-value"), run.end_t - run.start_t, (value) => `${duration(value)}${atLeast ? "+" : ""}`);
    const note = `From ${dateTime(run.start_t)} to ${dateTime(run.end_t)}. ${longest.note}`;
    $("tip-longest").textContent = note;
    notes.push(["Longest unbroken run", note]);
  });
  fill("stat-share", share, () => {
    const percent = Number.parseFloat(share.value);
    $("share-label").textContent = share.label;
    countTo($("share-value"), percent, (value) => `${value.toFixed(1)}%`);
    $("share-fill").style.width = `${Math.min(100, Math.max(0, percent))}%`;
    $("tip-share").textContent = share.note;
    notes.push([share.label, share.note]);
  });
  $("stats").hidden = notes.length === 0;

  const ended = shown && shown.monitoring_stopped;
  $("history-note").hidden = !ended;
  if (ended) {
    const opening = past && history.current_run
      ? "Apart from the current run, these figures come from earlier readings, which end"
      : "These figures come from readings that end";
    $("history-note").textContent = `${opening} on ${dateOnly(shown.last_t)}. ${site.history_note || ""}`.trim();
  }
  $("small-print-list").replaceChildren(...notes.flatMap(([label, note]) => {
    const term = document.createElement("dt");
    term.textContent = label;
    const detail = document.createElement("dd");
    detail.textContent = note;
    return [term, detail];
  }));
  $("small-print").hidden = notes.length === 0 && !ended;

  const record = shown && shown.record;
  const playable = record && record.excess !== null
    && tonesExplainLevel(record.tones, record.l90 - record.excess, record.l90);
  $("loudest").hidden = !record;
  $("loudest-no-play").hidden = !record || Boolean(playable);
  if (record) {
    $("loudest-line").textContent = record.excess === null
      ? "Not comparable with the background yet"
      : record.excess >= 0.5
        ? `${record.excess.toFixed(1)} dB above the background`
        : "About the same as the background";
    $("loudest-when").textContent =
      `The loudest 15 minutes logged with ${past ? "the earlier" : "this"} microphone, on ${dateTime(record.t)}, with ${site.source_label} running throughout${record.wind_checked === false ? "" : " and no wind flagged"}. This is the steady level, so passing sounds don't count.${record.wind_checked === false ? " Wind wasn't measured for this reading." : ""}`;
    $("play-record").hidden = !playable;
  }
  $("record-section").hidden = !record && $("small-print").hidden;
  // The headline clock depends on the current run
  if (live) renderStatus();
}

function fill(id, value, write) {
  $(id).hidden = !value;
  if (value) write();
}

// Tooltips show on hover or focus, and tapping the "i" pins one open
function wireTips() {
  const close = (except) => {
    for (const button of document.querySelectorAll(".info[aria-expanded='true']")) {
      if (button !== except) button.setAttribute("aria-expanded", "false");
    }
  };
  for (const button of document.querySelectorAll(".info")) {
    button.setAttribute("aria-describedby", button.getAttribute("aria-controls"));
    button.addEventListener("click", (event) => {
      event.stopPropagation();
      const open = button.getAttribute("aria-expanded") !== "true";
      close(button);
      button.setAttribute("aria-expanded", String(open));
    });
  }
  document.addEventListener("click", () => close());
  document.addEventListener("keydown", (event) => {
    if (event.key === "Escape") close();
  });
}

// Loading

async function refreshLive() {
  const path = demo ? "demo/live.json" : "data/live.json";
  live = await getJson(path);
  if (demo && live) {
    live.received_at = Date.now() / 1000 - 12;
    if (demo === "off") Object.assign(live, { state: "off", provisional: true, excess: 0.4, tones: live.tones.map((tone) => ({ ...tone, present: false, excess: 0.5 })) });
    if (demo === "unknown") live.state = "unknown";
    if (demo === "earlier") Object.assign(live, { provisional: true, excess: null, tones: live.tones.map((tone) => ({ ...tone, excess: null })) });
    if (demo === "fault") Object.assign(live, { state: "fault", mic_ok: false });
    if (demo === "stale") live.received_at = Date.now() / 1000 - 7400;
    if (demo === "none") live = null;
  }
  renderStatus();
}

async function refreshHistory() {
  const path = demo ? "demo/history.json" : "data/history.json";
  history = await getJson(path);
  if (demo === "none") history = null;
  if (history && demo) {
    const now = Math.floor(Date.now() / 1000);
    const shift = now - history.generated_at;
    for (const part of [history, history.current_run, history.longest_run, history.record, history.recent]) {
      for (const key of ["generated_at", "start_t", "end_t", "t", "since", "monitoring_since"]) {
        if (part && part[key]) part[key] += shift;
      }
    }
  }
  if (history && demo === "earlier") {
    history.earlier = { ...history, monitoring_stopped: true, last_t: history.generated_at - 1728000, current_run: null };
    Object.assign(history, { longest_run: null, record: null, typical: null, recent: null });
  }
  renderHistory();
}

const wave = new Wave($("wave"));

async function main() {
  if (demo) $("demo-banner").hidden = false;
  const configured = await getJson("site.json");
  if (configured) site = { ...site, ...configured };
  $("site-title").textContent = site.title;
  if (site.source_code_url) {
    const link = document.createElement("a");
    link.href = site.source_code_url;
    link.textContent = "The code behind this site is open source";
    $("source-link").replaceChildren(link, ".");
  }
  wireAudio();
  wireTips();
  // History first, because the headline depends on whether there is any
  await refreshHistory();
  await refreshLive();
  if (live && live.calibrated) {
    $("method-calibration").textContent = "Levels use a calibrated microphone, but this is not a certified measurement, and the figures are not dB(A).";
  }
  setInterval(refreshLive, LIVE_REFRESH_MS);
  setInterval(refreshHistory, HISTORY_REFRESH_MS);
  setInterval(() => {
    if (!$("status-sub").hidden) renderClock();
    renderSinceOpen();
  }, 1000);
  addEventListener("resize", () => {
    if (live) renderStatus();
  });
}

main();
