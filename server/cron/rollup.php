<?php
// Run every 5 minutes from cPanel Cron Jobs:
//   php /home/ACCOUNT/humwatch/cron/rollup.php
declare(strict_types=1);

if (PHP_SAPI !== 'cli') {
    http_response_code(404);
    exit;
}
require dirname(__DIR__) . '/lib/bootstrap.php';

$config = humwatch_config();
run_rollup(humwatch_db($config), $config, time());
