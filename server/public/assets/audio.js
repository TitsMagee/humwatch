// Generates the hum in the browser from the measured figures. No recorded audio is involved.
// Measured ratios between the tones and the background are kept exactly. No harmonics or
// other tones are added, because the page must not sound worse than what was measured.

const PEAK = 0.6;
const FADE_SECONDS = 1.2;
const NOISE_SECONDS = 8;
const BAND_LOW_HZ = 30;
const BAND_HIGH_HZ = 260;
const BIRD_GAIN = 0.05;

// Tones that were actually present, as amplitudes. Levels use the same scale as the
// logger, where a sine of amplitude A reads 20*log10(A) dB.
export function toneAmplitudes(tones) {
  return (tones || [])
    .filter((tone) => tone.present && tone.db !== null && tone.db !== undefined)
    .map((tone) => ({ hz: tone.hz, amplitude: 10 ** (tone.db / 20) }));
}

// A mix of tones and background, scaled to play safely with their ratios intact.
// baseDb is the level of the background alone. The same reading on that scale from
// noise with RMS s is 2*s^2, so s = 10^(baseDb/20) / sqrt(2).
export function mixLevels(tones, baseDb) {
  const parts = toneAmplitudes(tones);
  const noiseRms = baseDb === null || baseDb === undefined ? 0 : 10 ** (baseDb / 20) / Math.SQRT2;
  // Noise peaks at around three times its RMS
  const total = parts.reduce((sum, part) => sum + part.amplitude, 0) + 3 * noiseRms;
  const scale = total > 0 ? PEAK / total : 0;
  return {
    noise: noiseRms * scale,
    tones: parts.map((part) => ({ hz: part.hz, gain: part.amplitude * scale })),
  };
}

// Whether tones plus background add up to the measured level. If they fall well short, the
// level came mostly from something else, and playing the tones would misrepresent it.
export function tonesExplainLevel(tones, baseDb, levelDb, toleranceDb = 3) {
  if (baseDb === null || baseDb === undefined || levelDb === null || levelDb === undefined) return false;
  const power = (tones || [])
    .filter((tone) => tone.present && tone.db !== null && tone.db !== undefined)
    .reduce((sum, tone) => sum + 10 ** (tone.db / 10), 10 ** (baseDb / 10));
  return Math.abs(10 * Math.log10(power) - levelDb) <= toleranceDb;
}

// A mix with the tones alone, for hearing the hum by itself.
export function humOnlyLevels(tones) {
  return mixLevels(tones, null);
}

function renderBandNoise(context) {
  const rate = context.sampleRate;
  const length = NOISE_SECONDS * rate;
  const offline = new OfflineAudioContext(1, length, rate);
  const white = offline.createBuffer(1, length, rate);
  const samples = white.getChannelData(0);
  for (let i = 0; i < length; i++) samples[i] = Math.random() * 2 - 1;
  const source = offline.createBufferSource();
  source.buffer = white;
  const high = offline.createBiquadFilter();
  high.type = "highpass";
  high.frequency.value = BAND_LOW_HZ;
  const low = offline.createBiquadFilter();
  low.type = "lowpass";
  low.frequency.value = BAND_HIGH_HZ;
  source.connect(high).connect(low).connect(offline.destination);
  source.start();
  return offline.startRendering().then((rendered) => {
    const data = rendered.getChannelData(0);
    const fade = Math.floor(rate / 4);
    // Skip the filter settling at the start, and overlap the ends so the loop has no seam
    const usable = length - fade;
    const output = context.createBuffer(1, usable, rate);
    const out = output.getChannelData(0);
    for (let i = 0; i < usable; i++) out[i] = data[i + fade];
    for (let i = 0; i < fade; i++) {
      const mix = i / fade;
      out[i] = out[i] * mix + data[i] * (1 - mix);
    }
    let sum = 0;
    for (let i = 0; i < usable; i++) sum += out[i] * out[i];
    const rms = Math.sqrt(sum / usable) || 1;
    for (let i = 0; i < usable; i++) out[i] /= rms;
    return output;
  });
}

