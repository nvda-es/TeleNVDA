"""The loopback HTTP server which NVDA and the browser page talk through.

This is the most sensitive piece of the screen sharing feature. A server listening
on the loopback interface is reachable by every page open in every browser on the
computer, and this one can start a screen capture. Without protection, any visited
website could ask it to share the screen.

Four measures make that impossible, and none of them is optional:

* the socket binds to 127.0.0.1 alone, never to every interface;
* the port is picked by the system for each session, so it cannot be guessed
  ahead of time and hardcoded in a malicious page;
* every request must carry a 256 bit token, compared in constant time, which is
  handed to the page through the command line of the browser and appears nowhere
  else;
* a request with an Origin header that is not the exact local origin is refused,
  which stops a page from another site from reaching the server at all.

The server exists only while a session does, and it serves exactly one document.

The exchange itself is a mailbox rather than a socket. NVDA drops commands in,
the page collects them by polling, and posts its events back. A WebSocket would
be tidier, but this needs no extra dependency and no handshake, and the volume is
a handful of small JSON objects per session.
"""

import base64
import hmac
import json
import os
import threading
import time
from logging import getLogger

from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlparse

logger = getLogger("local_bridge")

#: Nothing the page legitimately posts comes close to this.
MAX_BODY = 128 * 1024

#: A page which stopped collecting its commands must not make NVDA grow without end.
MAX_PENDING = 500

#: File served to the browser, next to this module.
PAGE_SUBDIR = "web"
PAGE_NAME = "screen_share.html"

#: Bytes drawn from the system generator for the session token. The standard
#: secrets module is not part of the library NVDA ships, so the token is built
#: here from os.urandom, which is the same source secrets itself draws from.
TOKEN_BYTES = 32


def _make_token():
	"""Return an unguessable URL safe token for one session."""
	raw = base64.urlsafe_b64encode(os.urandom(TOKEN_BYTES))
	return raw.decode("ascii").rstrip("=")


def get_page_path():
	"""Return the absolute path of the signalling page, or None when it is missing."""
	path = os.path.join(os.path.abspath(os.path.dirname(__file__)), PAGE_SUBDIR, PAGE_NAME)
	return path if os.path.isfile(path) else None


class _Handler(BaseHTTPRequestHandler):
	server_version = "TeleNVDA"
	protocol_version = "HTTP/1.1"

	def log_message(self, fmt, *args):
		pass  # NVDA has its own log; the HTTP access log would only be noise.

	def _authorised(self, query):
		token = (query.get("token") or [""])[0]
		if not hmac.compare_digest(str(token), self.server.token):
			return False
		origin = self.headers.get("Origin")
		# A page served by the bridge itself sends no Origin on same origin requests,
		# so its absence is normal. A value that is present and wrong is not.
		if origin is not None and origin != self.server.origin:
			return False
		# Only a request carrying the token of this session proves the page is alive,
		# which is what tells the engine the session is still going.
		self.server.last_contact = time.monotonic()
		return True

	def _reply(self, status, body=b"", content_type="application/json"):
		self.send_response(status)
		self.send_header("Content-Type", content_type)
		self.send_header("Content-Length", str(len(body)))
		self.send_header("Cache-Control", "no-store")
		self.end_headers()
		if body:
			self.wfile.write(body)

	def _reply_json(self, payload, status=200):
		self._reply(status, json.dumps(payload).encode("utf-8"))

	def do_GET(self):
		parsed = urlparse(self.path)
		query = parse_qs(parsed.query)
		if not self._authorised(query):
			return self._reply(403)
		if parsed.path == "/":
			try:
				with open(self.server.page_path, "rb") as page:
					body = page.read()
			except OSError:
				logger.exception("Unable to read the screen sharing page")
				return self._reply(500)
			return self._reply(200, body, "text/html; charset=utf-8")
		if parsed.path == "/poll":
			try:
				since = int((query.get("since") or ["0"])[0])
			except ValueError:
				since = 0
			commands, nxt = self.server.mailbox.fetch(since)
			return self._reply_json({"commands": commands, "next": nxt})
		return self._reply(404)

	def do_POST(self):
		parsed = urlparse(self.path)
		query = parse_qs(parsed.query)
		if not self._authorised(query):
			return self._reply(403)
		if parsed.path != "/event":
			return self._reply(404)
		try:
			length = int(self.headers.get("Content-Length", "0"))
		except ValueError:
			return self._reply(400)
		if length <= 0 or length > MAX_BODY:
			return self._reply(400)
		try:
			event = json.loads(self.rfile.read(length).decode("utf-8"))
		except (ValueError, UnicodeDecodeError):
			return self._reply(400)
		if not isinstance(event, dict):
			return self._reply(400)
		try:
			self.server.on_event(event)
		except Exception:
			logger.exception("Error while handling an event from the screen sharing page")
		return self._reply_json({"ok": True})


