<?php
// Finds the private folder. In the repo it sits beside these files. On a host it is a
// folder called humwatch outside the web root: beside public_html, or further up when
// the site is a subdomain whose folder sits inside public_html.
$dir = dirname(__DIR__);
if (is_file($dir . '/lib/bootstrap.php')) {
    return $dir;
}
for (; $dir !== dirname($dir); $dir = dirname($dir)) {
    if (@is_file($dir . '/humwatch/lib/bootstrap.php')) {
        return $dir . '/humwatch';
    }
}
throw new RuntimeException('Cannot find the private humwatch folder');
