// node --test server/tests/format.test.mjs
import assert from "node:assert/strict";
import { test } from "node:test";

import { humOnlyLevels, mixLevels, toneAmplitudes } from "../public/assets/audio.js";
import { currentRun, duration, longestRun, runningShare, signedDb, status } from "../public/assets/format.js";

const NOW = 1800000000;
const reading = (extra) => ({ received_at: NOW - 10, state: "on", mic_ok: true, excess: 7.5, ...extra });

test("durations read naturally", () => {
  assert.equal(duration(30), "under a minute");
  assert.equal(duration(60), "1 minute");
  assert.equal(duration(3 * 3600), "3 hours");
  assert.equal(duration(5 * 86400 + 4 * 3600), "5 days, 4 hours");
  assert.equal(duration(3 * 86400), "3 days");
});

test("signed dB uses a real minus sign", () => {
  assert.equal(signedDb(7.46), "+7.5 dB");
  assert.equal(signedDb(-2), "−2.0 dB");
});

test("running reading states the excess", () => {
  const result = status(reading(), "the mill", NOW);
  assert.equal(result.headline, "The mill is running");
  assert.match(result.detail, /7\.5 dB louder/);
  assert.equal(result.numbers, true);
});

test("no baseline means no comparison is claimed", () => {
  assert.doesNotMatch(status(reading({ excess: null }), "the mill", NOW).detail, /dB/);
});

test("an unconfirmed stop is hedged", () => {
  const result = status(reading({ state: "off", provisional: true }), "the mill", NOW);
  assert.equal(result.headline, "The mill may have stopped");
  assert.match(result.detail, /Nobody has confirmed/);
});

test("a confirmed detector drops the hedge", () => {
  const result = status(reading({ state: "off", provisional: false }), "the mill", NOW);
  assert.equal(result.headline, "The mill appears to have stopped");
});

test("a fault never says the source stopped, and hides figures", () => {
  const result = status(reading({ state: "fault", mic_ok: false }), "the mill", NOW);
  assert.equal(result.kind, "fault");
  assert.equal(result.numbers, false);
  assert.match(result.detail, /doesn't mean the mill has stopped/);
});

test("old data is flagged rather than shown as current", () => {
  const result = status(reading({ received_at: NOW - 200 }), "the mill", NOW);
  assert.equal(result.kind, "stale");
  assert.equal(result.numbers, false);
});

test("stale wins over a fault or a confident state", () => {
  assert.equal(status(reading({ received_at: NOW - 200, state: "fault", mic_ok: false }), "x", NOW).kind, "stale");
});

test("no data at all", () => {
  assert.equal(status(null, "the mill", NOW).kind, "none");
});

test("a run after a monitoring gap says at least", () => {
  const history = { current_run: { state: "on", after_gap: true, start_t: NOW - 7200, end_t: NOW } };
  const run = currentRun(history, "the mill", NOW);
  assert.equal(run.value, "At least 2 hours");
  assert.match(run.note, /longer/);
});

test("a run after a recorded stop is stated plainly", () => {
  const history = { current_run: { state: "on", after_gap: false, start_t: NOW - 7200, end_t: NOW } };
  assert.equal(currentRun(history, "the mill", NOW).value, "2 hours");
});

test("no current run figure while state is unclear", () => {
  const history = { current_run: { state: "unknown", start_t: NOW - 7200, end_t: NOW } };
  assert.equal(currentRun(history, "the mill", NOW), null);
});

test("longest run cut by a gap says at least", () => {
  const run = longestRun({ longest_run: { start_t: 0, end_t: 7200, ended_by: "data_gap" } });
  assert.equal(run.value, "At least 2 hours");
  assert.match(run.note, /gap/);
});

test("longest run ended by a stop is stated plainly", () => {
  assert.equal(longestRun({ longest_run: { start_t: 0, end_t: 7200, ended_by: "off" } }).value, "2 hours");
});

test("share leaves out unclear readings and says so", () => {
  const share = runningShare({
    generated_at: NOW,
    recent: { since: NOW - 30 * 86400, on_percent: 96.4, minutes_on: 964, minutes_off: 36, minutes_unknown: 120 },
  });
  assert.equal(share.value, "96.4%");
  assert.equal(share.label, "Share of the past 30 days running");
  assert.match(share.note, /left out/);
});

test("only tones that are present are played", () => {
  const parts = toneAmplitudes([
    { hz: 100, db: -40, present: true },
    { hz: 200, db: -40, present: false },
    { hz: 300, db: null, present: true },
  ]);
  assert.deepEqual(parts.map((part) => part.hz), [100]);
  assert.ok(Math.abs(parts[0].amplitude - 0.01) < 1e-9);
});

test("the mix keeps the measured dB difference between tones", () => {
  const levels = humOnlyLevels([
    { hz: 100, db: -40, present: true },
    { hz: 200, db: -60, present: true },
  ]);
  const difference = 20 * Math.log10(levels.tones[0].gain / levels.tones[1].gain);
  assert.ok(Math.abs(difference - 20) < 1e-9);
});

test("the mix keeps the measured ratio of tone to background", () => {
  const levels = mixLevels([{ hz: 100, db: -40, present: true }], -60);
  // A sine reading -40 dB has amplitude 0.01. Noise reading -60 dB has RMS 0.001 / sqrt(2).
  const ratio = levels.tones[0].gain / levels.noise;
  assert.ok(Math.abs(ratio - 0.01 / (0.001 / Math.SQRT2)) < 1e-6);
});

test("the mix stays within a safe peak however loud the reading", () => {
  const levels = mixLevels([{ hz: 100, db: 0, present: true }, { hz: 200, db: 0, present: true }], -3);
  const peak = levels.tones.reduce((sum, tone) => sum + tone.gain, 0) + 3 * levels.noise;
  assert.ok(peak <= 0.6 + 1e-9);
});

test("nothing to play gives silence, not an error", () => {
  assert.deepEqual(mixLevels([], null), { noise: 0, tones: [] });
});
