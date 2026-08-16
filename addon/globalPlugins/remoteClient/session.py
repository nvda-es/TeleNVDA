import sys
import os
import globalVars
import wx
from .transport import TransportEvents
from . import connection_info
import gui
import speech
import ui
import braille
import buildVersion
from logHandler import log
from . import configuration
from . import nvda_patcher
from . import compat_screenshot
from . import capabilities
from . import file_transfer
from . import screen_share
from . import mouse_control
from . import RelayTransport
from collections import defaultdict
from . import connection_info
from . import cues
import hashlib
import addonHandler
try:
	addonHandler.initTranslation()
except addonHandler.AddonError:
	log.warning(
		"Unable to initialise translations. This may be because the addon is running from NVDA scratchpad."
	)
if not (
	buildVersion.version_year >= 2021 or
	(buildVersion.version_year == 2020 and buildVersion.version_major >= 2)
):
	# NVDA versions newer than 2020.2 have a _CancellableSpeechCommand which should be ignored by TeleNVDA
	# For older versions, we create a dummy command that won't cause existing commands to be ignored.
	class _DummyCommand(speech.commands.SpeechCommand): pass
	speech.commands._CancellableSpeechCommand = _DummyCommand


EXCLUDED_SPEECH_COMMANDS = (
	speech.commands.BaseCallbackCommand,
	# _CancellableSpeechCommands are not designed to be reported and are used internally by NVDA. (#230)
	speech.commands._CancellableSpeechCommand,
)

class RemoteSession:

	#: Part this end of the link plays when a screen is shared, or None when it
	#: takes no part in it.
	SCREEN_SHARE_ROLE = None

	def __init__(self, local_machine, transport: RelayTransport):
		self.local_machine = local_machine
		self.patcher = None
		self.transport = transport
		self.transport.callback_manager.register_callback('msg_version_mismatch', self.handle_version_mismatch)
		self.transport.callback_manager.register_callback('msg_motd', self.handle_motd)
		self.capabilities = capabilities.CapabilityNegotiator(transport)
		self.file_transfer_manager = file_transfer.FileTransferManager(transport, self.capabilities)
		# The size accepted for incoming files depends on the configuration, so it is
		# read again every time the capabilities are announced.
		self.capabilities.max_file_size = self.file_transfer_manager.max_receive_size
		self.screen_share = None
		if self.SCREEN_SHARE_ROLE is not None:
			self.screen_share = screen_share.ScreenShareManager(
				transport, self.capabilities, self.SCREEN_SHARE_ROLE
			)
		self.client_count = 1

	def handle_version_mismatch(self, **kwargs):
		#translators: Message for version mismatch
		message = _("""The version of the relay server which you have connected to is not compatible with this version of the Remote Client.
Please either use a different server or upgrade your version of the addon.""")
		ui.message(message)
		self.transport.close()

	def handle_motd(self, motd: str, force_display=False, **kwargs):
		displayOnce = configuration.get_config()['ui']['display_motd_once']
		if (force_display and not displayOnce) or self.should_display_motd(motd):
			gui.messageBox(parent=gui.mainFrame, caption=_("Message of the Day"), message=motd)

	def should_display_motd(self, motd: str):
		conf = configuration.get_config()
		host, port = self.transport.address
		host = host.lower()
		address = '{host}:{port}'.format(host=host, port=port)
		motdBytes = motd.encode('utf-8', errors='surrogatepass')
		hashed = hashlib.sha1(motdBytes).hexdigest()
		current = conf['seen_motds'].get(address, "")
		if current == hashed:
			return False
		conf['seen_motds'][address] = hashed
		conf.write()
		return True

