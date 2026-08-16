"""Screen sharing of the controlled computer, over a peer to peer WebRTC link.

The add-on itself never encodes or decodes video. It opens a Chromium browser
window on a page it serves on the loopback interface, and that page owns the
WebRTC session: it captures the screen on the controlled computer and displays it
on the controlling one. NVDA only carries the signalling needed to set that link
up, through the relay both computers are already connected to.

The browser was chosen over a Python stack for two reasons. A WebRTC stack such as
aiortc cannot be vendored in the add-on, because NVDA ships three different Python
ABIs and no wheel exists for the oldest one. Video encoding in the same process as
NVDA would also compete with speech for the interpreter lock, which is
unacceptable for a screen reader. Microsoft Edge is already installed on every
supported version of Windows, updates itself, and encodes in hardware; Chrome and
Brave are accepted in its place.

Everything here degrades gracefully. When no such browser is installed, or when
screen sharing is turned off in the configuration, :func:`is_available` returns
False, the feature is never announced, and the add-on behaves exactly as before.
"""

from logging import getLogger

import wx

import addonHandler
import gui
import ui

from . import capabilities, configuration, edge_engine
from .transport import TransportEvents

logger = getLogger("screen_share")

try:
	addonHandler.initTranslation()
except addonHandler.AddonError:
	logger.warning("Unable to initialise translations. This may be because the addon is running from NVDA scratchpad.")

#: Role played by this computer during a session.
ROLE_PUBLISHER = "publisher"  # The controlled computer, which captures its screen.
ROLE_VIEWER = "viewer"  # The controlling computer, which displays the picture.

#: States a session goes through.
STATE_IDLE = "idle"
STATE_REQUESTING = "requesting"  # A request was sent, the peer has not answered yet.
STATE_CONNECTING = "connecting"  # Both sides agreed, the WebRTC link is being set up.
STATE_ACTIVE = "active"  # Pictures are flowing.

#: Signalling messages exchanged between the two clients.
MSG_REQUEST = "screen_share_request"
MSG_RESPONSE = "screen_share_response"
MSG_STOP = "screen_share_stop"
MSG_OFFER = "webrtc_offer"
MSG_ANSWER = "webrtc_answer"
MSG_CANDIDATE = "webrtc_candidate"

#: Message asking the relay for temporary TURN credentials.
MSG_TURN_CREDENTIALS = "turn_credentials"

#: Longest session description or ICE candidate accepted from a peer. A malicious
#: or broken peer must not be able to make the engine allocate unbounded memory.
MAX_SIGNALING_PAYLOAD = 64 * 1024



def is_enabled():
	"""Whether the user left screen sharing turned on."""
	try:
		return bool(configuration.get_config()["screen_share"]["enabled"])
	except Exception:
		logger.debug("Unable to read the screen sharing configuration", exc_info=True)
		return False


def is_available():
	"""Whether this installation can take part in a screen sharing session."""
	return is_enabled() and edge_engine.is_available()


def is_input_control_allowed():
	"""Whether this computer accepts to be driven with the remote mouse.

	Sharing the screen and lending the mouse are a single permission: someone watching
	this screen almost always needs to point at something on it. The answer therefore
	comes from the very same setting, read through :mod:`mouse_control` which also uses
	it when no picture is shared at all.
	"""
	from . import mouse_control
	return mouse_control.is_remote_input_allowed()


def _capture_settings():
	try:
		section = configuration.get_config()["screen_share"]
		return {
			"max_fps": int(section["max_fps"]),
			"max_width": int(section["max_width"]),
			"quality": str(section["quality"]),
		}
	except Exception:
		logger.debug("Unable to read the capture settings", exc_info=True)
		return {"max_fps": 15, "max_width": 1600, "quality": "balanced"}


