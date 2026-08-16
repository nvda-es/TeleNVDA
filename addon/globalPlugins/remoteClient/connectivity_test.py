"""Connectivity checks for TCP/TLS and WebSocket relay endpoints."""

import socket
import ssl
import time

from . import connectivity_log
from .transport import WebSocketRelayTransport


def test_endpoint(host, port, transport_type="tcp", ws_path="/", timeout=10):
	started = time.monotonic()
	result = {
		"host": host,
		"port": int(port),
		"transport": transport_type,
		"success": False,
	}
	try:
		socket.getaddrinfo(host, port)
		result["dns"] = True
		if transport_type == "websocket":
			transport = WebSocketRelayTransport(
				serializer=_NullSerializer(),
				address=(host, int(port)),
				ws_path=ws_path,
				insecure=False,
				timeout=timeout,
			)
			socket_obj = transport.create_websocket()
			socket_obj.close()
		else:
			context = ssl.create_default_context()
			with socket.create_connection((host, int(port)), timeout=timeout) as raw:
				with context.wrap_socket(raw, server_hostname=host):
					pass
		result["success"] = True
		result["message"] = "Connection and TLS validation succeeded"
	except Exception as error:
		result["message"] = str(error)
	result["duration_ms"] = round((time.monotonic() - started) * 1000)
	connectivity_log.write_result(result)
	return result


def run_async(host, port, transport_type="tcp", ws_path="/", callback=None):
	import threading

	def worker():
		result = test_endpoint(host, port, transport_type, ws_path)
		if callback:
			callback(result)

	thread = threading.Thread(target=worker, name="TeleNVDA connectivity test", daemon=True)
	thread.start()
	return thread


class _NullSerializer:
	def serialize(self, **kwargs):
		return b""

	def deserialize(self, value):
		return {}
