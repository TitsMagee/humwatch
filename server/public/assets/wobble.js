// The throb heard when tones a few hertz apart drift in and out of step (beating).
// Worked out from the measured tones only: nothing here is added for effect.

// Differences slower than this are heard as a throb or wobble; faster ones as roughness.
export const WOBBLE_MAX_HZ = 15;
// Tone frequencies are measured to 0.5 Hz, so closer pairs can't be told apart.
const RESOLUTION_HZ = 0.5;

export function tonesForBeats(tones) {
  return (tones || [])
    .filter((tone) => tone.present && tone.db !== null && tone.db !== undefined)
    .map((tone) => ({ hz: tone.hz, amplitude: 10 ** (tone.db / 20) }));
}

// Pairs that beat slowly enough to be heard as a wobble, strongest first.
// depth is how far that pair alone swings the loudness: 1 would be full silence and back.
export function beatPairs(tones) {
  const parts = tonesForBeats(tones);
  const power = parts.reduce((sum, part) => sum + part.amplitude ** 2, 0);
  const pairs = [];
  for (let i = 0; i < parts.length; i++) {
    for (let j = i + 1; j < parts.length; j++) {
      const rate = Math.abs(parts[i].hz - parts[j].hz);
      if (rate >= RESOLUTION_HZ && rate <= WOBBLE_MAX_HZ) {
        pairs.push({
          low: Math.min(parts[i].hz, parts[j].hz),
          high: Math.max(parts[i].hz, parts[j].hz),
          rate,
          depth: (2 * parts[i].amplitude * parts[j].amplitude) / power,
        });
      }
    }
  }
  return { power, pairs: pairs.sort((a, b) => b.depth - a.depth) };
}

// Loudness envelope over time from the slow beats, relative to its own peak.
// Fast differences average out within the ear's response, so only slow pairs are kept.
export function envelope(beats, phases, t) {
  if (!beats.power) return 0;
  let value = beats.power;
  beats.pairs.forEach((pair, index) => {
    value += pair.depth * beats.power * Math.cos(2 * Math.PI * pair.rate * t + phases[index]);
  });
  const peak = beats.power * (1 + beats.pairs.reduce((sum, pair) => sum + pair.depth, 0));
  return Math.sqrt(Math.max(0, value) / peak);
}

// One plain sentence about the strongest beat, or null if there's nothing to say.
export function wobbleSentence(beats) {
  const strongest = beats.pairs[0];
  if (!strongest || strongest.depth < 0.2) {
    return beats.power ? "Its tones don't beat slowly against each other in this reading, so it holds steady rather than throbbing." : null;
  }
  const rate = strongest.rate < 1 ? "less than once a second" : `about ${Math.round(strongest.rate)} times a second`;
  return `Tones at ${Math.round(strongest.low)} and ${Math.round(strongest.high)} Hz drift in and out of step, so the hum swells and fades ${rate}. That is heard as a throb.`;
}
