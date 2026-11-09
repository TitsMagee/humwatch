<?php
// Turns minutes into runs, 15 minute blocks, records and the public history file.

declare(strict_types=1);

const QUARTER_SECONDS = 900;
const QUARTER_MIN_MINUTES = 12;
const RECENT_DAYS = 30;
const MONITORING_STOPPED_SECONDS = 3600;

function meta_get(PDO $db, string $key, string $default = ''): string
{
    $query = $db->prepare('SELECT v FROM meta WHERE k = ?');
    $query->execute([$key]);
    $value = $query->fetchColumn();
    return $value === false ? $default : (string) $value;
}

function meta_set(PDO $db, string $key, string $value): void
{
    $db->prepare('INSERT INTO meta (k, v) VALUES (?, ?) ON DUPLICATE KEY UPDATE v = VALUES(v)')
        ->execute([$key, $value]);
}

function median_of(array $values): ?float
{
    if (!$values) {
        return null;
    }
    sort($values);
    $count = count($values);
    $middle = intdiv($count, 2);
    return $count % 2 ? (float) $values[$middle] : ($values[$middle - 1] + $values[$middle]) / 2.0;
}

// Pure run logic: folds one minute into the open run. Returns [closedRun|null, openRun].
function next_run(?array $run, array $minute, int $maxGapMinutes): array
{
    $t = (int) $minute['t'];
    $provisional = (int) $minute['provisional'];
    if ($run === null) {
        return [null, new_run($minute)];
    }
    $gap = $t - (int) $run['end_t'];
    $sameMic = $run['mic_period'] === $minute['mic_period'];
    $tooLong = $gap > 60 * ($maxGapMinutes + 1);
    if ($sameMic && !$tooLong && $run['state'] === $minute['state']) {
        $run['end_t'] = $t;
        $run['provisional'] = max((int) $run['provisional'], $provisional);
        return [null, $run];
    }
    if (!$sameMic) {
        $run['ended_by'] = 'new_mic';
    } elseif ($tooLong) {
        $run['ended_by'] = 'data_gap';
    } else {
        $run['ended_by'] = $minute['state'];
    }
    return [$run, new_run($minute)];
}

function new_run(array $minute): array
{
    return [
        'start_t' => (int) $minute['t'],
        'end_t' => (int) $minute['t'],
        'state' => $minute['state'],
        'mic_period' => $minute['mic_period'],
        'provisional' => (int) $minute['provisional'],
        'ended_by' => null,
    ];
}

function save_run(PDO $db, array $run): void
{
    $db->prepare(
        'INSERT INTO runs (start_t, end_t, state, mic_period, provisional, ended_by) VALUES (?,?,?,?,?,?)
         ON DUPLICATE KEY UPDATE end_t=VALUES(end_t), provisional=VALUES(provisional), ended_by=VALUES(ended_by)'
    )->execute([$run['start_t'], $run['end_t'], $run['state'], $run['mic_period'], $run['provisional'], $run['ended_by']]);
}

function update_runs(PDO $db, array $config): void
{
    $cursor = (int) meta_get($db, 'runs_cursor', '0');
    $open = $db->query('SELECT * FROM runs WHERE ended_by IS NULL ORDER BY start_t DESC LIMIT 1')->fetch();
    $run = $open === false ? null : $open;

    $query = $db->prepare('SELECT t, state, provisional, mic_period FROM minutes WHERE t > ? ORDER BY t');
    $query->execute([$cursor]);
    $db->beginTransaction();
    foreach ($query->fetchAll() as $minute) {
        [$closed, $run] = next_run($run, $minute, (int) $config['max_gap_minutes']);
        if ($closed !== null) {
            save_run($db, $closed);
        }
        $cursor = (int) $minute['t'];
    }
    if ($run !== null) {
        save_run($db, $run);
    }
    meta_set($db, 'runs_cursor', (string) $cursor);
    $db->commit();
}

