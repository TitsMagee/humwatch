<?php
declare(strict_types=1);

require (require dirname(__DIR__) . '/paths.php') . '/lib/bootstrap.php';

if ($_SERVER['REQUEST_METHOD'] !== 'POST') {
    json_response(405, ['error' => 'POST only']);
    exit;
}
try {
    $config = humwatch_config();
    [$status, $body] = handle_clip(
        humwatch_db($config),
        $config,
        (string) file_get_contents('php://input', false, null, 0, CLIP_MAX_BYTES + 1),
        (string) ($_SERVER['HTTP_X_HUMWATCH_TIMESTAMP'] ?? ''),
        (string) ($_SERVER['HTTP_X_HUMWATCH_SIGNATURE'] ?? ''),
        (string) ($_SERVER['HTTP_X_HUMWATCH_EVENT'] ?? ''),
        time()
    );
    json_response($status, $body);
} catch (Throwable $error) {
    error_log('humwatch clip: ' . $error->getMessage());
    json_response(500, ['error' => 'server error']);
}
