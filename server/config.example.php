<?php
// Copy to config.php, outside the web root. Never commit config.php.
return [
    'db_dsn' => 'mysql:host=localhost;dbname=example_humwatch;charset=utf8mb4',
    'db_user' => 'example_user',
    'db_pass' => '',

    // At least 32 random characters. Same value as upload.secret on the logger.
    'ingest_secret' => '',

    // Create with: php -r "echo password_hash('your password', PASSWORD_DEFAULT);"
    'review_password_hash' => '',

    // Must stay outside the web root: clips can contain voices.
    'clips_dir' => __DIR__ . '/clips',
    'clip_retention_days' => 30,

    // Where live.json and history.json go, inside the web root.
    'public_data_dir' => __DIR__ . '/public/data',

    // Short gaps (a Pi restart, a dropped upload) don't count as interruptions.
    'max_gap_minutes' => 5,

    // Labels offered on the review page.
    'event_labels' => ['site_machinery', 'lorry', 'motor', 'plane', 'road_traffic', 'weather', 'people', 'other'],
];
