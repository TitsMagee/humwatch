<?php
// Login and session handling for the private review page.

declare(strict_types=1);

const LOGIN_MAX_ATTEMPTS = 5;
const LOGIN_WINDOW_SECONDS = 900;

function review_session_start(): void
{
    session_set_cookie_params([
        'lifetime' => 0,
        'path' => '/',
        'secure' => !empty($_SERVER['HTTPS']) && $_SERVER['HTTPS'] !== 'off',
        'httponly' => true,
        'samesite' => 'Strict',
    ]);
    session_name('humwatch_review');
    session_start();
    if (empty($_SESSION['csrf'])) {
        $_SESSION['csrf'] = bin2hex(random_bytes(32));
    }
}

function review_headers(): void
{
    header('X-Robots-Tag: noindex, nofollow');
    header('X-Content-Type-Options: nosniff');
    header('X-Frame-Options: DENY');
    header('Referrer-Policy: no-referrer');
    header('Cache-Control: no-store');
}

function review_logged_in(): bool
{
    return ($_SESSION['reviewer'] ?? false) === true;
}

function csrf_ok(): bool
{
    return is_string($_POST['csrf'] ?? null) && hash_equals($_SESSION['csrf'], $_POST['csrf']);
}

// Returns true on success. Failed tries are limited per address, so guessing is slow.
function review_login(PDO $db, array $config, string $password, string $address, int $now): bool
{
    $who = hash('sha256', $address);
    $count = $db->prepare('SELECT COUNT(*) FROM login_attempts WHERE who = ? AND t > ?');
    $count->execute([$who, $now - LOGIN_WINDOW_SECONDS]);
    if ((int) $count->fetchColumn() >= LOGIN_MAX_ATTEMPTS) {
        return false;
    }
    $hash = (string) $config['review_password_hash'];
    if ($hash !== '' && password_verify($password, $hash)) {
        session_regenerate_id(true);
        $_SESSION['reviewer'] = true;
        return true;
    }
    $db->prepare('INSERT INTO login_attempts (who, t) VALUES (?, ?)')->execute([$who, $now]);
    return false;
}

function pending_events(PDO $db, int $limit = 50): array
{
    $query = $db->prepare(
        'SELECT id, t, kind, band_db, wide_db, clip_file FROM events
         WHERE reviewed_at IS NULL ORDER BY t DESC LIMIT ' . max(1, min($limit, 200))
    );
    $query->execute();
    return $query->fetchAll();
}

function label_event(PDO $db, array $config, string $id, string $label, string $note, int $now): bool
{
    if (!in_array($label, $config['event_labels'], true)) {
        return false;
    }
    $update = $db->prepare('UPDATE events SET label = ?, note = ?, reviewed_at = ? WHERE id = ?');
    $update->execute([$label, mb_substr($note, 0, 255), $now, $id]);
    return $update->rowCount() > 0;
}
