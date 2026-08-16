"""Driving the mouse of the controlled computer from the controlling one.

This travels over the relay both computers are already connected to, as ordinary
``mouse`` messages, and is applied with ``SendInput``. Nothing here needs WebRTC, a
helper program or any change to the relay, so remote mouse control works against
every TeleNVDA server, including the ones which know nothing about screen sharing.

The feature is useful even without a picture. Moving the remote pointer makes the
screen reader of the controlled computer announce whatever sits under it, and that
speech already comes back through the existing connection, so a blind user can
explore a remote screen by ear or in braille while no pixel is ever transmitted.

Coordinates are always fractions of the virtual desktop, between 0 and 1, never
pixels. The two computers rarely share a resolution, a scaling factor or a monitor
layout, so a pixel position would land somewhere else on the other side.
"""

from logging import getLogger

import wx

import addonHandler
import gui
import ui

from . import configuration, cues, input

logger = getLogger("mouse_control")

try:
	addonHandler.initTranslation()
except addonHandler.AddonError:
	logger.warning("Unable to initialise translations. This may be because the addon is running from NVDA scratchpad.")

#: Message carrying one mouse event to the controlled computer.
MESSAGE_TYPE = "mouse"

#: Kinds of event a message may hold.
ACTION_MOVE = "m"
ACTION_BUTTON_DOWN = "md"
ACTION_BUTTON_UP = "mu"
ACTION_WHEEL = "w"

#: Buttons which may be named in a message.
BUTTONS = ("left", "right", "middle")

#: Shortest delay between two movements sent to the other computer. Pointing does not
#: need to be smooth, it needs to be accurate, and a screen reader has better things
#: to do than flooding the relay.
MOVE_INTERVAL = 0.05

#: Movements smaller than this fraction of the virtual desktop are not worth a
#: message. It is roughly one pixel on a wide desktop.
MOVE_THRESHOLD = 0.0002

#: Largest number of notches accepted in a wheel message, so that a broken or hostile
#: peer cannot make the controlled computer scroll to the end of a document.
MAX_WHEEL_NOTCHES = 10


def is_remote_input_allowed():
	"""Whether this computer lets the controlling one use its mouse.

	There is a single permission for the whole "let the other computer see and drive
	this screen" feature: allowing the screen to be shared also allows its mouse to be
	used. Nothing happens without the user answering the question asked when the
	controlling computer actually asks for it.
	"""
	try:
		return bool(configuration.get_config()["screen_share"]["enabled"])
	except Exception:
		logger.debug("Unable to read the screen sharing configuration", exc_info=True)
		return False


class MouseSender:
	"""Controlling side: mirror the local pointer onto the controlled computer.

	The pointer is polled rather than hooked. Movement fires hundreds of times per
	second, and a low level hook whose callback is too slow is silently removed by
	Windows, which would break every other use of that hook.
	"""

	def __init__(self, transport):
		self.transport = transport
		self.enabled = False
		self._timer = None
		self._last_position = None

	def start(self):
		"""Begin mirroring the pointer onto the controlled computer."""
		if self.enabled:
			return
		self.enabled = True
		self._last_position = None
		self._timer = wx.Timer(gui.mainFrame)
		gui.mainFrame.Bind(wx.EVT_TIMER, self._on_timer, self._timer)
		self._timer.Start(int(MOVE_INTERVAL * 1000))

	def stop(self):
		if not self.enabled:
			return
		self.enabled = False
		self._last_position = None
		timer = self._timer
		self._timer = None
		if timer is not None:
			# The connection may go away from a background thread, and a wx timer must
			# only be touched from the thread running the interface.
			wx.CallAfter(self._release_timer, timer)

	def _release_timer(self, timer):
		timer.Stop()
		gui.mainFrame.Unbind(wx.EVT_TIMER, handler=self._on_timer, source=timer)

	def _send(self, **payload):
		try:
			self.transport.send(type=MESSAGE_TYPE, **payload)
		except Exception:
			logger.exception("Unable to send a mouse event")
			return False
		configuration.record_activity()
		return True

	def _on_timer(self, event):
		if not self.enabled:
			return
		position = input.get_cursor_position()
		if position is None:
			return
		if self._last_position is not None:
			dx = abs(position[0] - self._last_position[0])
			dy = abs(position[1] - self._last_position[1])
			if dx < MOVE_THRESHOLD and dy < MOVE_THRESHOLD:
				return
		self._last_position = position
		self._send(t=ACTION_MOVE, x=round(position[0], 5), y=round(position[1], 5))

	def handle_hook_event(self, action=None, button=None, pressed=None, delta=None, horizontal=False, **kwargs):
		"""Forward a button or wheel event reported by the local mouse hook."""
		if not self.enabled:
			return
		# Buttons carry the position too, so that a click always lands where the user
		# aimed even if the last movement message was dropped or throttled away.
		position = input.get_cursor_position()
		payload = {}
		if position is not None:
			self._last_position = position
			payload["x"] = round(position[0], 5)
			payload["y"] = round(position[1], 5)
		if action == "button":
			self._send(
				t=ACTION_BUTTON_DOWN if pressed else ACTION_BUTTON_UP,
				b=button,
				**payload,
			)
		elif action == "wheel":
			self._send(t=ACTION_WHEEL, d=int(delta), h=bool(horizontal), **payload)


class MouseReceiver:
	"""Controlled side: apply the mouse events sent by the controlling computer.

	Permission is asked once per connection and remembered only for as long as it
	lasts, so that reconnecting never silently reuses an old answer.
	"""

	def __init__(self, local_machine):
		self.local_machine = local_machine
		#: None while the question has not been answered yet.
		self.granted = None
		#: True while the question is on screen, so that the flood of events which
		#: follows the first one does not open a dialog for each of them.
		self._asking = False

	def reset(self):
		"""Forget the answer, when the connection goes away."""
		self.granted = None
		self._asking = False

	def handle_message(self, **payload):
		if not is_remote_input_allowed():
			return
		if self.granted is False:
			return
		if self.granted is None:
			# The question is never skipped: the mouse is never handed over silently.
			if not self._asking:
				self._asking = True
				wx.CallAfter(self._ask_permission)
			return
		self.local_machine.send_mouse(**payload)

	def _ask_permission(self):
		answer = gui.messageBox(
			parent=gui.mainFrame,
			# Translators: title of the dialog asking whether the remote computer may use this mouse.
			caption=_("Remote mouse control"),
			# Translators: question asked before the controlling computer may use this mouse.
			message=_("The controlling computer asks to use the mouse and the keyboard of this computer. Do you accept?"),
			style=wx.YES_NO | wx.NO_DEFAULT | wx.ICON_QUESTION,
		)
		self._asking = False
		self.granted = answer == wx.YES
		if self.granted:
			cues.client_connected()
			# Translators: reported on the controlled computer when remote mouse control starts.
			ui.message(_("The controlling computer is now using this mouse"))
		else:
			# Translators: reported on the controlled computer when remote mouse control is refused.
			ui.message(_("Remote mouse control refused"))
