import addonHandler
import api
import configobj
import core
import braille
import ctypes
import ctypes.wintypes
import globalVars
import gui
from gui import NVDASettingsDialog
import IAccessibleHandler
import json
import logging
import os
import queueHandler
try:
	from winAPI.secureDesktop import post_secureDesktopStateChange
except:
	post_secureDesktopStateChange = None
try:
	from utils import security
except:
	pass
import buildVersion
import shlobj
import speech
import socket
import ssl
import sys
import threading
import ui
import uuid
import wx
from config import conf as nvda_conf
from globalPluginHandler import GlobalPlugin as _GlobalPlugin
from keyboardHandler import KeyboardInputGesture
try:
	# Added in NVDA 2025.1, used by NVDA's own Remote Access to avoid triggering an
	# action on the controlled computer when releasing modifiers.
	from keyboardHandler import canModifiersPerformAction
except ImportError:
	canModifiersPerformAction = None
from logHandler import log
from scriptHandler import script
from winUser import WM_QUIT
try:
	from winUser import VK_NONE
except ImportError:
	#: Reserved virtual key code used to notify the controlled computer that its key state changed.
	VK_NONE = 0xFF

logger = logging.getLogger(__name__)

_INACTIVITY_MONITOR_MAX_DELAY_SECONDS = 24 * 24 * 60 * 60
_INACTIVITY_MONITOR_IDLE_DELAY_SECONDS = 60

_SW_SHOW = 5
_SW_RESTORE = 9

def _get_user32():
	"""Return a private user32 binding with the prototypes needed to force a window to the foreground.

	A dedicated ctypes.WinDLL instance is used so that the restype/argtypes set here
	cannot interfere with the ones NVDA sets on its own user32 binding.
	"""
	user32 = getattr(_get_user32, "_cached", None)
	if user32 is not None:
		return user32
	user32 = ctypes.WinDLL("user32")
	user32.GetForegroundWindow.restype = ctypes.wintypes.HWND
	user32.GetForegroundWindow.argtypes = []
	user32.SetForegroundWindow.restype = ctypes.wintypes.BOOL
	user32.SetForegroundWindow.argtypes = [ctypes.wintypes.HWND]
	user32.BringWindowToTop.restype = ctypes.wintypes.BOOL
	user32.BringWindowToTop.argtypes = [ctypes.wintypes.HWND]
	user32.SetActiveWindow.restype = ctypes.wintypes.HWND
	user32.SetActiveWindow.argtypes = [ctypes.wintypes.HWND]
	user32.ShowWindow.restype = ctypes.wintypes.BOOL
	user32.ShowWindow.argtypes = [ctypes.wintypes.HWND, ctypes.c_int]
	user32.IsWindow.restype = ctypes.wintypes.BOOL
	user32.IsWindow.argtypes = [ctypes.wintypes.HWND]
	user32.IsIconic.restype = ctypes.wintypes.BOOL
	user32.IsIconic.argtypes = [ctypes.wintypes.HWND]
	user32.GetWindowThreadProcessId.restype = ctypes.wintypes.DWORD
	user32.GetWindowThreadProcessId.argtypes = [ctypes.wintypes.HWND, ctypes.c_void_p]
	user32.AttachThreadInput.restype = ctypes.wintypes.BOOL
	user32.AttachThreadInput.argtypes = [ctypes.wintypes.DWORD, ctypes.wintypes.DWORD, ctypes.wintypes.BOOL]
	_get_user32._cached = user32
	return user32

def force_window_to_foreground(handle):
	"""Try hard to bring the given window to the foreground.

	Windows refuses SetForegroundWindow when the calling thread does not own the
	foreground window, so its input queue is temporarily attached to the foreground one.
	Returns True only if the window really ended up in the foreground.
	"""
	if not handle:
		return False
	try:
		user32 = _get_user32()
		handle = ctypes.wintypes.HWND(int(handle))
		if not user32.IsWindow(handle):
			return False
		foreground = user32.GetForegroundWindow()
		if foreground == handle.value:
			return True
		current_thread = ctypes.windll.kernel32.GetCurrentThreadId()
		foreground_thread = user32.GetWindowThreadProcessId(foreground, None) if foreground else 0
		attached = False
		if foreground_thread and foreground_thread != current_thread:
			attached = bool(user32.AttachThreadInput(foreground_thread, current_thread, True))
		try:
			user32.ShowWindow(handle, _SW_RESTORE if user32.IsIconic(handle) else _SW_SHOW)
			user32.BringWindowToTop(handle)
			user32.SetForegroundWindow(handle)
			user32.SetActiveWindow(handle)
		finally:
			if attached:
				user32.AttachThreadInput(foreground_thread, current_thread, False)
		return user32.GetForegroundWindow() == handle.value
	except Exception:
		logger.debug("Unable to force a window to the foreground", exc_info=True)
		return False

from . import bridge
from . import configuration
from . import cues
from . import dialogs
if buildVersion.version_year < 2025:
	from . import keyboard_hook
else:
	import inputCore
from . import keep_awake
from . import local_machine
from . import mouse_hook
from . import serializer
from . import server
from . import updater
from . import url_handler
from .socket_utils import SERVER_PORT, address_to_hostport, wrap_socket
from .transport import RelayTransport, WebSocketRelayTransport, TransportEvents

from .session import MasterSession, SlaveSession
try:
	addonHandler.initTranslation()
except addonHandler.AddonError:
	log.warning(
		"Unable to initialise translations. This may be because the addon is running from NVDA scratchpad."
	)
speakOnDemand = {"speakOnDemand": True} if buildVersion.version_year >= 2024 else {}
if buildVersion.version_year < 2025:
	logging.getLogger("keyboard_hook").addHandler(logging.StreamHandler(sys.stdout))

client = None