class _Server(ThreadingHTTPServer):
	daemon_threads = True

	#: Set as soon as the page asks for anything at all. Zero while it never did.
	last_contact = 0.0

	def handle_error(self, request, client_address):
		# The browser drops its polling sockets without ceremony when the window
		# closes. The traceback the base class would print is never interesting.
		pass


class _Mailbox:
	"""Commands waiting for the page to collect them."""

	def __init__(self):
		self._lock = threading.Lock()
		self._commands = []

	def post(self, command):
		with self._lock:
			if len(self._commands) >= MAX_PENDING:
				logger.warning("The screen sharing page stopped collecting its commands")
				return
			self._commands.append((len(self._commands) + 1, command))

	def fetch(self, since):
		with self._lock:
			return [c for (i, c) in self._commands if i > since], len(self._commands)


class LocalBridge:
	"""The loopback server the browser page is served from and talks to."""

	def __init__(self, on_event):
		self._on_event = on_event
		self._server = None
		self._thread = None
		self._mailbox = _Mailbox()
		self.token = None
		self.origin = None

	@property
	def running(self):
		return self._server is not None

	@property
	def silence(self):
		"""Seconds since the page last asked for anything, or None while it never did.

		This, rather than the browser process, is what says whether the page is still
		there: the command line the engine starts often hands over to another process
		and exits at once, so a dead process proves nothing.
		"""
		server = self._server
		if server is None or not server.last_contact:
			return None
		return time.monotonic() - server.last_contact

	def start(self):
		"""Bind, serve, and return the local origin. Raises RuntimeError when unusable."""
		if self.running:
			return self.origin
		page_path = get_page_path()
		if page_path is None:
			raise RuntimeError("The screen sharing page is missing from the add-on")
		self.token = _make_token()
		server = _Server(("127.0.0.1", 0), _Handler)
		server.token = self.token
		server.page_path = page_path
		server.mailbox = self._mailbox
		server.on_event = self._on_event
		self.origin = "http://127.0.0.1:%d" % server.server_address[1]
		server.origin = self.origin
		self._server = server
		self._thread = threading.Thread(target=server.serve_forever, name="screen_share_bridge", daemon=True)
		self._thread.start()
		logger.debug("Screen sharing bridge listening on %s", self.origin)
		return self.origin

	def page_url(self, role):
		"""Return the address to open in the browser for the given role."""
		if not self.running:
			raise RuntimeError("The screen sharing bridge is not running")
		return "%s/?token=%s&role=%s" % (self.origin, self.token, role)

	def send(self, command):
		"""Queue one command for the page."""
		self._mailbox.post(command)

	def stop(self):
		server, self._server = self._server, None
		self._thread = None
		self.token = None
		self.origin = None
		self._mailbox = _Mailbox()
		if server is None:
			return
		try:
			server.shutdown()
			server.server_close()
		except Exception:
			logger.exception("Unable to stop the screen sharing bridge")