function update_quarters(PDO $db, int $now): void
{
    $cursor = (int) meta_get($db, 'quarters_cursor', '0');
    // Minutes arrive in order, so a block is only finished once a later minute has
    // arrived. Going by the clock alone would skip a backlog sent after an outage.
    $latest = (int) $db->query('SELECT COALESCE(MAX(t), 0) FROM minutes')->fetchColumn();
    $complete = (int) (floor(min($now, $latest) / QUARTER_SECONDS) * QUARTER_SECONDS);
    $query = $db->prepare(
        'SELECT t, mic_period, state, l90, excess, wind FROM minutes WHERE t >= ? AND t < ? ORDER BY t'
    );
    $query->execute([$cursor, $complete]);

    $blocks = [];
    foreach ($query->fetchAll() as $minute) {
        $blocks[(int) (floor((int) $minute['t'] / QUARTER_SECONDS) * QUARTER_SECONDS)][] = $minute;
    }
    $insert = $db->prepare(
        'INSERT INTO quarters (t, mic_period, minutes_seen, minutes_on, any_wind, l90, excess) VALUES (?,?,?,?,?,?,?)
         ON DUPLICATE KEY UPDATE mic_period=VALUES(mic_period), minutes_seen=VALUES(minutes_seen),
            minutes_on=VALUES(minutes_on), any_wind=VALUES(any_wind), l90=VALUES(l90), excess=VALUES(excess)'
    );
    $db->beginTransaction();
    foreach ($blocks as $t => $rows) {
        $on = array_filter($rows, fn($row) => $row['state'] === 'on');
        $levels = array_map(fn($row) => (float) $row['l90'], array_filter($on, fn($row) => $row['l90'] !== null));
        $excess = array_map(fn($row) => (float) $row['excess'], array_filter($on, fn($row) => $row['excess'] !== null));
        $wind = array_filter($rows, fn($row) => (int) $row['wind'] === 1);
        $insert->execute([
            $t, $rows[0]['mic_period'], count($rows), count($on), $wind ? 1 : 0, median_of($levels), median_of($excess),
        ]);
    }
    meta_set($db, 'quarters_cursor', (string) max($cursor, $complete));
    $db->commit();
}

// Public summary for the current mic period only: levels from other periods don't compare.
function build_history(PDO $db, int $now): array
{
    $current = $db->query('SELECT id, first_t, last_t, calibrated FROM mic_periods ORDER BY last_t DESC LIMIT 1')->fetch();
    if ($current === false) {
        return ['generated_at' => $now, 'current_run' => null, 'longest_run' => null, 'record' => null, 'recent' => null];
    }
    $history = period_summary($db, $current, $now);
    $history['earlier'] = earlier_summary($db, $current, $now);
    return $history;
}

// Until the current mic has a stopped level to compare with, it can't produce a loudest
// or typical reading, so the page keeps showing the previous mic's figures, labelled.
function earlier_summary(PDO $db, array $current, int $now): ?array
{
    $measured = $db->prepare('SELECT 1 FROM minutes WHERE mic_period = ? AND excess IS NOT NULL LIMIT 1');
    $measured->execute([$current['id']]);
    if ($measured->fetchColumn() !== false) {
        return null;
    }
    $previous = $db->prepare(
        'SELECT id, first_t, last_t, calibrated FROM mic_periods WHERE id <> ? AND last_t < ? ORDER BY last_t DESC LIMIT 1'
    );
    $previous->execute([$current['id'], $current['last_t']]);
    $row = $previous->fetch();
    return $row === false ? null : period_summary($db, $row, $now);
}

