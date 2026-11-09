# Design

How HumWatch works and why. Written for people changing the code or adapting it to another site.

## Overview

```
Mic -> Pi: analyse -> minute summaries -> local SQLite queue
                                               |  signed HTTPS, outbound only
                                               v
Web hosting: PHP API -> MySQL -> cron rollup -> live.json, history.json -> public pages
                         |
                         +-> private review page (login) -> clips, stored outside the web root
```

- **The Pi only calls out.** No ports are opened at home, and it never holds database credentials. A signed request can add rows, and nothing else.
- **Visitors read static JSON files.** Traffic doesn't touch the database, so cheap hosting can cope.
- **Raw audio is never public.**

## Measurement

Audio runs at 48 kHz through a 2 s Hann FFT once a second, giving 0.5 Hz resolution. Levels are dB re full scale, where a full-scale sine reads 0 dB, plus `spl_offset_db` if that has been measured. A mic's calibration file corrects its frequency response.

Per minute, the logger keeps:

| Figure | Use |
|---|---|
| Hum-band L90, L50, Leq, Lmax | L90 ignores passing sounds, so it best reflects the hum |
| Wide-band L90 and Leq | Sounds outside the hum |
| Sub-band L50 | Experimental wind flag |
| Each tone group's peak, excess and frequency | Fingerprint |
| Median spectrum (kept only on the Pi) | Learning and re-analysis |

Figures are unweighted. A-weighting would discount the low frequencies this project exists to measure.

## Is the source running?

**A tone is present** in a minute when its group's peak in the median spectrum is at least `excess_db` above the stopped baseline and at least `prominence_db` above neighbouring frequencies. Both are needed: excess alone fires when everything gets louder, and prominence alone fired on a dead mic.

**A minute counts** when at least `min_groups` tone groups are present. Individual tones drift over weeks, so the rule relies on the set rather than any one tone.

**The state** comes from the trailing `window_minutes`:

- ON when at least `on_fraction` of minutes count
- OFF when at most `off_fraction` count, and the level is also `off_level_drop_db` under its median over the last `reference_days`
- Otherwise it holds its last state
- UNKNOWN with too little data, no baseline, or just after a fault

Level alone doesn't work. At the development site, a low-load night was only about 1.5 dB above a stop, while the tone counts differed clearly (about 3 groups against about 0).

### Evidence so far

Tested at one site on about 49 days of outdoor data, using a previous mic and pipeline:

| Measure | Result |
|---|---|
| Confirmed stop minutes wrongly showing ON | 8.1% |
| Clearly running minutes showing OFF | 0.11% |
| Typical delay in spotting a change | 7-21 minutes |

**These figures don't fully carry over.** They come from replaying this detector over tone presence logged by the old pipeline, which used a different mic and a fixed 14 dB threshold. Other limits:

- Only five confirmed stops, and two of those were found by the detector itself
- The new per-minute tone measure, and its `excess_db` and `prominence_db` defaults, have not been tested against a real stop
- It has not been tried at any other site, or on any other type of source

So every OFF is marked `provisional` until `detector.validated` is set by someone who has checked it against stops they witnessed themselves.

### Sites with no recorded stop

`interim-baseline` estimates a baseline with the source running: for each frequency, the floor either side of it (about 40 Hz wide), at its 10th percentile across the minutes used. A tone must then stand clear of that floor, so `excess` and `prominence` measure nearly the same thing. This is the baseline-free test that fired on a dead mic, so it is weaker than a real baseline. It is limited in these ways:

- ON and OFF are always `provisional`, and the page says so
- `excess` is published as null, because there is no stopped level to compare with
- The below-baseline fault allows an extra 10 dB, as a real stop sits under the running level this baseline holds. A dead mic should still fall below that
- It has not been tested against a real stop

`find-stops` looks for candidates in the saved spectra: at most one tone group standing out for 20 minutes or more, with the level at least 3 dB below the two hours either side. A stretch with no level drop is `weak`, and a drop of 20 dB or more is `check_mic`. Run over the five weeks of archived readings from the development site, which were scored by an older tone measure, it marked six stretches `likely`: both confirmed stops, two more suspected stops, and two mornings when clips showed the source still running. So a person has to judge each one before it becomes a baseline.

## Mic faults

A dead mic and a stopped source look alike: no tones, low level. At the development site, a rain-damaged mic read about 28 dB below a real stop. So every minute is checked for no audio, digital silence, clipping, a level under `health.floor_db`, or a level far below the stopped baseline. Faulty minutes report `fault`, are never counted as OFF, and clear the detector's memory of the source being off.

## Mic periods

Changing the mic, its position, housing or gain makes old levels incomparable. Each row records its `mic_period`, a baseline only applies to the period it came from, and public records only compare within the current period.

## Reliability

- **SQLite in WAL mode**, because appended CSVs were corrupted by power cuts in the earlier system.
- **Uploads resend until acknowledged.** The server treats repeats as updates.
- **Devices are found by name**, because USB card numbers change between boots. If audio stops, the logger reopens the device and reports `no_audio` meanwhile.
- **Nothing is logged before NTP sync.** A Pi has no battery clock and boots with a stale time.

## Server

| Part | Job |
|---|---|
| `api/ingest.php` | Checks the signature, stores minutes and events, writes `live.json` |
| `api/clip.php` | Stores a FLAC clip outside the web root |
| `cron/rollup.php` | Builds runs and 15-minute blocks, writes `history.json`, deletes old clips |
| `review/` | Password-protected page to listen to and label events |

Signatures are HMAC-SHA256 over the timestamp and the body's hash, and requests over 5 minutes old are refused.

**Runs** are unbroken stretches of one state. Gaps up to `max_gap_minutes` are ignored. `ended_by` records what ended each run, so a run cut short by a monitoring gap (`data_gap`) or a mic change (`new_mic`) isn't presented as the source stopping.

**The record** is the loudest clock-aligned 15-minute block in the current mic period, measured by the median of its one-minute L90s. It needs at least 12 logged minutes, all ON, with no wind flag.

## Public pages

`public/index.html` is a static page that reads `data/live.json`, `data/history.json` and `site.json`. It has no cookies, analytics or third-party requests. Add `?demo=on`, `off`, `unknown`, `fault`, `stale` or `none` to preview each state with sample data, which is always bannered.

**Headlines** say only what's known. A fault or a stale reading hides the figures and states that it says nothing about the source.

**Sound** is generated in the browser, from the tones present in a reading. Ratios between tones and background follow the measured levels. No harmonics are added, and playback can't reproduce real loudness. The background is a band-limited noise at the measured level, with synthesised birdsong added for scale: the birdsong is an illustration, not a measurement.

**Set `source_label`** in `site.json` to how the page should refer to the source, such as "the factory". Think about what that reveals, and whether you want to name anyone.

## Other sounds

Sustained rises in the wide band or hum band become events. They are only candidates: at this level a lorry, a yard machine and a plane look alike, so a person labels them. Clips are off by default.

## Privacy

Clips can capture voices. They're off by default, down-sampled, kept out of the web root, served only after login and deleted after `retention_days`. The public files only carry fields named in `clean_live()` and `build_history()`, so anything added to the logger's data stays private unless it's added there.

## Open questions

- Sources with no stops: is a reference recording from a similar quiet location good enough?
- The wind flag has never been tested against measured wind.
- `off_level_drop_db` and the tone thresholds may well need changing per site.
- Other mains frequencies and wider bands are configurable, but untested.
