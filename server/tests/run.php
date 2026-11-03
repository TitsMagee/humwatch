<?php
// Tests for the server code. They need a real, EMPTY database, because they create and
// wipe the tables. Never point this at a live site's database.
//
//   HUMWATCH_TEST_DSN='mysql:host=localhost;dbname=humwatch_test;charset=utf8mb4' \
//   HUMWATCH_TEST_USER=... HUMWATCH_TEST_PASS=... php tests/run.php
declare(strict_types=1);

require dirname(__DIR__) . '/lib/bootstrap.php';
require dirname(__DIR__) . '/lib/review.php';

$dsn = getenv('HUMWATCH_TEST_DSN');
if ($dsn === false || stripos($dsn, 'test') === false) {
    fwrite(STDERR, "Set HUMWATCH_TEST_DSN to a database with 'test' in its name\n");
    exit(2);
}
$db = new PDO($dsn, (string) getenv('HUMWATCH_TEST_USER'), (string) getenv('HUMWATCH_TEST_PASS'), [
    PDO::ATTR_ERRMODE => PDO::ERRMODE_EXCEPTION,
    PDO::ATTR_DEFAULT_FETCH_MODE => PDO::FETCH_ASSOC,
    PDO::ATTR_EMULATE_PREPARES => false,
]);

$failures = 0;
function check(string $name, bool $passed): void
{
    global $failures;
    echo($passed ? 'ok    ' : 'FAIL  ') . $name . "\n";
    $failures += $passed ? 0 : 1;
}

function reset_tables(PDO $db): void
{
    foreach (['events', 'quarters', 'runs', 'minutes', 'mic_periods', 'login_attempts', 'meta'] as $table) {
        $db->exec("DROP TABLE IF EXISTS $table");
    }
    foreach (array_filter(array_map('trim', explode(';', (string) file_get_contents(dirname(__DIR__) . '/schema.sql')))) as $statement) {
        $db->exec($statement);
    }
}

$temp = sys_get_temp_dir() . '/humwatch-test-' . bin2hex(random_bytes(4));
mkdir($temp);
$config = [
    'ingest_secret' => str_repeat('s', 40),
    'review_password_hash' => password_hash('right', PASSWORD_DEFAULT),
    'clips_dir' => $temp . '/clips',
    'clip_retention_days' => 30,
    'public_data_dir' => $temp . '/data',
    'max_gap_minutes' => 5,
    'event_labels' => ['lorry', 'other'],
];

function signed(array $config, string $body, int $now): array
{
    $timestamp = (string) $now;
    return [$timestamp, hash_hmac('sha256', $timestamp . "\n" . hash('sha256', $body), $config['ingest_secret'])];
}

function send(PDO $db, array $config, array $payload, int $now): array
{
    $body = (string) json_encode($payload);
    [$timestamp, $signature] = signed($config, $body, $now);
    return handle_ingest($db, $config, $body, $timestamp, $signature, $now);
}

function minute_row(int $t, string $state = 'on', array $extra = []): array
{
    return $extra + [
        't' => $t, 'mic_period' => 'mic-1', 'n_frames' => 60, 'state' => $state, 'provisional' => false,
        'fault' => null, 'calibrated' => false, 'l90' => 40.0, 'l50' => 41.0, 'leq' => 42.0, 'lmax' => 50.0,
        'wide_l90' => 30.0, 'wide_leq' => 32.0, 'sub_l50' => 20.0, 'excess' => 6.0, 'wind' => false,
        'groups_present' => 3,
        'tones' => [['low' => 66, 'high' => 68, 'hz' => 67.0, 'db' => 30.0, 'excess' => 10.0, 'present' => true]],
    ];
}

$now = 1800000000;
reset_tables($db);