function period_summary(PDO $db, array $current, int $now): array
{
    $period = $current['id'];
    $lastT = (int) $current['last_t'];
    // Past this, an open run can't honestly be called current
    $stopped = $now - $lastT > MONITORING_STOPPED_SECONDS;

    $latest = $db->prepare('SELECT start_t, end_t, state, provisional FROM runs WHERE mic_period = ? AND ended_by IS NULL ORDER BY start_t DESC LIMIT 1');
    $latest->execute([$period]);
    $open = $latest->fetch();

    // A run that began when monitoring did may really have started earlier
    $afterGap = false;
    if ($open !== false) {
        $previous = $db->prepare('SELECT ended_by FROM runs WHERE mic_period = ? AND start_t < ? ORDER BY start_t DESC LIMIT 1');
        $previous->execute([$period, $open['start_t']]);
        $endedBy = $previous->fetchColumn();
        $afterGap = $endedBy === false || in_array($endedBy, ['data_gap', 'new_mic', 'fault', 'unknown'], true);
    }

    $longest = $db->prepare(
        "SELECT start_t, end_t, ended_by FROM runs WHERE mic_period = ? AND state = 'on'
         ORDER BY (end_t - start_t) DESC LIMIT 1"
    );
    $longest->execute([$period]);
    $long = $longest->fetch();

    // The loudest full block, source on throughout, without wind
    $records = $db->prepare(
        'SELECT t, l90, excess FROM quarters
         WHERE mic_period = ? AND minutes_on >= ? AND minutes_on = minutes_seen AND any_wind = 0 AND l90 IS NOT NULL
         ORDER BY l90 DESC LIMIT 1'
    );
    $records->execute([$period, QUARTER_MIN_MINUTES]);
    $record = $records->fetch();
    if ($record !== false) {
        $tones = $db->prepare('SELECT tones FROM minutes WHERE t >= ? AND t < ? AND tones IS NOT NULL ORDER BY t LIMIT 1 OFFSET 7');
        $tones->execute([$record['t'], $record['t'] + QUARTER_SECONDS]);
        // Imported readings have no wind figure, and the page shouldn't imply otherwise
        $wind = $db->prepare('SELECT COUNT(*) FROM minutes WHERE t >= ? AND t < ? AND wind IS NOT NULL');
        $wind->execute([$record['t'], $record['t'] + QUARTER_SECONDS]);
        $record = [
            't' => (int) $record['t'],
            'l90' => (float) $record['l90'],
            'excess' => $record['excess'] === null ? null : (float) $record['excess'],
            'wind_checked' => (int) $wind->fetchColumn() > 0,
            'tones' => json_decode((string) $tones->fetchColumn(), true) ?: [],
        ];
    } else {
        $record = null;
    }

    // Counted back from the last reading, so a period that has ended keeps its figures
    $since = max($lastT - RECENT_DAYS * 86400, (int) $current['first_t']);
    $counts = $db->prepare(
        "SELECT state, COUNT(*) AS n FROM minutes WHERE mic_period = ? AND t >= ? GROUP BY state"
    );
    $counts->execute([$period, $since]);
    $byState = array_column($counts->fetchAll(), 'n', 'state');
    $known = (int) ($byState['on'] ?? 0) + (int) ($byState['off'] ?? 0);
    $typical = typical_reading($db, $period, $since, $lastT);

    return [
        'generated_at' => $now,
        'calibrated' => (int) $current['calibrated'] === 1,
        'monitoring_since' => (int) $current['first_t'],
        'last_t' => $lastT,
        'monitoring_stopped' => $stopped,
        'current_run' => $open === false || $stopped ? null : [
            'state' => $open['state'],
            'provisional' => (int) $open['provisional'] === 1,
            'after_gap' => $afterGap,
            'start_t' => (int) $open['start_t'],
            'end_t' => (int) $open['end_t'],
        ],
        'longest_run' => $long === false ? null : [
            'start_t' => (int) $long['start_t'],
            'end_t' => (int) $long['end_t'],
            // A run still open when the readings stopped was cut off, not finished
            'ended_by' => $long['ended_by'] ?? ($stopped ? 'data_gap' : null),
        ],
        'record' => $record,
        'typical' => $typical,
        'recent' => [
            'since' => $since,
            'until' => $lastT,
            'on_percent' => $known ? round(100 * (int) ($byState['on'] ?? 0) / $known, 1) : null,
            'minutes_on' => (int) ($byState['on'] ?? 0),
            'minutes_off' => (int) ($byState['off'] ?? 0),
            'minutes_unknown' => (int) ($byState['unknown'] ?? 0) + (int) ($byState['fault'] ?? 0),
        ],
    ];
}

