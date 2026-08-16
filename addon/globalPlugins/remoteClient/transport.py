import threading
import os
import sys
import buildVersion
import time
import queue
import ssl
import socket
import select
import hashlib
import base64
import json
from collections import defaultdict
from typing import Tuple
from logging import getLogger
log = getLogger('transport')
from . import callback_manager
from . import configuration
from . import proxy_utils, sspi_proxy, ws_protocol
from .socket_utils import SERVER_PORT, address_to_hostport, hostport_to_address
from enum import Enum
sys.path.append(os.path.join(os.path.abspath(os.path.dirname(__file__)), "lib64" if buildVersion.version_year >= 2026 else "lib32"))
from Cryptodome.Cipher import AES
import websocket
sys.path.remove(sys.path[-1])

PROTOCOL_VERSION: int = 2
EXCLUDED_FROM_ENCRYPTION: list[str] = ["join", "protocol_version", "encrypted", "channel_joined", "motd", "nvda_not_connected", "client_left", "ping", "error", "client_joined", "generate_key"]

#: Screen sharing signalling messages exchanged between two clients.
#: Unlike every other message, the relay does not broadcast them: it reads their
#: "type" and "target" fields to deliver them to a single peer. They can therefore
#: not be wrapped in an "encrypted" message. When an encryption password is in use,
#: only their payload is sealed, in a nested "enc" object, so that the relay keeps
#: routing them without ever seeing the session description or the ICE candidates.
SIGNALING_TYPES: frozenset = frozenset((
	"screen_share_request",
	"screen_share_response",
	"screen_share_stop",
	"webrtc_offer",
	"webrtc_answer",
	"webrtc_candidate",
))

#: Screen sharing messages exchanged with the relay itself rather than with a peer.
#: They carry no private data and the relay obviously has to read them, so they are
#: always sent in clear text.
SIGNALING_RELAY_TYPES: frozenset = frozenset(("capabilities", "turn_credentials"))

#: Fields of a signalling message which stay readable by the relay.
#: "target" tells it where to deliver the message and "origin" is stamped by it.
SIGNALING_CLEAR_FIELDS: Tuple[str, ...] = ("target", "origin")

class TransportEvents(Enum):
	CONNECTED = 'transport_connected'
	CERTIFICATE_AUTHENTICATION_FAILED = 'certificate_authentication_failed'
	CONNECTION_FAILED = 'transport_connection_failed'
	CLOSING = 'transport_closing'
	DISCONNECTED = 'transport_disconnected'


class Transport:
	connected: bool
	successful_connects: int
	callback_manager: callback_manager.CallbackManager
	connect_event: threading.Event

	def __init__(self, serializer):
		self.serializer = serializer
		self.callback_manager = callback_manager.CallbackManager()
		self.connected = False
		self.successful_connects = 0
		self.connected_event = threading.Event()

	def transport_connected(self):
		self.successful_connects += 1
		self.connected = True
		self.connected_event.set()
		self.callback_manager.call_callbacks(TransportEvents.CONNECTED)

