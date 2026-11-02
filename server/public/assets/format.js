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

function excessLine(excess) {
  if (excess === null || excess === undefined) {
    return "Its level can't be compared with the background until a reference has been taken during a stop.";
  }
  if (excess >= 0.5) return `${excess.toFixed(1)} dB louder than the background without it.`;
  return "About the same level as the background without it.";
}

// What the headline should say. numbers: whether level figures can be trusted right now.
export function status(live, label, now) {
  const name = capitalise(label);
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

export function currentRun(history, label, now) {
  const run = history && history.current_run;
  if (!run) return null;
  const seconds = Math.max(now, run.end_t) - run.start_t;
  const span = duration(seconds);
  if (run.state === "on") {
    return {
      label: `Current run of ${label}`,
      value: run.after_gap ? `At least ${span}` : span,
      note: run.after_gap
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
  const days = Math.max(1, Math.round((history.generated_at - recent.since) / 86400));
  const unclear = recent.minutes_unknown;
  return {
    label: days === 1 ? "Share of the past day running" : `Share of the past ${days} days running`,
    value: `${recent.on_percent.toFixed(1)}%`,
    note: unclear
      ? `Based on ${duration(known * 60)} of clear readings. ${capitalise(duration(unclear * 60))} of unclear readings are left out.`
      : `Based on ${duration(known * 60)} of readings.`,
  };
}