// Same test as tonesExplainLevel() in the page: tones plus background must add up to the level.
function tones_explain_level(array $tones, float $baseDb, float $levelDb, float $toleranceDb = 3.0): bool
{
    $power = 10 ** ($baseDb / 10);
    foreach ($tones as $tone) {
        if (($tone['present'] ?? false) === true && is_numeric($tone['db'] ?? null)) {
            $power += 10 ** ((float) $tone['db'] / 10);
        }
    }
    return abs(10 * log10($power) - $levelDb) <= $toleranceDb;
}

// A running minute close to the usual level, whose tones account for that level, so the
// page can play a fair example when there is no live reading.
function typical_reading(PDO $db, string $period, int $since, int $until): ?array
{
    $where = "mic_period = ? AND state = 'on' AND t BETWEEN ? AND ? AND excess IS NOT NULL AND l90 IS NOT NULL";
    $count = $db->prepare("SELECT COUNT(*) FROM minutes WHERE $where");
    $count->execute([$period, $since, $until]);
    $total = (int) $count->fetchColumn();
    if ($total === 0) {
        return null;
    }
    $middle = $db->prepare("SELECT excess FROM minutes WHERE $where ORDER BY excess LIMIT 1 OFFSET " . intdiv($total, 2));
    $middle->execute([$period, $since, $until]);
    $usual = (float) $middle->fetchColumn();

    $near = $db->prepare(
        "SELECT t, l90, excess, tones FROM minutes WHERE $where AND groups_present >= 2
         AND excess BETWEEN ? AND ? ORDER BY t DESC LIMIT 500"
    );
    $near->execute([$period, $since, $until, $usual - 0.5, $usual + 0.5]);
    foreach ($near->fetchAll() as $minute) {
        $tones = json_decode((string) $minute['tones'], true) ?: [];
        $base = (float) $minute['l90'] - (float) $minute['excess'];
        if ($tones && tones_explain_level($tones, $base, (float) $minute['l90'])) {
            return [
                't' => (int) $minute['t'],
                'l90' => (float) $minute['l90'],
                'excess' => (float) $minute['excess'],
                'usual_excess' => round($usual, 1),
                'tones' => $tones,
            ];
        }
    }
    return null;
}

function delete_old_clips(PDO $db, array $config, int $now): int
{
    $cutoff = $now - (int) $config['clip_retention_days'] * 86400;
    $query = $db->prepare('SELECT id, clip_file FROM events WHERE clip_file IS NOT NULL AND t < ?');
    $query->execute([$cutoff]);
    $clear = $db->prepare('UPDATE events SET clip_file = NULL WHERE id = ?');
    $deleted = 0;
    foreach ($query->fetchAll() as $event) {
        $path = $config['clips_dir'] . '/' . basename($event['clip_file']);
        if (is_file($path)) {
            unlink($path);
            $deleted++;
        }
        $clear->execute([$event['id']]);
    }
    return $deleted;
}

function run_rollup(PDO $db, array $config, int $now): array
{
    update_runs($db, $config);
    update_quarters($db, $now);
    $history = build_history($db, $now);
    write_public_json($config['public_data_dir'], 'history.json', $history);
    delete_old_clips($db, $config, $now);
    $db->prepare('DELETE FROM login_attempts WHERE t < ?')->execute([$now - 86400]);
    return $history;
}
