// What the page says about the readings. No page or browser code, so tests can run it directly.

export const STALE_SECONDS = 180;

export function capitalise(text) {
  return text.charAt(0).toUpperCase() + text.slice(1);
}

export function duration(seconds) {
  if (seconds < 60) return "under a minute";
  const minutes = Math.floor(seconds / 60);
  if (minutes < 60) return unit(minutes, "minute");
  const hours = Math.floor(minutes / 60);
  if (hours < 48) return `${unit(hours, "hour")}`;
  const days = Math.floor(hours / 24);
  const left = hours - days * 24;
  return left ? `${unit(days, "day")}, ${unit(left, "hour")}` : unit(days, "day");
}

function unit(count, name) {
  return `${count} ${name}${count === 1 ? "" : "s"}`;
}

export function signedDb(value) {
  const rounded = Math.round(value * 10) / 10;
  return `${rounded >= 0 ? "+" : "−"}${Math.abs(rounded).toFixed(1)} dB`;
}

export function dateTime(seconds, timeZone) {
  return new Intl.DateTimeFormat("en-GB", {
    weekday: "short", day: "numeric", month: "short", year: "numeric",
    hour: "2-digit", minute: "2-digit", timeZone,
  }).format(new Date(seconds * 1000));
}

export function dateOnly(seconds, timeZone) {
  return new Intl.DateTimeFormat("en-GB", { day: "numeric", month: "long", year: "numeric", timeZone })
    .format(new Date(seconds * 1000));
}

function excessLine(excess) {
  if (excess === null || excess === undefined) {
    return "Its level can't be compared with the background until a reference has been taken during a stop.";
  }
  if (excess >= 0.5) return `${excess.toFixed(1)} dB louder than the background without it.`;
  return "About the same level as the background without it.";
}

// What the headline should say. numbers: whether level figures can be trusted right now.
export function status(live, label, now, hasHistory = false) {
  const name = capitalise(label);
  if (!live && hasHistory) {
    return {
      kind: "none", numbers: false,
      headline: "No live readings at the moment",
      detail: `Earlier readings are further down this page. This says nothing about ${label} right now.`,
    };
  }
  if (!live) {
    return {
      kind: "none", numbers: false,
      headline: "Monitoring hasn't started yet",
      detail: "Readings will appear here once the microphone is running.",
    };
  }
  const age = now - live.received_at;
  if (age > STALE_SECONDS) {
    return {
      kind: "stale", numbers: false,
      headline: "No recent readings",
      detail: `The last one was ${duration(age)} ago. The monitor or its connection may be down, which says nothing about ${label}.`,
    };
  }
  if (live.state === "fault" || live.mic_ok === false) {
    return {
      kind: "fault", numbers: false,
      headline: "The microphone has a fault",
      detail: `Readings are paused until it's fixed. This doesn't mean ${label} has stopped.`,
    };
  }
  if (live.state === "on") {
    if (live.provisional) {
      return {
        kind: "on", numbers: true,
        headline: `${name} appears to be running`,
        detail: "Going by its tones alone, until the monitor has recorded a stop to compare them with.",
      };
    }
    return { kind: "on", numbers: true, headline: `${name} is running`, detail: excessLine(live.excess) };
  }
  if (live.state === "off") {
    return {
      kind: "off", numbers: true,
      headline: live.provisional ? `${name} may have stopped` : `${name} appears to have stopped`,
      detail: live.provisional
        ? "Its tones have dropped away and the level has fallen. Nobody has confirmed this yet."
        : "Its tones have dropped away and the level has fallen.",
    };
  }
  return {
    kind: "unknown", numbers: true,
    headline: `Can't tell if ${label} is running`,
    detail: "The latest readings don't clearly show it either way.",
  };
}

// The figures to show: the previous mic's while the current one has no stopped level
export function figures(history) {
  return (history && history.earlier) || history;
}

export function currentRun(history, label, now) {
  const run = history && history.current_run;
  if (!run) return null;
  const seconds = Math.max(now, run.end_t) - run.start_t;
  const span = duration(seconds);
  if (run.state === "on") {
    return {
      label: `Current run of ${label}`,
      value: run.after_gap ? `At least ${span}` : span,
      note: run.provisional
        ? "Going by its tones alone, and it may have been running for longer."
        : run.after_gap
          ? "Monitoring only resumed at the start of this run, so it may have been running for longer."
          : "Since the monitor last recorded it stopping.",
    };
  }
  if (run.state === "off") {
    return {
      label: `${capitalise(label)} has been quiet for`,
      value: span,
      note: run.provisional ? "Nobody has confirmed this stop yet." : "Going by the tones and the level.",
    };
  }
  return null;
}