// One short rising and falling phrase, entirely synthesised.
function chirp(context, destination, when) {
  const notes = 2 + Math.floor(Math.random() * 5);
  const base = 2400 + Math.random() * 2000;
  let t = when;
  for (let i = 0; i < notes; i++) {
    const length = 0.06 + Math.random() * 0.12;
    const oscillator = context.createOscillator();
    const gain = context.createGain();
    const start = base * (0.85 + Math.random() * 0.3);
    oscillator.frequency.setValueAtTime(start, t);
    oscillator.frequency.exponentialRampToValueAtTime(start * (0.75 + Math.random() * 0.6), t + length);
    gain.gain.setValueAtTime(0, t);
    gain.gain.linearRampToValueAtTime(1, t + length * 0.25);
    gain.gain.linearRampToValueAtTime(0, t + length);
    oscillator.connect(gain).connect(destination);
    oscillator.start(t);
    oscillator.stop(t + length + 0.01);
    t += length + 0.03 + Math.random() * 0.1;
  }
}

export class HumPlayer {
  constructor() {
    this.context = null;
    this.master = null;
    this.humBus = null;
    this.nodes = [];
    this.birdTimer = null;
    this.volume = 0.35;
    this.noiseBuffer = null;
  }

  get playing() {
    return this.nodes.length > 0;
  }

  async _ensureContext() {
    if (!this.context) {
      this.context = new (window.AudioContext || window.webkitAudioContext)();
      this.master = this.context.createGain();
      this.master.gain.value = 0;
      this.master.connect(this.context.destination);
    }
    if (this.context.state === "suspended") await this.context.resume();
    if (!this.noiseBuffer) this.noiseBuffer = await renderBandNoise(this.context);
  }

  // levels from mixLevels or humOnlyLevels. humOn starts with the tones audible or not.
  async play(levels, { birds = false, humOn = true } = {}) {
    await this._ensureContext();
    this._clear();
    const now = this.context.currentTime;

    this.humBus = this.context.createGain();
    this.humBus.gain.value = humOn ? 1 : 0;
    this.humBus.connect(this.master);
    for (const tone of levels.tones) {
      const oscillator = this.context.createOscillator();
      oscillator.frequency.value = tone.hz;
      const gain = this.context.createGain();
      gain.gain.value = tone.gain;
      oscillator.connect(gain).connect(this.humBus);
      oscillator.start();
      this.nodes.push(oscillator);
    }
    if (levels.noise > 0) {
      const noise = this.context.createBufferSource();
      noise.buffer = this.noiseBuffer;
      noise.loop = true;
      const gain = this.context.createGain();
      gain.gain.value = levels.noise;
      noise.connect(gain).connect(this.master);
      noise.start();
      this.nodes.push(noise);
    }
    if (birds) this._startBirds();
    this.master.gain.cancelScheduledValues(now);
    this.master.gain.setValueAtTime(0, now);
    this.master.gain.linearRampToValueAtTime(this.volume, now + FADE_SECONDS);
  }

  setHum(on) {
    if (!this.humBus) return;
    const now = this.context.currentTime;
    this.humBus.gain.cancelScheduledValues(now);
    this.humBus.gain.setValueAtTime(this.humBus.gain.value, now);
    this.humBus.gain.linearRampToValueAtTime(on ? 1 : 0, now + FADE_SECONDS);
  }

  setVolume(value) {
    this.volume = value;
    if (this.playing) this.master.gain.setTargetAtTime(value, this.context.currentTime, 0.05);
  }

  stop() {
    if (!this.playing) return;
    const now = this.context.currentTime;
    this.master.gain.cancelScheduledValues(now);
    this.master.gain.setValueAtTime(this.master.gain.value, now);
    this.master.gain.linearRampToValueAtTime(0, now + 0.3);
    const nodes = this.nodes;
    this.nodes = [];
    clearTimeout(this.birdTimer);
    for (const node of nodes) node.stop(now + 0.35);
  }

  _clear() {
    clearTimeout(this.birdTimer);
    for (const node of this.nodes) {
      try {
        node.stop();
      } catch {
        // Already stopped
      }
    }
    this.nodes = [];
    if (this.humBus) this.humBus.disconnect();
  }

  _startBirds() {
    const bus = this.context.createGain();
    bus.gain.value = BIRD_GAIN;
    bus.connect(this.master);
    const schedule = () => {
      chirp(this.context, bus, this.context.currentTime + 0.05);
      this.birdTimer = setTimeout(schedule, 800 + Math.random() * 3500);
    };
    schedule();
  }
}
