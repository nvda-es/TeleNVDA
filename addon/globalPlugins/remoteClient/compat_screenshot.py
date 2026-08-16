"""PowerShell screenshot capture for computers without native screenshot support.

The standard NVDA Remote protocol has no screenshot message. This module uses
the same clipboard and keyboard sequence as the working TeleNVDA beta capture:

* the short PowerShell launcher is placed on the controlled computer's clipboard;
* Windows+R opens the Run dialog and the launcher is pasted;
* the actual capture script replaces the clipboard content before the launcher
	is executed, so the Run dialog length limit is not an issue;
* PowerShell captures the virtual screen, encodes it as a JPEG and Base64, and
	puts it back on the clipboard behind a recognisable marker;
* the controlled computer's standard clipboard-push gesture sends that text back
	to the controlling computer.
"""

import threading

from logHandler import log

# Prefix identifying a clipboard push which actually carries a screenshot.
MARKER = "TELENVDA_BETA:"

VK_RETURN = 0x0D
VK_SHIFT = 0x10
VK_INSERT = 0x2D
VK_C = 0x43
VK_CONTROL = 0x11
VK_R = 0x52
VK_V = 0x56
VK_LWIN = 0x5B

# The Run dialog only accepts about 260 characters, so it runs a short command
# which writes the actual capture script from the clipboard to a temporary file.
LAUNCHER_COMMAND = (
	'powershell -NoProfile -w hidden -c "'
	"$f=$env:TEMP+'\\tnb.ps1';"
	"Get-Clipboard -Raw|Out-File -LiteralPath $f -Encoding UTF8;"
	"powershell -NoProfile -w hidden -ExecutionPolicy Bypass -STA -File $f;"
	"Remove-Item -LiteralPath $f -Force\""
)

# This is the PowerShell script used by the working beta implementation.
SCRIPT = (
	"try{"
	"Add-Type -AssemblyName System.Windows.Forms;"
	"Add-Type -AssemblyName System.Drawing;"
	"$b=[System.Windows.Forms.SystemInformation]::VirtualScreen;"
	"$bmp=New-Object System.Drawing.Bitmap $b.Width,$b.Height;"
	"$g=[System.Drawing.Graphics]::FromImage($bmp);"
	"$g.CopyFromScreen($b.Location,[System.Drawing.Point]::Empty,$b.Size);"
	"$g.Dispose();"
	"$ms=New-Object System.IO.MemoryStream;"
	"$bmp.Save($ms,[System.Drawing.Imaging.ImageFormat]::Jpeg);"
	"$bmp.Dispose();"
	"[System.Windows.Forms.Clipboard]::SetText('" + MARKER + "'+[Convert]::ToBase64String($ms.ToArray()))"
	"}catch{"
	"[System.Windows.Forms.Clipboard]::SetText('TELENVDA_ERR:'+$_.Exception.Message)"
	"}"
)


class CompatScreenshotRequest:
	def __init__(self, send, on_failure=None):
		self._send = send
		self._on_failure = on_failure
		self._cancelled = threading.Event()

	def start(self, delay=0.0):
		threading.Thread(
			target=self._run,
			args=(delay,),
			name="TeleNVDA compatible screenshot",
			daemon=True,
		).start()

	def cancel(self):
		self._cancelled.set()

	def _wait(self, seconds):
		"""Wait for the given delay, returning False when the request was cancelled."""
		return not self._cancelled.wait(seconds)

	def _key(self, vk_code, extended=False, pressed=True):
		log.debug("compat_screenshot: key %#04x extended=%s pressed=%s" % (vk_code, extended, pressed))
		self._send(type="key", vk_code=vk_code, extended=extended, pressed=pressed)

	def _stroke(self, vk_code, extended=False, modifiers=()):
		for modifier, modifier_extended in modifiers:
			self._key(modifier, extended=modifier_extended)
		self._key(vk_code, extended=extended)
		self._key(vk_code, extended=extended, pressed=False)
		for modifier, modifier_extended in reversed(modifiers):
			self._key(modifier, extended=modifier_extended, pressed=False)

	def _push_clipboard(self, nvda_key_extended):
		"""Trigger the controlled computer's NVDA+control+shift+c command."""
		self._stroke(VK_C, modifiers=(
			(VK_INSERT, nvda_key_extended),
			(VK_CONTROL, False),
			(VK_SHIFT, False),
		))

	def _run(self, delay):
		try:
			if not self._wait(delay):
				log.info("compat_screenshot: cancelled before starting, the controlled computer answered")
				return
			log.info("compat_screenshot: starting the compatible screenshot sequence")
			self._send(type="set_clipboard_text", text=LAUNCHER_COMMAND)
			if not self._wait(0.5):
				return
			log.info("compat_screenshot: opening the Run dialog")
			self._stroke(VK_R, modifiers=((VK_LWIN, True),))
			if not self._wait(0.7):
				return
			# Paste the short launcher into the Run dialog.
			self._stroke(VK_V, modifiers=((VK_CONTROL, False),))
			if not self._wait(0.5):
				return
			# PowerShell reads the script from the clipboard once it starts, so the
			# script can replace the clipboard after the launcher has been pasted.
			self._send(type="set_clipboard_text", text=SCRIPT)
			if not self._wait(0.5):
				return
			log.info("compat_screenshot: running the capture script on the controlled computer")
			self._stroke(VK_RETURN)
			if not self._wait(3.8):
				return
			log.info("compat_screenshot: requesting the clipboard of the controlled computer")
			self._push_clipboard(nvda_key_extended=True)
			if not self._wait(14.0):
				log.info("compat_screenshot: screenshot received, sequence stopped")
				return
			log.warning("compat_screenshot: the controlled computer did not return any screenshot")
			if self._on_failure is not None:
				self._on_failure()
		except Exception:
			log.exception("compat_screenshot: unable to request a screenshot from the controlled computer")
