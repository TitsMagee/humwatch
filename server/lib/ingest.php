<?php
// Accepts signed data from the logger. Plain functions, so tests can call them directly.

declare(strict_types=1);

const INGEST_MAX_BYTES = 4194304;
const CLIP_MAX_BYTES = 8388608;
const CLOCK_SKEW_SECONDS = 300;
const STATES = ['on', 'off', 'unknown', 'fault'];
const FAULTS = ['no_audio', 'digital_silence', 'below_floor', 'below_baseline', 'clipping'];
const EVENT_KINDS = ['wide', 'band'];

function signature_valid(string $secret, string $timestamp, string $signature, string $body, int $now): bool
{
    if ($secret === '' || !ctype_digit($timestamp) || abs($now - (int) $timestamp) > CLOCK_SKEW_SECONDS) {
        return false;
    }
    $expected = hash_hmac('sha256', $timestamp . "\n" . hash('sha256', $body), $secret);
    return hash_equals($expected, $signature);
}

function number_or_null($value): ?float
{
    return (is_int($value) || is_float($value)) && is_finite((float) $value) ? (float) $value : null;
}

function valid_mic_period($value): bool
{
    return is_string($value) && preg_match('/^[A-Za-z0-9._-]{1,64}$/', $value) === 1;
}

function valid_time($value, int $now): bool
{
    // Backlogs can be long after an outage, but not from another decade
    return is_int($value) && $value > $now - 400 * 86400 && $value < $now + CLOCK_SKEW_SECONDS;
}

function clean_tones($tones): array
{
    $clean = [];
    foreach (is_array($tones) ? array_slice($tones, 0, 64) : [] as $tone) {
        if (!is_array($tone) || number_or_null($tone['hz'] ?? null) === null) {
            continue;
        }
        $clean[] = [
            'low' => number_or_null($tone['low'] ?? null),
            'high' => number_or_null($tone['high'] ?? null),
            'hz' => number_or_null($tone['hz']),
            'db' => number_or_null($tone['db'] ?? null),
            'excess' => number_or_null($tone['excess'] ?? null),
            'present' => ($tone['present'] ?? false) === true,
        ];
    }
    return $clean;
}

function store_minute(PDOStatement $insert, array $row, int $now): bool
{
    $fault = $row['fault'] ?? null;
    if (
        !valid_time($row['t'] ?? null, $now)
        || !valid_mic_period($row['mic_period'] ?? null)
        || !in_array($row['state'] ?? null, STATES, true)
        || ($fault !== null && !in_array($fault, FAULTS, true))
    ) {
        return false;
    }
    $groups = $row['groups_present'] ?? null;
    $wind = $row['wind'] ?? null;
    $insert->execute([
        $row['t'],
        $row['mic_period'],
        min(max((int) ($row['n_frames'] ?? 0), 0), 65535),
        $row['state'],
        ($row['provisional'] ?? false) === true ? 1 : 0,
        $fault,
        ($row['calibrated'] ?? false) === true ? 1 : 0,
        number_or_null($row['l90'] ?? null),
        number_or_null($row['l50'] ?? null),
        number_or_null($row['leq'] ?? null),
        number_or_null($row['lmax'] ?? null),
        number_or_null($row['wide_l90'] ?? null),
        number_or_null($row['wide_leq'] ?? null),
        number_or_null($row['sub_l50'] ?? null),
        number_or_null($row['excess'] ?? null),
        $wind === null ? null : ($wind === true ? 1 : 0),
        is_int($groups) && $groups >= 0 && $groups <= 255 ? $groups : null,
        json_encode(clean_tones($row['tones'] ?? [])),
    ]);
    return true;
}

function store_event(PDOStatement $insert, array $event, int $now): bool
{
    if (
        !valid_event_id($event['id'] ?? null)
        || !valid_time($event['t'] ?? null, $now)
        || !in_array($event['kind'] ?? null, EVENT_KINDS, true)
        || !valid_mic_period($event['mic_period'] ?? null)
    ) {
        return false;
    }
    $insert->execute([
        $event['id'],
        $event['t'],
        $event['kind'],
        $event['mic_period'],
        number_or_null($event['band_db'] ?? null),
        number_or_null($event['wide_db'] ?? null),
        ($event['has_clip'] ?? false) === true ? 1 : 0,
    ]);
    return true;
}

function valid_event_id($id): bool
{
    return is_string($id) && preg_match('/^[0-9]{1,12}-(wide|band)$/', $id) === 1;
}

