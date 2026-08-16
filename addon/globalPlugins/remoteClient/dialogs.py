import json
import random
import threading
from urllib import request
import wx
import gui
from gui.settingsDialogs import SettingsPanel
from . import serializer
from . import server
from . import transport
from . import socket_utils
from logHandler import log
import addonHandler
try:
	addonHandler.initTranslation()
except addonHandler.AddonError:
	log.warning(
		"Unable to initialise translations. This may be because the addon is running from NVDA scratchpad."
	)
from . import configuration
from . import keep_awake
from .proxy_utils import PROXY_MODES, SUPPORTED_PROXY_TYPES
import config as NVDAConfig
import os
import sys
sys.path.append(os.path.dirname(__file__))
import miniupnpc
del sys.path[-1]


WX_VERSION = int(wx.version()[0])
WX_CENTER = wx.Center if WX_VERSION>=4 else wx.CENTER_ON_SCREEN

# Widths the shared picture may be reduced to before being encoded. Encoding a
# whole 4K desktop in real time saturates most processors, and the picture is
# displayed much smaller than that on the other side anyway.
SCREEN_SHARE_WIDTHS = (1280, 1600, 1920, 2560, 3840)
SCREEN_SHARE_DEFAULT_WIDTH = 1600

# Quality settings, in the same order as the labels offered in the dialog.
SCREEN_SHARE_QUALITIES = ("low", "balanced", "high")
SCREEN_SHARE_DEFAULT_QUALITY = "balanced"

# Labels of the proxy modes, in the same order as proxy_utils.PROXY_MODES.
def _proxy_mode_choices():
	return (
		# Translators: Proxy mode where the user enters the proxy host, port and credentials manually.
		_("Manual configuration"),
		# Translators: Proxy mode where the proxy is read from the Windows system settings.
		_("Automatic Windows proxy detection"),
		# Translators: Proxy mode where no proxy is used at all.
		_("No proxy"),
	)


def _keep_awake_max_duration_choices():
	return (
		# Translators: Maximum duration for which sleep prevention is active.
		_("30 minutes"),
		_("1 hour"),
		_("2 hours"),
		_("4 hours"),
		_("8 hours"),
		# Translators: Option to prevent the computer from going to sleep without a time limit.
		_("Always prevent the computer from going to sleep"),
	)