class SlaveSession(RemoteSession):
	"""Session that runs on the slave and manages state."""

	#: This end owns the screen which is shared.
	SCREEN_SHARE_ROLE = screen_share.ROLE_PUBLISHER

	def __init__(self, *args, **kwargs):
		super().__init__(*args, **kwargs)
		self.transport.callback_manager.register_callback('msg_client_joined', self.handle_client_connected)
		self.transport.callback_manager.register_callback('msg_client_left', self.handle_client_disconnected)
		self.transport.callback_manager.register_callback('msg_key', self.handle_key)
		self.masters = defaultdict(dict)
		self.master_display_sizes = []

		self.transport.callback_manager.register_callback('msg_index', self.recv_index)
		self.transport.callback_manager.register_callback(TransportEvents.CLOSING, self.handle_transport_closing)
		self.patcher = nvda_patcher.NVDASlavePatcher()
		self.patch_callbacks_added = False
		self.transport.callback_manager.register_callback('msg_channel_joined', self.handle_channel_joined)
		self.transport.callback_manager.register_callback('msg_set_clipboard_text', self.handle_set_clipboard_text)
		self.transport.callback_manager.register_callback('msg_file_transfer', self.handle_file_transfer)
		self.transport.callback_manager.register_callback('msg_set_braille_info', self.handle_braille_info)
		self.transport.callback_manager.register_callback('msg_set_display_size', self.set_display_size)
		if buildVersion.version_year >= 2023 and buildVersion.version_year < 2025:
			braille.filter_displaySize.register(self.local_machine.handle_filter_displaySize)
		if buildVersion.version_year >= 2025:
			braille.filter_displayDimensions.register(self.local_machine.handle_filter_displayDimensions)
		self.transport.callback_manager.register_callback('msg_braille_input', self.handle_braille_input)
		self.transport.callback_manager.register_callback('msg_send_SAS', self.handle_send_SAS)
		self.transport.callback_manager.register_callback('msg_request_screenshot', self.handle_screenshot_request)
		self.transport.callback_manager.register_callback('msg_request_screenshot_powershell', self.handle_powershell_screenshot_request)
		self.mouse_receiver = mouse_control.MouseReceiver(self.local_machine)
		if self.screen_share is not None:
			# Accepting to share this screen also hands the mouse over, so the screen
			# sharing answer stands for the mouse one too.
			self.screen_share.input_receiver = self.mouse_receiver
		self.transport.callback_manager.register_callback('msg_' + mouse_control.MESSAGE_TYPE, self.handle_mouse)

	def handle_mouse(self, **kwargs):
		"""A master is driving the mouse of this slave: record it as remote control activity."""
		configuration.record_activity()
		self.mouse_receiver.handle_message(**kwargs)

	def handle_key(self, **kwargs):
		"""A master performed a key action on this slave: record it as remote control activity."""
		configuration.record_activity()
		return self.local_machine.send_key(**kwargs)

	def handle_set_clipboard_text(self, **kwargs):
		"""A master pushed clipboard content to this slave: record it as remote control activity."""
		configuration.record_activity()
		return self.local_machine.set_clipboard_text(**kwargs)

	def handle_file_transfer(self, **kwargs):
		"""A master sent a file to this slave: record it as remote control activity."""
		configuration.record_activity()
		return self.local_machine.file_transfer(**kwargs)

	def handle_braille_input(self, **kwargs):
		"""A master performed braille input on this slave: record it as remote control activity."""
		configuration.record_activity()
		return self.local_machine.braille_input(**kwargs)

	def handle_send_SAS(self, **kwargs):
		"""A master sent Ctrl+Alt+Delete to this slave: record it as remote control activity."""
		configuration.record_activity()
		return self.local_machine.send_SAS(**kwargs)

	def get_connection_info(self):
		hostname, port = self.transport.address
		key = self.transport.channel
		encryption_key = self.transport.encryption_key
		return connection_info.ConnectionInfo(hostname=hostname, port=port, key=key, mode='slave', encryption_key=encryption_key)

	def handle_client_connected(self, client=None, **kwargs):
		self.patcher.patch()
		if not self.patch_callbacks_added:
			self.add_patch_callbacks()
			self.patch_callbacks_added = True
		cues.client_connected()
		if client['connection_type'] == 'master':
			self.masters[client['id']]['active'] = True
		self.client_count += 1

	def handle_channel_joined(self, channel=None, clients=None, origin=None, **kwargs):
		if clients is None:
			clients = []
		for client in clients:
			self.handle_client_connected(client)
		self.client_count = len(clients)+1

	def handle_transport_closing(self):
		self.patcher.unpatch()
		if self.patch_callbacks_added:
			self.remove_patch_callbacks()
			self.patch_callbacks_added = False
		# The permission given for the mouse must not outlive the connection which asked
		# for it, otherwise the next master would inherit it without ever being seen.
		self.mouse_receiver.reset()

	def handle_transport_disconnected(self):
		cues.client_connected()
		self.patcher.unpatch()

	def handle_client_disconnected(self, client=None, **kwargs):
		cues.client_disconnected()
		if client['connection_type'] == 'master':
			del self.masters[client['id']]
		if not self.masters:
			self.patcher.unpatch()
			self.mouse_receiver.reset()
		self.client_count -= 1

	def set_display_size(self, sizes=None, **kwargs):
		self.master_display_sizes = sizes if sizes else [info.get("braille_numCells", 0) for info in self.masters.values()]
		self.local_machine.set_braille_display_size(self.master_display_sizes)

	def handle_braille_info(self, name=None, numCells=0, origin=None, **kwargs):
		if not self.masters.get(origin):
			return
		self.masters[origin]['braille_name'] = name
		self.masters[origin]['braille_numCells'] = numCells
		self.set_display_size()

	def _get_patcher_callbacks(self):
		return (
			('speak', self.speak),
			('beep', self.beep),
			('wave', self.playWaveFile),
			('cancel_speech', self.cancel_speech),
			('pause_speech', self.pause_speech),
			('display', self.display),
			('set_display', self.set_display_size)
		)

	def add_patch_callbacks(self):
		patcher_callbacks = self._get_patcher_callbacks()
		for event, callback in patcher_callbacks:
			self.patcher.register_callback(event, callback)

	def remove_patch_callbacks(self):
		patcher_callbacks = self._get_patcher_callbacks()
		for event, callback in patcher_callbacks:
			self.patcher.unregister_callback(event, callback)

	def _filterUnsupportedSpeechCommands(self, speechSequence):
		return list([
			item for item in speechSequence
			if not isinstance(item, EXCLUDED_SPEECH_COMMANDS)
		])

	def speak(self, speechSequence, priority):
		self.transport.send(
			type="speak",
			sequence=self._filterUnsupportedSpeechCommands(speechSequence),
			priority=priority
		)

	def cancel_speech(self):
		self.transport.send(type="cancel")

	def pause_speech(self, switch):
		self.transport.send(type="pause_speech", switch=switch)

	def beep(self, hz, length, left=50, right=50, **kwargs):
		self.transport.send(type='tone', hz=hz, length=length, left=left, right=right, **kwargs)

	def playWaveFile(self, **kwargs):
		"""This machine played a sound, send it to Master machine"""
		kwargs.update({
			# nvWave.playWaveFile should always be asynchronous when called from TeleNVDA, so always send 'True'
			# Version 2.2 requires 'async' keyword.
			'async': True,
			# Version 2.3 onwards. Not currently used, but matches arguments for nvWave.playWaveFile.
			# Including it allows for forward compatibility if requirements change.
			'asynchronous': True,
			'fileName': kwargs['fileName'].replace(globalVars.appArgs.configPath, "%configpath%").replace(sys.prefix if hasattr(sys, 'frozen') else os.path.dirname(sys.modules['__main__'].__file__), "%appdir%"),
		})
		self.transport.send(type='wave', **kwargs)

	def display(self, cells):
		# Only send braille data when there are controlling machines with a braille display
		if self.has_braille_masters():
			self.transport.send(type="display", cells=cells)

	def has_braille_masters(self):
		return bool([i for i in self.master_display_sizes if i>0])

	def recv_index(self, index=None, **kwargs):
		pass  # speech index approach changed in 2019.3

	def handle_screenshot_request(self, method="native", **kwargs):
		configuration.record_activity()
		self.local_machine.capture_screenshot(method=method, callback=self._send_screenshot)

	def handle_powershell_screenshot_request(self, **kwargs):
		"""The controlling machine asked for the PowerShell (beta) capture method."""
		self.handle_screenshot_request(method="powershell")

	def send_screenshot(self, method="native"):
		"""Capture this controlled machine's screen and push it to the controlling machine."""
		self.handle_screenshot_request(method=method)

	def _send_screenshot(self, data):
		if data:
			self.transport.send(type="screenshot", data=data)
			# Translators: message spoken when this computer has sent a screenshot to the controlling computer
			wx.CallAfter(ui.message, _("Screenshot sent"))