class TCPTransport(Transport):
	buffer: bytes
	closed: bool
	queue: queue.Queue
	insecure: bool
	server_sock_lock: threading.Lock
	
	def __init__(self, serializer, address: Tuple[str, int], timeout: int=0, insecure: bool=False, encryption_key: str=''):
		super().__init__(serializer=serializer)
		self.closed = False
		#Buffer to hold partially received data
		self.buffer = b''
		self.queue = queue.Queue()
		self.address = address
		self.server_sock = None
		# Reading/writing from an SSL socket is not thread safe.
		# See https://bugs.python.org/issue41597#msg375692
		# Guard access to the socket with a lock.
		self.server_sock_lock = threading.Lock()
		self.queue_thread = None
		self.timeout = timeout
		self.reconnector_thread = ConnectorThread(self)
		self.insecure=insecure
		self.encryption_key = encryption_key
		self.encryption_hash=hashlib.sha256(encryption_key.encode("utf-8")).digest() if encryption_key else None
		self.send_alpn = True

	def run(self):
		self.closed = False
		try:
			self.server_sock = self.create_outbound_socket(*self.address, insecure=self.insecure)
			self.server_sock.connect(self.address)
		except ssl.SSLCertVerificationError as ex:
			fingerprint=None
			try:
				tmp_con = self.create_outbound_socket(*self.address, insecure = True)
				tmp_con.connect(self.address)
				certBin = tmp_con.getpeercert(True)
				tmp_con.close()
				fingerprint = hashlib.sha256(certBin).hexdigest().lower()
			except Exception: pass
			config = configuration.get_config()
			if hostport_to_address(self.address) in config['trusted_certs'] and config['trusted_certs'][hostport_to_address(self.address)]==fingerprint:
				self.insecure=True
				return self.run()
			self.last_fail_fingerprint = fingerprint
			self.callback_manager.call_callbacks(TransportEvents.CERTIFICATE_AUTHENTICATION_FAILED)
			raise
		except ssl.SSLError:
			if self.send_alpn:
				self.send_alpn = False
				return self.run()
			else:
				self.callback_manager.call_callbacks(TransportEvents.CONNECTION_FAILED)
				raise
		except Exception:
			self.callback_manager.call_callbacks(TransportEvents.CONNECTION_FAILED)
			raise
		self.transport_connected()
		self.queue_thread = threading.Thread(target=self.send_queue)
		self.queue_thread.daemon = True
		self.queue_thread.start()
		while self.server_sock is not None:
			try:
				readers, writers, error = select.select([self.server_sock], [], [self.server_sock])
			except socket.error:
				self.buffer = b''
				break
			if self.server_sock in error:
				self.buffer = b""
				break
			if self.server_sock in readers:
				try:
					self.handle_server_data()
				except socket.error:
					self.buffer = b''
					break
		self.connected = False
		self.connected_event.clear()
		self.callback_manager.call_callbacks(TransportEvents.DISCONNECTED)
		self._disconnect()

	def create_outbound_socket(self, host, port, insecure=False):
		if host.lower().endswith(".onion"):
			server_sock = socket.socket(socket.AF_INET)
		else:
			address = socket.getaddrinfo(host, port)[0]
			server_sock = socket.socket(*address[:3])
		if self.timeout:
			server_sock.settimeout(self.timeout)
		server_sock.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
		server_sock.ioctl(socket.SIO_KEEPALIVE_VALS, (1, 60000, 2000))
		ctx = (ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT))
		if self.send_alpn:
			ctx.set_alpn_protocols(['nvdaremote/2.0'])
		ctx.minimum_version = ssl.TLSVersion.TLSv1_2
		if insecure:
			ctx.check_hostname = False
			ctx.verify_mode = ssl.CERT_NONE
		ctx.load_default_certs()
		server_sock = ctx.wrap_socket(sock=server_sock, server_hostname=host)
		return server_sock

	def getpeercert(self, binary_form=False):
		if self.server_sock is None: return None
		return self.server_sock.getpeercert(binary_form)

	def handle_server_data(self):
		# This approach may be problematic:
		# See also server.py handle_data in class Client.
		buffSize = 16384
		with self.server_sock_lock:
			# select operates on the raw socket. Even though it said there was data to
			# read, that might be SSL data which might not result in actual data for
			# us. Therefore, do a non-blocking read so SSL doesn't try to wait for
			# more data for us.
			# We don't make the socket non-blocking earlier because then we'd have to
			# handle retries during the SSL handshake.
			# See https://stackoverflow.com/questions/3187565/select-and-ssl-in-python
			# and https://docs.python.org/3/library/ssl.html#notes-on-non-blocking-sockets
			self.server_sock.setblocking(False)
			try:
				data = self.buffer + self.server_sock.recv(buffSize)
			except ssl.SSLWantReadError:
				# There's no data for us.
				return
			finally:
				self.server_sock.setblocking(True)
		self.buffer = b''
		if not data:
			self._disconnect()
			return
		if b'\n' not in data:
			self.buffer += data
			return
		while b'\n' in data:
			line, sep, data = data.partition(b'\n')
			self.parse(line)
		self.buffer += data

	def parse(self, line, isDecrypted=False):
		obj = self.serializer.deserialize(line)
		if 'type' not in obj:
			return
		if self.encryption_hash is not None and not isDecrypted:
			if obj['type'] in SIGNALING_TYPES:
				obj = self.open_signaling_envelope(obj)
				if obj is None:
					return
			elif obj['type'] not in EXCLUDED_FROM_ENCRYPTION and obj['type'] not in SIGNALING_RELAY_TYPES:
				return
		if obj['type']=='encrypted' and self.encryption_hash is not None:
			cipher = AES.new(self.encryption_hash, AES.MODE_GCM, nonce=base64.b64decode(obj['nonce'].encode("utf-8")))
			try:
				decrypted_data = cipher.decrypt_and_verify(base64.b64decode(obj['data'].encode("utf-8")), base64.b64decode(obj['tag'].encode("utf-8")))
				return self.parse(decrypted_data, isDecrypted=True)
			except:
				return
		callback = "msg_"+obj['type']
		del obj['type']
		self.callback_manager.call_callbacks(callback, **obj)

	def seal_signaling_envelope(self, kwargs):
		"""Encrypt the private part of a screen sharing signalling message.

		The routing fields stay readable so that the relay can deliver the message
		to the intended peer, everything else is sealed with the session password.
		"""
		envelope = {key: kwargs[key] for key in SIGNALING_CLEAR_FIELDS if key in kwargs}
		payload = {key: value for key, value in kwargs.items() if key not in SIGNALING_CLEAR_FIELDS}
		cipher = AES.new(self.encryption_hash, AES.MODE_GCM)
		data, tag = cipher.encrypt_and_digest(json.dumps(payload).encode("utf-8"))
		envelope['enc'] = {
			'nonce': base64.b64encode(cipher.nonce).decode(),
			'data': base64.b64encode(data).decode(),
			'tag': base64.b64encode(tag).decode(),
		}
		return envelope

	def open_signaling_envelope(self, obj):
		"""Return the signalling message with its payload decrypted, or None when invalid.

		A missing or unreadable payload means the message was not produced by a peer
		holding the session password, so it is dropped rather than acted upon.
		"""
		envelope = obj.pop('enc', None)
		if not isinstance(envelope, dict):
			log.warning("Dropping an unencrypted %s signalling message" % obj['type'])
			return None
		try:
			cipher = AES.new(self.encryption_hash, AES.MODE_GCM, nonce=base64.b64decode(envelope['nonce'].encode("utf-8")))
			decrypted = cipher.decrypt_and_verify(base64.b64decode(envelope['data'].encode("utf-8")), base64.b64decode(envelope['tag'].encode("utf-8")))
			payload = json.loads(decrypted.decode("utf-8"))
		except Exception:
			log.warning("Dropping a %s signalling message which could not be decrypted" % obj['type'])
			return None
		if not isinstance(payload, dict):
			return None
		# The routing fields are authoritative: they are the ones the relay acted upon.
		for key in ('type',) + SIGNALING_CLEAR_FIELDS:
			payload.pop(key, None)
		obj.update(payload)
		return obj

	def send_queue(self):
		while True:
			item = self.queue.get()
			if item is None:
				return
			try:
				with self.server_sock_lock:
					self.server_sock.sendall(item)
			except socket.error:
				return

	def send(self, type, **kwargs):
		if self.encryption_hash is not None and type in SIGNALING_TYPES:
			kwargs = self.seal_signaling_envelope(kwargs)
		obj = self.serializer.serialize(type=type, **kwargs)
		if self.encryption_hash is not None and type not in EXCLUDED_FROM_ENCRYPTION and type not in SIGNALING_TYPES and type not in SIGNALING_RELAY_TYPES:
			cipher = AES.new(self.encryption_hash, AES.MODE_GCM)
			nonce = base64.b64encode(cipher.nonce).decode()
			data, tag = cipher.encrypt_and_digest(obj)
			data = base64.b64encode(data).decode()
			tag = base64.b64encode(tag).decode()
			return self.send(type='encrypted', nonce=nonce, data=data, tag=tag)
		if self.connected:
			self.queue.put(obj)

	def _disconnect(self):
		"""Disconnect the transport due to an error, without closing the connector thread."""
		if self.queue_thread is not None:
			self.queue.put(None)
			self.queue_thread.join()
			self.queue_thread = None
		clear_queue(self.queue)
		if self.server_sock:
			self.server_sock.close()
			self.server_sock = None

	def close(self):
		self.callback_manager.call_callbacks(TransportEvents.CLOSING)
		self.reconnector_thread.running = False
		self._disconnect()
		self.closed = True
		self.reconnector_thread = ConnectorThread(self)