export function longestRun(history) {
  const run = history && history.longest_run;
  if (!run || run.end_t <= run.start_t) return null;
  const span = duration(run.end_t - run.start_t);
  const notes = {
    off: "It ended when the monitor recorded a stop.",
    data_gap: "A gap in monitoring cut this short, so the real run may have been longer.",
    new_mic: "A change of microphone cut this short, so the real run may have been longer.",
    fault: "A microphone fault cut this short, so the real run may have been longer.",
    unknown: "It ended when the readings became unclear, so the real run may have been longer.",
  };
  const open = run.ended_by === null || run.ended_by === undefined;
  return {
    value: open || run.ended_by === "off" ? span : `At least ${span}`,
    note: open ? "This run is still going." : notes[run.ended_by] || "",
  };
}

export function runningShare(history) {
  const recent = history && history.recent;
  if (!recent || recent.on_percent === null || recent.on_percent === undefined) return null;
  const known = recent.minutes_on + recent.minutes_off;
  const days = Math.max(1, Math.round(((recent.until ?? history.generated_at) - recent.since) / 86400));
  const unclear = recent.minutes_unknown;
  const span = days === 1 ? "day" : `${days} days`;
  return {
    label: history.monitoring_stopped ? `Share of the last ${span} logged, running` : `Share of the past ${span} running`,
    value: `${recent.on_percent.toFixed(1)}%`,
    note: unclear
      ? `Based on ${duration(known * 60)} of clear readings. ${capitalise(duration(unclear * 60))} of unclear readings are left out.`
      : `Based on ${duration(known * 60)} of readings.`,
  };
}

// The headline clock for a run: whole days, hours and minutes, leading zeros dropped
export function runClock(seconds) {
  const total = Math.max(0, Math.floor(seconds / 60));
  const days = Math.floor(total / 1440);
  const hours = Math.floor((total % 1440) / 60);
  const minutes = total % 60;
  const parts = [[days, "day"], [hours, "hour"], [minutes, "minute"]]
    .filter(([count], index, all) => count > 0 || all.slice(0, index).some(([earlier]) => earlier > 0) || index === 2)
    .map(([count, name]) => ({ count, unit: `${name}${count === 1 ? "" : "s"}` }));
  const words = parts.map((part) => `${part.count} ${part.unit}`);
  const text = words.length > 1 ? `${words.slice(0, -1).join(", ")} and ${words.at(-1)}` : words[0];
  return { parts, text };
}

// How many times the background's sound energy a level above it carries
export function energyTimes(db) {
  const times = 10 ** (db / 10);
  return times < 10 ? times.toFixed(1).replace(/\.0$/, "") : String(Math.round(times));
}

function aboveNote(db) {
  return db >= 1
    ? ` That's about ${energyTimes(db)} times the sound energy of the background alone. Every 10 dB is roughly a doubling in how loud it seems.`
    : "";
}

export function averageExcess(history) {
  const recent = history && history.recent;
  const days = recent ? Math.max(1, Math.round(((recent.until ?? history.generated_at) - recent.since) / 86400)) : 0;
  const span = days === 1 ? "day" : `${days} days`;
  if (recent && typeof recent.mean_excess === "number") {
    return {
      db: recent.mean_excess,
      note: `The average of every running minute over the last ${span}, compared with the level measured while it was stopped.${aboveNote(recent.mean_excess)}`,
    };
  }
  const typical = history && history.typical;
  if (typical && typeof typical.usual_excess === "number") {
    return {
      db: typical.usual_excess,
      note: `The usual level of every running minute over the last ${span}, compared with the level measured while it was stopped.${aboveNote(typical.usual_excess)}`,
    };
  }
  return null;
}

export function loudestExcess(history, label) {
  const record = history && history.record;
  if (!record || typeof record.excess !== "number") return null;
  return {
    db: record.excess,
    t: record.t,
    note: `The loudest steady 15 minutes logged, with ${label} running throughout, on ${dateTime(record.t)}. Passing sounds don't count.${aboveNote(record.excess)}`,
  };
}

// Day and night at the site, by its own clock