class ClientPanel(wx.Panel):

	def __init__(self, parent=None, id=wx.ID_ANY):
		super().__init__(parent, id)
		sizer = wx.BoxSizer(wx.HORIZONTAL)
		sizer.Add(wx.StaticText(self, wx.ID_ANY, label=_("&Transport:")))
		self.transport_choice = wx.Choice(self, wx.ID_ANY, choices=(_("Standard (TCP)"), _("WebSocket over HTTPS")))
		self.transport_choice.SetSelection(0)
		self.transport_choice.Bind(wx.EVT_CHOICE, self.on_transport_changed)
		sizer.Add(self.transport_choice)
		# Translators: The label of an edit field in connect dialog to enter name or address of the remote computer.
		sizer.Add(wx.StaticText(self, wx.ID_ANY, label=_("&Host:")))
		self.host = wx.ComboBox(self, wx.ID_ANY)
		sizer.Add(self.host)
		sizer.Add(wx.StaticText(self, wx.ID_ANY, label=_("&Port:")))
		self.port = wx.SpinCtrl(self, wx.ID_ANY, min=1, max=65535, value="6837")
		sizer.Add(self.port)
		# Translators: Label of the edit field to enter key (password) to secure the remote connection.
		sizer.Add(wx.StaticText(self, wx.ID_ANY, label=_("&Key:")))
		self.key = wx.TextCtrl(self, wx.ID_ANY)
		sizer.Add(self.key)
		# Translators: The button used to generate a random key/password.
		self.generate_key = wx.Button(parent=self, label=_("&Generate Key"))
		self.generate_key.Bind(wx.EVT_BUTTON, self.on_generate_key)
		sizer.Add(self.generate_key)
		# Translators: Label of an edit field to enter a second password to exchange encrypted data.
		sizer.Add(wx.StaticText(self, wx.ID_ANY, label=_("En&cryption password (optional):")))
		self.encryption_key = wx.TextCtrl(self, wx.ID_ANY)
		sizer.Add(self.encryption_key)
		sizer.Add(wx.StaticText(self, wx.ID_ANY, label=_("WebSocket &path:")))
		self.ws_path = wx.TextCtrl(self, wx.ID_ANY, value="/")
		sizer.Add(self.ws_path)
		self.main_sizer = sizer
		self._build_proxy_controls(sizer)
		self.SetSizer(sizer)
		self.load_settings()
		self.Fit()

	def _build_proxy_controls(self, sizer):
		"""Build the proxy group, only relevant for the WebSocket transport."""
		# Translators: Title of the group of proxy settings in the connect dialog.
		self.proxy_box = wx.StaticBoxSizer(wx.VERTICAL, self, label=_("Proxy"))
		box = self.proxy_box.GetStaticBox()
		self.proxy_box.Add(wx.StaticText(box, wx.ID_ANY, label=_("Proxy &mode:")))
		self.proxy_mode = wx.Choice(box, wx.ID_ANY, choices=_proxy_mode_choices())
		self.proxy_mode.SetSelection(PROXY_MODES.index("auto"))
		self.proxy_mode.Bind(wx.EVT_CHOICE, self.on_proxy_mode_changed)
		self.proxy_box.Add(self.proxy_mode)
		self.proxy_box.Add(wx.StaticText(box, wx.ID_ANY, label=_("HTTP/SOCKS &proxy host:")))
		self.proxy_host = wx.TextCtrl(box, wx.ID_ANY)
		self.proxy_box.Add(self.proxy_host)
		self.proxy_box.Add(wx.StaticText(box, wx.ID_ANY, label=_("Proxy por&t:")))
		self.proxy_port = wx.SpinCtrl(box, wx.ID_ANY, min=0, max=65535)
		# Translators: tooltip clarifying that this port is the intermediary proxy port, not the relay/server port above.
		self.proxy_port.SetToolTip(_("Port of an intermediary HTTP/SOCKS proxy server, if any. Left at 0 when no proxy is used. Unrelated to the relay/server port above."))
		self.proxy_box.Add(self.proxy_port)
		self.proxy_box.Add(wx.StaticText(box, wx.ID_ANY, label=_("Proxy &type:")))
		self.proxy_type = wx.Choice(box, wx.ID_ANY, choices=SUPPORTED_PROXY_TYPES)
		self.proxy_type.SetSelection(0)
		self.proxy_box.Add(self.proxy_type)
		self.proxy_box.Add(wx.StaticText(box, wx.ID_ANY, label=_("Proxy &username:")))
		self.proxy_username = wx.TextCtrl(box, wx.ID_ANY)
		self.proxy_box.Add(self.proxy_username)
		self.proxy_box.Add(wx.StaticText(box, wx.ID_ANY, label=_("Proxy pass&word:")))
		self.proxy_password = wx.TextCtrl(box, wx.ID_ANY, style=wx.TE_PASSWORD)
		self.proxy_box.Add(self.proxy_password)
		sizer.Add(self.proxy_box)

	def load_settings(self):
		"""Preload the transport and proxy fields from the saved configuration."""
		cs = configuration.get_config()['controlserver']
		self.transport_choice.SetSelection(1 if cs.get('transport', 'tcp') == 'websocket' else 0)
		if self.transport_choice.GetSelection() == 1:
			self.port.SetValue(443)
			self.ws_path.SetValue(cs.get('ws_path', '/') or '/')
		configured_mode = str(cs.get('proxy_mode', 'auto')).lower()
		self.proxy_mode.SetSelection(PROXY_MODES.index(configured_mode) if configured_mode in PROXY_MODES else PROXY_MODES.index("auto"))
		self.proxy_host.SetValue(cs.get('proxy_host', ''))
		self.proxy_port.SetValue(int(cs.get('proxy_port', 0) or 0))
		configured_type = str(cs.get('proxy_type', 'http')).lower()
		self.proxy_type.SetSelection(SUPPORTED_PROXY_TYPES.index(configured_type) if configured_type in SUPPORTED_PROXY_TYPES else 0)
		self.proxy_username.SetValue(cs.get('proxy_username', ''))
		self.proxy_password.SetValue(cs.get('proxy_password', ''))
		self.update_proxy_controls()

	def save_proxy_settings(self):
		"""Persist the proxy settings so that the transport layer can pick them up."""
		config = configuration.get_config()
		cs = config['controlserver']
		cs['proxy_mode'] = PROXY_MODES[self.proxy_mode.GetSelection()]
		cs['proxy_host'] = self.proxy_host.GetValue().strip()
		cs['proxy_port'] = int(self.proxy_port.GetValue())
		cs['proxy_type'] = self.proxy_type.GetStringSelection()
		cs['proxy_username'] = self.proxy_username.GetValue()
		cs['proxy_password'] = self.proxy_password.GetValue()
		if not configuration.readonly:
			try:
				config.write()
			except Exception:
				log.exception("Unable to save the proxy settings")

	def update_proxy_controls(self):
		show_proxy = self.transport_choice.GetSelection() == 1
		self.main_sizer.Show(self.proxy_box, show_proxy, recursive=True)
		manual = self.proxy_mode.GetSelection() == PROXY_MODES.index("manual")
		for control in (self.proxy_host, self.proxy_port, self.proxy_type, self.proxy_username, self.proxy_password):
			control.Enable(manual)

	def _relayout(self):
		self.Layout()
		self.Fit()
		window = self.GetParent()
		while window is not None and not isinstance(window, wx.Dialog):
			window = window.GetParent()
		if window is not None and window.GetSizer() is not None:
			window.GetSizer().Fit(window)

	def on_proxy_mode_changed(self, evt):
		evt.Skip()
		self.update_proxy_controls()

	def on_transport_changed(self, evt):
		if self.transport_choice.GetSelection() == 1 and self.port.GetValue() == socket_utils.SERVER_PORT:
			self.port.SetValue(443)
		elif self.transport_choice.GetSelection() == 0 and self.port.GetValue() == 443:
			self.port.SetValue(socket_utils.SERVER_PORT)
		self.update_proxy_controls()
		self._relayout()
		evt.Skip()

	def get_transport_type(self):
		return "websocket" if self.transport_choice.GetSelection() == 1 else "tcp"

	def get_address(self):
		host, parsed_port = socket_utils.address_to_hostport(self.host.GetValue())
		address_text = self.host.GetValue().strip()
		has_explicit_port = address_text.startswith("[") and "]:" in address_text or not address_text.startswith("[") and ":" in address_text
		return host, parsed_port if has_explicit_port else int(self.port.GetValue())

	def on_generate_key(self, evt):
		if not self.host.GetValue():
			gui.messageBox(_("Host must be set."), _("Error"), wx.OK | wx.ICON_ERROR)
			self.host.SetFocus()
		else:
			evt.Skip()
			self.generate_key_command()

	def generate_key_command(self, insecure=False):
		# The key is requested over the same transport as the connection itself, so the proxy
		# settings must already be persisted for the WebSocket transport to pick them up.
		self.save_proxy_settings()
		address = self.get_address()
		transport_class = transport.WebSocketRelayTransport if self.get_transport_type() == "websocket" else transport.RelayTransport
		transport_kwargs = {"address": address, "serializer": serializer.JSONSerializer(), "insecure": insecure}
		if self.get_transport_type() == "websocket":
			transport_kwargs["ws_path"] = self.ws_path.GetValue() or "/"
		self.key_connector = transport_class(**transport_kwargs)
		self.key_connector.callback_manager.register_callback('msg_generate_key', self.handle_key_generated)
		self.key_connector.callback_manager.register_callback(transport.TransportEvents.CERTIFICATE_AUTHENTICATION_FAILED, self.handle_certificate_failed)
		t = threading.Thread(target=self.key_connector.run)
		t.start()

	def handle_key_generated(self, key=None):
		self.key.SetValue(key)
		self.key.SetFocus()
		self.key_connector.close()
		self.key_connector = None

	def handle_certificate_failed(self):
		try:
			connector = self.key_connector
			if connector is None or not configuration.trust_certificate(
				connector.address,
				connector.last_fail_fingerprint,
			):
				log.warning("Unable to automatically trust the server certificate")
				return
		except Exception as ex:
			log.error(ex)
			return
		connector.close()
		self.key_connector = None
		self.generate_key_command(True)


