<?php
// Shared set-up. Everything in lib/ stays outside the web root.

declare(strict_types=1);

require_once __DIR__ . '/ingest.php';
require_once __DIR__ . '/rollup.php';

function humwatch_config(): array
{
    return require dirname(__DIR__) . '/config.php';
}

function humwatch_db(array $config): PDO
{
    return new PDO($config['db_dsn'], $config['db_user'], $config['db_pass'], [
        PDO::ATTR_ERRMODE => PDO::ERRMODE_EXCEPTION,
        PDO::ATTR_DEFAULT_FETCH_MODE => PDO::FETCH_ASSOC,
        PDO::ATTR_EMULATE_PREPARES => false,
    ]);
}

function json_response(int $status, array $body): void
{
    http_response_code($status);
    header('Content-Type: application/json');
    header('X-Content-Type-Options: nosniff');
    echo json_encode($body);
}

// Write so that a visitor never reads a half-written file.
function write_public_json(string $dir, string $name, array $data): void
{
    if (!is_dir($dir)) {
        mkdir($dir, 0755, true);
    }
    $temp = tempnam($dir, 'tmp');
    file_put_contents($temp, json_encode($data));
    chmod($temp, 0644);
    rename($temp, $dir . '/' . $name);
}
