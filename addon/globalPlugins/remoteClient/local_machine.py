import os
import globalVars
import wx
from . import input
from . import cues
from . import configuration
from . import file_transfer
from . import mouse_control
import api
import nvwave
import tones
import speech
import ctypes
import braille
import inputCore

try:
	from systemUtils import hasUiAccess
except ModuleNotFoundError:
	from config import hasUiAccess
import ui
import buildVersion
import logging
import addonHandler
import base64
import gui

logger = logging.getLogger("local_machine")
from logHandler import log

import subprocess
import tempfile
import threading
try:
	addonHandler.initTranslation()
except addonHandler.AddonError:
	log.warning(
		"Unable to initialise translations. This may be because the addon is running from NVDA scratchpad.",
	)

# Screenshots are converted from the raw screen bitmap to JPEG before being Base64 encoded,
# so that the resulting message stays small enough for the relay.
SCREENSHOT_SUFFIX = ".jpg"


def setSpeechCancelledToFalse():
	"""
	This function updates the state of speech so that it is aware that future
	speech should not be cancelled. In the long term this is a fragile solution
	as NVDA does not support modifying the internal state of speech.
	"""
	if buildVersion.version_year >= 2021:
		# workaround as beenCanceled is readonly as of NVDA#12395
		speech.speech._speechState.beenCanceled = False
	else:
		speech.beenCanceled = False