// Signatures
$body = '{"minutes":[]}';
[$timestamp, $signature] = signed($config, $body, $now);
check('valid signature accepted', handle_ingest($db, $config, $body, $timestamp, $signature, $now)[0] === 200);
check('wrong signature refused', handle_ingest($db, $config, $body, $timestamp, str_repeat('0', 64), $now)[0] === 401);
check('changed body refused', handle_ingest($db, $config, $body . ' ', $timestamp, $signature, $now)[0] === 401);
check('old timestamp refused', handle_ingest($db, $config, $body, $timestamp, $signature, $now + 301)[0] === 401);
check('empty secret refuses everything', handle_ingest($db, ['ingest_secret' => ''] + $config, $body, $timestamp, hash_hmac('sha256', $timestamp . "\n" . hash('sha256', $body), ''), $now)[0] === 401);

// Ingest
$result = send($db, $config, ['minutes' => [minute_row($now - 120), minute_row($now - 60)]], $now);
check('minutes stored', $result[1]['minutes'] === 2 && (int) $db->query('SELECT COUNT(*) FROM minutes')->fetchColumn() === 2);
send($db, $config, ['minutes' => [minute_row($now - 120)]], $now);
check('a resent minute is not duplicated', (int) $db->query('SELECT COUNT(*) FROM minutes')->fetchColumn() === 2);
$result = send($db, $config, ['minutes' => [
    minute_row($now - 30, 'hacked'),
    minute_row(5),
    minute_row($now - 40, 'on', ['mic_period' => "x'; DROP TABLE minutes;--"]),
    'not a row',
]], $now);
check('bad rows rejected, not stored', $result[1]['rejected'] === 4 && (int) $db->query('SELECT COUNT(*) FROM minutes')->fetchColumn() === 2);

// Public live file only carries named fields
send($db, $config, ['live' => ['t' => $now, 'state' => 'on', 'fault' => 'below_floor', 'level' => 40.5,
    'mic_period' => 'private-note', 'secret' => 'x', 'tones' => []]], $now);
$live = json_decode((string) file_get_contents($temp . '/data/live.json'), true);
check('live file written', $live['state'] === 'on' && $live['level'] === 40.5 && $live['mic_ok'] === false);
check('live file leaks nothing extra', !isset($live['mic_period']) && !isset($live['secret']) && !isset($live['fault']));

// Run logic
reset_tables($db);
$rows = [];
$start = 1800000000 - 1800000000 % 900;
for ($i = 0; $i < 30; $i++) {
    $rows[] = minute_row($start + $i * 60);                // running 30 minutes
}
for ($i = 33; $i < 40; $i++) {
    $rows[] = minute_row($start + $i * 60);                // short gap: still the same run
}
for ($i = 40; $i < 60; $i++) {
    $rows[] = minute_row($start + $i * 60, 'off', ['provisional' => true, 'l90' => 34.0, 'excess' => 0.0, 'groups_present' => 0]);
}
for ($i = 75; $i < 95; $i++) {
    $rows[] = minute_row($start + $i * 60, 'on', ['l90' => 45.0]);  // after a long gap, louder
}
$later = $start + 95 * 60;
send($db, $config, ['minutes' => $rows], $later);
$history = run_rollup($db, $config, $later);
$runs = $db->query('SELECT state, start_t, end_t, provisional, ended_by FROM runs ORDER BY start_t')->fetchAll();
check('three runs', count($runs) === 3);
check('short gap does not break a run', $runs[0]['state'] === 'on' && (int) $runs[0]['end_t'] === $start + 39 * 60);
check('run ended by off', $runs[0]['ended_by'] === 'off');
check('off run is provisional and ended by a data gap', $runs[1]['state'] === 'off' && (int) $runs[1]['provisional'] === 1 && $runs[1]['ended_by'] === 'data_gap');
check('latest run stays open', end($runs)['ended_by'] === null);
check('history shows the current run', $history['current_run']['state'] === 'on' && $history['current_run']['start_t'] === $start + 75 * 60 && $history['current_run']['after_gap'] === true);
check('longest run found', $history['longest_run']['start_t'] === $start && $history['longest_run']['ended_by'] === 'off');
check('record is the loudest all-on block', $history['record'] !== null && $history['record']['l90'] === 45.0 && $history['record']['tones'] !== []);
check('on-percent counts known minutes', $history['recent']['minutes_off'] === 20 && $history['recent']['minutes_on'] === 57);

