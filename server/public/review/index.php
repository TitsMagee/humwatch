<?php
declare(strict_types=1);

$root = require dirname(__DIR__) . '/paths.php';
require $root . '/lib/bootstrap.php';
require $root . '/lib/review.php';

review_headers();
header("Content-Security-Policy: default-src 'none'; media-src 'self'; style-src 'unsafe-inline'; form-action 'self'; base-uri 'none'");
review_session_start();

$config = humwatch_config();
$db = humwatch_db($config);
$message = '';

function h(?string $text): string
{
    return htmlspecialchars((string) $text, ENT_QUOTES, 'UTF-8');
}

if ($_SERVER['REQUEST_METHOD'] === 'POST') {
    if (!csrf_ok()) {
        http_response_code(403);
        exit('Please reload the page and try again.');
    }
    $action = $_POST['action'] ?? '';
    if ($action === 'login') {
        $address = (string) ($_SERVER['REMOTE_ADDR'] ?? '');
        if (!review_login($db, $config, (string) ($_POST['password'] ?? ''), $address, time())) {
            sleep(1);
            $message = 'That did not work. Too many tries will lock this address for 15 minutes.';
        }
    } elseif ($action === 'logout' && review_logged_in()) {
        $_SESSION = [];
        session_destroy();
        header('Location: ./');
        exit;
    } elseif ($action === 'label' && review_logged_in()) {
        $saved = label_event(
            $db,
            $config,
            (string) ($_POST['id'] ?? ''),
            (string) ($_POST['label'] ?? ''),
            (string) ($_POST['note'] ?? ''),
            time()
        );
        $message = $saved ? 'Saved.' : 'Could not save that label.';
    }
}
?>
<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Review sounds</title>
<style>
body { font: 16px/1.5 system-ui, sans-serif; max-width: 48rem; margin: 2rem auto; padding: 0 1rem; }
.event { border-top: 1px solid #ccc; padding: 1rem 0; }
audio { width: 100%; margin: .5rem 0; }
.message { font-weight: 600; }
label { display: block; margin-top: .5rem; }
</style>
</head>
<body>
<h1>Review sounds</h1>
<?php if ($message !== ''): ?><p class="message" role="status"><?= h($message) ?></p><?php endif; ?>

<?php if (!review_logged_in()): ?>
<form method="post">
  <input type="hidden" name="csrf" value="<?= h($_SESSION['csrf']) ?>">
  <input type="hidden" name="action" value="login">
  <label for="password">Password</label>
  <input id="password" type="password" name="password" required autocomplete="current-password">
  <button>Log in</button>
</form>
<?php else: ?>
<form method="post">
  <input type="hidden" name="csrf" value="<?= h($_SESSION['csrf']) ?>">
  <input type="hidden" name="action" value="logout">
  <button>Log out</button>
</form>
<?php $events = pending_events($db); ?>
<?php if (!$events): ?><p>Nothing waiting for review.</p><?php endif; ?>
<?php foreach ($events as $event): ?>
<section class="event">
  <strong><?= h(gmdate('D j M Y, H:i', (int) $event['t'])) ?> UTC</strong>
  (<?= $event['kind'] === 'wide' ? 'rise above the hum band' : 'rise in the hum band' ?>)
  <?php if ($event['clip_file'] !== null): ?>
  <audio controls preload="none" src="clip.php?id=<?= h(urlencode($event['id'])) ?>"></audio>
  <?php else: ?>
  <p>No clip for this one.</p>
  <?php endif; ?>
  <form method="post">
    <input type="hidden" name="csrf" value="<?= h($_SESSION['csrf']) ?>">
    <input type="hidden" name="action" value="label">
    <input type="hidden" name="id" value="<?= h($event['id']) ?>">
    <label for="label-<?= h($event['id']) ?>">What was it?</label>
    <select id="label-<?= h($event['id']) ?>" name="label">
      <?php foreach ($config['event_labels'] as $label): ?>
      <option value="<?= h($label) ?>"><?= h(str_replace('_', ' ', $label)) ?></option>
      <?php endforeach; ?>
    </select>
    <label for="note-<?= h($event['id']) ?>">Note (optional)</label>
    <input id="note-<?= h($event['id']) ?>" name="note" maxlength="255">
    <button>Save</button>
  </form>
</section>
<?php endforeach; ?>
<?php endif; ?>
</body>
</html>
