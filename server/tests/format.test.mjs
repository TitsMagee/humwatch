// node --test server/tests/format.test.mjs
import assert from "node:assert/strict";
import { test } from "node:test";

import { beatPairs, envelope, wobbleSentence } from "../public/assets/wobble.js";
import { humOnlyLevels, mixLevels, toneAmplitudes, tonesExplainLevel } from "../public/assets/audio.js";
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

test("share of readings that have ended counts back from the last one", () => {
  const share = runningShare({
    generated_at: NOW,
    monitoring_stopped: true,
    recent: { since: NOW - 40 * 86400, until: NOW - 10 * 86400, on_percent: 96, minutes_on: 96, minutes_off: 4, minutes_unknown: 0 },
  });
  assert.equal(share.label, "Share of the last 30 days logged, running");
});

test("no live reading with history doesn't claim monitoring never began", () => {
  const result = status(null, "the mill", NOW, true);
  assert.equal(result.headline, "No live readings at the moment");
  assert.match(result.detail, /says nothing about the mill/);
  assert.equal(result.numbers, false);
});

test("a level the tones can't explain isn't offered as playback", () => {
  const tones = [{ hz: 100, db: -58, present: true }];
  assert.equal(tonesExplainLevel(tones, -51, -32), false);
});

test("a level the tones do explain can be played", () => {
  const tones = [{ hz: 100, db: -45, present: true }, { hz: 120, db: -45, present: true }];
  assert.equal(tonesExplainLevel(tones, -50, -41), true);
});

test("no background figure means no comparison can be played", () => {
  assert.equal(tonesExplainLevel([{ hz: 100, db: -45, present: true }], null, -41), false);
});

test("close tones beat at their difference, strongest pair first", () => {
  const beats = beatPairs([
    { hz: 148.5, db: -40, present: true },
    { hz: 153.5, db: -40, present: true },
    { hz: 74.0, db: -60, present: true },
    { hz: 80.0, db: -60, present: true },
  ]);
  assert.equal(beats.pairs.length, 2);
  assert.equal(beats.pairs[0].rate, 5);
  assert.ok(beats.pairs[0].depth > beats.pairs[1].depth);
});

test("pairs too far apart or too close to resolve don't count as wobble", () => {
  const beats = beatPairs([
    { hz: 100, db: -40, present: true },
    { hz: 100.2, db: -40, present: true },
    { hz: 150, db: -40, present: true },
  ]);
  assert.deepEqual(beats.pairs, []);
});

test("two equal tones swing from full to silence and back", () => {
  const beats = beatPairs([{ hz: 100, db: -40, present: true }, { hz: 104, db: -40, present: true }]);
  assert.ok(Math.abs(envelope(beats, [0], 0) - 1) < 1e-9);
  assert.ok(envelope(beats, [0], 0.125) < 1e-6);
});

test("the envelope never exceeds its peak", () => {
  const beats = beatPairs([{ hz: 100, db: -40, present: true }, { hz: 103, db: -45, present: true }, { hz: 107, db: -50, present: true }]);
  for (let t = 0; t < 2; t += 0.01) assert.ok(envelope(beats, [0, 0, 0], t) <= 1 + 1e-9);
});

test("the wobble sentence names the strongest pair", () => {
  const beats = beatPairs([{ hz: 148.7, db: -40, present: true }, { hz: 153.8, db: -41, present: true }]);
  assert.match(wobbleSentence(beats), /149 and 154 Hz .* about 5 times a second/);
});

test("a steady hum says so rather than inventing a throb", () => {
  const beats = beatPairs([{ hz: 100, db: -40, present: true }, { hz: 150, db: -40, present: true }]);
  assert.match(wobbleSentence(beats), /holds steady/);
  assert.equal(wobbleSentence(beatPairs([])), null);
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

test("a running reading with no stop to compare against says so", () => {
  const result = status(reading({ state: "on", provisional: true, excess: null }), "the mill", NOW);
  assert.match(result.headline, /appears to be running/);
  assert.match(result.detail, /tones alone/);
});

test("an interim run does not claim a recorded stop", () => {
  const run = currentRun({ current_run: { state: "on", provisional: true, after_gap: false, start_t: NOW - 3600, end_t: NOW } }, "the mill", NOW);
  assert.match(run.note, /tones alone/);
  assert.doesNotMatch(run.note, /last recorded/);
});