class ConnectivityDialog(wx.Dialog):
	def __init__(self, parent):
		super().__init__(parent, wx.ID_ANY, title=_("Connectivity test"))
		config = configuration.get_config()['controlserver']
		sizer = wx.BoxSizer(wx.VERTICAL)
		sizer.Add(wx.StaticText(self, wx.ID_ANY, label=_("Test a relay endpoint without joining a channel.")))
		row = wx.BoxSizer(wx.HORIZONTAL)
		row.Add(wx.StaticText(self, wx.ID_ANY, label=_("&Host:")))
		self.host = wx.TextCtrl(self, wx.ID_ANY, value=config.get('host', ''))
		row.Add(self.host, 1, wx.EXPAND)
		row.Add(wx.StaticText(self, wx.ID_ANY, label=_("&Port:")))
		self.port = wx.SpinCtrl(self, wx.ID_ANY, min=1, max=65535, value=str(config.get('port', 443 if config.get('transport') == 'websocket' else socket_utils.SERVER_PORT)))
		row.Add(self.port)
		sizer.Add(row, 0, wx.EXPAND)
		row = wx.BoxSizer(wx.HORIZONTAL)
		row.Add(wx.StaticText(self, wx.ID_ANY, label=_("&Transport:")))
		self.transport = wx.Choice(self, wx.ID_ANY, choices=(_("Standard (TCP)"), _("WebSocket over HTTPS")))
		self.transport.SetSelection(1 if config.get('transport', 'tcp') == 'websocket' else 0)
		row.Add(self.transport)
		row.Add(wx.StaticText(self, wx.ID_ANY, label=_("WebSocket &path:")))
		self.ws_path = wx.TextCtrl(self, wx.ID_ANY, value=config.get('ws_path', '/'))
		row.Add(self.ws_path, 1, wx.EXPAND)
		sizer.Add(row, 0, wx.EXPAND)
		buttons = wx.BoxSizer(wx.HORIZONTAL)
		self.test_button = wx.Button(self, wx.ID_ANY, label=_("&Test"))
		self.test_button.Bind(wx.EVT_BUTTON, self.on_test)
		buttons.Add(self.test_button)
		close_button = wx.Button(self, wx.ID_CANCEL, label=_("Close"))
		buttons.Add(close_button)
		sizer.Add(buttons, 0, wx.ALIGN_RIGHT | wx.TOP, 10)
		self.SetSizerAndFit(sizer)
		self.Center()

	def on_test(self, evt):
		if not self.host.GetValue().strip():
			gui.messageBox(_("Host must be set."), _("Error"), wx.OK | wx.ICON_ERROR, parent=self)
			return
		self.test_button.Enable(False)
		from . import connectivity_test
		transport_type = 'websocket' if self.transport.GetSelection() == 1 else 'tcp'
		connectivity_test.run_async(
			self.host.GetValue().strip(),
			self.port.GetValue(),
			transport_type,
			self.ws_path.GetValue() or '/',
			callback=self.on_result,
		)

	def on_result(self, result):
		wx.CallAfter(self.test_button.Enable, True)
		message = result.get('message', _('Unknown error'))
		style = wx.OK | (wx.ICON_INFORMATION if result.get('success') else wx.ICON_ERROR)
		wx.CallAfter(gui.messageBox, message, _("Connectivity test"), style, self)

class ServerPanel(wx.Panel):

	def __init__(self, parent=None, id=wx.ID_ANY):
		super().__init__(parent, id)
		sizer = wx.BoxSizer(wx.HORIZONTAL)
		# Translators: Used in server mode to obtain the external IP address for the server (controlled computer) for direct connection.
		self.get_IP = wx.Button(parent=self, label=_("Get External &IP"))
		self.get_IP.Bind(wx.EVT_BUTTON, self.on_get_IP)
		sizer.Add(self.get_IP)
		# Translators: Label of the field displaying the external IP address if using direct (client to server) connection.
		sizer.Add(wx.StaticText(self, wx.ID_ANY, label=_("&External IP:")))
		self.external_IP = wx.TextCtrl(self, wx.ID_ANY, style=wx.TE_READONLY|wx.TE_MULTILINE)
		sizer.Add(self.external_IP)
		# Translators: The label of an edit field in connect dialog to enter the port the server will listen on.
		sizer.Add(wx.StaticText(self, wx.ID_ANY, label=_("&Port:")))
		self.port = wx.SpinCtrl(self, wx.ID_ANY, min=1, max=65535, value=str(socket_utils.SERVER_PORT))
		sizer.Add(self.port)
		# Translators: label of a checkbox which allows forwarding a port using UPNP
		self.useUPNP = wx.CheckBox(self, wx.ID_ANY, label=_("Use &UPNP to forward this port if possible"))
		sizer.Add(self.useUPNP)
		sizer.Add(wx.StaticText(self, wx.ID_ANY, label=_("&Key:")))
		self.key = wx.TextCtrl(self, wx.ID_ANY)
		sizer.Add(self.key)
		self.generate_key = wx.Button(parent=self, label=_("&Generate Key"))
		self.generate_key.Bind(wx.EVT_BUTTON, self.on_generate_key)
		sizer.Add(self.generate_key)
		# Translators: Label of an edit field to enter a second password to exchange encrypted data.
		sizer.Add(wx.StaticText(self, wx.ID_ANY, label=_("En&cryption password (optional):")))
		self.encryption_key = wx.TextCtrl(self, wx.ID_ANY)
		sizer.Add(self.encryption_key)
		self.SetSizerAndFit(sizer)

	def on_generate_key(self, evt):
		evt.Skip()
		res = str(random.randrange(1, 9))
		for n in range(6):
			res += str(random.randrange(0, 9))
		self.key.SetValue(res)
		self.key.SetFocus()

	def on_get_IP(self, evt):
		evt.Skip()
		self.get_IP.Enable(False)
		result = gui.messageBox(
			# Translators: message asking the user wether perform portcheck with UPNP or not
			_("Would you like to use UPNP to forward the chosen port before detecting your IP address?"),
			# Translators: title of the message asking the user to try portcheck with UPNP
			_("Do you want to use UPNP?"),
			wx.YES_NO|wx.ICON_QUESTION, self)
		if result==wx.YES:
			t = threading.Thread(target=self.do_portcheck, args=[int(self.port.GetValue()), True])
		else:
			t = threading.Thread(target=self.do_portcheck, args=[int(self.port.GetValue())])
		t.daemon = True
		t.start()

	def do_portcheck(self, port, UPNP=False):
		config = configuration.get_config()
		if UPNP:
			try:
				upnp = miniupnpc.UPnP()
				upnp.discoverdelay = 200
				upnp.discover()
				upnp.selectigd()
				upnp.addportmapping(port, 'TCP', upnp.lanaddr, port, 'TeleNVDA', '', 60)
			except Exception as e:
				self.on_get_IP_fail(e)
				self.get_IP.Enable(True)
				raise
		temp_server = server.Server(port=port, password=None)
		try:
			Headers = { 'User-Agent' : 'Mozilla/5.0 (Windows NT 6.1; Win64; x64)' }
			p = request.Request(config['ui']['portcheck'].format(port=port), headers=Headers, method="GET")
			req = request.urlopen(p)
			data = req.read()
			result = json.loads(data)
			wx.CallAfter(self.on_get_IP_success, result)
		except Exception as e:
			self.on_get_IP_fail(e)
			raise
		finally:
			temp_server.close()
			self.get_IP.Enable(True)
			if UPNP:
				upnp.deleteportmapping(port, 'TCP')

	def on_get_IP_success(self, data):
		ip = data['host']
		port = data['port']
		is_open = data['open']
		if is_open:
			gui.messageBox(message=_("Successfully retrieved IP address. Port {port} is open.").format(port=port), caption=_("Success"), style=wx.OK)
		else:
			gui.messageBox(message=_("Retrieved external IP, but port {port} is not currently forwarded.").format(port=port), caption=_("Warning"), style=wx.ICON_WARNING|wx.OK)
		self.external_IP.SetValue(ip)
		self.external_IP.SetSelection(0, len(ip))
		self.external_IP.SetFocus()


	def on_get_IP_fail(self, exc):
		gui.messageBox(message=_("Unable to contact portcheck server or UPNP device, please manually retrieve your IP address and forward ports if required. See the NVDA log for more details."), caption=_("Error"), style=wx.ICON_ERROR|wx.OK)