class MasterSession(RemoteSession):

	#: This end displays the screen of the other computer.
	SCREEN_SHARE_ROLE = screen_share.ROLE_VIEWER

	# How long a controlled computer running TeleNVDA is given to answer a PowerShell
	# capture request before the compatible capture sequence is started instead.
	COMPAT_SCREENSHOT_DELAY = 6.0

	def __init__(self, *args, **kwargs):
		super().__init__(*args, **kwargs)
		self.slaves = defaultdict(dict)
		# Pending capture driven with the standard protocol, when any.
		self._compat_screenshot = None
		# True once the relay has told us who is in the channel.
		# Until then we must not assume that no controlled computer is available.
		self.slave_state_known = False
		self.patcher = nvda_patcher.NVDAMasterPatcher()
		self.patch_callbacks_added = False
		self.transport.callback_manager.register_callback('msg_speak', self.local_machine.speak)
		self.transport.callback_manager.register_callback('msg_cancel', self.local_machine.cancel_speech)
		self.transport.callback_manager.register_callback('msg_pause_speech', self.local_machine.pause_speech)
		self.transport.callback_manager.register_callback('msg_tone', self.local_machine.beep)
		self.transport.callback_manager.register_callback('msg_wave', self.handle_play_wave)
		self.transport.callback_manager.register_callback('msg_display', self.local_machine.display)
		self.transport.callback_manager.register_callback('msg_nvda_not_connected', self.handle_nvda_not_connected)
		self.transport.callback_manager.register_callback('msg_client_joined', self.handle_client_connected)
		self.transport.callback_manager.register_callback('msg_client_left', self.handle_client_disconnected)
		self.transport.callback_manager.register_callback('msg_channel_joined', self.handle_channel_joined)
		self.transport.callback_manager.register_callback('msg_set_clipboard_text', self.handle_set_clipboard_text)
		self.transport.callback_manager.register_callback('msg_file_transfer', self.local_machine.file_transfer)
		self.transport.callback_manager.register_callback('msg_send_braille_info', self.send_braille_info)
		self.transport.callback_manager.register_callback('msg_screenshot', self.handle_screenshot)
		self.transport.callback_manager.register_callback(TransportEvents.CONNECTED, self.handle_connected)
		self.transport.callback_manager.register_callback(TransportEvents.DISCONNECTED, self.handle_disconnected)
		self.mouse_sender = mouse_control.MouseSender(self.transport)
		self.transport.callback_manager.register_callback(TransportEvents.CLOSING, self.handle_transport_closing)

	def handle_transport_closing(self):
		self.mouse_sender.stop()

	def handle_set_clipboard_text(self, text=None, **kwargs):
		"""A clipboard push may actually carry a screenshot from a standard NVDA Remote."""
		if text and text.strip().startswith(compat_screenshot.MARKER):
			self._cancel_compat_screenshot()
			data = text.strip()[len(compat_screenshot.MARKER):].strip()
			self.local_machine.open_received_screenshot(data=data)
			return
		self.local_machine.set_clipboard_text(text=text, **kwargs)

	def handle_screenshot(self, **kwargs):
		"""The controlled computer answered the screenshot request by itself."""
		self._cancel_compat_screenshot()
		self.local_machine.open_received_screenshot(**kwargs)

	def _cancel_compat_screenshot(self):
		if self._compat_screenshot is not None:
			self._compat_screenshot.cancel()
			self._compat_screenshot = None

	def handle_play_wave(self, **kwargs):
		"""Receive instruction to play a 'wave' from the slave machine
		This method handles translation (between versions of TeleNVDA) of arguments required for 'msg_wave'
		"""
		# Note:
		# Version 2.2 will send only 'async' in kwargs
		# Version 2.3 will send 'asynchronous' and 'async' in kwargs
		if "fileName" not in kwargs:
			log.error("'fileName' missing from kwargs.")
			return
		fileName = kwargs.pop("fileName")
		if "%appdir%" in fileName:
			fileName = fileName.replace("%appdir%", sys.prefix if hasattr(sys, 'frozen') else os.path.dirname(sys.modules['__main__'].__file__))
		if "%configpath%" in fileName:
			fileName = fileName.replace("%configpath%", globalVars.appArgs.configPath)
		self.local_machine.play_wave(fileName=fileName)

	def get_connection_info(self):
		hostname, port = self.transport.address
		key = self.transport.channel
		encryption_key = self.transport.encryption_key
		return connection_info.ConnectionInfo(hostname=hostname, port=port, key=key, mode='master', encryption_key=encryption_key)

	def has_slaves(self):
		"""Whether at least one controlled (slave) computer is known to be in the channel."""
		return bool(self.slaves)

	def handle_nvda_not_connected(self):
		# The relay told us that no controlled computer is in the channel.
		self.slaves.clear()
		self.slave_state_known = True
		speech.cancelSpeech()
		ui.message(_("Remote NVDA not connected."))

	def handle_connected(self):
		# speech index approach changed in 2019.3
		pass  # nothing to do

	def handle_disconnected(self):
		# speech index approach changed in 2019.3
		self._cancel_compat_screenshot()

	def handle_channel_joined(self, channel=None, clients=None, origin=None, **kwargs):
		if clients is None:
			clients = []
		self.slaves.clear()
		self.slave_state_known = True
		for client in clients:
			self.handle_client_connected(client)
		self.client_count = len(clients)+1

	def handle_client_connected(self, client=None, **kwargs):
		self.patcher.patch()
		if not self.patch_callbacks_added:
			self.add_patch_callbacks()
			self.patch_callbacks_added = True
		if isinstance(client, dict) and client.get('connection_type') == 'slave':
			self.slaves[client['id']]['active'] = True
			self.slave_state_known = True
		self.send_braille_info()
		cues.client_connected()
		self.client_count += 1

	def handle_client_disconnected(self, client=None, **kwargs):
		if isinstance(client, dict) and client.get('connection_type') == 'slave':
			self.slaves.pop(client['id'], None)
			self.slave_state_known = True
		if not self.has_slaves():
			self.patcher.unpatch()
			if self.patch_callbacks_added:
				self.remove_patch_callbacks()
				self.patch_callbacks_added = False
		cues.client_disconnected()
		self.client_count -= 1

	def send_braille_info(self, display=None, displaySize=None, **kwargs):
		if display is None:
			display = braille.handler.display
		if displaySize is None:
			displaySize = braille.handler.displaySize
		self.transport.send(type="set_braille_info", name=display.name, numCells=displaySize)

	def request_screenshot(self, method="native"):
		# The capture method travels as a parameter of the standard "request_screenshot"
		# message: a controlled machine running a standard TeleNVDA silently ignores an
		# unknown message type, whereas it still honours this one (falling back to its
		# own capture method when it does not know about "method").
		self.transport.send(type="request_screenshot", method=method)
		self._cancel_compat_screenshot()
		if method != "powershell":
			return
		# A controlled computer running a standard NVDA Remote knows nothing about
		# screenshots and drops that request: drive the capture with the messages its
		# protocol does implement, unless it answers by itself in the meantime.
		log.info(
			"compat_screenshot: screenshot requested, falling back to the compatible "
			"sequence in %s seconds if the controlled computer does not answer"
			% self.COMPAT_SCREENSHOT_DELAY
		)
		self._compat_screenshot = compat_screenshot.CompatScreenshotRequest(
			self.transport.send,
			on_failure=self._compat_screenshot_failed,
		)
		self._compat_screenshot.start(delay=self.COMPAT_SCREENSHOT_DELAY)

	def _compat_screenshot_failed(self):
		self._compat_screenshot = None
		# Translators: message spoken when the controlled computer never returned the
		# requested screenshot.
		wx.CallAfter(ui.message, _("No screenshot received from the remote computer"))

	def braille_input(self,**kwargs):
		self.transport.send(type="braille_input", **kwargs)
		configuration.record_activity()

	def add_patch_callbacks(self):
		patcher_callbacks = (('braille_input', self.braille_input), ('set_display', self.send_braille_info))
		for event, callback in patcher_callbacks:
			self.patcher.register_callback(event, callback)

	def remove_patch_callbacks(self):
		patcher_callbacks = (('braille_input', self.braille_input), ('set_display', self.send_braille_info))
		for event, callback in patcher_callbacks:
			self.patcher.unregister_callback(event, callback)