class LocalMachine:
	def __init__(self):
		self.is_muted = False
		self.receiving_braille = False
		self._cached_sizes = None
		if buildVersion.version_year >= 2023:
			braille.decide_enabled.register(self.handle_decide_enabled)

	def terminate(self):
		if buildVersion.version_year >= 2023:
			braille.decide_enabled.unregister(self.handle_decide_enabled)

	def play_wave(self, fileName):
		"""Instructed by remote machine to play a wave file."""
		if self.is_muted:
			return
		if os.path.exists(fileName):
			# ignore async / asynchronous from kwargs:
			# playWaveFile should play asynchronously from TeleNVDA.
			nvwave.playWaveFile(fileName=fileName, asynchronous=True)

	def beep(self, hz, length, left, right, **kwargs):
		if self.is_muted:
			return
		tones.beep(hz, length, left, right)

	def cancel_speech(self, **kwargs):
		if self.is_muted:
			return
		wx.CallAfter(speech._manager.cancel)

	def pause_speech(self, switch, **kwargs):
		if self.is_muted:
			return
		wx.CallAfter(speech.pauseSpeech, switch)

	def speak(
		self,
		sequence,
		priority=speech.priorities.Spri.NORMAL,
		**kwargs,
	):
		if self.is_muted:
			return
		setSpeechCancelledToFalse()
		if not configuration.get_config()["ui"]["allow_speech_commands"]:
			sequence = [s for s in sequence if isinstance(s, str)]
		wx.CallAfter(speech._manager.speak, sequence, priority)

	def display(self, cells, **kwargs):
		if (
			self.receiving_braille
			and braille.handler.displaySize > 0
			and len(cells) <= braille.handler.displaySize
		):
			# We use braille.handler._writeCells since this respects thread safe displays and automatically falls back to noBraille if desired
			cells = cells + [0] * (braille.handler.displaySize - len(cells))
			wx.CallAfter(braille.handler._writeCells, cells)

	def braille_input(self, **kwargs):
		try:
			inputCore.manager.executeGesture(input.BrailleInputGesture(**kwargs))
		except inputCore.NoInputGestureAction:
			pass

	def set_braille_display_size(self, sizes, **kwargs):
		if buildVersion.version_year >= 2023:
			self._cached_sizes = sizes
			return
		sizes.append(braille.handler.display.numCells)
		try:
			size = min(i for i in sizes if i > 0)
		except ValueError:
			size = braille.handler.display.numCells
		braille.handler.displaySize = size
		braille.handler.enabled = bool(size)

	def handle_filter_displaySize(self, value):
		if not self._cached_sizes:
			return value
		sizes = self._cached_sizes + [value]
		try:
			return min(i for i in sizes if i > 0)
		except ValueError:
			return value

	def handle_filter_displayDimensions(self, value):
		if not self._cached_sizes:
			return value._replace(numRows=1)
		sizes = self._cached_sizes + [value.numCols]
		try:
			return braille.DisplayDimensions(numRows=1, numCols=min(i for i in sizes if i > 0))
		except ValueError:
			return value._replace(numRows=1)

	def handle_decide_enabled(self):
		return not self.receiving_braille

	def send_key(self, vk_code=None, extended=None, pressed=None, **kwargs):
		wx.CallAfter(input.send_key, vk_code, None, extended, pressed)

	def send_mouse(self, t=None, x=None, y=None, b=None, d=None, h=False, **kwargs):
		"""Apply one mouse event sent by the controlling computer.

		Coordinates are fractions of the virtual desktop rather than pixels, because the
		two computers rarely share a resolution or a monitor layout. Anything malformed
		is dropped rather than guessed: this comes from the network.
		"""
		position = None
		if x is not None and y is not None:
			try:
				position = (float(x), float(y))
			except (TypeError, ValueError):
				return
		if t == mouse_control.ACTION_MOVE:
			if position is None:
				return
			wx.CallAfter(input.move_mouse, position[0], position[1])
		elif t in (mouse_control.ACTION_BUTTON_DOWN, mouse_control.ACTION_BUTTON_UP):
			if b not in mouse_control.BUTTONS:
				return
			pressed = t == mouse_control.ACTION_BUTTON_DOWN
			if position is None:
				wx.CallAfter(input.click_mouse, b, pressed)
			else:
				wx.CallAfter(input.click_mouse, b, pressed, position[0], position[1])
		elif t == mouse_control.ACTION_WHEEL:
			try:
				delta = int(d)
			except (TypeError, ValueError):
				return
			# Clamp instead of trusting the peer, so that a single message cannot scroll
			# a document from end to end.
			limit = mouse_control.MAX_WHEEL_NOTCHES
			delta = min(max(delta, -limit), limit)
			if not delta:
				return
			if position is None:
				wx.CallAfter(input.scroll_mouse, delta, bool(h))
			else:
				wx.CallAfter(input.scroll_mouse, delta, bool(h), position[0], position[1])

	def set_clipboard_text(self, text, **kwargs):
		cues.clipboard_received()
		ui.message(_("Clipboard updated"))
		api.copyToClip(text=text)

	def send_SAS(self, **kwargs):
		"""
		This function simulates as "a secure attention sequence" such as CTRL+ALT+DEL.
		SendSAS requires UI Access, so we provide a warning when this fails.
		This warning will only be read by the remote NVDA if it is currently connected to the machine.
		"""
		if hasUiAccess():
			ctypes.windll.sas.SendSAS(0)
		else:
			# Translators: Sent when a user fails to send CTRL+ALT+DEL from a remote NVDA instance
			ui.message(_("No permission on device to trigger CTRL+ALT+DEL from remote"))
			logger.warning("UI Access is disabled on this machine so cannot trigger CTRL+ALT+DEL")

	def _capture_native_screenshot(self):
		width, height = wx.GetDisplaySize()
		bitmap = wx.Bitmap(width, height, 24)
		dc = wx.MemoryDC(bitmap)
		dc.Blit(0, 0, width, height, wx.ScreenDC(), 0, 0)
		dc.SelectObject(wx.NullBitmap)
		fd, path = tempfile.mkstemp(prefix="teleNVDA-screenshot-", suffix=SCREENSHOT_SUFFIX)
		os.close(fd)
		try:
			# The raw bitmap is converted to JPEG to keep the transferred message small.
			if not bitmap.ConvertToImage().SaveFile(path, wx.BITMAP_TYPE_JPEG):
				raise RuntimeError("Unable to encode the screenshot as JPEG")
			with open(path, "rb") as stream:
				return base64.b64encode(stream.read()).decode("ascii")
		finally:
			try:
				os.unlink(path)
			except OSError:
				pass

	def _powershell_executable(self):
		"""Return the PowerShell command to run.

		The absolute path is preferred because the PATH environment variable of the NVDA
		process can be restricted, especially when NVDA runs as a service or on the secure
		desktop.
		"""
		system_root = os.environ.get("SystemRoot", r"C:\Windows")
		path = os.path.join(system_root, "System32", "WindowsPowerShell", "v1.0", "powershell.exe")
		if os.path.isfile(path):
			return path
		return "powershell.exe"

	def _capture_powershell_screenshot(self):
		fd, path = tempfile.mkstemp(prefix="teleNVDA-screenshot-", suffix=SCREENSHOT_SUFFIX)
		os.close(fd)
		script = (
			"Add-Type -AssemblyName System.Windows.Forms,System.Drawing; "
			"$b=[System.Windows.Forms.SystemInformation]::VirtualScreen; "
			"$i=New-Object System.Drawing.Bitmap $b.Width,$b.Height; "
			"$g=[System.Drawing.Graphics]::FromImage($i); "
			"$g.CopyFromScreen($b.Left,$b.Top,0,0,$i.Size); "
			"$i.Save('"
		)
		# Keep the script plain PowerShell so it works on Windows PowerShell 5.1.
		# The bitmap is saved as JPEG to keep the transferred message small. The explicit GDI+
		# encoder API is deliberately avoided here because anti-virus heuristics flag it.
		script += path.replace("'", "''") + "',[System.Drawing.Imaging.ImageFormat]::Jpeg); $g.Dispose(); $i.Dispose()"
		try:
			result = subprocess.run((self._powershell_executable(), "-NoProfile", "-NonInteractive", "-ExecutionPolicy", "Bypass", "-Command", script), capture_output=True, timeout=20, creationflags=subprocess.CREATE_NO_WINDOW)
			if result.returncode:
				raise RuntimeError(result.stderr.decode(errors="replace"))
			if not os.path.getsize(path):
				raise RuntimeError("PowerShell produced an empty screenshot")
			with open(path, "rb") as stream:
				return base64.b64encode(stream.read()).decode("ascii")
		finally:
			try:
				os.unlink(path)
			except OSError:
				pass

	def capture_screenshot(self, method="native", callback=None):
		def capture_native():
			try:
				data = self._capture_native_screenshot()
			except Exception:
				logger.exception("Unable to capture screenshot")
				data = None
			if callback:
				callback(data)

		def capture_powershell():
			try:
				data = self._capture_powershell_screenshot()
			except Exception:
				logger.exception("Unable to capture screenshot with PowerShell; falling back to the native method")
				data = None
			if data:
				if callback:
					callback(data)
				return
			# PowerShell can be missing or blocked by a security policy on this machine.
			wx.CallAfter(capture_native)

		if method == "native":
			# wx drawing objects must only be used from the GUI thread.
			wx.CallAfter(capture_native)
			return
		threading.Thread(target=capture_powershell, name="TeleNVDA screenshot", daemon=True).start()

	def open_received_screenshot(self, data, **kwargs):
		try:
			directory = configuration.get_screenshot_directory()
			try:
				fd, path = tempfile.mkstemp(prefix="teleNVDA-remote-", suffix=SCREENSHOT_SUFFIX, dir=directory)
			except OSError:
				# The configured directory can become unavailable after the options were saved.
				logger.warning("Unable to use screenshot directory %s; falling back to the user temp directory", directory)
				fd, path = tempfile.mkstemp(prefix="teleNVDA-remote-", suffix=SCREENSHOT_SUFFIX, dir=tempfile.gettempdir())
			with os.fdopen(fd, "wb") as stream:
				stream.write(base64.b64decode(data.encode("ascii"), validate=True))
			os.startfile(path)
		except Exception:
			logger.exception("Unable to open received screenshot")

	def file_transfer(self, name, content, **kwargs):
		if globalVars.appArgs.secure:
			return
		# The name comes from another computer and must never be able to designate
		# anything else than a file name in the folder chosen by the user.
		name = file_transfer.sanitize_file_name(name)
		fd = wx.FileDialog(
			gui.mainFrame,
			# Translators: message displayed in transfer file dialog when receiving a file
			message=_("Choose where to save the received file"),
			defaultDir=os.environ["userprofile"],
			defaultFile=name,
			# Translators: supported file types when sending or receiving files
			wildcard=_("All files (*.*)") + "|*.*",
			style=wx.FD_SAVE | wx.FD_OVERWRITE_PROMPT,
		)
		if fd.ShowModal() == wx.ID_OK:
			try:
				f = open(fd.GetPath(), "wb")
				file_content = base64.b64decode(content.encode("utf-8"))
				f.write(file_content)
				f.close()
				cues.clipboard_received()
				# Translators: message spoken when the file has been received successfully
				ui.message(_("File received"))
			except:
				logger.exception("Unable to save received file to disk")