class ScreenShareManager:
	"""Drive a screen sharing session and carry its signalling over the relay."""

	def __init__(self, transport, negotiator, role):
		self.transport = transport
		self.negotiator = negotiator
		self.role = role
		self.state = STATE_IDLE
		#: Identifier of the peer this session is held with.
		self.peer_id = None
		self.helper = edge_engine.EdgeEngine(self._handle_helper_event, self._handle_helper_exit)
		#: ICE servers given by the relay, used as a fallback when a direct link fails.
		self.ice_servers = []
		#: Whether the peer agreed, for this session, to be driven with the mouse.
		self.input_allowed = False
		#: Set by the controlled session, so that accepting to share this screen also
		#: hands the mouse over without asking a second question.
		self.input_receiver = None
		callbacks = transport.callback_manager
		callbacks.register_callback("msg_" + MSG_REQUEST, self.handle_request)
		callbacks.register_callback("msg_" + MSG_RESPONSE, self.handle_response)
		callbacks.register_callback("msg_" + MSG_STOP, self.handle_stop)
		callbacks.register_callback("msg_" + MSG_OFFER, self.handle_offer)
		callbacks.register_callback("msg_" + MSG_ANSWER, self.handle_answer)
		callbacks.register_callback("msg_" + MSG_CANDIDATE, self.handle_candidate)
		callbacks.register_callback("msg_" + MSG_TURN_CREDENTIALS, self.handle_turn_credentials)
		# Both ends need the ICE servers before a session begins, and the controlled one
		# has no time to ask for them once a request arrives, so they are fetched as soon
		# as the channel is joined.
		callbacks.register_callback(TransportEvents.CONNECTED, self._on_connected)
		# A session cannot outlive the link it is signalled over.
		callbacks.register_callback(TransportEvents.DISCONNECTED, self.terminate)

	# Session control.

	def _on_connected(self):
		if is_available():
			self._request_turn_credentials()

	@property
	def active(self):
		return self.state != STATE_IDLE

	def toggle(self):
		"""Start the session when there is none, stop the current one otherwise.

		Returns a message to report to the user.
		"""
		if self.active:
			self.stop()
			# Translators: message spoken when screen sharing is turned off
			return _("Screen sharing stopped")
		return self.start()

	def start(self):
		"""Ask the controlled computer to share its screen. Returns a message to report."""
		if self.role != ROLE_VIEWER:
			# Translators: message spoken when screen sharing is requested from the wrong computer
			return _("Screen sharing can only be started from the controlling computer")
		if not is_available():
			# Translators: message spoken when screen sharing cannot run on this computer
			return _("Screen sharing is not available on this computer")
		peers = self.negotiator.peers_supporting(capabilities.FEATURE_SCREEN_SHARE)
		if not peers:
			# Translators: message spoken when the other computer cannot share its screen
			return _("The other computer does not support screen sharing")
		self.peer_id = peers[0]
		self.state = STATE_REQUESTING
		# The relay only hands out TURN credentials to clients which asked for them,
		# and they expire, so they are requested for each session rather than kept.
		self._request_turn_credentials()
		# Seeing a screen without being able to point at it is of little use, so the mouse
		# is always asked for. The controlled computer alone decides whether to grant it.
		self._send(MSG_REQUEST, allow_input=True)
		# Translators: message spoken when screen sharing has been requested
		return _("Screen sharing requested")

	def stop(self, notify_peer=True):
		"""End the current session, telling the peer about it unless it asked for it."""
		if not self.active:
			return
		if notify_peer and self.peer_id is not None:
			self._send(MSG_STOP)
		self.state = STATE_IDLE
		self.peer_id = None
		self.ice_servers = []
		self.input_allowed = False
		self.helper.stop()

	def terminate(self):
		"""Release everything, when the session or NVDA itself is going away."""
		self.stop(notify_peer=False)

	# Signalling received from the peer.

	def handle_request(self, origin=None, allow_input=False, **kwargs):
		"""The controlling computer asks this one to share its screen."""
		if not self._accept_from(origin):
			return
		if self.role != ROLE_PUBLISHER or not is_available():
			self._refuse(origin, "unavailable")
			return
		if self.active:
			self._refuse(origin, "busy")
			return
		# Remote input is only ever granted when this computer allows it, whatever the
		# controlling computer asked for.
		allow_input = bool(allow_input) and is_input_control_allowed()
		wx.CallAfter(self._ask_permission, origin, allow_input)

	def _ask_permission(self, origin, allow_input):
		if allow_input:
			# Translators: question asked before this screen is shared, with mouse control
			question = _("Do you want to share your screen? The controlling computer will see this screen and will be able to use its mouse.")
		else:
			# Translators: question asked before this screen is shared
			question = _("Do you want to share your screen? The controlling computer will see this screen.")
		answer = gui.messageBox(
			parent=gui.mainFrame,
			# Translators: title of the screen sharing request dialog
			caption=_("Screen sharing request"),
			message=question,
			style=wx.YES_NO | wx.NO_DEFAULT | wx.ICON_QUESTION,
		)
		if answer == wx.YES:
			self._accept_request(origin, allow_input)
		else:
			self._refuse(origin, "declined")

	def _accept_request(self, origin, allow_input):
		if self.active:
			# The user took long enough to answer that another session started.
			self._refuse(origin, "busy")
			return
		self.peer_id = origin
		self.state = STATE_CONNECTING
		try:
			self.helper.start(ROLE_PUBLISHER)
		except Exception:
			logger.exception("Unable to start the screen sharing video engine")
			self.state = STATE_IDLE
			self.peer_id = None
			self._refuse(origin, "unavailable")
			return
		if allow_input and self.input_receiver is not None:
			# The user has just answered the only question there is, so the mouse events
			# which follow must not open a second one.
			self.input_receiver.granted = True
		self._send(MSG_RESPONSE, accepted=True, allow_input=allow_input)
		self.helper.send(
			command="start",
			role=ROLE_PUBLISHER,
			allow_input=allow_input,
			ice_servers=self.ice_servers,
			**_capture_settings()
		)
		# Translators: message spoken on the controlled computer when it starts sharing its screen
		ui.message(_("Sharing this screen"))

	def _refuse(self, origin, reason):
		self._send(MSG_RESPONSE, target=origin, accepted=False, reason=reason)

	def handle_response(self, origin=None, accepted=False, allow_input=False, reason="", **kwargs):
		"""The controlled computer answered our request."""
		if not self._accept_from(origin) or origin != self.peer_id:
			return
		if self.state != STATE_REQUESTING:
			return
		if not accepted:
			self.state = STATE_IDLE
			self.peer_id = None
			ui.message(_refusal_message(reason))
			return
		self.state = STATE_CONNECTING
		try:
			self.helper.start(ROLE_VIEWER)
		except Exception:
			logger.exception("Unable to start the screen sharing video engine")
			self.stop()
			# Translators: message spoken when the screen sharing window could not be opened
			ui.message(_("Unable to start screen sharing"))
			return
		self.input_allowed = bool(allow_input)
		self.helper.send(
			command="start",
			role=ROLE_VIEWER,
			allow_input=self.input_allowed,
			ice_servers=self.ice_servers,
		)

	def handle_stop(self, origin=None, **kwargs):
		if not self._accept_from(origin) or origin != self.peer_id:
			return
		self.stop(notify_peer=False)
		# Translators: message spoken when the other computer ended screen sharing
		ui.message(_("Screen sharing ended"))

	def handle_offer(self, origin=None, sdp=None, **kwargs):
		self._forward_to_helper(origin, "offer", sdp=sdp)

	def handle_answer(self, origin=None, sdp=None, **kwargs):
		self._forward_to_helper(origin, "answer", sdp=sdp)

	def handle_candidate(self, origin=None, candidate=None, **kwargs):
		self._forward_to_helper(origin, "candidate", candidate=candidate)

	def handle_turn_credentials(self, ice_servers=None, **kwargs):
		"""The relay sent the temporary credentials of its TURN server."""
		if isinstance(ice_servers, list):
			self.ice_servers = ice_servers

	def _forward_to_helper(self, origin, kind, **payload):
		"""Hand a session description or an ICE candidate over to the video engine."""
		if not self._accept_from(origin) or origin != self.peer_id:
			return
		if self.state not in (STATE_CONNECTING, STATE_ACTIVE):
			return
		for value in payload.values():
			if not isinstance(value, str) or len(value) > MAX_SIGNALING_PAYLOAD:
				logger.warning("Discarding an oversized or malformed %s", kind)
				return
		self.helper.send(command=kind, **payload)

	def _accept_from(self, origin):
		"""Whether a signalling message really comes from an identified peer.

		The origin is stamped by the relay, so a client cannot claim to be another
		one. A message without an origin comes from a relay too old to be trusted
		for one to one delivery, and is therefore dropped.
		"""
		if origin is None:
			logger.debug("Discarding a screen sharing message without an origin")
			return False
		return True

	# Events coming from the video engine.

	def _handle_helper_event(self, event):
		kind = event.get("event")
		if kind == "offer":
			self._send(MSG_OFFER, sdp=event.get("sdp", ""))
		elif kind == "answer":
			self._send(MSG_ANSWER, sdp=event.get("sdp", ""))
		elif kind == "candidate":
			self._send(MSG_CANDIDATE, candidate=event.get("candidate", ""))
		elif kind == "input":
			self._forward_input(event)
		elif kind == "connected":
			self.state = STATE_ACTIVE
			# Translators: message spoken when the screen sharing picture starts flowing
			wx.CallAfter(ui.message, _("Screen sharing started"))
		elif kind == "failed":
			logger.warning("Screen sharing failed: %s", event.get("reason", ""))
			wx.CallAfter(self._report_failure)
		elif kind == "closed":
			wx.CallAfter(self._handle_helper_exit)

	def _forward_input(self, event):
		"""Send a mouse event aimed at the picture to the computer being watched.

		The page reports positions as fractions of the picture, which are already the
		fractions of the virtual desktop the other computer expects, so the event
		travels as an ordinary mouse message and is applied by the very same code as
		the remote mouse used without any picture.
		"""
		from . import mouse_control
		if self.role != ROLE_VIEWER or not self.input_allowed:
			return
		if self.state not in (STATE_CONNECTING, STATE_ACTIVE):
			return
		action = event.get("t")
		if action not in (
			mouse_control.ACTION_MOVE,
			mouse_control.ACTION_BUTTON_DOWN,
			mouse_control.ACTION_BUTTON_UP,
			mouse_control.ACTION_WHEEL,
		):
			return
		payload = {"t": action}
		for name in ("x", "y"):
			value = event.get(name)
			if not isinstance(value, (int, float)) or isinstance(value, bool):
				return
			if not 0.0 <= value <= 1.0:
				return
			payload[name] = round(float(value), 5)
		if action in (mouse_control.ACTION_BUTTON_DOWN, mouse_control.ACTION_BUTTON_UP):
			button = event.get("b")
			if button not in mouse_control.BUTTONS:
				return
			payload["b"] = button
		elif action == mouse_control.ACTION_WHEEL:
			delta = event.get("d")
			if not isinstance(delta, int) or isinstance(delta, bool) or not delta:
				return
			limit = mouse_control.MAX_WHEEL_NOTCHES
			payload["d"] = max(-limit, min(limit, delta))
			payload["h"] = bool(event.get("h"))
		try:
			self.transport.send(type=mouse_control.MESSAGE_TYPE, **payload)
		except Exception:
			logger.exception("Unable to send a mouse event coming from the screen sharing window")
			return
		configuration.record_activity()

	def _handle_helper_exit(self):
		if not self.active:
			return
		self.stop()
		# Translators: message spoken when screen sharing stopped unexpectedly
		wx.CallAfter(ui.message, _("Screen sharing ended"))

	def _report_failure(self):
		self.stop()
		# Translators: message spoken when the screen sharing link could not be established
		ui.message(_("Unable to establish the screen sharing connection"))

	# Sending.

	def _request_turn_credentials(self):
		"""Ask the relay for the credentials of its TURN server.

		This one is answered by the relay itself, so it carries no target.
		"""
		try:
			self.transport.send(type=MSG_TURN_CREDENTIALS)
		except Exception:
			logger.exception("Unable to ask the relay for TURN credentials")

	def _send(self, message_type, target=None, **kwargs):
		"""Send a signalling message to the current peer, or to the relay itself.

		Unlike every other message of the protocol, these are delivered to a single
		client, which the relay picks from the target field.
		"""
		if target is None:
			target = self.peer_id
		try:
			self.transport.send(type=message_type, target=target, **kwargs)
		except Exception:
			logger.exception("Unable to send the %s screen sharing message", message_type)


def _refusal_message(reason):
	if reason == "declined":
		# Translators: message spoken when the other computer refused to share its screen
		return _("The other computer refused to share its screen")
	if reason == "busy":
		# Translators: message spoken when the other computer is already sharing its screen
		return _("The other computer is already sharing its screen")
	# Translators: message spoken when the other computer cannot share its screen
	return _("The other computer is unable to share its screen")
