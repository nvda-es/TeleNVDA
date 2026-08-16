"""Constants and helpers for the NVDA Remote WebSocket transport."""

SUBPROTOCOL = "nvdaremote/2.0"
DEFAULT_PATH = "/"


def normalize_path(path):
	path = (path or DEFAULT_PATH).strip()
	if not path.startswith("/"):
		path = "/" + path
	return path


def websocket_url(host, port, path=DEFAULT_PATH):
	if ":" in host and not host.startswith("["):
		host = f"[{host}]"
	return f"wss://{host}:{port}{normalize_path(path)}"
