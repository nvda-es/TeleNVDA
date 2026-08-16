"""Capability negotiation between TeleNVDA clients.

The relay simply forwards every message to the other members of the channel, and
clients ignore message types they do not know about. This module uses that
property to let two clients tell each other which optional features they
implement, without breaking older clients such as the original TeleNVDA or the
standard NVDA Remote Access, which will silently drop the announcement.

Each client broadcasts a ``telenvda_capabilities`` message when it joins a
channel and whenever another client joins. A peer which never answers is
considered a legacy client, so every optional feature falls back to the
behaviour understood by the original add-on.
"""

from logging import getLogger

import addonHandler

from .transport import TransportEvents

logger = getLogger("capabilities")

#: Message type used to announce what a client is able to do.
MESSAGE_TYPE = "telenvda_capabilities"

#: Version of the negotiation format itself, so that it can evolve later.
NEGOTIATION_VERSION = 1

#: Streaming file transfer made of acknowledged chunks, without the 10 MB limit.
FEATURE_CHUNKED_FILE_TRANSFER = "chunked_file_transfer"

#: WebRTC screen sharing of the controlled computer, with optional mouse control.
FEATURE_SCREEN_SHARE = "screen_share"

#: Optional features implemented by this build.
LOCAL_FEATURES = (FEATURE_CHUNKED_FILE_TRANSFER,)

#: Capability strings understood by the relay itself, which uses them to decide
#: whether it may route screen sharing signalling to a given client.
RELAY_CAPABILITY_SCREEN_SHARE = "screen_share/1"
RELAY_CAPABILITY_INPUT_CONTROL = "input_control/1"


def available_features():
	"""Return the optional features this installation can actually offer right now.

	Screen sharing depends on a helper program which may not be shipped or may be
	turned off in the configuration, so it is only announced when usable.
	"""
	features = list(LOCAL_FEATURES)
	# Imported lazily: screen_share imports this module to read the feature names.
	from . import screen_share
	if screen_share.is_available():
		features.append(FEATURE_SCREEN_SHARE)
	return features


def get_addon_version():
	"""Return the version of the running add-on, or an empty string when unknown."""
	try:
		return addonHandler.getCodeAddon().manifest["version"]
	except Exception:
		logger.debug("Unable to read the add-on version", exc_info=True)
		return ""