class GlobalPlugin(_GlobalPlugin):
	# Translators: script category for add-on gestures
	scriptCategory = _("TeleNVDA")

	def __init__(self, *args, **kwargs):
		global client
		super().__init__(*args, **kwargs)
		if client is not None and not getattr(client, "_terminated", False):
			raise RuntimeError("TeleNVDA is already running. Only one instance can be loaded at a time.")
		for addon in addonHandler.getAvailableAddons(): 
			if addon.name == "remote" and not addon.isDisabled:
				raise RuntimeError("TeleNVDA cannot be used while NVDA Remote is running. Please, disable NVDA Remote and restart NVDA.")
		self.local_machine = local_machine.LocalMachine()
		self.update_manager = updater.UpdateManager()
		self._startup_update_checked = False
		self._terminated = False
		self._inactivity_timer = None
		self.slave_session = None
		self.master_session = None
		self.create_menu()
		NVDASettingsDialog.categoryClasses.append(dialogs.OptionsDialog)
		self.connecting = False
		self.muted = False # Used to know if mute remote was activated manually
		self.url_handler_window = url_handler.URLHandlerWindow(callback=self.verify_connect)
		url_handler.register_url_handler()
		self.master_transport = None
		self.slave_transport = None
		self.server = None
		self.hook_thread = None
		self.mouse_hook_thread = None
		self.mouse_hook = None
		self.sending_keys = False
		# Whether keyboard control was taken by starting a screen sharing session, so that
		# ending that session gives the keyboard back rather than leaving it stranded.
		self.screen_share_took_control = False
		self.key_modifiers = set()
		self.hostPendingModifiers = set()
		self.hostPendingNonmodifier = None
		self.master_connection_interrupted = False
		self.master_disconnect_requested = False
		self.ignoreGesture = False
		# Scripts which stay handled locally while keystrokes are sent to the
		# controlled computer, instead of being forwarded to it.
		self.guestScripts = (
			self.script_sendKeys,
			self.script_ignoreNextGesture,
			self.script_screenshot,
			self.script_screenshot_powershell,
			self.script_toggle_screen_share,
			self.script_toggle_remote_mouse,
		)
		self.is_connect_dialog_open = False
		self._connect_dialog = None
		self.sd_server = None
		self.sd_relay = None
		self.sd_bridge = None
		try:
			configuration.get_config()
		except configobj.ParseError:
			os.remove(os.path.abspath(os.path.join(globalVars.appArgs.configPath, configuration.CONFIG_FILE_NAME)))
			queueHandler.queueFunction(queueHandler.eventQueue, wx.CallAfter, wx.MessageBox, _("Your NVDA Remote configuration was corrupted and has been reset."), _("NVDA Remote Configuration Error"), wx.OK|wx.ICON_EXCLAMATION)
		self._schedule_inactivity_monitor()
		if hasattr(shlobj, 'SHGetKnownFolderPath'):
			self.temp_location = os.path.join(shlobj.SHGetKnownFolderPath(shlobj.FolderId.PROGRAM_DATA), 'temp')
		else:
			self.temp_location = os.path.join(shlobj.SHGetFolderPath(0, shlobj.CSIDL_COMMON_APPDATA), 'temp')
		self.ipc_file = os.path.join(self.temp_location, 'remote.ipc')
		self.sd_focused = False
		if post_secureDesktopStateChange:
			post_secureDesktopStateChange.register(self.onSecureDesktopChange)
		if hasattr(globalVars, 'teleNVDA'):
			self.postStartupHandler()
		core.postNvdaStartup.register(self.postStartupHandler)
		globalVars.teleNVDA = None
		self.keep_awake = keep_awake.KeepAwake()
		self.keep_awake.start()
		self.start_mouse_hook()
		if buildVersion.version_year >= 2025:
			# Like NVDA's own Remote Access, the handler stays registered for the whole life of
			# the plugin and simply returns early when keys are not being sent to the remote
			# computer. Registering and unregistering it on every toggle made the local keyboard
			# unusable whenever the two operations got out of sync.
			inputCore.decide_handleRawKey.register(self.handleRawKeys)
		client = self

	@staticmethod
	def _read_native_remote_autoconnect():
		"""Return the automatic connection configured in NVDA's own Remote Access.

		:return: a dictionary describing the connection, or ``None`` when NVDA has no
			built-in Remote Access, when its automatic connection is turned off or when
			it is incomplete.
		"""
		remote = nvda_conf.get('remote')
		if not remote:
			return None
		# NVDA 2026.1 renamed this section and some of its keys. Both spellings are
		# accepted so that the settings of earlier versions are still recognised.
		server = remote.get('controlServer') or remote.get('controlserver')
		if not server:
			return None
		if not server.get('autoconnect'):
			return None
		key = str(server.get('key') or '')
		if not key:
			return None
		self_hosted = bool(server.get('selfHosted', server.get('self_hosted', False)))
		host = str(server.get('host') or '')
		if not self_hosted and not host:
			return None
		try:
			port = int(server.get('port', SERVER_PORT))
		except (TypeError, ValueError):
			port = SERVER_PORT
		try:
			mode = int(server.get('connectionMode', server.get('connection_type', 0)))
		except (TypeError, ValueError):
			mode = 0
		return {
			'key': key,
			'self_hosted': self_hosted,
			'host': host,
			'port': port,
			# 0 means being controlled, 1 means controlling, in both add-ons.
			'connection_type': 1 if mode else 0,
		}

	def _import_native_remote_settings(self):
		"""Copy the automatic connection of NVDA's own Remote Access into TeleNVDA.

		TeleNVDA turns the Remote Access built into NVDA off, because two remote control
		features fighting over the same keyboard and the same relay make little sense.
		A user who had set up its automatic connection would otherwise silently lose it,
		so those settings are copied over once, and the connection then happens through
		TeleNVDA with the very same server, port and key.
		"""
		if configuration.readonly or globalVars.appArgs.secure:
			return
		try:
			if configuration.were_native_remote_settings_imported():
				return
			native = self._read_native_remote_autoconnect()
			config = configuration.get_config()
			cs = config['controlserver']
			if native is not None and not cs['autoconnect']:
				cs['autoconnect'] = True
				cs['self_hosted'] = native['self_hosted']
				cs['connection_type'] = native['connection_type']
				cs['key'] = native['key']
				if native['self_hosted']:
					cs['port'] = native['port']
				else:
					cs['host'] = native['host']
					# The Remote Access built into NVDA only speaks the plain TCP protocol.
					cs['transport'] = 'tcp'
				config.write()
				log.info(
					"TeleNVDA: the automatic connection of the Remote Access built into "
					"NVDA has been copied to TeleNVDA."
				)
			configuration.mark_native_remote_settings_imported()
		except Exception:
			log.exception("Unable to import the native NVDA Remote automatic connection")

	def _manage_native_remote(self):
		if configuration.readonly:
			return
		try:
			remote = nvda_conf.get('remote')
			if remote is None or 'enabled' not in remote:
				return
			managed, original_enabled = configuration.get_native_remote_state()
			if managed and configuration.should_restore_native_remote_on_reactivation():
				if remote['enabled'] != original_enabled:
					remote['enabled'] = original_enabled
					nvda_conf.save()
				configuration.clear_native_remote_state()
				return
			if not managed:
				original_enabled = bool(remote['enabled'])
				if not configuration.save_native_remote_state(original_enabled):
					return
			if remote['enabled'] is not False:
				remote['enabled'] = False
				nvda_conf.save()
		except Exception:
			log.exception("Unable to manage native NVDA Remote configuration")

	def _suppress_native_remote_autoconnect(self):
		"""Keep the Remote Access built into NVDA from connecting during this startup.

		NVDA starts its own client before the add-ons are loaded, and both listen to the
		same startup notification, the built-in one being called first. It cannot be
		stopped while NVDA is still walking its handlers, so its automatic connection is
		turned off just long enough for the notification to be over, and put back
		afterwards: the client is terminated by then anyway.
		"""
		if globalVars.appArgs.secure:
			return
		try:
			remote = nvda_conf.get('remote')
			server = (remote.get('controlServer') or remote.get('controlserver')) if remote else None
			if not server or not server.get('autoconnect'):
				return
			server['autoconnect'] = False
		except Exception:
			log.exception("Unable to suspend the native NVDA Remote automatic connection")
			return

		def restore():
			try:
				server['autoconnect'] = True
			except Exception:
				log.exception("Unable to restore the native NVDA Remote automatic connection")

		wx.CallAfter(restore)

	def _shutdown_native_remote(self):
		"""Stop the Remote Access support built into NVDA for the current session.

		Turning the NVDA option off only takes effect on the next start, because NVDA
		creates its Remote Access menu before the add-ons are loaded. Whenever that
		option was still on when NVDA started, its menu therefore sits next to the
		TeleNVDA one until the user restarts. Terminating the built-in client removes
		the duplicate menu straight away.
		"""
		if self._terminated or globalVars.appArgs.secure:
			return
		try:
			import _remoteClient
		except ImportError:
			# Versions of NVDA older than 2025.1 have no built-in Remote Access.
			return
		try:
			if not _remoteClient.remoteRunning():
				return
			_remoteClient.terminate()
		except Exception:
			log.exception("Unable to stop the native NVDA Remote Access client")
		else:
			log.info("TeleNVDA: the Remote Access support built into NVDA was stopped for this session.")

	def _schedule_inactivity_monitor(self):
		if self._terminated:
			return
		if self._inactivity_timer is not None:
			self._inactivity_timer.Stop()
		remaining = configuration.get_inactivity_timeout_remaining()
		if remaining is None:
			delay = _INACTIVITY_MONITOR_IDLE_DELAY_SECONDS
		else:
			delay = max(1, min(remaining, _INACTIVITY_MONITOR_MAX_DELAY_SECONDS))
		self._inactivity_timer = wx.CallLater(int(delay * 1000), self._check_inactivity)

	def restart_inactivity_monitor(self):
		self._schedule_inactivity_monitor()

	def _check_inactivity(self):
		self._inactivity_timer = None
		if self._terminated:
			return
		self._disable_autoconnect_for_inactivity()
		self._schedule_inactivity_monitor()

	def _disable_autoconnect_for_inactivity(self):
		if not configuration.should_disable_autoconnect_for_inactivity():
			return False
		cs = configuration.get_config()['controlserver']
		cs['autoconnect'] = False
		if not configuration.readonly:
			configuration.get_config().write()
		# Translators: Spoken when auto-connect is automatically disabled after a long period without remote control activity.
		ui.message(_("The auto-connect option was automatically disabled after a long period without any remote control activity."))
		log.info("TeleNVDA: auto-connect was automatically disabled after a long period without any remote control activity.")
		return True

	def postStartupHandler(self):
		self._import_native_remote_settings()
		self._manage_native_remote()
		# The built-in client also listens to postNvdaStartup, and is notified before
		# this add-on, so its automatic connection is neutralised right away.
		self._suppress_native_remote_autoconnect()
		# The built-in client cannot be stopped while NVDA is still walking its
		# handlers, so it is stopped once the notification is over.
		wx.CallAfter(self._shutdown_native_remote)
		cs = configuration.get_config()['controlserver']
		if globalVars.appArgs.secure:
			self.handle_secure_desktop()
		self._disable_autoconnect_for_inactivity()
		if cs['autoconnect'] and not self.master_session and not self.slave_session:
			wx.CallLater(50,self.perform_autoconnect)
		if (
			not self._startup_update_checked
			and not globalVars.appArgs.secure
			and configuration.get_config().get('updates', {}).get('check_at_startup', True)
		):
			self._startup_update_checked = True
			if updater.has_pending_install():
				# A previous update was downloaded but NVDA failed to finish
				# installing it. This usually happens because the older
				# version was never marked for removal, so NVDA cannot
				# replace it with the staged update and fails on every
				# restart. Try to unblock it by requesting the removal of the
				# installed version, then offer to restart so NVDA can
				# complete the pending installation.
				if updater.recover_pending_install():
					log.warning(
						"TeleNVDA: a previous update is still pending installation at %s; "
						"the installed version has been scheduled for removal so NVDA can "
						"finish the update on the next restart.",
						updater.pending_install_path(),
					)
					wx.CallLater(100, self._prompt_finish_pending_update)
				else:
					log.warning(
						"TeleNVDA: a previous update is still pending installation at %s; "
						"skipping the automatic startup update check.",
						updater.pending_install_path(),
					)
			else:
				self._start_update_check(manual=False)

	def _prompt_finish_pending_update(self):
		if self._terminated:
			return
		if gui.messageBox(
			_(
				"A previously downloaded TeleNVDA update could not be finished "
				"automatically. NVDA must be restarted to complete the "
				"installation. Restart NVDA now?"
			),
			_("TeleNVDA update"),
			wx.YES | wx.NO | wx.ICON_INFORMATION,
		) == wx.YES:
			core.restart()

	def _current_addon_version(self):
		try:
			addon = addonHandler.getCodeAddon()
			return addon.version
		except (addonHandler.AddonError, AttributeError):
			return "0.0.0"

	def _start_update_check(self, manual):
		started = self.update_manager.check_async(
			current_version=self._current_addon_version(),
			callback=self._on_update_check_finished,
			manual=manual,
		)
		if started:
			self.update_item.Enable(False)
		elif manual:
			gui.messageBox(
				_("An update check is already in progress."),
				_("TeleNVDA update"),
				wx.OK | wx.ICON_INFORMATION,
			)

	def _on_update_check_finished(self, update, error, manual):
		wx.CallAfter(self._handle_update_check_finished, update, error, manual)

	def _handle_update_check_finished(self, update, error, manual):
		if self._terminated:
			return
		self.update_item.Enable(True)
		if error:
			if manual:
				gui.messageBox(
					_("Unable to check for TeleNVDA updates.\n\n{error}").format(error=error),
					_("TeleNVDA update"),
					wx.OK | wx.ICON_ERROR,
				)
			return
		if update is None:
			if manual:
				gui.messageBox(
					_("TeleNVDA is up to date."),
					_("TeleNVDA update"),
					wx.OK | wx.ICON_INFORMATION,
				)
			return
		message = _(
			"A TeleNVDA update is available: version {version}.\n\n"
			"Do you want to download and install it now?"
		).format(version=update.version)
		if gui.messageBox(message, _("TeleNVDA update"), wx.YES | wx.NO | wx.ICON_INFORMATION) != wx.YES:
			return
		self.update_item.Enable(False)
		if not self.update_manager.download_async(update, self._on_update_download_finished):
			self.update_item.Enable(True)
			gui.messageBox(
				_("Another TeleNVDA update operation is already in progress."),
				_("TeleNVDA update"),
				wx.OK | wx.ICON_INFORMATION,
			)

	def _on_update_download_finished(self, path, error):
		wx.CallAfter(self._handle_update_download_finished, path, error)

	def _handle_update_download_finished(self, path, error):
		if self._terminated:
			if path:
				try:
					os.unlink(path)
				except OSError:
					pass
			return
		self.update_item.Enable(True)
		if error:
			gui.messageBox(
				_("Unable to download or verify the TeleNVDA update.\n\n{error}").format(error=error),
				_("TeleNVDA update"),
				wx.OK | wx.ICON_ERROR,
			)
			return
		try:
			updater.install_package(path)
		except Exception as error:
			gui.messageBox(
				_("NVDA could not install the TeleNVDA update.\n\n{error}").format(error=error),
				_("TeleNVDA update"),
				wx.OK | wx.ICON_ERROR,
			)
		else:
			if gui.messageBox(
				_("The TeleNVDA update was installed. NVDA must be restarted to finish the installation. Restart NVDA now?"),
				_("TeleNVDA update"),
				wx.YES | wx.NO | wx.ICON_INFORMATION,
			) == wx.YES:
				core.restart()
		finally:
			try:
				os.unlink(path)
			except OSError:
				pass

	def perform_autoconnect(self):
		cs = configuration.get_config()['controlserver']
		channel = cs['key']
		encryption_key = cs['encryption_key']
		if cs['self_hosted']:
			port = cs['port']
			address = ('localhost',port)
			UPNP = cs['UPNP']
			self.start_control_server(port, channel, UPNP)
			transport_type = 'tcp'
		else:
			address = address_to_hostport(cs['host'])
			transport_type = cs.get('transport', 'tcp')
		if cs['connection_type']==0:
			self.connect_as_slave(address, channel, encryption_key, transport_type=transport_type, ws_path=cs.get('ws_path', '/'))
		else:
			self.connect_as_master(address, channel, encryption_key, transport_type=transport_type, ws_path=cs.get('ws_path', '/'))

	def create_menu(self):
		if getattr(self, "_terminated", False) or getattr(self, "remote_item", None) is not None:
			return
		self.menu = wx.Menu()
		tools_menu = gui.mainFrame.sysTrayIcon.toolsMenu
		# Translators: Item in TeleNVDA submenu to connect to a remote computer.
		self.connect_item = self.menu.Append(wx.ID_ANY, _("Connect..."), _("Remotely connect to another computer running NVDA Remote Access or TeleNVDA"))
		gui.mainFrame.sysTrayIcon.Bind(wx.EVT_MENU, self.do_connect, self.connect_item)
		# Translators: Item in TeleNVDA submenu to disconnect from a remote computer.
		self.disconnect_item = self.menu.Append(wx.ID_ANY, _("Disconnect"), _("Disconnect from another computer running NVDA Remote Access or TeleNVDA"))
		gui.mainFrame.sysTrayIcon.Bind(wx.EVT_MENU, self.on_disconnect_item, self.disconnect_item)
		self.menu.Remove(self.disconnect_item.Id)
		# Translators: Menu item in TeleNVDA submenu to mute speech and sounds from the remote computer.
		self.mute_item = self.menu.Append(wx.ID_ANY, _("Mute remote"), _("Mute speech and sounds from the remote computer"))
		self.mute_item.Enable(False)
		gui.mainFrame.sysTrayIcon.Bind(wx.EVT_MENU, self.on_mute_item, self.mute_item)
		# Translators: Menu item in TeleNVDA submenu to push clipboard content to the remote computer.
		self.push_clipboard_item = self.menu.Append(wx.ID_ANY, _("&Push clipboard"), _("Push the clipboard to the other machine"))
		self.push_clipboard_item.Enable(False)
		gui.mainFrame.sysTrayIcon.Bind(wx.EVT_MENU, self.on_push_clipboard_item, self.push_clipboard_item)
		# Translators: Menu item in TeleNVDA submenu to send a file to the remote computer.
		self.send_file_item = self.menu.Append(wx.ID_ANY, _("Send &file..."), _("Send a file to the other machine"))
		self.send_file_item.Enable(False)
		gui.mainFrame.sysTrayIcon.Bind(wx.EVT_MENU, self.on_send_file_item, self.send_file_item)
		self.screenshot_item = self.menu.Append(wx.ID_ANY, _("Request &screenshot"), _("Capture the remote screen using TeleNVDA"))
		self.screenshot_item.Enable(False)
		gui.mainFrame.sysTrayIcon.Bind(wx.EVT_MENU, self.on_screenshot_item, self.screenshot_item)
		self.screenshot_powershell_item = self.menu.Append(wx.ID_ANY, _("Request screenshot (&PowerShell)"), _("Capture the remote screen using the PowerShell beta method"))
		self.screenshot_powershell_item.Enable(False)
		gui.mainFrame.sysTrayIcon.Bind(wx.EVT_MENU, self.on_screenshot_powershell_item, self.screenshot_powershell_item)
		self.copyLinkMenu = wx.Menu()
		# Translators: Menu item in TeleNVDA submenu to copy a link to the current session compatible with NVDA Remote.
		self.copy_link_remote_item = self.copyLinkMenu .Append(wx.ID_ANY, _("NVDA &Remote protocol (recommended)"), _("Copy a link to the remote session compatible with both NVDA Remote and TeleNVDA"))
		self.copy_link_remote_item.Enable(False)
		gui.mainFrame.sysTrayIcon.Bind(wx.EVT_MENU, self.on_copy_link_remote_item, self.copy_link_remote_item)
		# Translators: Menu item in TeleNVDA submenu to copy a link to the current session compatible with TeleNVDA.
		self.copy_link_tele_item = self.copyLinkMenu .Append(wx.ID_ANY, _("&TeleNVDA protocol"), _("Copy a link to the remote session compatible only with TeleNVDA"))
		self.copy_link_tele_item.Enable(False)
		gui.mainFrame.sysTrayIcon.Bind(wx.EVT_MENU, self.on_copy_link_tele_item, self.copy_link_tele_item)
		# Translators: Menu item in TeleNVDA submenu to copy a link to the current session.
		self.copy_link_item=self.menu.AppendSubMenu(self.copyLinkMenu, _("Copy &link"), _("Copy a link to the remote session"))
		# Translators: Menu item in TeleNVDA submenu to open add-on options.
		self.options_item = self.menu.Append(wx.ID_ANY, _("&Options..."), _("Options"))
		gui.mainFrame.sysTrayIcon.Bind(wx.EVT_MENU, self.on_options_item, self.options_item)
		# Translators: Item in TeleNVDA submenu to check for updates.
		self.update_item = self.menu.Append(wx.ID_ANY, _("Check for &updates..."), _("Check for TeleNVDA updates"))
		gui.mainFrame.sysTrayIcon.Bind(wx.EVT_MENU, self.on_update_item, self.update_item)
		# Translators: Item in TeleNVDA submenu to test relay connectivity.
		self.connectivity_test_item = self.menu.Append(wx.ID_ANY, _("Connectivity &test..."), _("Test DNS, TLS and WebSocket connectivity"))
		gui.mainFrame.sysTrayIcon.Bind(wx.EVT_MENU, self.on_connectivity_test_item, self.connectivity_test_item)
		# Translators: Menu item in TeleNVDA submenu to send Control+Alt+Delete to the remote computer.
		self.send_ctrl_alt_del_item = self.menu.Append(wx.ID_ANY, _("Send Ctrl+Alt+Del"), _("Send Ctrl+Alt+Del"))
		gui.mainFrame.sysTrayIcon.Bind(wx.EVT_MENU, self.on_send_ctrl_alt_del, self.send_ctrl_alt_del_item)
		self.send_ctrl_alt_del_item.Enable(False)

		# Translators: Label of menu in NVDA tools menu.
		self.remote_item=tools_menu.AppendSubMenu(self.menu, _("R&emote"), _("TeleNVDA"))

	def start_mouse_hook(self):
		if self.mouse_hook_thread is not None:
			return
		self.mouse_hook_ready = threading.Event()
		self.mouse_hook_thread = threading.Thread(target=self.mouse_hook_loop)
		self.mouse_hook_thread.daemon = True
		self.mouse_hook_thread.start()

	def mouse_hook_loop(self):
		log.debug("Mouse hook thread start")
		hook = None
		try:
			hook = mouse_hook.MouseHook()
			if not hook.handle:
				log.error("Unable to install the mouse hook")
				return
			hook.register_callback(self.mouse_hook_callback)
			self.mouse_hook = hook
			self.mouse_hook_ready.set()
			msg = ctypes.wintypes.MSG()
			while True:
				result = ctypes.windll.user32.GetMessageW(ctypes.byref(msg), None, 0, 0)
				if result <= 0:
					break
			log.debug("Mouse hook thread end")
		except Exception:
			log.exception("Unable to run the mouse hook")
		finally:
			self.mouse_hook_ready.set()
			if hook is not None:
				hook.free()
			self.mouse_hook = None

	def stop_mouse_hook(self):
		thread = self.mouse_hook_thread
		if thread is None:
			return
		if hasattr(self, "mouse_hook_ready"):
			self.mouse_hook_ready.wait(timeout=1)
		if thread.is_alive() and thread.ident is not None:
			thread_id = getattr(thread, "native_id", None) or thread.ident
			ctypes.windll.user32.PostThreadMessageW(thread_id, WM_QUIT, 0, 0)
			thread.join()
		self.mouse_hook_thread = None

	def mouse_hook_callback(self, action=None, button=None, pressed=None, delta=None, horizontal=False, **kwargs):
		self.keep_awake.notify_local_input()
		session = self.master_session
		if session is not None:
			session.mouse_sender.handle_hook_event(
				action=action,
				button=button,
				pressed=pressed,
				delta=delta,
				horizontal=horizontal,
			)

	def terminate(self):
		global client
		self._terminated = True
		if buildVersion.version_year >= 2025:
			inputCore.decide_handleRawKey.unregister(self.handleRawKeys)
		self.stop_mouse_hook()
		self.keep_awake.stop()
		if self._inactivity_timer is not None:
			self._inactivity_timer.Stop()
			self._inactivity_timer = None
		self.update_manager.terminate()
		if post_secureDesktopStateChange:
			post_secureDesktopStateChange.unregister(self.onSecureDesktopChange)
		configuration.flush_activity()
		try:
			addon = addonHandler.getCodeAddon()
			if getattr(addon, 'isPendingDisable', False):
				configuration.mark_native_remote_for_reactivation()
		except addonHandler.AddonError:
			pass
		self.disconnect()
		self.local_machine.terminate()
		self.local_machine = None
		NVDASettingsDialog.categoryClasses.remove(dialogs.OptionsDialog)
		self.copyLinkMenu.Remove(self.copy_link_remote_item.Id)
		self.copy_link_remote_item.Destroy()
		self.copy_link_remote_item=None
		self.copyLinkMenu.Remove(self.copy_link_tele_item.Id)
		self.copy_link_tele_item.Destroy()
		self.copy_link_tele_item=None
		try:
			self.menu.Remove(self.connect_item.Id)
			self.menu.Remove(self.disconnect_item.Id)
		except:
			pass
		self.connect_item.Destroy()
		self.connect_item=None
		self.disconnect_item.Destroy()
		self.disconnect_item=None
		self.menu.Remove(self.mute_item.Id)
		self.mute_item.Destroy()
		self.mute_item=None
		self.menu.Remove(self.push_clipboard_item.Id)
		self.push_clipboard_item.Destroy()
		self.push_clipboard_item=None
		self.menu.Remove(self.send_file_item.Id)
		self.send_file_item.Destroy()
		self.send_file_item=None
		self.menu.Remove(self.screenshot_item.Id)
		self.screenshot_item.Destroy()
		self.screenshot_item=None
		self.menu.Remove(self.screenshot_powershell_item.Id)
		self.screenshot_powershell_item.Destroy()
		self.screenshot_powershell_item=None
		self.menu.Remove(self.copy_link_item.Id)
		self.copy_link_item.Destroy()
		self.copy_link_item = None
		self.menu.Remove(self.options_item.Id)
		self.options_item.Destroy()
		self.options_item=None
		self.menu.Remove(self.update_item.Id)
		self.update_item.Destroy()
		self.update_item=None
		self.menu.Remove(self.connectivity_test_item.Id)
		self.connectivity_test_item.Destroy()
		self.connectivity_test_item = None
		self.menu.Remove(self.send_ctrl_alt_del_item.Id)
		self.send_ctrl_alt_del_item.Destroy()
		self.send_ctrl_alt_del_item=None
		tools_menu = gui.mainFrame.sysTrayIcon.toolsMenu
		tools_menu.Remove(self.remote_item.Id)
		self.remote_item.Destroy()
		self.remote_item=None
		try:
			self.menu.Destroy()
			self.copyLinkMenu.Destroy()
		except (RuntimeError, AttributeError):
			pass
		try:
			os.unlink(self.ipc_file)
		except:
			pass
		self.menu=None
		url_handler.unregister_url_handler()
		self.url_handler_window.destroy()
		self.url_handler_window=None
		core.postNvdaStartup.unregister(self.postStartupHandler)
		client = None

	def on_disconnect_item(self, evt):
		if evt:
			evt.Skip()
		def disconnect_as_slave_with_alert():
			if (self.slave_transport is not None
				and configuration.get_config()['ui']['alert_before_slave_disconnect']
				and not gui.message.isModalMessageBoxActive()):  # Check if a modal message box is open
				result = gui.messageBox(
					# Translators: question before disconnecting
					message=_("Are you sure you want to disconnect the controlled computer?"),
					# Translators: question title
					caption=_("Warning!"),
					style=wx.YES | wx.NO | wx.ICON_WARNING
				)
				if result == wx.YES:
					self.disconnect()
			elif (self.master_transport is not None
				  or (self.slave_transport is not None
					  and not configuration.get_config()['ui']['alert_before_slave_disconnect'])):
				self.disconnect()
		wx.CallAfter(disconnect_as_slave_with_alert)

	def on_mute_item(self, evt):
		if evt:
			evt.Skip()
		if not self.muted:
			# Translators: Menu item in TeleNVDA submenu to unmute speech and sounds from the remote computer.
			self.mute_item.SetItemLabel(_("Unmute remote"))
			ui.message(_("Mute speech and sounds from the remote computer"))
			if not configuration.get_config()['ui']['mute_when_controlling_local_machine']:
				self.local_machine.is_muted = True
			self.muted = True
		else:
			# Translators: Menu item in TeleNVDA submenu to mute speech and sounds from the remote computer.
			self.mute_item.SetItemLabel(_("Mute remote"))
			ui.message(_("Unmute speech and sounds from the remote computer"))
			if not configuration.get_config()['ui']['mute_when_controlling_local_machine']:
				self.local_machine.is_muted = False
			self.muted = False

	def on_push_clipboard_item(self, evt):
		connector = self.slave_transport or self.master_transport
		try:
			connector.send(type='set_clipboard_text', text=api.getClipData())
			configuration.record_activity()
			cues.clipboard_pushed()
		except TypeError:
			log.exception("Unable to push clipboard")


	def on_send_file_item(self, evt):
		session = self.slave_session or self.master_session
		connector = self.slave_transport or self.master_transport
		if session is None or not getattr(connector, 'connected', False):
			ui.message(_("Not connected."))
			return
		if globalVars.appArgs.secure:
			return
		# Check if a file dialog is already open
		if getattr(self, 'is_send_file_dialog_open', False):
			return
		# Set the flag to True, indicating that the file dialog is open
		setattr(self, 'is_send_file_dialog_open', True)
		try:
			fd = wx.FileDialog(gui.mainFrame,
				# Translators: message displayed in transfer file dialog when sending a file
				message=_("Choose the file you want to send to the remote computer"),
				# Translators: supported file types when sending or receiving files
				wildcard=_("All files (*.*)") + "|*.*",
				defaultDir=os.environ['userprofile'], style=wx.FD_OPEN | wx.FD_FILE_MUST_EXIST | wx.FD_PREVIEW)
			try:
				if fd.ShowModal() != wx.ID_OK:
					return
				path = fd.GetPath()
			finally:
				fd.Destroy()
		finally:
			# Reset the flag to False after the file dialog is closed
			setattr(self, 'is_send_file_dialog_open', False)
		session.file_transfer_manager.send_file(path)

	@script(
		# Translators: report number of connected computers gesture description
		_("Reports number of computers connected to a session"),
		**speakOnDemand)
	def script_reportConnectedClients(self, gesture):
		session = self.master_session or self.slave_session
		if not session:
			ui.message(_("Not connected."))
			return
		# TRANSLATORS: message reported when get number of clients gesture is pressed
		ui.message(ngettext("This is the only computer connected to this session", "There are {} computers connected to this session", session.client_count).format(session.client_count))

	@script(
		# Translators: send file gesture description
		_("Sends the specified file to the remote machine"),
		gesture="kb:control+shift+NVDA+f",
		**speakOnDemand)
	def script_send_file(self, gesture):
		wx.CallAfter(self.on_send_file_item, None)

	@script(
		# Translators: push clipboard gesture description
		_("Sends the contents of the clipboard to the remote machine"),
		gesture="kb:control+shift+NVDA+c",
		**speakOnDemand)
	def script_push_clipboard(self, gesture):
		connector = self.slave_transport or self.master_transport
		if not getattr(connector,'connected',False):
			ui.message(_("Not connected."))
			return
		try:
			connector.send(type='set_clipboard_text', text=api.getClipData())
			configuration.record_activity()
			cues.clipboard_pushed()
			ui.message(_("Clipboard pushed"))
		except (TypeError, OSError):
			ui.message(_("Unable to push clipboard"))

	@script(
		# Translators: toggle screen sharing gesture description
		_("Shows or stops showing the screen of the controlled computer"),
		gesture="kb:control+shift+NVDA+v",
		**speakOnDemand)
	def script_toggle_screen_share(self, gesture):
		"""Start or stop watching the screen of the controlled computer.

		The gesture works from either end: the controlling computer starts and stops
		the session, while the controlled one can only end a session it accepted.
		On the controlling computer, watching a screen without being able to act on it
		is of little use, so the keyboard follows the picture.
		"""
		session = None
		if self.master_session is not None and self._is_master_connected():
			session = self.master_session
		elif self.slave_session is not None and self._is_slave_connected():
			session = self.slave_session
		if session is None or session.screen_share is None:
			ui.message(_("Not connected."))
			return
		configuration.record_activity()
		was_active = session.screen_share.active
		ui.message(session.screen_share.toggle())
		if session is not self.master_session:
			return
		if not was_active and session.screen_share.active:
			self._take_control_for_screen_share(gesture)
		elif was_active and not session.screen_share.active and self.screen_share_took_control:
			self._switch_to_local_control()

	def _take_control_for_screen_share(self, gesture):
		"""Send the keyboard to the controlled computer now that its screen is requested."""
		if self.sending_keys:
			# The user already took control, so ending the session must not take it away.
			return
		if not self._is_master_connected() or not self._remote_slave_available():
			return
		self._switch_to_remote_control(gesture)
		self.screen_share_took_control = True

	@script(
		# Translators: toggle remote mouse control gesture description
		_("Starts or stops driving the mouse of the controlled computer"),
		gesture="kb:control+shift+NVDA+m",
		**speakOnDemand)
	def script_toggle_remote_mouse(self, gesture):
		"""Mirror the local mouse onto the controlled computer, or stop doing so.

		No picture is needed: the screen reader of the controlled computer announces
		whatever the pointer lands on, and that speech comes back over the connection
		which is already open.
		"""
		if self.master_session is None or not self._is_master_connected():
			# Translators: message spoken when remote mouse control is asked for without a connection.
			ui.message(_("Not connected."))
			return
		sender = self.master_session.mouse_sender
		configuration.record_activity()
		if sender.enabled:
			sender.stop()
			# Translators: message spoken when the local mouse stops driving the controlled computer.
			ui.message(_("Remote mouse control stopped"))
		else:
			sender.start()
			# Translators: message spoken when the local mouse starts driving the controlled computer.
			ui.message(_("Remote mouse control started"))

	def _screenshot(self, method):
		"""Get a screenshot of the controlled computer, whichever end the gesture was pressed on.

		On the controlling computer the capture is requested from the controlled computer.
		On the controlled computer the local screen is captured and pushed to the controller.
		"""
		if self.master_session is not None and self._is_master_connected():
			log.info("compat_screenshot: requesting a %s screenshot from the controlled computer" % method)
			self.master_session.request_screenshot(method)
			configuration.record_activity()
			# Translators: message spoken when a screenshot of the remote computer has been requested
			ui.message(_("Screenshot requested"))
			return
		if self.slave_session is not None and self._is_slave_connected():
			self.slave_session.send_screenshot(method)
			configuration.record_activity()
			# Translators: message spoken when this computer sends a screenshot to the controlling computer
			ui.message(_("Sending screenshot"))
			return
		ui.message(_("Not connected."))

	@script(
		# Translators: remote screenshot gesture description
		_("Takes a screenshot of the controlled computer and opens it on the controlling computer"),
		gesture="kb:control+shift+NVDA+p",
		**speakOnDemand)
	def script_screenshot(self, gesture):
		self._screenshot("native")

	@script(
		# Translators: remote screenshot using PowerShell gesture description
		_("Takes a screenshot of the controlled computer using the PowerShell beta method and opens it on the controlling computer"),
		gesture="kb:control+windows+alt+p",
		**speakOnDemand)
	def script_screenshot_powershell(self, gesture):
		self._screenshot("powershell")

	def on_copy_link_remote_item(self, evt):
		session = self.master_session or self.slave_session
		url = session.get_connection_info().get_url_to_connect(0)
		api.copyToClip(str(url))

	def on_copy_link_tele_item(self, evt):
		session = self.master_session or self.slave_session
		url = session.get_connection_info().get_url_to_connect(1)
		api.copyToClip(str(url))

	def on_options_item(self, evt):
		wx.CallAfter(gui.mainFrame.popupSettingsDialog if hasattr(gui.mainFrame, "popupSettingsDialog") else gui.mainFrame._popupSettingsDialog, gui.NVDASettingsDialog, dialogs.OptionsDialog)
		evt.Skip()

	def on_update_item(self, evt):
		self._start_update_check(manual=True)
		evt.Skip()

	def on_connectivity_test_item(self, evt):
		dlg = dialogs.ConnectivityDialog(gui.mainFrame)
		dlg.ShowModal()
		dlg.Destroy()
		evt.Skip()

	def on_screenshot_item(self, evt):
		self._screenshot("native")
		evt.Skip()

	def on_screenshot_powershell_item(self, evt):
		self._screenshot("powershell")
		evt.Skip()

	def on_send_ctrl_alt_del(self, evt):
		self.master_transport.send('send_SAS')
		configuration.record_activity()
		# Translators: message spoken when the Ctrl+Alt+Delete has been sent to the remote machine successfully
		ui.message(_("Ctrl+Alt+Delete has been sent to the remote machine"))

	def disconnect(self):
		if self.master_transport is None and self.slave_transport is None:
			return
		if self.server is not None:
			self.server.close()
			self.server = None
		if self.master_transport is not None:
			self.disconnect_as_master()
		if self.slave_transport is not None:
			self.disconnect_as_slave()
		# Translators: Presented when disconnected from the remote computer.
		ui.message(_("Disconnected!"))
		cues.disconnected()
		if self.menu.FindItemById(self.disconnect_item.Id):
			self.menu.Remove(self.disconnect_item.Id)
		if not self.menu.FindItemById(self.connect_item.Id):
			self.menu.Insert(0, self.connect_item)
		self.push_clipboard_item.Enable(False)
		self.send_file_item.Enable(False)
		self.screenshot_item.Enable(False)
		self.screenshot_powershell_item.Enable(False)
		self.copy_link_remote_item.Enable(False)
		self.copy_link_tele_item.Enable(False)

	def _is_master_connected(self):
		return self.master_transport is not None and self.master_transport.connected

	def _is_slave_connected(self):
		return self.slave_transport is not None and self.slave_transport.connected

	def _remote_slave_available(self):
		"""Whether a controlled computer is actually reachable.

		Being connected to the relay is not enough: the controlled computer may have
		left the channel. While the relay has not reported the channel content yet,
		we optimistically assume that a controlled computer is available.
		"""
		if not self._is_master_connected():
			return False
		if self.master_session is None:
			return False
		if not self.master_session.slave_state_known:
			return True
		return self.master_session.has_slaves()

	def _abort_remote_control(self):
		"""Give keyboard control back to the local machine because the controlled computer is gone."""
		if not self.sending_keys:
			return
		self._return_to_local_control()
		# Translators: Presented when the controlled computer left the session while keyboard control was remote.
		ui.message(_("The remote computer is no longer connected. Control returned to local machine."))

	def on_master_client_left(self, client=None, **kwargs):
		if self._remote_slave_available():
			return
		self._abort_remote_control()

	def on_remote_nvda_not_connected(self, **kwargs):
		self._abort_remote_control()

	def on_request_local_control(self, **kwargs):
		if not self._is_master_connected() or not self.sending_keys:
			return
		self._return_to_local_control(release_keys=True)
		ui.message(_("Controlling local machine."))

	def _release_remote_keys(self):
		"""Release every modifier still held down on the controlled computer.

		This mirrors NVDA's own Remote Access: when the modifiers being released could
		perform an action on their own (alt opening the menu bar for instance), a reserved
		VK_NONE key press is sent first so that the controlled computer records a key state
		change instead of acting on the release.
		"""
		if not self._is_master_connected():
			self.key_modifiers = set()
			return
		if self.key_modifiers and canModifiersPerformAction is not None:
			try:
				generalized = KeyboardInputGesture._generalizeModifiers(self.key_modifiers)
			except AttributeError:
				generalized = None
			if generalized is not None and canModifiersPerformAction(generalized):
				self.master_transport.send(type="key", vk_code=VK_NONE, extended=False, pressed=True)
				self.master_transport.send(type="key", vk_code=VK_NONE, extended=False, pressed=False)
		for k in self.key_modifiers:
			self.master_transport.send(type="key", vk_code=k[0], extended=k[1], pressed=False)
		self.key_modifiers = set()

	def _return_to_local_control(self, release_keys=False, stop_hook=False):
		was_sending_keys = self.sending_keys
		if release_keys:
			self._release_remote_keys()
		self.sending_keys = False
		self.screen_share_took_control = False
		if self.master_session is not None:
			self.set_receiving_braille(False)
		if was_sending_keys:
			if buildVersion.version_year==2022 and buildVersion.version_major==4:
				security.postSessionLockStateChanged.unregister(self.onSessionLockStateChange)
			elif buildVersion.version_year>=2023:
				security.post_sessionLockStateChanged.unregister(self.onSessionLockStateChange)
		self.hostPendingModifiers = set()
		self.hostPendingNonmodifier = None
		self.key_modifiers = set()
		if stop_hook and buildVersion.version_year < 2025:
			if self.hook_thread is not None:
				ctypes.windll.user32.PostThreadMessageW(self.hook_thread.ident, WM_QUIT, 0, 0)
				self.hook_thread.join()
				self.hook_thread = None
		if configuration.get_config()['ui']['mute_when_controlling_local_machine'] and not self.muted:
			self.local_machine.is_muted = True
		self.muted = False
		return was_sending_keys

	def disconnect_as_master(self):
		self.master_disconnect_requested = True
		self.master_transport.close()
		self.master_transport = None
		self.master_session = None

	def disconnecting_as_master(self):
		if self.menu:
			if not self.menu.FindItemById(self.connect_item.Id):
				self.menu.Insert(0, self.connect_item)
			if self.menu.FindItemById(self.disconnect_item.Id):
				self.menu.Remove(self.disconnect_item.Id)
			# Translators: Menu item in TeleNVDA submenu to mute speech and sounds from the remote computer.
			self.mute_item.SetItemLabel(_("Mute remote"))
			self.mute_item.Enable(False)
			self.push_clipboard_item.Enable(False)
			self.send_file_item.Enable(False)
			self.screenshot_item.Enable(False)
			self.screenshot_powershell_item.Enable(False)
			self.copy_link_remote_item.Enable(False)
			self.copy_link_tele_item.Enable(False)
			self.send_ctrl_alt_del_item.Enable(False)
		self._return_to_local_control(stop_hook=True)
		self.local_machine.is_muted = False

	def disconnect_as_slave(self):
		self.slave_transport.close()
		self.slave_transport = None
		self.slave_session = None

	def on_connected_as_master_failed(self):
		if self.master_transport.successful_connects == 0:
			self.disconnect_as_master()
			# Translators: Title of the connection error dialog.
			gui.messageBox(parent=gui.mainFrame, caption=_("Error Connecting"),
			# Translators: Message shown when cannot connect to the remote computer.
			message=_("Unable to connect to the remote computer"), style=wx.OK | wx.ICON_WARNING)

	def _get_open_connect_dialog(self):
		"""Return the connect dialog currently open, or None if there is none left."""
		dlg = getattr(self, '_connect_dialog', None)
		if dlg is None:
			return None
		try:
			if not dlg:
				# The underlying C++ object has already been destroyed.
				return None
			if not dlg.IsShown():
				return None
		except RuntimeError:
			return None
		return dlg

	def _raise_connect_dialog(self, dlg):
		"""Bring an already open connect dialog to the foreground.

		Returns True if the dialog really is in the foreground afterwards.
		"""
		try:
			if dlg.IsIconized():
				dlg.Iconize(False)
			dlg.Raise()
			handle = dlg.GetHandle()
		except RuntimeError:
			return False
		# Activating the top level window restores the focus on the control the user
		# was on, so no explicit SetFocus is done here.
		return force_window_to_foreground(handle)

	def _close_connect_dialog(self, dlg):
		"""Force the given connect dialog to close, whether it is modal or not."""
		if self._connect_dialog is dlg:
			self._connect_dialog = None
		self.is_connect_dialog_open = False
		try:
			if dlg.IsModal():
				dlg.EndModal(wx.ID_CANCEL)
			else:
				dlg.Hide()
				dlg.Destroy()
		except RuntimeError:
			pass

	def do_connect(self, evt):
		if evt:
			evt.Skip()
		dlg = self._get_open_connect_dialog()
		if dlg is not None:
			if self._raise_connect_dialog(dlg):
				return
			# The dialog is stuck behind another window: close it and open a fresh one
			# so that the user always ends up on a dialog in the foreground.
			self._close_connect_dialog(dlg)
			wx.CallLater(100, self._show_connect_dialog)
			return
		if self.is_connect_dialog_open:
			# The dialog is being created or closed; nothing to raise yet.
			return
		self._show_connect_dialog()

	def _show_connect_dialog(self):
		# Set the flag to True, indicating that the connect dialog is open
		self.is_connect_dialog_open = True
		last_cons = configuration.get_config()['connections']['last_connected']
		# Translators: Title of the connect dialog.
		dlg = dialogs.DirectConnectDialog(parent=gui.mainFrame, id=wx.ID_ANY, title=_("TeleNVDA - Connect"))
		self._connect_dialog = dlg
		host_items = list(reversed(last_cons))
		for default_host in configuration.DEFAULT_SERVER_HOSTS:
			if default_host not in host_items:
				host_items.append(default_host)
		dlg.panel.host.SetItems(host_items)
		dlg.panel.host.SetSelection(0)
		def handle_dlg_complete(dlg_result):
			if self._connect_dialog is dlg:
				self._connect_dialog = None
			if dlg_result != wx.ID_OK:
				# Reset the flag to False when the dialog is closed
				self.is_connect_dialog_open = False
				return
			if dlg.client_or_server.GetSelection() == 0: #client
				# Persist the proxy settings first: the transport layer reads them from the
				# configuration when it opens the WebSocket connection.
				dlg.panel.save_proxy_settings()
				server_addr, port = dlg.panel.get_address()
				channel = dlg.panel.key.GetValue()
				encryption_key = dlg.panel.encryption_key.GetValue()
				transport_type = dlg.panel.get_transport_type()
				ws_path = dlg.panel.ws_path.GetValue() or "/"
				if dlg.connection_type.GetSelection() == 0:
					self.connect_as_slave((server_addr, port), channel, encryption_key, transport_type=transport_type, ws_path=ws_path)
				else:
					self.connect_as_master((server_addr, port), channel, encryption_key, transport_type=transport_type, ws_path=ws_path)
			else: #We want a server
				channel = dlg.panel.key.GetValue()
				encryption_key = dlg.panel.encryption_key.GetValue()
				self.start_control_server(int(dlg.panel.port.GetValue()), channel, useUPNP=bool(dlg.panel.useUPNP.GetValue()))
				if dlg.connection_type.GetSelection() == 0:
					self.connect_as_slave(('127.0.0.1', int(dlg.panel.port.GetValue())), channel, insecure=True, encryption_key=encryption_key)
				else:
					self.connect_as_master(('127.0.0.1', int(dlg.panel.port.GetValue())), channel, insecure=True, encryption_key=encryption_key)
			# Reset the flag to False when the dialog is closed
			self.is_connect_dialog_open = False
		gui.runScriptModalDialog(dlg, callback=handle_dlg_complete)
		# The dialog is shown asynchronously and may be created behind the window the
		# user was working in, so explicitly push it to the foreground once it exists.
		wx.CallLater(150, self._ensure_connect_dialog_foreground)

	def _ensure_connect_dialog_foreground(self):
		dlg = self._get_open_connect_dialog()
		if dlg is not None:
			self._raise_connect_dialog(dlg)

	def on_connected_as_master(self):
		was_interrupted = self.master_connection_interrupted
		self.master_connection_interrupted = False
		configuration.write_connection_to_config(self.master_transport.address)
		if not self.menu.FindItemById(self.disconnect_item.Id):
			self.menu.Insert(0, self.disconnect_item)
		if self.menu.FindItemById(self.connect_item.Id):
			self.menu.Remove(self.connect_item.Id)
		self.mute_item.Enable(True)
		self.push_clipboard_item.Enable(True)
		if not globalVars.appArgs.secure:
			self.send_file_item.Enable(True)
		self.screenshot_item.Enable(True)
		self.screenshot_powershell_item.Enable(True)
		self.copy_link_remote_item.Enable(True)
		self.copy_link_tele_item.Enable(True)
		self.send_ctrl_alt_del_item.Enable(True)
		self.start_mouse_hook()
		if buildVersion.version_year < 2025:
			# We might have already created a hook thread before if we're restoring an
			# interrupted connection. We must not create another.
			if not self.hook_thread:
				self.hook_thread = threading.Thread(target=self.hook)
				self.hook_thread.daemon = True
				self.hook_thread.start()
		# Translators: Presented when connected to the remote computer.
		ui.message(_("Connected!"))
		if was_interrupted:
			# Translators: Presented when an interrupted remote connection becomes available again. Keyboard control remains local until the user toggles it.
			ui.message(_("The remote computer is available again. Control remains local."))
		cues.connected()
		if configuration.get_config()['ui']['mute_when_controlling_local_machine'] and not self.sending_keys:
			self.local_machine.is_muted = True

	def on_disconnected_as_master(self):
		if self.master_disconnect_requested:
			return
		was_sending_keys = self._return_to_local_control(stop_hook=True)
		self.master_connection_interrupted = True
		if was_sending_keys:
			# Translators: Presented when the remote connection is interrupted while keyboard control is remote.
			ui.message(_("Connection interrupted. Control returned to local machine."))
		else:
			# Translators: Presented when connection to a remote computer was interrupted.
			ui.message(_("Connection interrupted"))

	def _create_relay_transport(self, address, key, encryption_key, connection_type, insecure=False, transport_type="tcp", ws_path="/"):
		transport_class = WebSocketRelayTransport if transport_type == "websocket" else RelayTransport
		kwargs = {
			"address": address,
			"serializer": serializer.JSONSerializer(),
			"channel": key,
			"connection_type": connection_type,
			"insecure": insecure,
			"encryption_key": encryption_key,
		}
		if transport_type == "websocket":
			kwargs["ws_path"] = ws_path
		return transport_class(**kwargs)

	def connect_as_master(self, address, key, encryption_key, insecure=False, transport_type="tcp", ws_path="/"):
		self.master_disconnect_requested = False
		transport = self._create_relay_transport(address, key, encryption_key, 'master', insecure, transport_type, ws_path)
		self.master_session = MasterSession(transport=transport, local_machine=self.local_machine)
		transport.callback_manager.register_callback(TransportEvents.CERTIFICATE_AUTHENTICATION_FAILED, self.on_certificate_as_master_failed)
		transport.callback_manager.register_callback(TransportEvents.CONNECTED, self.on_connected_as_master)
		transport.callback_manager.register_callback(TransportEvents.CONNECTION_FAILED, self.on_connected_as_master_failed)
		transport.callback_manager.register_callback(TransportEvents.CLOSING, self.disconnecting_as_master)
		transport.callback_manager.register_callback(TransportEvents.DISCONNECTED, self.on_disconnected_as_master)
		transport.callback_manager.register_callback('msg_request_local_control', self.on_request_local_control)
		transport.callback_manager.register_callback('msg_client_left', self.on_master_client_left)
		transport.callback_manager.register_callback('msg_nvda_not_connected', self.on_remote_nvda_not_connected)
		self.master_transport = transport
		self.master_transport.reconnector_thread.start()

	def connect_as_slave(self, address, key, encryption_key, insecure=False, transport_type="tcp", ws_path="/"):
		if not nvda_conf['keyboard']['handleInjectedKeys'] and gui.messageBox(
		# Translators: A message to warn the user that handle keys from other applications should be on.
		message=_("The option to handle keys from other applications is disabled in your NVDA keyboard settings. In order to allow the keyboard of this machine to be controlled, this option should be enabled. Would you like to do this now?"),
		# Translators: The title of the warning dialog displayed when handle keys from other applications is disabled.
		caption=_("Warning"),style=wx.YES|wx.NO|wx.ICON_WARNING)==wx.YES:
			nvda_conf['keyboard']['handleInjectedKeys']=True
		transport = self._create_relay_transport(address, key, encryption_key, 'slave', insecure, transport_type, ws_path)
		self.slave_session = SlaveSession(transport=transport, local_machine=self.local_machine)
		self.slave_transport = transport
		transport.callback_manager.register_callback(TransportEvents.CERTIFICATE_AUTHENTICATION_FAILED, self.on_certificate_as_slave_failed)
		self.slave_transport.callback_manager.register_callback(TransportEvents.CONNECTED, self.on_connected_as_slave)
		self.slave_transport.reconnector_thread.start()
		if not self.menu.FindItemById(self.disconnect_item.Id):
			self.menu.Insert(0, self.disconnect_item)
		if self.menu.FindItemById(self.connect_item.Id):
			self.menu.Remove(self.connect_item.Id)

	def handle_certificate_failed(self, transport):
		self.last_fail_address = transport.address
		self.last_fail_key = transport.channel
		self.last_fail_encryption_key = transport.encryption_key
		self.last_fail_transport_type = 'websocket' if isinstance(transport, WebSocketRelayTransport) else 'tcp'
		self.last_fail_ws_path = getattr(transport, 'ws_path', '/')
		self.disconnect()
		try:
			cert_hash = transport.last_fail_fingerprint
			if configuration.trust_certificate(self.last_fail_address, cert_hash):
				return True
			log.warning("Unable to automatically trust the server certificate")
		except Exception as ex:
			log.error(ex)
		return False

	def on_certificate_as_master_failed(self):
		if self.handle_certificate_failed(self.master_transport):
			self.connect_as_master(self.last_fail_address, self.last_fail_key, self.last_fail_encryption_key, insecure=True, transport_type=self.last_fail_transport_type, ws_path=self.last_fail_ws_path)

	def on_certificate_as_slave_failed(self):
		if self.handle_certificate_failed(self.slave_transport):
			self.connect_as_slave(self.last_fail_address, self.last_fail_key, self.last_fail_encryption_key, insecure=True, transport_type=self.last_fail_transport_type, ws_path=self.last_fail_ws_path)

	def on_connected_as_slave(self):
		log.info("Control connector connected")
		cues.control_server_connected()
		# Translators: Presented in direct (client to server) remote connection when the controlled computer is ready.
		speech.speakMessage(_("Connected to control server"))
		self.push_clipboard_item.Enable(True)
		if not globalVars.appArgs.secure:
			self.send_file_item.Enable(True)
		self.screenshot_item.Enable(True)
		self.screenshot_powershell_item.Enable(True)
		self.copy_link_remote_item.Enable(True)
		self.copy_link_tele_item.Enable(True)
		configuration.write_connection_to_config(self.slave_transport.address)

	def start_control_server(self, server_port, channel, useUPNP=False):
		self.server = server.Server(server_port, channel, UPNP=useUPNP)
		server_thread = threading.Thread(target=self.server.run)
		server_thread.daemon = True
		server_thread.start()

	def hook(self):
		log.debug("Hook thread start")
		keyhook = keyboard_hook.KeyboardHook()
		keyhook.register_callback(self.hook_callback)
		msg = ctypes.wintypes.MSG()
		while ctypes.windll.user32.GetMessageW(ctypes.byref(msg), None, 0, 0):
			pass
		log.debug("Hook thread end")
		keyhook.free()

	def hook_callback(self, **kwargs):
		self.keep_awake.notify_local_input()
		#Prevent disabling sending keys if another key is held down
		if not self.sending_keys:
			return False
		if not self._is_master_connected():
			# Never join the hook thread from the hook thread itself.
			wx.CallAfter(self._return_to_local_control, False, True)
			return False
		if not self._remote_slave_available():
			wx.CallAfter(self._abort_remote_control)
			return False
		keyCode = (kwargs['vk_code'], kwargs['extended'])
		if not kwargs['pressed'] and keyCode in self.hostPendingModifiers:
			self.hostPendingModifiers.discard(keyCode)
			return False
		if not kwargs['pressed'] and keyCode == self.hostPendingNonmodifier:
			self.hostPendingNonmodifier = None
			return False
		gesture = KeyboardInputGesture(self.key_modifiers, keyCode[0], kwargs['scan_code'], keyCode[1])
		if gesture.isModifier:
			if kwargs['pressed']:
				self.key_modifiers.add(keyCode)
			else:
				self.key_modifiers.discard(keyCode)
		elif kwargs['pressed']:
			script = gesture.script
			if self.ignoreGesture:
				self.ignoreGesture = False
			elif script in self.guestScripts:
				wx.CallAfter(script, gesture)
				return True
		self.master_transport.send(type="key", **kwargs)
		configuration.record_activity()
		return True #Don't pass it on

	def set_receiving_braille(self, state):
		if state and self.master_session.patch_callbacks_added and braille.handler.enabled:
			self.master_session.patcher.patch_braille_input()
			if buildVersion.version_year < 2023:
				braille.handler.enabled = False
				if braille.handler._cursorBlinkTimer:
					braille.handler._cursorBlinkTimer.Stop()
					braille.handler._cursorBlinkTimer=None
				if braille.handler.buffer is braille.handler.messageBuffer:
					braille.handler.buffer.clear()
					braille.handler.buffer = braille.handler.mainBuffer
					if braille.handler._messageCallLater:
						braille.handler._messageCallLater.Stop()
						braille.handler._messageCallLater = None
			self.local_machine.receiving_braille=True
		elif not state:
			self.master_session.patcher.unpatch_braille_input()
			if buildVersion.version_year < 2023:
				braille.handler.enabled = bool(braille.handler.displaySize)
			self.local_machine.receiving_braille=False

	def onSecureDesktopChange(self, isSecureDesktop: bool):
		'''
		@param isSecureDesktop: True if the new desktop is the secure desktop.
		'''
		if isSecureDesktop:
			self.enter_secure_desktop()
		else:
			self.leave_secure_desktop()

	def event_gainFocus(self, obj, nextHandler):
		if not hasattr(IAccessibleHandler, 'SecureDesktopNVDAObject'):
			nextHandler()
			return
		if isinstance(obj, IAccessibleHandler.SecureDesktopNVDAObject):
			self.sd_focused = True
			self.enter_secure_desktop()
		elif self.sd_focused and not isinstance(obj, IAccessibleHandler.SecureDesktopNVDAObject):
			#event_leaveFocus won't work for some reason
			self.sd_focused = False
			self.leave_secure_desktop()
		nextHandler()

	def enter_secure_desktop(self):
		"""function ran when entering a secure desktop."""
		if self.slave_transport is None:
			return
		if not os.path.exists(self.temp_location):
			os.makedirs(self.temp_location)
		channel = str(uuid.uuid4())
		self.sd_server = server.Server(port=0, password=channel, bind_host='127.0.0.1')
		port = self.sd_server.server_socket.getsockname()[1]
		server_thread = threading.Thread(target=self.sd_server.run)
		server_thread.daemon = True
		server_thread.start()
		self.sd_relay = RelayTransport(address=('127.0.0.1', port), serializer=serializer.JSONSerializer(), channel=channel, insecure=True)
		self.sd_relay.callback_manager.register_callback('msg_client_joined', self.on_master_display_change)
		self.slave_transport.callback_manager.register_callback('msg_set_braille_info', self.on_master_display_change)
		self.sd_bridge = bridge.BridgeTransport(self.slave_transport, self.sd_relay)
		relay_thread = threading.Thread(target=self.sd_relay.run)
		relay_thread.daemon = True
		relay_thread.start()
		data = [port, channel]
		with open(self.ipc_file, 'w') as fp:
			json.dump(data, fp)

	def leave_secure_desktop(self):
		if self.sd_server is None:
			return #Nothing to do
		self.sd_bridge.disconnect()
		self.sd_bridge = None
		self.sd_server.close()
		self.sd_server = None
		self.sd_relay.close()
		self.sd_relay = None
		self.slave_transport.callback_manager.unregister_callback('msg_set_braille_info', self.on_master_display_change)
		self.slave_session.set_display_size()

	def on_master_display_change(self, **kwargs):
		self.sd_relay.send(type='set_display_size', sizes=self.slave_session.master_display_sizes)

	SD_CONNECT_BLOCK_TIMEOUT = 1
	def handle_secure_desktop(self):
		try:
			with open(self.ipc_file) as fp:
				data = json.load(fp)
			os.unlink(self.ipc_file)
			port, channel = data
			test_socket=socket.socket(socket.AF_INET, socket.SOCK_STREAM)
			test_socket=wrap_socket(test_socket, ssl_version=ssl.PROTOCOL_TLS_CLIENT)
			test_socket.connect(('127.0.0.1', port))
			test_socket.close()
			self.connect_as_slave(('127.0.0.1', port), channel, insecure=True)
			# So we don't miss the first output when switching to a secure desktop,
			# block the main thread until the connection is established. We're
			# connecting to localhost, so this should be pretty fast. Use a short
			# timeout, though.
			self.slave_transport.connected_event.wait(self.SD_CONNECT_BLOCK_TIMEOUT)
		except:
			pass

	def verify_connect(self, con_info):
		if self.is_connected() or self.connecting:
			gui.messageBox(_("TeleNVDA is already connected. Disconnect before opening a new connection."), _("TeleNVDA Already Connected"), wx.OK|wx.ICON_WARNING)
			return
		self.connecting = True
		server_addr = con_info.get_address()
		key = con_info.key
		encryption_key = con_info.encryption_key
		if con_info.mode == 'master':
			message = _("Do you wish to control the machine on server {server} with key {key}?").format(server=server_addr, key=key)
		elif con_info.mode == 'slave':
			message = _("Do you wish to allow this machine to be controlled on server {server} with key {key}?").format(server=server_addr, key=key)
		if gui.messageBox(message, _("TeleNVDA Connection Request"), wx.YES|wx.NO|wx.NO_DEFAULT|wx.ICON_WARNING) != wx.YES:
			self.connecting = False
			return
		if con_info.mode == 'master':
			self.connect_as_master((con_info.hostname, con_info.port), key=key, encryption_key=encryption_key)
		elif con_info.mode == 'slave':
			self.connect_as_slave((con_info.hostname, con_info.port), key=key, encryption_key=encryption_key)
		self.connecting = False

	def is_connected(self):
		connector = self.slave_transport or self.master_transport
		if connector is not None:
			return connector.connected
		return False

	@script(
		# Translators: Copy link compatible with NVDA Remote gesture description
		description=_("Copies a link to the remote session to the clipboard compatible with both NVDA Remote and TeleNVDA"),
		**speakOnDemand)
	def script_copy_remote_link(self, gesture):
		connector = self.slave_transport or self.master_transport
		if not getattr(connector,'connected',False):
			ui.message(_("Not connected."))
			return
		self.on_copy_link_remote_item(None)
		ui.message(_("Copied link"))

	@script(
		# Translators: Copy link compatible with TeleNVDA gesture description
		description=_("Copies a link to the remote session to the clipboard compatible only with TeleNVDA"),
		**speakOnDemand)
	def script_copy_tele_link(self, gesture):
		connector = self.slave_transport or self.master_transport
		if not getattr(connector,'connected',False):
			ui.message(_("Not connected."))
			return
		self.on_copy_link_tele_item(None)
		ui.message(_("Copied link"))

	@script(
		# Translators: description for the Connect gesture
		_("""Opens a dialog to start a remote session"""),
		gesture="kb:alt+NVDA+pageUp",
		**speakOnDemand)
	def script_connect(self, gesture):
		if self.master_transport or self.slave_transport:
			ui.message(_("TeleNVDA Already Connected"))
			return
		self.do_connect(None)

	@script(
		# Translators: description for the Disconnect gesture
		_("""Disconnect a remote session"""),
		gesture="kb:alt+NVDA+pageDown",
		**speakOnDemand)
	def script_disconnect(self, gesture):
		if self.master_transport is None and self.slave_transport is None:
			ui.message(_("Not connected."))
			return
		self.on_disconnect_item(None)

	@script(
		# Translators: description for the gesture that connects or disconnects a remote session.
		_("""If a remote session is active, disconnects it; otherwise opens a dialog to start a remote session"""),
		gesture="kb:NVDA+alt+r",
		**speakOnDemand)
	def script_toggle_connection(self, gesture):
		if self.master_transport is None and self.slave_transport is None:
			self.do_connect(None)
		else:
			self.on_disconnect_item(None)

	@script(
		# Translators: gesture description for the ignoreNextGesture script
		_("""Set the host to ignore the next gesture completely, sending next gesture to the guest as is. Useful when you need to use the gesture asigned to toggle between guest and host, in the guest machine."""),
		gesture = "kb:control+f11",
		**speakOnDemand)
	def script_ignoreNextGesture(self, gesture):
		if not self._is_master_connected() or not self.sending_keys:
			return gesture.send()
		self.ignoreGesture = True
		# Translators: Report when the next gesture will be send to the guest ignoring everything else.
		ui.message(_("Send next gesture to the guest"))

	@script(
		# Translators: Documentation string for the script that toggles the control between guest and host machine.
		description=_("Toggles the control between guest and host machine"),
		gesture="kb:NVDA+alt+tab",
		**speakOnDemand)
	def script_sendKeys(self, gesture):
		if not self._is_master_connected():
			if self._is_slave_connected():
				self.slave_transport.send(type="request_local_control")
				return
			if self.sending_keys:
				self._return_to_local_control()
			# Translators: Presented when Insert+Alt+Tab is pressed without an active remote connection.
			ui.message(_("No remote computer is connected."))
			return
		if not self.sending_keys and not self._remote_slave_available():
			# Connected to the relay, but no controlled computer is in the channel.
			# Taking control now would swallow every keystroke locally.
			# Translators: Presented when Insert+Alt+Tab is pressed without an active remote connection.
			ui.message(_("No remote computer is connected."))
			return
		if self.sending_keys:
			self._switch_to_local_control()
		else:
			self._switch_to_remote_control(gesture)

	def _switch_to_remote_control(self, gesture):
		"""Start sending the local keyboard to the controlled computer."""
		self.sending_keys = True
		self.set_receiving_braille(True)
		if buildVersion.version_year==2022 and buildVersion.version_major==4:
			security.postSessionLockStateChanged.register(self.onSessionLockStateChange)
		elif buildVersion.version_year>=2023:
			security.post_sessionLockStateChanged.register(self.onSessionLockStateChange)
		if gesture is not None:
			# The keys of the toggling gesture are still held down locally, so their release
			# must be handled by the local machine rather than forwarded to the remote one.
			self.hostPendingModifiers = set(gesture.modifiers)
			self.hostPendingNonmodifier = (gesture.vkCode, gesture.isExtended)
		else:
			self.hostPendingModifiers = set()
			self.hostPendingNonmodifier = None
		# Translators: Presented when sending keyboard keys from the controlling computer to the controlled computer.
		ui.message(_("Controlling remote machine."))
		if configuration.get_config()['ui']['mute_when_controlling_local_machine'] and not self.muted:
			# Only change this value if user didn't explicitly mute the remote machine
			self.local_machine.is_muted = False

	def _switch_to_local_control(self):
		"""Give the keyboard back to the controlling computer."""
		self._return_to_local_control(release_keys=True)
		# Translators: Presented when keyboard control is back to the controlling computer.
		ui.message(_("Controlling local machine."))

	def handleRawKeys(self, vkCode, scanCode, extended, pressed):
		if not self.sending_keys:
			return True
		if not self._is_master_connected():
			wx.CallAfter(self._return_to_local_control)
			return True
		if not self._remote_slave_available():
			wx.CallAfter(self._abort_remote_control)
			return True
		keyCode = (vkCode, extended)
		if not pressed and keyCode in self.hostPendingModifiers:
			self.hostPendingModifiers.discard(keyCode)
			return True
		if not pressed and keyCode == self.hostPendingNonmodifier:
			self.hostPendingNonmodifier = None
			return True
		gesture = KeyboardInputGesture(self.key_modifiers, keyCode[0], scanCode, keyCode[1])
		if gesture.isModifier:
			if pressed:
				self.key_modifiers.add(keyCode)
			else:
				self.key_modifiers.discard(keyCode)
		elif pressed:
			script = gesture.script
			if self.ignoreGesture:
				self.ignoreGesture = False
			elif script in self.guestScripts:
				wx.CallAfter(script, gesture)
				return False
		self.master_transport.send(type="key", vk_code=vkCode, scan_code=scanCode, extended=extended, pressed=pressed)
		configuration.record_activity()
		return False

	def onSessionLockStateChange(self, isNowLocked):
		if isNowLocked and self._is_master_connected() and self.sending_keys:
			self.script_sendKeys(None)

	@script(
		# Translators: gesture description for the toggle remote mute script
		description=_("""Mute or unmute the speech coming from the remote computer"""),
		gesture="kb:control+alt+m",
		**speakOnDemand)
	def script_toggle_remote_mute(self, gesture):
		if not self.is_connected() or self.connecting or self.slave_transport: return
		self.on_mute_item(None)

	@script(
		# Translators: send Ctrl+Alt+Delete gesture description
		description=_("""Sends Ctrl+Alt+Delete to the remote machine"""),
		**speakOnDemand)
	def script_send_ctrl_alt_del(self, gesture):
		if not self.is_connected() or self.connecting or self.slave_transport: return
		self.on_send_ctrl_alt_del(None)

	@script(
		# Translators: open addon settings gesture description
		description=_("""Opens the addon settings panel inside NVDA settings dialog"""))
	def script_options(self, gesture):
		self.on_options_item(None)