// Rollup again changes nothing, and a later backlog is picked up
run_rollup($db, $config, $later + 300);
check('rollup can repeat safely', count($db->query('SELECT start_t FROM runs')->fetchAll()) === count($runs));
$backlogStart = $start + 90 * 60;
$rows = [];
for ($i = 0; $i < 20; $i++) {
    $rows[] = minute_row($backlogStart + (5 + $i) * 60, 'on', ['l90' => 47.0]);
}
send($db, $config, ['minutes' => $rows], $later + 1500);
$history = run_rollup($db, $config, $later + 3000);
check('backlog after an outage reaches the record', $history['record']['l90'] === 47.0);

// Readings that have stopped: nothing is called current, and the figures stay
$stoppedAt = $start + 114 * 60 + 2 * 86400;
$history = build_history($db, $stoppedAt);
check('an open run is not called current once readings stop', $history['current_run'] === null && $history['monitoring_stopped'] === true);
check('the last run reads as cut off', $history['longest_run']['ended_by'] === 'off' || $history['longest_run']['ended_by'] === 'data_gap');
check('figures are kept after readings stop', $history['recent']['on_percent'] !== null && $history['record'] !== null);
check('wind is reported as checked when logged', $history['record']['wind_checked'] === true);
$open = $db->query("SELECT start_t FROM runs WHERE ended_by IS NULL")->fetchColumn();
check('nothing is stored differently by reading the history', $open !== false);

// A new mic starts afresh
send($db, $config, ['minutes' => [minute_row($later + 3000, 'on', ['mic_period' => 'mic-2', 'l90' => 10.0])]], $later + 3060);
$history = run_rollup($db, $config, $later + 3060);
$ended = $db->query("SELECT ended_by FROM runs WHERE mic_period='mic-1' ORDER BY start_t DESC LIMIT 1")->fetchColumn();
check('new mic ends the old run', $ended === 'new_mic');
check('history moves to the new mic', $history['current_run']['start_t'] === $later + 3000 && $history['current_run']['after_gap'] === true && $history['record'] === null);

// Clips
$flac = 'fLaC' . str_repeat('x', 100);
$event = ['id' => $now . '-wide', 't' => $now, 'kind' => 'wide', 'mic_period' => 'mic-1', 'band_db' => 40.0, 'wide_db' => 50.0, 'has_clip' => true];
[$timestamp, $signature] = signed($config, $flac, $now);
check('clip for an unknown event waits', handle_clip($db, $config, $flac, $timestamp, $signature, $event['id'], $now)[0] === 409);
send($db, $config, ['events' => [$event]], $now);
check('clip saved', handle_clip($db, $config, $flac, $timestamp, $signature, $event['id'], $now)[0] === 200 && is_file($config['clips_dir'] . '/' . $event['id'] . '.flac'));
check('path tricks refused', handle_clip($db, $config, $flac, $timestamp, $signature, '../../evil', $now)[0] === 400);
[$timestamp2, $signature2] = signed($config, 'not flac', $now);
check('non-FLAC refused', handle_clip($db, $config, 'not flac', $timestamp2, $signature2, $event['id'], $now)[0] === 400);
check('old clips deleted', delete_old_clips($db, $config, $now + 31 * 86400) === 1 && !is_file($config['clips_dir'] . '/' . $event['id'] . '.flac'));

// Review
check('wrong password refused', !review_login($db, $config, 'wrong', '1.2.3.4', $now));
for ($i = 0; $i < 5; $i++) {
    review_login($db, $config, 'wrong', '5.6.7.8', $now);
}
check('lockout blocks even the right password', !review_login($db, $config, 'right', '5.6.7.8', $now));
check('event appears for review', count(pending_events($db)) === 1);
check('unknown label refused', !label_event($db, $config, $event['id'], 'nonsense', '', $now));
check('label saved', label_event($db, $config, $event['id'], 'lorry', 'reversing', $now) && pending_events($db) === []);

echo $failures === 0 ? "All passed\n" : "$failures failed\n";
exit($failures === 0 ? 0 : 1);