class CapabilityNegotiator:
	"""Track which optional features the other members of the channel support."""

	def __init__(self, transport, max_file_size=None):
		self.transport = transport
		#: Largest file this client accepts to receive, or None when unlimited.
		#: May be a callable returning that value, since it depends on the configuration.
		self.max_file_size = max_file_size
		#: Identifiers of the other clients currently in the channel.
		self.peer_ids = set()
		#: Capabilities announced by each peer, keyed by client identifier.
		self.peer_capabilities = {}
		callbacks = transport.callback_manager
		callbacks.register_callback("msg_" + MESSAGE_TYPE, self.handle_capabilities)
		callbacks.register_callback("msg_channel_joined", self.handle_channel_joined)
		callbacks.register_callback("msg_client_joined", self.handle_client_joined)
		callbacks.register_callback("msg_client_left", self.handle_client_left)
		callbacks.register_callback(TransportEvents.CONNECTED, self.announce)
		callbacks.register_callback(TransportEvents.CONNECTED, self.announce_to_relay)
		callbacks.register_callback(TransportEvents.DISCONNECTED, self.reset)

	def local_capabilities(self, reply=True):
		max_file_size = self.max_file_size
		if callable(max_file_size):
			max_file_size = max_file_size()
		return {
			"negotiation_version": NEGOTIATION_VERSION,
			"addon": "TeleNVDA",
			"addon_version": get_addon_version(),
			"features": available_features(),
			"max_file_size": max_file_size,
			"reply": reply,
		}

	def announce_to_relay(self):
		"""Tell the relay which capabilities it may route signalling for.

		Only the relay reads this message, and only a relay built with screen sharing
		support does anything with it. Older relays simply forward it to the other
		clients, which ignore an unknown message type.
		"""
		from . import mouse_control, screen_share
		capabilities = []
		if screen_share.is_available():
			capabilities.append(RELAY_CAPABILITY_SCREEN_SHARE)
		# Driving the remote mouse needs no picture and no helper program, so it is
		# announced on its own even when screen sharing is unavailable.
		if mouse_control.is_remote_input_allowed():
			capabilities.append(RELAY_CAPABILITY_INPUT_CONTROL)
		if not capabilities:
			return
		try:
			self.transport.send(type="capabilities", capabilities=capabilities)
		except Exception:
			logger.exception("Unable to announce capabilities to the relay")

	def announce(self, reply=True):
		"""Tell the other members of the channel what this client supports."""
		try:
			self.transport.send(type=MESSAGE_TYPE, **self.local_capabilities(reply=reply))
		except Exception:
			logger.exception("Unable to announce capabilities")

	def reset(self):
		self.peer_ids.clear()
		self.peer_capabilities.clear()

	def handle_channel_joined(self, clients=None, **kwargs):
		self.reset()
		for client in clients or []:
			client_id = (client or {}).get("id")
			if client_id is not None:
				self.peer_ids.add(client_id)
		self.announce()

	def handle_client_joined(self, client=None, user_id=None, **kwargs):
		client_id = (client or {}).get("id", user_id)
		if client_id is not None:
			self.peer_ids.add(client_id)
		# The new client was not in the channel when we first announced ourselves.
		self.announce()

	def handle_client_left(self, client=None, user_id=None, **kwargs):
		client_id = (client or {}).get("id", user_id)
		if client_id is None:
			return
		self.peer_ids.discard(client_id)
		self.peer_capabilities.pop(client_id, None)

	def handle_capabilities(self, origin=None, reply=False, **kwargs):
		if origin is None:
			# The relay only stamps an origin from protocol version 2 onwards.
			# Without it we cannot tell peers apart, so ignore the announcement.
			logger.debug("Ignoring a capability announcement without an origin")
			return
		self.peer_ids.add(origin)
		self.peer_capabilities[origin] = {
			"negotiation_version": kwargs.get("negotiation_version", 0),
			"addon": kwargs.get("addon", ""),
			"addon_version": kwargs.get("addon_version", ""),
			"features": list(kwargs.get("features") or []),
			"max_file_size": kwargs.get("max_file_size"),
		}
		if reply:
			self.announce(reply=False)

	@property
	def peer_count(self):
		return len(self.peer_ids)

	def all_peers_support(self, feature):
		"""Whether every other client in the channel announced the given feature."""
		if not self.peer_ids:
			return False
		for peer_id in self.peer_ids:
			capabilities = self.peer_capabilities.get(peer_id)
			if capabilities is None:
				# A client which never announced anything is a legacy one.
				return False
			if feature not in capabilities["features"]:
				return False
		return True

	def peers_supporting(self, feature):
		"""Return the identifiers of the peers which announced the given feature."""
		return [
			peer_id
			for peer_id, capabilities in self.peer_capabilities.items()
			if feature in capabilities["features"]
		]

	def negotiated_max_file_size(self):
		"""Return the smallest size limit announced by the peers, or None when unlimited."""
		limits = [
			capabilities.get("max_file_size")
			for capabilities in self.peer_capabilities.values()
		]
		limits = [limit for limit in limits if limit]
		return min(limits) if limits else None

	def describe_peers(self):
		"""Return a human readable description of the peers, for logging purposes."""
		return ", ".join(
			"{id}: {addon} {version} ({features})".format(
				id=peer_id,
				addon=capabilities["addon"] or "unknown",
				version=capabilities["addon_version"] or "unknown",
				features=",".join(capabilities["features"]) or "none",
			)
			for peer_id, capabilities in self.peer_capabilities.items()
		)