// Returns [status, body]. Repeats are harmless: a batch whose reply was lost is simply sent again.
function handle_ingest(PDO $db, array $config, string $body, string $timestamp, string $signature, int $now): array
{
    if (strlen($body) > INGEST_MAX_BYTES) {
        return [413, ['error' => 'too large']];
    }
    if (!signature_valid($config['ingest_secret'], $timestamp, $signature, $body, $now)) {
        return [401, ['error' => 'bad signature']];
    }
    $data = json_decode($body, true);
    if (!is_array($data)) {
        return [400, ['error' => 'bad json']];
    }

    $minutes = is_array($data['minutes'] ?? null) ? $data['minutes'] : [];
    $events = is_array($data['events'] ?? null) ? $data['events'] : [];
    $rejected = 0;

    $db->beginTransaction();
    try {
        $insertMinute = $db->prepare(
            'INSERT INTO minutes (t, mic_period, n_frames, state, provisional, fault, calibrated,
                l90, l50, leq, lmax, wide_l90, wide_leq, sub_l50, excess, wind, groups_present, tones)
             VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
             ON DUPLICATE KEY UPDATE mic_period=VALUES(mic_period), n_frames=VALUES(n_frames),
                state=VALUES(state), provisional=VALUES(provisional), fault=VALUES(fault),
                calibrated=VALUES(calibrated), l90=VALUES(l90), l50=VALUES(l50), leq=VALUES(leq),
                lmax=VALUES(lmax), wide_l90=VALUES(wide_l90), wide_leq=VALUES(wide_leq),
                sub_l50=VALUES(sub_l50), excess=VALUES(excess), wind=VALUES(wind),
                groups_present=VALUES(groups_present), tones=VALUES(tones)'
        );
        $insertEvent = $db->prepare(
            'INSERT INTO events (id, t, kind, mic_period, band_db, wide_db, has_clip)
             VALUES (?,?,?,?,?,?,?)
             ON DUPLICATE KEY UPDATE has_clip=VALUES(has_clip)'
        );
        $period = $db->prepare(
            'INSERT INTO mic_periods (id, first_t, last_t, calibrated) VALUES (?,?,?,?)
             ON DUPLICATE KEY UPDATE first_t=LEAST(first_t, VALUES(first_t)),
                last_t=GREATEST(last_t, VALUES(last_t)), calibrated=VALUES(calibrated)'
        );
        foreach ($minutes as $row) {
            if (!is_array($row) || !store_minute($insertMinute, $row, $now)) {
                $rejected++;
                continue;
            }
            $period->execute([
                $row['mic_period'], $row['t'], $row['t'], ($row['calibrated'] ?? false) === true ? 1 : 0,
            ]);
        }
        foreach ($events as $event) {
            if (!is_array($event) || !store_event($insertEvent, $event, $now)) {
                $rejected++;
            }
        }
        $db->commit();
    } catch (Throwable $error) {
        $db->rollBack();
        throw $error;
    }

    $live = $data['live'] ?? null;
    if (is_array($live) && valid_time($live['t'] ?? null, $now)) {
        write_public_json($config['public_data_dir'], 'live.json', clean_live($live, $now));
    }
    return [200, ['ok' => true, 'minutes' => count($minutes) - $rejected, 'rejected' => $rejected]];
}

// Only named fields ever reach the public file.
function clean_live(array $live, int $now): array
{
    $state = in_array($live['state'] ?? null, STATES, true) ? $live['state'] : 'unknown';
    $fault = $live['fault'] ?? null;
    return [
        't' => $live['t'],
        'received_at' => $now,
        'state' => $state,
        'provisional' => ($live['provisional'] ?? false) === true,
        'mic_ok' => $fault === null,
        'calibrated' => ($live['calibrated'] ?? false) === true,
        'level' => number_or_null($live['level'] ?? null),
        'l90' => number_or_null($live['l90'] ?? null),
        'excess' => number_or_null($live['excess'] ?? null),
        'tones' => clean_tones($live['tones'] ?? []),
    ];
}

function handle_clip(PDO $db, array $config, string $body, string $timestamp, string $signature, string $eventId, int $now): array
{
    if (strlen($body) > CLIP_MAX_BYTES) {
        return [413, ['error' => 'too large']];
    }
    if (!signature_valid($config['ingest_secret'], $timestamp, $signature, $body, $now)) {
        return [401, ['error' => 'bad signature']];
    }
    if (!valid_event_id($eventId) || substr($body, 0, 4) !== 'fLaC') {
        return [400, ['error' => 'bad clip']];
    }
    $find = $db->prepare('SELECT id FROM events WHERE id = ?');
    $find->execute([$eventId]);
    if ($find->fetch() === false) {
        return [409, ['error' => 'unknown event']];
    }
    $dir = $config['clips_dir'];
    if (!is_dir($dir)) {
        mkdir($dir, 0700, true);
    }
    $name = $eventId . '.flac';
    if (file_put_contents($dir . '/' . $name, $body, LOCK_EX) === false) {
        return [500, ['error' => 'could not save']];
    }
    $db->prepare('UPDATE events SET clip_file = ?, has_clip = 1 WHERE id = ?')->execute([$name, $eventId]);
    return [200, ['ok' => true]];
}