const hourFormats = new Map();

export function localHour(seconds, timeZone) {
  if (!hourFormats.has(timeZone)) {
    hourFormats.set(timeZone, new Intl.DateTimeFormat("en-GB", { hour: "numeric", hourCycle: "h23", timeZone }));
  }
  const parts = hourFormats.get(timeZone).formatToParts(new Date(seconds * 1000));
  return Number(parts.find((part) => part.type === "hour").value) % 24;
}

export function isNight(seconds, chart) {
  const hour = localHour(seconds, chart.timezone);
  return hour >= chart.night_starts || hour < chart.day_starts;
}

const PART_NAMES = { day: "daytime", night: "night-time" };

// The background that applies at a moment: { db, from, minutes, part, name } or null
export function backgroundAt(chart, seconds) {
  if (!chart || !chart.background) return null;
  const part = isNight(seconds, chart) ? "night" : "day";
  const level = chart.background[part];
  return level && typeof level.db === "number" ? { ...level, part, name: PART_NAMES[part] } : null;
}

export function hasBackground(chart) {
  return Boolean(chart && chart.background && (chart.background.day || chart.background.night)
    && chart.l90 && chart.l90.some((db) => db !== null));
}

// The latest minute against the background for this time of day, or failing that, against
// the stopped reference the logger already compared it with.
export function liveAbove(live, chart, now) {
  if (!live) return null;
  const background = typeof live.l90 === "number" ? backgroundAt(chart, now) : null;
  if (background) return { db: live.l90 - background.db, background };
  if (typeof live.excess === "number") return { db: live.excess, background: null };
  return null;
}

export function readoutValue(db) {
  return db >= 0.5 ? signedDb(db) : "About 0 dB";
}

export function readoutLabel(above) {
  const which = above.background ? `the ${above.background.name} background` : "the background";
  return above.db >= 0.5 ? `above ${which}, right now` : `against ${which}, right now`;
}

function backgroundSource(background, label) {
  if (background.from === "stops") {
    return `the typical ${background.name} level while ${label} was stopped, from ${duration(background.minutes * 60)} of readings`;
  }
  return `the reference level taken while ${label} was stopped`;
}

export function readoutNote(above, label) {
  const against = above.background ? backgroundSource(above.background, label) : `the reference level taken while ${label} was stopped`;
  return `The hum band's L90 in the latest minute: the level it stays above 90% of the time, so passing sounds hardly count. ` +
    `It's compared with ${against}.${aboveNote(above.db)} Unweighted, so not dB(A).`;
}

export function strongestTone(tones) {
  const measured = (tones || []).filter((tone) => tone.present && typeof tone.excess === "number");
  return measured.length ? measured.reduce((best, tone) => (tone.excess > best.excess ? tone : best)) : null;
}

// Each 15 minute block between two moments, with the background that applied to it
export function chartPoints(chart, from, to) {
  const points = [];
  chart.l90.forEach((db, index) => {
    const start = chart.start + index * chart.step;
    if (start < from || start >= to) return;
    points.push({ t: start + chart.step / 2, start, db, on: chart.on[index], background: backgroundAt(chart, start) });
  });
  return points;
}

// How far running blocks sat above the background, on average, by day and by night
export function aboveByPart(points) {
  const sums = { day: [0, 0], night: [0, 0] };
  for (const point of points) {
    if (point.db === null || point.on !== 1 || !point.background) continue;
    const sum = sums[point.background.part];
    sum[0] += point.db - point.background.db;
    sum[1] += 1;
  }
  const mean = ([total, count]) => (count ? { db: total / count, blocks: count } : null);
  return { day: mean(sums.day), night: mean(sums.night) };
}

export function backgroundNote(chart, label) {
  const { day, night } = chart.background;
  const notes = day && night && day.from === "reference" && night.from === "reference"
    ? [`Day and night share one background: ${backgroundSource(day, label)}, until enough stops are logged to tell them apart.`]
    : ["day", "night"].map((part) => {
    const level = chart.background[part];
    if (!level) return `There's no ${PART_NAMES[part]} background yet.`;
    return `The ${PART_NAMES[part]} background is ${backgroundSource({ ...level, name: PART_NAMES[part] }, label)}.`;
  });
  return `${notes.join(" ")} Day is 07:00 to 23:00. Levels are the hum band's L90, unweighted, in 15 minute blocks.`;
}
