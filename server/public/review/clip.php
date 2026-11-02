<?php
// Serves a clip to a logged-in reviewer. Clips live outside the web root.
declare(strict_types=1);

$root = require dirname(__DIR__) . '/paths.php';
require $root . '/lib/bootstrap.php';
require $root . '/lib/review.php';

review_headers();
review_session_start();
if (!review_logged_in()) {
    http_response_code(403);
    exit;
}
$id = (string) ($_GET['id'] ?? '');
if (!valid_event_id($id)) {
    http_response_code(404);
    exit;
}
$config = humwatch_config();
$path = $config['clips_dir'] . '/' . $id . '.flac';
if (!is_file($path)) {
    http_response_code(404);
    exit;
}
header('Content-Type: audio/flac');
header('Content-Length: ' . filesize($path));
readfile($path);