class DirectConnectDialog(wx.Dialog):

	def __init__(self, parent, id, title):
		super().__init__(parent, id, title=title)
		main_sizer = self.main_sizer = wx.BoxSizer(wx.VERTICAL)
		self.client_or_server = wx.RadioBox(self, wx.ID_ANY, choices=(_("Client"), _("Server")), style=wx.RA_VERTICAL)
		self.client_or_server.Bind(wx.EVT_RADIOBOX, self.on_client_or_server)
		self.client_or_server.SetSelection(0)
		main_sizer.Add(self.client_or_server)
		choices = [_("Allow this machine to be controlled"), _("Control another machine")]
		self.connection_type = wx.RadioBox(self, wx.ID_ANY, choices=choices, style=wx.RA_VERTICAL)
		self.connection_type.SetSelection(0)
		main_sizer.Add(self.connection_type)
		self.container = wx.Panel(parent=self)
		self.panel = ClientPanel(parent=self.container)
		main_sizer.Add(self.container)
		buttons = self.CreateButtonSizer(wx.OK | wx.CANCEL)
		main_sizer.Add(buttons, flag=wx.BOTTOM)
		main_sizer.Fit(self)
		self.SetSizer(main_sizer)
		self.Center(wx.BOTH | WX_CENTER)
		ok = wx.FindWindowById(wx.ID_OK, self)
		ok.Bind(wx.EVT_BUTTON, self.on_ok)
		self.client_or_server.SetFocus()

	def on_client_or_server(self, evt):
		evt.Skip()
		self.panel.Destroy()
		if self.client_or_server.GetSelection() == 0:
			self.panel = ClientPanel(parent=self.container)
		else:
			self.panel = ServerPanel(parent=self.container)
		self.main_sizer.Fit(self)

	def on_ok(self, evt):
		if self.client_or_server.GetSelection() == 0:
			if not self.panel.host.GetValue() or not self.panel.key.GetValue():
				gui.messageBox(_("Both host and key must be set."), _("Error"), wx.OK | wx.ICON_ERROR)
				self.panel.host.SetFocus()
				return
			elif len(self.panel.key.GetValue()) < 6:
				gui.messageBox(_("The key must be longer than 6 characters."), _("Error"), wx.OK | wx.ICON_ERROR)
				self.panel.key.SetFocus()
				return
			elif is_sequential(self.panel.key.GetValue()):
				# Translators: error message for key/password being sequential, example 123456
				gui.messageBox(_("The key must not be sequential. Please, avoid keys such as 1234, 4321 or similar."), _("Error"), wx.OK | wx.ICON_ERROR)
				self.panel.key.SetFocus()
				return
		elif self.client_or_server.GetSelection() == 1:
			if not self.panel.port.GetValue() or not self.panel.key.GetValue():
				gui.messageBox(_("Both port and key must be set."), _("Error"), wx.OK | wx.ICON_ERROR)
				self.panel.port.SetFocus()
				return
			elif len(self.panel.key.GetValue()) < 6:
				gui.messageBox(_("The key must be longer than 6 characters."), _("Error"), wx.OK | wx.ICON_ERROR)
				self.panel.key.SetFocus()
				return
			elif is_sequential(self.panel.key.GetValue()):
				# Translators: error message for key/password being sequential, example 123456
				gui.messageBox(_("The key must not be sequential. Please, avoid keys such as 1234, 4321 or similar."), _("Error"), wx.OK | wx.ICON_ERROR)
				self.panel.key.SetFocus()
				return
		evt.Skip()

