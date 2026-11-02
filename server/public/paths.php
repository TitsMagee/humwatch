<?php
// Finds the private folder: beside these files in the repo, or at ~/humwatch when
// these files are copied into a cPanel public_html.
$here = dirname(__DIR__);
return is_file($here . '/lib/bootstrap.php') ? $here : $here . '/humwatch';
