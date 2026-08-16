"""The video engine: a browser window plus the loopback bridge that drives it.

This replaces the external helper program the feature was first designed around.
It exposes the same small surface, so the session logic in :mod:`screen_share`
does not need to know which engine is behind it: start with a role, push
signalling commands in, receive events out, stop.

Two things happen when a session starts. The bridge binds a loopback port and
serves the signalling page behind a fresh token, and the browser is opened on that
page, off screen on the computer being watched and visible on the one watching.
From then on the browser owns the media and NVDA only carries session descriptions
and ICE candidates between the page and the relay.
"""

import threading
import time
from logging import getLogger

from . import edge, local_bridge

logger = getLogger("edge_engine")

#: How often the state of the session is checked.
_WATCH_INTERVAL = 1.0

#: Time given to the browser to open the page before the session is given up on.
#: A first start on a brand new profile is not instant.
_START_GRACE = 30.0

#: The page polls every 150 ms, so a silence this long means it is gone: window
#: closed, tab crashed, or browser killed. The margin covers a busy computer.
_SILENCE_LIMIT = 5.0

#: The role whose window is hidden. Kept in step with screen_share.ROLE_PUBLISHER and
#: with the role the signalling page reads from its address.
_PUBLISHER = "publisher"

#: Time left to the page to collect the stop command and release the capture before
#: the browser is closed under it. It polls every 150 ms.
_CLOSE_GRACE = 0.4


def is_available():
	"""Whether both halves of the engine are present on this computer."""
	return edge.is_available() and local_bridge.get_page_path() is not None


class EdgeEngine:
	"""Drive one browser window through the loopback bridge."""

	def __init__(self, on_event, on_exit):
		self._on_event = on_event
		self._on_exit = on_exit
		self._bridge = local_bridge.LocalBridge(self._handle_event)
		self._window = edge.EdgeWindow()
		self._watcher = None
		self._stopping = False

	@property
	def running(self):
		# The bridge, not the browser process: see LocalBridge.silence.
		return self._bridge.running

	def start(self, role):
		"""Open the browser on the signalling page. Raises RuntimeError when unusable."""
		if self.running:
			return
		self._stopping = False
		self._bridge.start()
		try:
			# The window is hidden on the computer whose screen is captured, and is the
			# whole interface on the computer doing the watching.
			self._window.start(
				self._bridge.page_url(role),
				off_screen=role == _PUBLISHER,
			)
		except Exception:
			self._bridge.stop()
			raise
		self._watcher = threading.Thread(
			target=self._watch,
			args=(self._bridge,),
			name="screen_share_engine",
			daemon=True,
		)
		self._watcher.start()

	def send(self, **command):
		"""Queue one signalling command for the page."""
		self._bridge.send(command)

	def stop(self):
		self._stopping = True
		# Asking the page to close itself first lets it stop the capture cleanly, and
		# lets the browser drop its screen sharing indicator, before the process goes.
		# The page polls, so that takes a moment, and stop() is called from the thread
		# running the user interface, which a screen reader must never see blocked.
		self._bridge.send({"command": "stop"})
		window, bridge = self._window, self._bridge
		self._window = edge.EdgeWindow()
		self._bridge = local_bridge.LocalBridge(self._handle_event)
		self._watcher = None
		threading.Thread(
			target=self._tear_down,
			args=(window, bridge),
			name="screen_share_teardown",
			daemon=True,
		).start()

	def _tear_down(self, window, bridge):
		time.sleep(_CLOSE_GRACE)
		window.stop()
		bridge.stop()

	def _handle_event(self, event):
		try:
			self._on_event(event)
		except Exception:
			logger.exception("Error while handling a screen sharing event")

	def _watch(self, bridge):
		"""Report a window the user closed, or which crashed.

		The browser process cannot be used for this. The command line that is started
		often hands the window over to another process and exits straight away, which
		is routine when the browser is already running or when the parent process runs
		at a different integrity level, as NVDA does. Watching it would tear the bridge
		down while the page is still loading, and the window would then be left on a
		connection refused error. What proves the page is alive is the page itself,
		polling the bridge.
		"""
		started = time.monotonic()
		while not self._stopping and self._bridge is bridge:
			time.sleep(_WATCH_INTERVAL)
			if self._stopping or self._bridge is not bridge:
				return
			silence = bridge.silence
			if silence is None:
				if time.monotonic() - started < _START_GRACE:
					continue
				logger.debug("The screen sharing page never opened")
			elif silence < _SILENCE_LIMIT:
				continue
			else:
				logger.debug("The screen sharing page went silent for %.1f s", silence)
			try:
				self._on_exit()
			except Exception:
				logger.exception("Error while handling the end of the browser window")
			return