class OptionsDialog(SettingsPanel):

	# Translators: title for the TeleNVDA settings category in NVDA settings dialog
	title = _("TeleNVDA")

	def makeSettings(self, sizer):
		# Translators: A checkbox in add-on options dialog to set whether remote server is started when NVDA starts.
		self.autoconnect = wx.CheckBox(self, wx.ID_ANY, label=_("Auto-connect to control server on startup"))
		self.autoconnect.Bind(wx.EVT_CHECKBOX, self.on_autoconnect)
		sizer.Add(self.autoconnect)
		# Translators: A checkbox in add-on options dialog to enable update checks when NVDA starts.
		self.check_updates = wx.CheckBox(self, wx.ID_ANY, label=_("Check for TeleNVDA updates on startup"))
		sizer.Add(self.check_updates)
		#Translators: Whether or not to use a relay server when autoconnecting
		self.client_or_server = wx.RadioBox(self, wx.ID_ANY, choices=(_("Use Remote Control Server"), _("Host Control Server")), style=wx.RA_VERTICAL)
		self.client_or_server.Bind(wx.EVT_RADIOBOX, self.on_client_or_server)
		self.client_or_server.SetSelection(0)
		self.client_or_server.Enable(False)
		sizer.Add(self.client_or_server)
		choices = [_("Allow this machine to be controlled"), _("Control another machine")]
		self.connection_type = wx.RadioBox(self, wx.ID_ANY, choices=choices, style=wx.RA_VERTICAL)
		self.connection_type.SetSelection(0)
		self.connection_type.Enable(False)
		sizer.Add(self.connection_type)
		# Translators: A checkbox in add-on options dialog to set whether auto-connect is automatically turned off after a configurable period without any real remote control activity.
		self.disable_autoconnect_inactivity = wx.CheckBox(self, wx.ID_ANY, label=_("Automatically disable auto-connect without any remote control activity"))
		self.disable_autoconnect_inactivity.Bind(wx.EVT_CHECKBOX, self.on_disable_autoconnect_inactivity)
		self.disable_autoconnect_inactivity.Enable(False)
		sizer.Add(self.disable_autoconnect_inactivity)
		# Translators: Label for the inactivity duration field. The value uses days:hours:minutes.
		sizer.Add(wx.StaticText(self, wx.ID_ANY, label=_("Inactivity duration (days:hours:minutes):")))
		self.inactivity_duration = wx.TextCtrl(
			self,
			wx.ID_ANY,
			value=configuration.format_inactivity_duration(configuration.DEFAULT_INACTIVITY_AUTO_DISABLE_SECONDS),
		)
		self.inactivity_duration.SetToolTip(_("Format: days:hours:minutes. For example, 30:00:00 means 30 days and 00:00:01 means 1 minute."))
		self.inactivity_duration.Enable(False)
		sizer.Add(self.inactivity_duration)
		sizer.Add(wx.StaticText(self, wx.ID_ANY, label=_("&Transport:")))
		self.transport = wx.Choice(self, wx.ID_ANY, choices=(_("Standard (TCP)"), _("WebSocket over HTTPS")))
		self.transport.SetSelection(0)
		self.transport.Enable(False)
		self.transport.Bind(wx.EVT_CHOICE, self.on_transport_changed)
		sizer.Add(self.transport)
		sizer.Add(wx.StaticText(self, wx.ID_ANY, label=_("&Host:")))
		self.host = wx.TextCtrl(self, wx.ID_ANY)
		self.host.Enable(False)
		sizer.Add(self.host)
		# Translators: label of the port used to reach the relay or server, as opposed to the proxy port below.
		sizer.Add(wx.StaticText(self, wx.ID_ANY, label=_("&Port (server/relay):")))
		self.port = wx.SpinCtrl(self, wx.ID_ANY, min=1, max=65535)
		self.port.Enable(False)
		# Translators: tooltip clarifying that this port is used to reach the relay/server, not the proxy below.
		self.port.SetToolTip(_("Port used to reach the relay or server (default 6837, or 443 for WebSocket over HTTPS). Unrelated to the proxy port below."))
		sizer.Add(self.port)
		# Translators: label of a checkbox which allows forwarding a port using UPNP
		self.useUPNP = wx.CheckBox(self, wx.ID_ANY, label=_("Use &UPNP to forward this port if possible"))
		self.useUPNP.Enable(False)
		sizer.Add(self.useUPNP)
		sizer.Add(wx.StaticText(self, wx.ID_ANY, label=_("&Key:")))
		self.key = wx.TextCtrl(self, wx.ID_ANY)
		self.key.Enable(False)
		sizer.Add(self.key)
		# Translators: Label of an edit field to enter a second password to exchange encrypted data.
		sizer.Add(wx.StaticText(self, wx.ID_ANY, label=_("En&cryption password (optional):")))
		self.encryption_key = wx.TextCtrl(self, wx.ID_ANY)
		self.encryption_key.Enable(False)
		sizer.Add(self.encryption_key)
		sizer.Add(wx.StaticText(self, wx.ID_ANY, label=_("WebSocket &path:")))
		self.ws_path = wx.TextCtrl(self, wx.ID_ANY, value="/")
		self.ws_path.Enable(False)
		sizer.Add(self.ws_path)
		sizer.Add(wx.StaticText(self, wx.ID_ANY, label=_("Proxy &mode:")))
		self.proxy_mode = wx.Choice(self, wx.ID_ANY, choices=_proxy_mode_choices())
		self.proxy_mode.SetSelection(PROXY_MODES.index("auto"))
		self.proxy_mode.Enable(False)
		self.proxy_mode.Bind(wx.EVT_CHOICE, self.on_proxy_mode_changed)
		sizer.Add(self.proxy_mode)
		sizer.Add(wx.StaticText(self, wx.ID_ANY, label=_("HTTP/SOCKS &proxy host:")))
		self.proxy_host = wx.TextCtrl(self, wx.ID_ANY)
		self.proxy_host.Enable(False)
		sizer.Add(self.proxy_host)
		sizer.Add(wx.StaticText(self, wx.ID_ANY, label=_("Proxy por&t:")))
		self.proxy_port = wx.SpinCtrl(self, wx.ID_ANY, min=0, max=65535)
		self.proxy_port.Enable(False)
		# Translators: tooltip clarifying that this port is the intermediary proxy port, not the relay/server port above.
		self.proxy_port.SetToolTip(_("Port of an intermediary HTTP/SOCKS proxy server, if any. Left at 0 when no proxy is used. Unrelated to the relay/server port above."))
		sizer.Add(self.proxy_port)
		sizer.Add(wx.StaticText(self, wx.ID_ANY, label=_("Proxy &type:")))
		self.proxy_type = wx.Choice(self, wx.ID_ANY, choices=SUPPORTED_PROXY_TYPES)
		self.proxy_type.SetSelection(0)
		self.proxy_type.Enable(False)
		sizer.Add(self.proxy_type)
		sizer.Add(wx.StaticText(self, wx.ID_ANY, label=_("Proxy &username:")))
		self.proxy_username = wx.TextCtrl(self, wx.ID_ANY)
		self.proxy_username.Enable(False)
		sizer.Add(self.proxy_username)
		sizer.Add(wx.StaticText(self, wx.ID_ANY, label=_("Proxy pass&word:")))
		self.proxy_password = wx.TextCtrl(self, wx.ID_ANY, style=wx.TE_PASSWORD)
		self.proxy_password.Enable(False)
		sizer.Add(self.proxy_password)
		# Translators: A checkbox in add-on options dialog to set whether sounds play instead of beeps.
		self.play_sounds = wx.CheckBox(self, wx.ID_ANY, label=_("Play sounds instead of beeps"))
		sizer.Add(self.play_sounds)
		# Translators: A checkbox in add-on options dialog to set whether to display an alert before the controlled computer disconnects.
		self.alert_before_slave_disconnect = wx.CheckBox(self, wx.ID_ANY, label=_("Display an alert before the controlled computer disconnects"))
		sizer.Add(self.alert_before_slave_disconnect)
		# Translators: A checkbox in add-on options dialog to set whether to mute remote speech when controlling the local machine.
		self.mute_when_controlling_local_machine = wx.CheckBox(self, wx.ID_ANY, label=_("Mute remote speech when controlling local machine"))
		sizer.Add(self.mute_when_controlling_local_machine)
		# Translators: A checkbox in add-on options dialog to set whether allow or block speech commands
		self.speech_commands = wx.CheckBox(self, wx.ID_ANY, label=_("Process speech commands when controlling another computer"))
		sizer.Add(self.speech_commands)
		# Translators: A checkbox in add-on options dialog to set whether server welcome messages are displayed only once
		self.motd_once = wx.CheckBox(self, wx.ID_ANY, label=_("Show server welcome messages only once"))
		sizer.Add(self.motd_once)
		# Translators: A checkbox in add-on options dialog to prevent the computer from going to sleep when it is left unattended.
		self.keep_awake = wx.CheckBox(self, wx.ID_ANY, label=_("Prevent this computer from going to sleep when it is not used"))
		self.keep_awake.Bind(wx.EVT_CHECKBOX, self.on_keep_awake)
		sizer.Add(self.keep_awake)
		# Translators: Label for the delay after which an F15 key press is sent to keep the computer awake.
		sizer.Add(wx.StaticText(self, wx.ID_ANY, label=_("Delay without activity before keeping the computer awake (in seconds):")))
		self.keep_awake_delay = wx.SpinCtrl(self, wx.ID_ANY, min=5, max=3600)
		sizer.Add(self.keep_awake_delay)
		# Translators: Label for the maximum duration during which sleep is prevented.
		sizer.Add(wx.StaticText(self, wx.ID_ANY, label=_("Maximum duration for preventing the computer from going to sleep:")))
		self.keep_awake_max_duration = wx.Choice(self, wx.ID_ANY, choices=_keep_awake_max_duration_choices())
		self.keep_awake_max_duration.SetSelection(len(keep_awake.KEEP_AWAKE_MAX_DURATION_MINUTES) - 1)
		sizer.Add(self.keep_awake_max_duration)
		# Translators: A checkbox in add-on options dialog to allow sending files larger than 10 MB to computers running the original TeleNVDA.
		self.allow_large_legacy_transfers = wx.CheckBox(self, wx.ID_ANY, label=_("Allow files larger than 10 MB even when the other computer does not support the new transfer system"))
		self.allow_large_legacy_transfers.Bind(wx.EVT_CHECKBOX, self.on_allow_large_legacy_transfers)
		sizer.Add(self.allow_large_legacy_transfers)
		# Translators: Label for the maximum size of a file sent without the new transfer system.
		sizer.Add(wx.StaticText(self, wx.ID_ANY, label=_("Maximum size of those transfers (in MB):")))
		self.legacy_max_size = wx.SpinCtrl(self, wx.ID_ANY, min=10, max=1024)
		sizer.Add(self.legacy_max_size)
		# Translators: Label for the maximum size of a file accepted from the other computer, 0 meaning no limit.
		sizer.Add(wx.StaticText(self, wx.ID_ANY, label=_("Maximum size of received files, in MB (0 for no limit):")))
		self.max_received_size = wx.SpinCtrl(self, wx.ID_ANY, min=0, max=102400)
		sizer.Add(self.max_received_size)
		# Translators: A checkbox in add-on options dialog to allow sharing this screen and its mouse with the controlling computer.
		self.screen_share_enabled = wx.CheckBox(self, wx.ID_ANY, label=_("Allow sharing the screen of this computer and the use of its mouse, after confirmation"))
		self.screen_share_enabled.Bind(wx.EVT_CHECKBOX, self.on_screen_share_enabled)
		sizer.Add(self.screen_share_enabled)
		# Translators: Label for the maximum number of images per second sent while sharing the screen.
		sizer.Add(wx.StaticText(self, wx.ID_ANY, label=_("Maximum number of images per second:")))
		self.screen_share_max_fps = wx.SpinCtrl(self, wx.ID_ANY, min=1, max=30)
		sizer.Add(self.screen_share_max_fps)
		# Translators: Label for the width the shared picture is reduced to before being sent.
		sizer.Add(wx.StaticText(self, wx.ID_ANY, label=_("Reduce the shared picture to this width, in pixels:")))
		self.screen_share_max_width = wx.Choice(self, wx.ID_ANY, choices=[str(width) for width in SCREEN_SHARE_WIDTHS])
		sizer.Add(self.screen_share_max_width)
		# Translators: Label for the quality of the shared picture.
		sizer.Add(wx.StaticText(self, wx.ID_ANY, label=_("Quality of the shared picture:")))
		self.screen_share_quality = wx.Choice(self, wx.ID_ANY, choices=[
			# Translators: A choice of screen sharing quality, using the least bandwidth.
			_("Low, for a slow connection"),
			# Translators: A choice of screen sharing quality, the default one.
			_("Balanced"),
			# Translators: A choice of screen sharing quality, using the most bandwidth.
			_("High, for a fast connection"),
		])
		sizer.Add(self.screen_share_quality)
		# Translators: a text field in add-on options dialog to set the portcheck service URL
		sizer.Add(wx.StaticText(self, wx.ID_ANY, label=_("Portcheck &service URL: ")))
		self.portcheck = wx.TextCtrl(self, wx.ID_ANY)
		sizer.Add(self.portcheck)
		# Translators: A button in add-on options dialog to delete all fingerprints of unauthorized certificates.
		self.delete_fingerprints = wx.Button(self, wx.ID_ANY, label=_("Delete all trusted fingerprints"))
		self.delete_fingerprints.Bind(wx.EVT_BUTTON, self.on_delete_fingerprints)
		sizer.Add(self.delete_fingerprints)
		# Translators: Label for the folder where received screenshots are saved.
		sizer.Add(wx.StaticText(self, wx.ID_ANY, label=_("Screenshot save &folder:")), 0, wx.TOP, 10)
		screenshot_directory_sizer = wx.BoxSizer(wx.HORIZONTAL)
		self.screenshot_directory = wx.TextCtrl(self, wx.ID_ANY)
		screenshot_directory_sizer.Add(self.screenshot_directory, 1, wx.EXPAND)
		# Translators: Button used to choose the folder where received screenshots are saved.
		self.screenshot_directory_browse = wx.Button(self, wx.ID_ANY, label=_("&Browse..."))
		self.screenshot_directory_browse.Bind(wx.EVT_BUTTON, self.on_browse_screenshot_directory)
		screenshot_directory_sizer.Add(self.screenshot_directory_browse, 0, wx.LEFT, 5)
		sizer.Add(screenshot_directory_sizer, 0, wx.EXPAND)

	def on_keep_awake(self, evt):
		enabled = bool(self.keep_awake.GetValue())
		self.keep_awake_delay.Enable(enabled)
		self.keep_awake_max_duration.Enable(enabled)
		evt.Skip()

	def on_allow_large_legacy_transfers(self, evt):
		self.legacy_max_size.Enable(bool(self.allow_large_legacy_transfers.GetValue()))
		evt.Skip()

	def on_screen_share_enabled(self, evt):
		self._update_screen_share_controls()
		evt.Skip()

	def _update_screen_share_controls(self):
		enabled = bool(self.screen_share_enabled.GetValue())
		self.screen_share_max_fps.Enable(enabled)
		self.screen_share_max_width.Enable(enabled)
		self.screen_share_quality.Enable(enabled)

	def on_autoconnect(self, evt):
		if self.autoconnect.GetValue() and not self._autoconnect_was_enabled:
			# Translators note: default to "Allow this machine to be controlled" whenever auto-connect
			# is turned on, rather than keeping a possibly stale "Control another machine" selection.
			self.connection_type.SetSelection(0)
		self._autoconnect_was_enabled = bool(self.autoconnect.GetValue())
		self.set_controls()

	def set_controls(self):
		state = bool(self.autoconnect.GetValue())
		self.client_or_server.Enable(state)
		self.connection_type.Enable(state)
		self.disable_autoconnect_inactivity.Enable(state)
		self.inactivity_duration.Enable(state and self.disable_autoconnect_inactivity.GetValue())
		self.key.Enable(state)
		self.encryption_key.Enable(state)
		self.host.Enable(not bool(self.client_or_server.GetSelection()) and state)
		self.transport.Enable(not bool(self.client_or_server.GetSelection()) and state)
		self.ws_path.Enable(not bool(self.client_or_server.GetSelection()) and state)
		# The proxy is used by the relay transport itself, so it stays configurable even when
		# auto-connect is off: manual connections read the very same settings.
		proxy_state = not bool(self.client_or_server.GetSelection())
		self.proxy_mode.Enable(proxy_state)
		manual_proxy = self.proxy_mode.GetSelection() == PROXY_MODES.index("manual")
		self.proxy_host.Enable(proxy_state and manual_proxy)
		self.proxy_port.Enable(proxy_state and manual_proxy)
		self.proxy_type.Enable(proxy_state and manual_proxy)
		self.proxy_username.Enable(proxy_state and manual_proxy)
		self.proxy_password.Enable(proxy_state and manual_proxy)
		self.port.Enable(bool(self.client_or_server.GetSelection()) and state)
		self.useUPNP.Enable(bool(self.client_or_server.GetSelection()) and state)

	def on_client_or_server(self, evt):
		evt.Skip()
		self.set_controls()

	def on_disable_autoconnect_inactivity(self, evt):
		evt.Skip()
		self.set_controls()

	def on_proxy_mode_changed(self, evt):
		evt.Skip()
		self.set_controls()

	def on_transport_changed(self, evt):
		if self.transport.GetSelection() == 1 and self.port.GetValue() == socket_utils.SERVER_PORT:
			self.port.SetValue(443)
		elif self.transport.GetSelection() == 0 and self.port.GetValue() == 443:
			self.port.SetValue(socket_utils.SERVER_PORT)
		evt.Skip()

	def onPanelActivated(self):
		config = configuration.get_config()
		updates = config.get('updates', {})
		self.check_updates.SetValue(updates.get('check_at_startup', True))
		cs = config['controlserver']
		self_hosted = cs['self_hosted']
		connection_type = cs['connection_type']
		self.autoconnect.SetValue(cs['autoconnect'])
		self._autoconnect_was_enabled = bool(cs['autoconnect'])
		self.disable_autoconnect_inactivity.SetValue(cs['disable_autoconnect_after_inactivity'])
		self.inactivity_duration.SetValue(
			configuration.format_inactivity_duration(configuration.get_inactivity_auto_disable_seconds())
		)
		self.client_or_server.SetSelection(int(self_hosted))
		self.connection_type.SetSelection(connection_type)
		self.host.SetValue(cs['host'])
		self.transport.SetSelection(1 if cs.get('transport', 'tcp') == 'websocket' else 0)
		self.ws_path.SetValue(cs.get('ws_path', '/'))
		proxy_modes = PROXY_MODES
		configured_proxy_mode = cs.get('proxy_mode', 'auto')
		self.proxy_mode.SetSelection(proxy_modes.index(configured_proxy_mode) if configured_proxy_mode in proxy_modes else proxy_modes.index("auto"))
		self.port.SetValue(str(cs['port']))
		self.useUPNP.SetValue(cs['UPNP'])
		self.key.SetValue(cs['key'])
		self.encryption_key.SetValue(cs['encryption_key'])
		self.proxy_host.SetValue(cs.get('proxy_host', ''))
		self.proxy_port.SetValue(int(cs.get('proxy_port', 0) or 0))
		proxy_types = [self.proxy_type.GetString(i) for i in range(self.proxy_type.GetCount())]
		self.proxy_type.SetSelection(max(0, proxy_types.index(cs.get('proxy_type', 'http')) if cs.get('proxy_type', 'http') in proxy_types else 0))
		self.proxy_username.SetValue(cs.get('proxy_username', ''))
		self.proxy_password.SetValue(cs.get('proxy_password', ''))
		self.set_controls()
		self.play_sounds.SetValue(config['ui']['play_sounds'])
		self.alert_before_slave_disconnect.SetValue(config['ui']['alert_before_slave_disconnect'])
		self.mute_when_controlling_local_machine.SetValue(config['ui']['mute_when_controlling_local_machine'])
		self.speech_commands.SetValue(config['ui']['allow_speech_commands'])
		self.motd_once.SetValue(config['ui']['display_motd_once'])
		self.keep_awake.SetValue(config['keep_awake']['enabled'])
		self.keep_awake_delay.SetValue(int(config['keep_awake']['delay_seconds']))
		max_duration_minutes = int(config['keep_awake'].get('max_duration_minutes', 0) or 0)
		if max_duration_minutes not in keep_awake.KEEP_AWAKE_MAX_DURATION_MINUTES:
			max_duration_minutes = 0
		self.keep_awake_max_duration.SetSelection(
			keep_awake.KEEP_AWAKE_MAX_DURATION_MINUTES.index(max_duration_minutes)
		)
		self.keep_awake_delay.Enable(bool(config['keep_awake']['enabled']))
		self.keep_awake_max_duration.Enable(bool(config['keep_awake']['enabled']))
		self.portcheck.SetValue(config['ui']['portcheck'])
		file_transfer_section = config['file_transfer']
		self.allow_large_legacy_transfers.SetValue(file_transfer_section['allow_large_legacy_transfers'])
		self.legacy_max_size.SetValue(int(file_transfer_section['legacy_max_size_mb']))
		self.legacy_max_size.Enable(bool(file_transfer_section['allow_large_legacy_transfers']))
		self.max_received_size.SetValue(int(file_transfer_section['max_received_size_mb']))
		self.screen_share_enabled.SetValue(bool(config['screen_share']['enabled']))
		self.screen_share_max_fps.SetValue(int(config['screen_share']['max_fps']))
		max_width = int(config['screen_share']['max_width'])
		if max_width not in SCREEN_SHARE_WIDTHS:
			max_width = SCREEN_SHARE_DEFAULT_WIDTH
		self.screen_share_max_width.SetSelection(SCREEN_SHARE_WIDTHS.index(max_width))
		quality = str(config['screen_share']['quality'])
		if quality not in SCREEN_SHARE_QUALITIES:
			quality = SCREEN_SHARE_DEFAULT_QUALITY
		self.screen_share_quality.SetSelection(SCREEN_SHARE_QUALITIES.index(quality))
		self._update_screen_share_controls()
		self.screenshot_directory.SetValue(configuration.get_screenshot_directory())
		self.originalProfileName = NVDAConfig.conf.profiles[-1].name
		NVDAConfig.conf.profiles[-1].name = None
		self.Show()

	def on_delete_fingerprints(self, evt):
		if gui.messageBox(_("When connecting to an unauthorized server, you will again be prompted to accepts its certificate."), _("Are you sure you want to delete all stored trusted fingerprints?"), wx.YES|wx.NO|wx.NO_DEFAULT|wx.ICON_WARNING) == wx.YES:
			config = configuration.get_config()
			config['trusted_certs'].clear()
			if not configuration.readonly:
				config.write()
		evt.Skip()

	def on_browse_screenshot_directory(self, evt):
		current_directory = self.screenshot_directory.GetValue().strip()
		if not os.path.isdir(current_directory):
			current_directory = configuration.get_screenshot_directory()
		dialog = wx.DirDialog(
			self,
			message=_("Choose the folder where received screenshots will be saved"),
			defaultPath=current_directory,
			style=wx.DD_DEFAULT_STYLE | wx.DD_DIR_MUST_EXIST,
		)
		try:
			if dialog.ShowModal() == wx.ID_OK:
				self.screenshot_directory.SetValue(dialog.GetPath())
				self.screenshot_directory.SetFocus()
		finally:
			dialog.Destroy()
		evt.Skip()

	def onPanelDeactivated(self):
		NVDAConfig.conf.profiles[-1].name = self.originalProfileName
		self.Hide()

	def onDiscard(self):
		NVDAConfig.conf.profiles[-1].name = self.originalProfileName

	def onSave(self):
		if not "{port}" in self.portcheck.GetValue():
			# Translators: error message for invalid format on Portcheck service URL
			gui.messageBox(_("Invalid format for portcheck service URL. You must include {port} somewhere."), _("Error"), wx.OK | wx.ICON_ERROR)
			raise
		inactivity_seconds = None
		if self.disable_autoconnect_inactivity.GetValue():
			try:
				inactivity_seconds = configuration.parse_inactivity_duration(self.inactivity_duration.GetValue())
			except ValueError:
				gui.messageBox(
					_("Invalid inactivity duration. Use the format jj:hh:mm, for example 30:00:00 or 00:00:01."),
					_("Error"),
					wx.OK | wx.ICON_ERROR,
				)
				self.inactivity_duration.SetFocus()
				raise
		if self.autoconnect.GetValue():
			if not self.client_or_server.GetSelection() and (not self.host.GetValue() or not self.key.GetValue()):
				gui.messageBox(_("Both host and key must be set."), _("Error"), wx.OK | wx.ICON_ERROR)
				raise
			elif self.client_or_server.GetSelection() and not self.port.GetValue() or not self.key.GetValue():
				gui.messageBox(_("Both port and key must be set."), _("Error"), wx.OK | wx.ICON_ERROR)
				raise
			if len(self.key.GetValue()) < 6:
				# Translators: error message for key/password length less than 6 characters
				gui.messageBox(_("The key must be longer than 6 characters."), _("Error"), wx.OK | wx.ICON_ERROR)
				self.key.SetFocus()
				raise
			elif is_sequential(self.key.GetValue()):
				# Translators: error message for key/password being sequential, example 123456
				gui.messageBox(_("The key must not be sequential. Please, avoid keys such as 1234, 4321 or similar."), _("Error"), wx.OK | wx.ICON_ERROR)
				self.key.SetFocus()
				raise
		NVDAConfig.conf.profiles[-1].name = self.originalProfileName
		config = configuration.get_config()
		cs = config['controlserver']
		cs['autoconnect'] = self.autoconnect.GetValue()
		cs['disable_autoconnect_after_inactivity'] = self.disable_autoconnect_inactivity.GetValue()
		if inactivity_seconds is not None:
			cs['inactivity_auto_disable_seconds'] = inactivity_seconds
		self_hosted = bool(self.client_or_server.GetSelection())
		connection_type = self.connection_type.GetSelection()
		cs['self_hosted'] = self_hosted
		cs['connection_type'] = connection_type
		if not self_hosted:
			cs['host'] = self.host.GetValue()
		else:
			cs['port'] = int(self.port.GetValue())
			cs['UPNP'] = bool(self.useUPNP.GetValue())
		cs['key'] = self.key.GetValue()
		cs['encryption_key'] = self.encryption_key.GetValue()
		cs['transport'] = 'websocket' if self.transport.GetSelection() == 1 else 'tcp'
		cs['ws_path'] = self.ws_path.GetValue() or '/'
		cs['proxy_mode'] = PROXY_MODES[self.proxy_mode.GetSelection()]
		cs['proxy_host'] = self.proxy_host.GetValue()
		cs['proxy_port'] = int(self.proxy_port.GetValue())
		cs['proxy_type'] = self.proxy_type.GetStringSelection()
		cs['proxy_username'] = self.proxy_username.GetValue()
		cs['proxy_password'] = self.proxy_password.GetValue()
		config['ui']['play_sounds'] = self.play_sounds.GetValue()
		config['ui']['alert_before_slave_disconnect'] = self.alert_before_slave_disconnect.GetValue()
		config['ui']['mute_when_controlling_local_machine'] = self.mute_when_controlling_local_machine.GetValue()
		config['ui']['allow_speech_commands'] = self.speech_commands.GetValue()
		config['ui']['display_motd_once'] = self.motd_once.GetValue()
		config['ui']['portcheck'] = self.portcheck.GetValue()
		config['keep_awake']['enabled'] = self.keep_awake.GetValue()
		config['keep_awake']['delay_seconds'] = int(self.keep_awake_delay.GetValue())
		config['keep_awake']['max_duration_minutes'] = keep_awake.KEEP_AWAKE_MAX_DURATION_MINUTES[
			self.keep_awake_max_duration.GetSelection()
		]
		config['screenshots']['directory'] = self.screenshot_directory.GetValue().strip()
		config['file_transfer']['allow_large_legacy_transfers'] = self.allow_large_legacy_transfers.GetValue()
		config['file_transfer']['legacy_max_size_mb'] = int(self.legacy_max_size.GetValue())
		config['file_transfer']['max_received_size_mb'] = int(self.max_received_size.GetValue())
		config['screen_share']['enabled'] = self.screen_share_enabled.GetValue()
		config['screen_share']['max_fps'] = int(self.screen_share_max_fps.GetValue())
		config['screen_share']['max_width'] = SCREEN_SHARE_WIDTHS[self.screen_share_max_width.GetSelection()]
		config['screen_share']['quality'] = SCREEN_SHARE_QUALITIES[self.screen_share_quality.GetSelection()]
		config['updates']['check_at_startup'] = self.check_updates.GetValue()
		if not configuration.readonly:
			config.write()
		plugin_module = sys.modules.get(__package__)
		plugin = getattr(plugin_module, 'client', None)
		if plugin is not None and not getattr(plugin, '_terminated', False):
			plugin.restart_inactivity_monitor()
			plugin.keep_awake.reload()

class CertificateUnauthorizedDialog(wx.MessageDialog):

	def __init__(self, parent, fingerprint=None):
		# Translators: A title bar of a window presented when an attempt has been made to connect with a server with unauthorized certificate.
		title=_("TeleNVDA Connection Security Warning")
		# Translators: A message of a window presented when an attempt has been made to connect with a server with unauthorized certificate.
		message = _("Warning! The certificate of this server could not be verified.\nThis connection may not be secure. It is possible that someone is trying to overhear your communication.\nBefore continuing please make sure that the following server certificate fingerprint is a proper one.\nIf you have any questions, please contact the server administrator.\n\nServer SHA256 fingerprint: {fingerprint}\n\nDo you want to continue connecting?").format(fingerprint=fingerprint)
		super().__init__(parent, caption=title, message=message, style=wx.YES_NO|wx.CANCEL|wx.CANCEL_DEFAULT|wx.CENTRE)
		self.SetYesNoLabels(_("Connect and do not ask again for this server"), _("Connect"))

def is_sequential(password):
	if len(password) < 3:
		return False
	for i in range(len(password) - 2):
		if ord(password[i]) == ord(password[i + 1]) - 1 == ord(password[i + 2]) - 2:
			return True
	return False