class RelayTransport(TCPTransport):

	def __init__(self, serializer, address, timeout=0, channel=None, connection_type=None, protocol_version=PROTOCOL_VERSION, insecure=False, encryption_key=None):
		super().__init__(address=address, serializer=serializer, timeout=timeout, insecure=insecure, encryption_key=encryption_key)
		log.info("Connecting to %s channel %s" % (address, channel))
		self.channel = channel
		self.connection_type = connection_type
		self.protocol_version = protocol_version
		self.callback_manager.register_callback(TransportEvents.CONNECTED, self.on_connected)

	def on_connected(self):
		self.send('protocol_version', version=self.protocol_version)
		if self.channel is not None:
			self.send('join', channel=self.channel, connection_type=self.connection_type)
		else:
			self.send('generate_key')


class WebSocketTransport(TCPTransport):
	"""Transport the NVDA Remote JSON stream in WebSocket text frames."""

	def __init__(self, serializer, address, ws_path="/", timeout=0, channel=None,
				 connection_type=None, protocol_version=PROTOCOL_VERSION, insecure=False,
				 encryption_key=None):
		super().__init__(
			serializer=serializer,
			address=address,
			timeout=timeout,
			insecure=insecure,
			encryption_key=encryption_key,
		)
		self.ws_path = ws_protocol.normalize_path(ws_path)
		self.channel = channel
		self.connection_type = connection_type
		self.protocol_version = protocol_version
		self.websocket = None
		self._certificate_authentication_failed = False

	def _websocket_url(self):
		host, port = self.address
		return ws_protocol.websocket_url(host, port, self.ws_path)

	def _create_sspi_websocket(self, proxy_settings, insecure=None):
		"""Create a WebSocket over an HTTP CONNECT tunnel authenticated by SSPI."""
		if insecure is None:
			insecure = self.insecure
		host, port = self.address
		raw_socket = sspi_proxy.open_sspi_proxy_tunnel(
			proxy_settings,
			host,
			port,
			timeout=self.timeout or 60,
		)
		tls_socket = None
		try:
			context = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
			context.minimum_version = ssl.TLSVersion.TLSv1_2
			if insecure:
				context.check_hostname = False
				context.verify_mode = ssl.CERT_NONE
			else:
				context.load_default_certs()
				context.check_hostname = True
				context.verify_mode = ssl.CERT_REQUIRED
			tls_socket = context.wrap_socket(raw_socket, server_hostname=host)
			raw_socket = None
			return websocket.create_connection(
				self._websocket_url(),
				socket=tls_socket,
				subprotocols=[ws_protocol.SUBPROTOCOL],
				sslopt=self._ssl_options(insecure),
				timeout=self.timeout or None,
			)
		except Exception:
			if tls_socket is not None:
				tls_socket.close()
			elif raw_socket is not None:
				raw_socket.close()
			raise

	def _ssl_options(self, insecure):
		"""Build the websocket-client TLS options.

		``check_hostname`` must be disabled explicitly along with ``cert_reqs``,
		otherwise the SSL context refuses the combination and the connection
		fails before the handshake.
		"""
		if insecure:
			return {"cert_reqs": ssl.CERT_NONE, "check_hostname": False}
		return {"cert_reqs": ssl.CERT_REQUIRED}

	def _open_websocket(self, proxy_settings, insecure):
		"""Open the WebSocket through the resolved proxy, if any."""
		if proxy_utils.uses_sspi(proxy_settings):
			return self._create_sspi_websocket(proxy_settings, insecure=insecure)
		kwargs = {
			"subprotocols": [ws_protocol.SUBPROTOCOL],
			"sslopt": self._ssl_options(insecure),
			"timeout": self.timeout or None,
		}
		kwargs.update(proxy_utils.websocket_options(proxy_settings))
		try:
			return websocket.create_connection(self._websocket_url(), **kwargs)
		except Exception as error:
			if not proxy_utils.needs_windows_authentication(proxy_settings, error):
				raise
			log.debug("The proxy requires authentication, retrying with Windows credentials")
			return self._create_sspi_websocket(
				proxy_utils.with_windows_authentication(proxy_settings),
				insecure=insecure,
			)

	def create_websocket(self):
		self._certificate_authentication_failed = False
		conf = configuration.get_config().get("controlserver", {})
		url = self._websocket_url()
		proxy_settings = proxy_utils.resolve_for_url(proxy_utils.from_config(conf), url)
		log.debug(
			"Opening %s (proxy %s)",
			url,
			f"{proxy_settings.type}://{proxy_settings.host}:{proxy_settings.port}" if proxy_settings.enabled else "none",
		)
		try:
			return self._open_websocket(proxy_settings, self.insecure)
		except Exception as error:
			ssl_error = error if isinstance(error, ssl.SSLError) else error.__cause__
			if self.insecure or not isinstance(ssl_error, ssl.SSLError):
				raise
			fingerprint = None
			try:
				probe = self._open_websocket(proxy_settings, True)
				certificate_socket = getattr(probe, "sock", None)
				if certificate_socket is not None and not isinstance(certificate_socket, ssl.SSLSocket):
					certificate_socket = getattr(certificate_socket, "sock", None)
				if certificate_socket:
					fingerprint = hashlib.sha256(certificate_socket.getpeercert(True)).hexdigest().lower()
				probe.close()
			except Exception:
				log.exception("Unable to read the WebSocket certificate fingerprint")
			trusted = configuration.get_config().get("trusted_certs", {}).get(hostport_to_address(self.address))
			if fingerprint and trusted == fingerprint:
				self.insecure = True
				return self.create_websocket()
			self.last_fail_fingerprint = fingerprint
			self._certificate_authentication_failed = True
			self.callback_manager.call_callbacks(TransportEvents.CERTIFICATE_AUTHENTICATION_FAILED)
			raise

	def run(self):
		self.closed = False
		try:
			self.websocket = self.create_websocket()
		except ssl.SSLCertVerificationError:
			if not self._certificate_authentication_failed:
				self.callback_manager.call_callbacks(TransportEvents.CERTIFICATE_AUTHENTICATION_FAILED)
			raise
		except Exception:
			if not self._certificate_authentication_failed:
				self.callback_manager.call_callbacks(TransportEvents.CONNECTION_FAILED)
			raise
		self.transport_connected()
		while self.websocket is not None and not self.closed:
			try:
				data = self.websocket.recv()
			except Exception:
				break
			if data in (None, "", b""):
				break
			if isinstance(data, str):
				data = data.encode("utf-8")
			for line in data.splitlines():
				if line:
					self.parse(line)
		self.connected = False
		self.connected_event.clear()
		self.callback_manager.call_callbacks(TransportEvents.DISCONNECTED)
		self._disconnect()

	def send(self, type, **kwargs):
		obj = self.serializer.serialize(type=type, **kwargs)
		if self.encryption_hash is not None and type not in EXCLUDED_FROM_ENCRYPTION:
			cipher = AES.new(self.encryption_hash, AES.MODE_GCM)
			nonce = base64.b64encode(cipher.nonce).decode()
			data, tag = cipher.encrypt_and_digest(obj)
			return self.send(type="encrypted", nonce=nonce, data=base64.b64encode(data).decode(), tag=base64.b64encode(tag).decode())
		if self.connected and self.websocket is not None:
			self.websocket.send(obj.decode("utf-8"))

	def _disconnect(self):
		if self.websocket is not None:
			try:
				self.websocket.close()
			except Exception:
				pass
			self.websocket = None

	def close(self):
		self.callback_manager.call_callbacks(TransportEvents.CLOSING)
		self.reconnector_thread.running = False
		self.closed = True
		self._disconnect()
		self.connected = False
		self.reconnector_thread = ConnectorThread(self)


class RelayMixin:
	def on_connected(self):
		self.send("protocol_version", version=self.protocol_version)
		if self.channel is not None:
			self.send("join", channel=self.channel, connection_type=self.connection_type)
		else:
			self.send("generate_key")


class WebSocketRelayTransport(WebSocketTransport, RelayMixin):
	def __init__(self, *args, **kwargs):
		super().__init__(*args, **kwargs)
		self.callback_manager.register_callback(TransportEvents.CONNECTED, self.on_connected)

class ConnectorThread(threading.Thread):

	def __init__(self, connector, connect_delay=5):
		super().__init__()
		self.connect_delay = connect_delay
		self.running = True
		self.connector = connector
		self.name = self.name + "_connector_loop"
		self.daemon = True

	def run(self):
		while self.running:
			try:
				self.connector.run()
			except Exception:
				if not self.running:
					break
				time.sleep(self.connect_delay)
				continue
			else:
				time.sleep(self.connect_delay)
		log.info("Ending control connector thread %s" % self.name)

def clear_queue(queue):
	try:
		while True:
			queue.get_nowait()
	except Exception:
		pass
