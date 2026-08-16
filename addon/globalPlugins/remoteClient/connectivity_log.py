"""Small local log for connectivity diagnostics."""

import datetime
import json
import os

import globalVars


LOG_FILENAME = "teleNVDA-connectivity.log"


def log_path():
	return os.path.join(os.path.abspath(globalVars.appArgs.configPath), LOG_FILENAME)


def write_result(result):
	os.makedirs(os.path.dirname(log_path()), exist_ok=True)
	entry = {
		"timestamp": datetime.datetime.now(datetime.timezone.utc).isoformat(),
		**result,
	}
	with open(log_path(), "a", encoding="utf-8") as stream:
		stream.write(json.dumps(entry, ensure_ascii=False) + "\n")
