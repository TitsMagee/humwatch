# HumWatch

A persistent low-frequency hum is hard to prove and hard to describe. HumWatch logs one around the clock with a Raspberry Pi and a USB measurement mic, works out when its source is running, and publishes the results to a website on ordinary cheap web hosting.

It was built by a resident living beside an industrial site. It may also help people near other constant sources of hum, such as data centres, but it has only been tested at that one site so far. See [What it can't tell you](#what-it-cant-tell-you).

**Status: early.** The logger and server code exist, but neither has run end to end on real hardware or hosting yet. The public page runs on sample data, and its sounds haven't yet been checked by ear on real speakers.

## What it does

- Measures the hum band every second, and each tone in the source's fingerprint every minute
- Tells a running source from a stopped one, using tones plus level
- Reports a faulty mic as a fault rather than as a quiet source
- Queues data on the Pi, so an internet outage loses nothing
- Optionally saves short clips of other sounds for a person to judge, behind a login

## What you need

- A Raspberry Pi 3B or newer
- A USB measurement mic with no automatic gain. Automatic gain compresses the very differences you're trying to measure.
- A weatherproof outdoor set-up for the mic, and somewhere dry for the Pi
- Web hosting with PHP 8.1+, MySQL or MariaDB and cron jobs

## Getting started

### Logger

```sh
cd logger
python3 -m venv venv
venv/bin/pip install -e .
cp config.example.json config.json   # then edit it
venv/bin/humwatch list-devices
venv/bin/humwatch check
venv/bin/humwatch run
```

`logger/deploy/humwatch.service` runs it as a service.

### Server

1. Create a database and load `server/schema.sql` into it.
2. Copy everything in `server/` except `public/` to a private folder called `humwatch` in your home directory, outside the web root.
3. Copy `server/public/` into your web root (`public_html` on cPanel).
4. Copy `site.example.json` to `site.json` in your web root and fill it in.
5. Copy `config.example.php` to `config.php` in the private folder and fill it in. Set `public_data_dir` to the `data` folder in your web root.
6. Add a cron job every 5 minutes: `php /home/ACCOUNT/humwatch/cron/rollup.php`

To preview the page without any data, add `?demo=on` to its address. Replace `on` with `off`, `unknown`, `fault`, `stale` or `none` to see the other states.

### Tests

```sh
cd logger && venv/bin/pip install -e ".[dev]" && venv/bin/pytest
node --test server/tests/format.test.mjs
```

`server/tests/run.php` tests the server against an empty database. It wipes its tables, so it only runs when the database name contains `test`.

### Teaching it your site

The tones and baseline are specific to each site, and you need at least one stop you know about:

1. Log for a few days, noting when the source stops. Your own observation is what makes this trustworthy.
2. Write the stops in a CSV, one `start,end` per line, such as `2026-11-20T21:25,2026-11-20T22:13`.
3. Run `humwatch --config config.json baseline --stops stops.csv`, then `learn --stops stops.csv`, and add the tone groups to `config.json`.
4. After more confirmed stops, run `validate --stops stops.csv` to see how well the detector does. Only then set `detector.validated` to true.

You don't have to be there when a stop happens. Spectra are saved every minute for 120 days, so a baseline can be built later. `humwatch --config config.json find-stops --out stops.csv` lists stretches where the tones vanished and the level fell. Treat these as candidates and check them first: at the development site it found every confirmed stop, but also flagged two mornings when the source was still running.

Until there is a stop, `humwatch --config config.json interim-baseline` estimates a stand-in from the frequencies either side of each tone, using at least an hour of readings (a day or more is better). Restart the logger to use it. Readings are then marked provisional, the page says it is going by tones alone, and no level is given above the background. Leave out any tone that might come from something else, such as a heat pump, because this baseline can't rule it out. A baseline from real stops replaces it.

### Adding older readings

If you logged readings before using HumWatch, convert them to a file with one JSON row per minute, in the same form as the logger's minute rows, under their own `mic_period`. Then send them, before live logging starts, because the server rolls minutes up in order:

```sh
humwatch --config config.json send-archive old-readings.jsonl
```

If it stops partway, it tells you the `--skip` value to resume from. Add a `history_note` to `site.json` saying where the readings came from. Rows must be under 400 days old.

## What it can't tell you

Read this before publishing anything:

- **Levels aren't dB(A) and aren't certified.** They are unweighted, and uncalibrated unless you have measured the mic's offset. They are no substitute for a professional survey.
- **It doesn't work out what caused a sound.** It finds tones that come and go together. You have to establish where they come from.
- **A source that rarely stops is a problem.** The proper baseline needs a stop, which a data centre may never give you. `interim-baseline` is a stand-in (see below), but it is less well tested and cannot say how much louder the source makes things.
- **It says nothing about health effects.** Don't let your site imply it does.

## Publishing responsibly

Laws on defamation, privacy and recording differ by country, and this isn't legal advice. As a minimum:

- Publish what you measured, not who is to blame.
- Audio clips can capture voices, which may be personal data and may be restricted where you live. Clips are off by default. If you turn them on, keep them private and short-lived, and take advice first.
- Check what your site reveals about your address, including the domain's registration details.

## Licence

[MIT](LICENSE)
