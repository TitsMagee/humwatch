// node --test server/tests/format.test.mjs
import assert from "node:assert/strict";
import { test } from "node:test";

import { beatPairs, envelope, wobbleSentence } from "../public/assets/wobble.js";
import { humOnlyLevels, mixLevels, toneAmplitudes, tonesExplainLevel } from "../public/assets/audio.js";
import {
  aboveByPart, backgroundAt, backgroundNote, chartPoints, currentRun, duration, figures, hasBackground, isNight, liveAbove,
  longestRun, readoutLabel, readoutValue, runningShare, signedDb, status, strongestTone,
} from "../public/assets/format.js";

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

test("earlier figures are used only when the history offers them", () => {
  const earlier = { longest_run: {} };
  assert.equal(figures({ earlier }), earlier);
  const history = { earlier: null };
  assert.equal(figures(history), history);
  assert.equal(figures(null), null);
});

test("the run clock drops leading zeros but keeps the rest", async () => {
  const { runClock } = await import("../public/assets/format.js");
  assert.equal(runClock(4 * 86400 + 3 * 3600 + 12 * 60 + 59).text, "4 days, 3 hours and 12 minutes");
  assert.equal(runClock(86400 + 60).text, "1 day, 0 hours and 1 minute");
  assert.equal(runClock(2 * 3600).text, "2 hours and 0 minutes");
  assert.equal(runClock(30).text, "0 minutes");
});

test("average excess prefers the mean, falls back to the usual level", async () => {
  const { averageExcess, energyTimes } = await import("../public/assets/format.js");
  const recent = { since: NOW - 30 * 86400, until: NOW };
  assert.equal(averageExcess({ recent: { ...recent, mean_excess: 6.4 }, typical: { usual_excess: 6.1 } }).db, 6.4);
  assert.equal(averageExcess({ recent, typical: { usual_excess: 6.1 } }).db, 6.1);
  assert.equal(averageExcess({ recent }), null);
  assert.equal(energyTimes(10), "10");
  assert.equal(energyTimes(6), "4");
});

// 2027-01-15 00:00 UTC, when London is on UTC
const MIDNIGHT = 1799971200;
const chart = (extra) => ({
  start: MIDNIGHT, step: 900, until: MIDNIGHT + 86400, timezone: "Europe/London", day_starts: 7, night_starts: 23,
  l90: [], on: [], background: { day: { db: -55, from: "stops", minutes: 600 }, night: { db: -61, from: "stops", minutes: 120 } },
  ...extra,
});

test("night runs from 23:00 to 07:00 by the site's clock", () => {
  assert.equal(isNight(MIDNIGHT + 3600, chart()), true);
  assert.equal(isNight(MIDNIGHT + 6.99 * 3600, chart()), true);
  assert.equal(isNight(MIDNIGHT + 7 * 3600, chart()), false);
  assert.equal(isNight(MIDNIGHT + 23 * 3600, chart()), true);
  // 22:30 UTC is 23:30 in Berlin
  assert.equal(isNight(MIDNIGHT + 22.5 * 3600, chart({ timezone: "Europe/Berlin" })), true);
  assert.equal(isNight(MIDNIGHT + 22.5 * 3600, chart()), false);
});

test("the background follows the time of day", () => {
  assert.equal(backgroundAt(chart(), MIDNIGHT + 3600).db, -61);
  assert.equal(backgroundAt(chart(), MIDNIGHT + 12 * 3600).name, "daytime");
  assert.equal(backgroundAt(chart({ background: { day: null, night: null } }), MIDNIGHT), null);
  assert.equal(backgroundAt(null, MIDNIGHT), null);
});

test("a chart needs readings and a background to be worth drawing", () => {
  assert.equal(hasBackground(chart({ l90: [null, -50] })), true);
  assert.equal(hasBackground(chart({ l90: [null, null] })), false);
  assert.equal(hasBackground(chart({ l90: [-50], background: { day: null, night: null } })), false);
  assert.equal(hasBackground(null), false);
});

test("the live figure compares with the background for the time of day", () => {
  const live = { l90: -50, excess: 8 };
  const night = liveAbove(live, chart(), MIDNIGHT + 3600);
  assert.equal(night.db, 11);
  assert.equal(readoutLabel(night), "above the night-time background, right now");
  assert.equal(liveAbove(live, chart(), MIDNIGHT + 12 * 3600).db, 5);
});

test("without a day or night background, the live figure falls back to the logger's excess", () => {
  const above = liveAbove({ l90: -50, excess: 8 }, null, MIDNIGHT);
  assert.equal(above.db, 8);
  assert.equal(readoutLabel(above), "above the background, right now");
  assert.equal(liveAbove({ l90: -50, excess: null }, null, MIDNIGHT), null);
  assert.equal(liveAbove(null, chart(), MIDNIGHT), null);
});

test("a live figure at the background doesn't claim to be above it", () => {
  assert.equal(readoutValue(0.2), "About 0 dB");
  assert.equal(readoutValue(-3), "About 0 dB");
  assert.equal(readoutValue(6.04), "+6.0 dB");
  assert.match(readoutLabel({ db: -1, background: null }), /^against/);
});

test("the strongest tone is the present one standing furthest above the background", () => {
  const tones = [
    { hz: 60, excess: 15, present: true },
    { hz: 120, excess: 26, present: true },
    { hz: 211, excess: 30, present: false },
    { hz: 239, excess: null, present: true },
  ];
  assert.equal(strongestTone(tones).hz, 120);
  assert.equal(strongestTone([{ hz: 60, excess: null, present: true }]), null);
  assert.equal(strongestTone(undefined), null);
});

test("chart points cover the asked-for span, each with its background", () => {
  const data = chart({ l90: [-50, -51, null, -49], on: [1, 1, null, 1] });
  const points = chartPoints(data, MIDNIGHT + 900, MIDNIGHT + 3600);
  assert.deepEqual(points.map((point) => point.db), [-51, null, -49]);
  assert.equal(points[0].t, MIDNIGHT + 900 + 450);
  assert.equal(points[0].background.part, "night");
});

test("average above background counts running blocks only, split by day and night", () => {
  const night = { db: -61, part: "night" };
  const day = { db: -55, part: "day" };
  const parts = aboveByPart([
    { db: -50, on: 1, background: night },
    { db: -52, on: 1, background: night },
    { db: -60, on: 0, background: night },
    { db: null, on: null, background: night },
    { db: -49, on: 1, background: day },
  ]);
  assert.equal(parts.night.db, 10);
  assert.equal(parts.night.blocks, 2);
  assert.equal(parts.day.db, 6);
  assert.equal(aboveByPart([{ db: -50, on: 1, background: null }]).day, null);
});

test("the background note says where each level came from", () => {
  assert.match(backgroundNote(chart(), "the mill"), /night-time background is the typical night-time level while the mill was stopped, from 2 hours/);
  const reference = { db: -58, from: "reference", minutes: 3 };
  const shared = backgroundNote(chart({ background: { day: reference, night: reference } }), "the mill");
  assert.match(shared, /^Day and night share one background/);
  assert.match(backgroundNote(chart({ background: { day: reference, night: null } }), "the mill"), /no night-time background yet/);
});
