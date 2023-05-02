<?php
$result=array();
if (isset($_SERVER['HTTP_X_FORWARDED_FOR'])){
	$result['host'] = $_SERVER['HTTP_X_FORWARDED_FOR'];
}else{
	$result['host'] = $_SERVER['REMOTE_ADDR'];
}
$result['port'] = intval($_GET['port']);
$f = fsockopen($result['host'], $result['port'], $errno, $errmsg, 10.0);
$result['open'] = $f ? true : false;
if ($f){
	fclose($f);
}
echo json_encode($result, JSON_PRETTY_PRINT);
?>