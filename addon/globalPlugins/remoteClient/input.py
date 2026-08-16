import ctypes
from ctypes import (
	wintypes,
	Structure,
	c_long,
	POINTER,
	c_ulong,
	Union,
)
import braille
import brailleInput
import globalPluginHandler
import scriptHandler
import api
import vision
import baseObject

INPUT_MOUSE = 0
INPUT_KEYBOARD = 1
INPUT_HARDWARE = 2
MAPVK_VK_TO_VSC = 0
KEYEVENTF_EXTENDEDKEY = 0x0001
KEYEVENTF_KEYUP = 0x0002
KEYEVENT_SCANCODE = 0x0008
KEYEVENTF_UNICODE = 0x0004

MOUSEEVENTF_MOVE = 0x0001
MOUSEEVENTF_LEFTDOWN = 0x0002
MOUSEEVENTF_LEFTUP = 0x0004
MOUSEEVENTF_RIGHTDOWN = 0x0008
MOUSEEVENTF_RIGHTUP = 0x0010
MOUSEEVENTF_MIDDLEDOWN = 0x0020
MOUSEEVENTF_MIDDLEUP = 0x0040
MOUSEEVENTF_WHEEL = 0x0800
MOUSEEVENTF_HWHEEL = 0x1000
MOUSEEVENTF_VIRTUALDESK = 0x4000
MOUSEEVENTF_ABSOLUTE = 0x8000

#: Metrics describing the rectangle covering every monitor.
SM_XVIRTUALSCREEN = 76
SM_YVIRTUALSCREEN = 77
SM_CXVIRTUALSCREEN = 78
SM_CYVIRTUALSCREEN = 79

#: One notch of the mouse wheel.
WHEEL_DELTA = 120

#: Absolute mouse coordinates are expressed on this scale rather than in pixels.
ABSOLUTE_RANGE = 65535

#: Written in dwExtraInfo of the events this add-on injects, so that the mouse hook
#: can tell them apart from what the user really did. Without it, a computer acting
#: as both controlled and controlling machine would send back every event it applies,
#: which would loop endlessly between the two peers.
INJECTED_TAG = 0x54454C45  # "TELE"

#: Buttons understood in a mouse message, with the flags to press and release them.
MOUSE_BUTTONS = {
	"left": (MOUSEEVENTF_LEFTDOWN, MOUSEEVENTF_LEFTUP),
	"right": (MOUSEEVENTF_RIGHTDOWN, MOUSEEVENTF_RIGHTUP),
	"middle": (MOUSEEVENTF_MIDDLEDOWN, MOUSEEVENTF_MIDDLEUP),
}


class MOUSEINPUT(Structure):
	_fields_ = (
		("dx", c_long),
		("dy", c_long),
		("mouseData", wintypes.DWORD),
		("dwFlags", wintypes.DWORD),
		("time", wintypes.DWORD),
		("dwExtraInfo", POINTER(c_ulong)),
	)


class KEYBDINPUT(Structure):
	_fields_ = (
		("wVk", wintypes.WORD),
		("wScan", wintypes.WORD),
		("dwFlags", wintypes.DWORD),
		("time", wintypes.DWORD),
		("dwExtraInfo", POINTER(c_ulong)),
	)


class HARDWAREINPUT(Structure):
	_fields_ = (
		("uMsg", wintypes.DWORD),
		("wParamL", wintypes.WORD),
		("wParamH", wintypes.WORD),
	)


class INPUTUnion(Union):
	_fields_ = (
		("mi", MOUSEINPUT),
		("ki", KEYBDINPUT),
		("hi", HARDWAREINPUT),
	)


class INPUT(Structure):
	_fields_ = (
		("type", wintypes.DWORD),
		("union", INPUTUnion),
	)


class BrailleInputGesture(braille.BrailleDisplayGesture, brailleInput.BrailleInputGesture):
	def __init__(self, **kwargs):
		super().__init__()
		for key, value in kwargs.items():
			setattr(self, key, value)
		self.source = "remote{}{}".format(self.source[0].upper(), self.source[1:])
		self.scriptPath = getattr(self, "scriptPath", None)
		self.script = self.findScript() if self.scriptPath else None

	def findScript(self):
		if not (isinstance(self.scriptPath, list) and len(self.scriptPath) == 3):
			return None
		module, cls, scriptName = self.scriptPath
		focus = api.getFocusObject()
		if not focus:
			return None
		if scriptName.startswith("kb:"):
			# Emulate a key press.
			return scriptHandler._makeKbEmulateScript(scriptName)

		import globalCommands

		# Global plugin level.
		if cls == "GlobalPlugin":
			for plugin in globalPluginHandler.runningPlugins:
				if module == plugin.__module__:
					func = getattr(plugin, "script_%s" % scriptName, None)
					if func:
						return func

		# App module level.
		app = focus.appModule
		if app and cls == "AppModule" and module == app.__module__:
			func = getattr(app, "script_%s" % scriptName, None)
			if func:
				return func

		# Vision enhancement provider level
		for provider in vision.handler.getActiveProviderInstances():
			if isinstance(provider, baseObject.ScriptableObject):
				if cls == "VisionEnhancementProvider" and module == provider.__module__:
					func = getattr(app, "script_%s" % scriptName, None)
					if func:
						return func

		# Tree interceptor level.
		treeInterceptor = focus.treeInterceptor
		if treeInterceptor and treeInterceptor.isReady:
			func = getattr(treeInterceptor, "script_%s" % scriptName, None)
			if func:
				return func

		# NVDAObject level.
		func = getattr(focus, "script_%s" % scriptName, None)
		if func:
			return func
		for obj in reversed(api.getFocusAncestors()):
			func = getattr(obj, "script_%s" % scriptName, None)
			if func and getattr(func, "canPropagate", False):
				return func

		# Global commands.
		func = getattr(globalCommands.commands, "script_%s" % scriptName, None)
		if func:
			return func

		return None


def send_key(vk=None, scan=None, extended=False, pressed=True):
	i = INPUT()
	i.union.ki.wVk = vk
	if scan:
		i.union.ki.wScan = scan
	else:  # No scancode provided, try to get one
		i.union.ki.wScan = ctypes.windll.user32.MapVirtualKeyW(vk, MAPVK_VK_TO_VSC)
	if not pressed:
		i.union.ki.dwFlags |= KEYEVENTF_KEYUP
	if extended:
		i.union.ki.dwFlags |= KEYEVENTF_EXTENDEDKEY
	i.type = INPUT_KEYBOARD
	ctypes.windll.user32.SendInput(1, ctypes.byref(i), ctypes.sizeof(INPUT))


def get_virtual_desktop():
	"""Return the rectangle covering every monitor, as (left, top, width, height).

	The origin is negative when a monitor sits above or to the left of the primary one,
	which is why the position of the pointer cannot simply be read as a pixel count.
	"""
	metric = ctypes.windll.user32.GetSystemMetrics
	return (
		metric(SM_XVIRTUALSCREEN),
		metric(SM_YVIRTUALSCREEN),
		metric(SM_CXVIRTUALSCREEN),
		metric(SM_CYVIRTUALSCREEN),
	)


def get_cursor_position():
	"""Return where the pointer is on the virtual desktop, as a fraction of its size.

	Both coordinates are returned in the 0 to 1 range, so that they mean the same thing
	on a computer with another resolution, another scaling factor or another number of
	monitors. None is returned when the position cannot be read.
	"""
	point = wintypes.POINT()
	if not ctypes.windll.user32.GetCursorPos(ctypes.byref(point)):
		return None
	left, top, width, height = get_virtual_desktop()
	if width <= 1 or height <= 1:
		return None
	x = (point.x - left) / (width - 1)
	y = (point.y - top) / (height - 1)
	return (min(max(x, 0.0), 1.0), min(max(y, 0.0), 1.0))


def _injected_tag():
	"""Return the marker to store in dwExtraInfo, in the type the structure expects."""
	return ctypes.cast(ctypes.c_void_p(INJECTED_TAG), POINTER(c_ulong))


def _send_mouse_input(flags, x=None, y=None, data=0):
	"""Inject one mouse event, optionally moving the pointer at the same time.

	``x`` and ``y`` are fractions of the virtual desktop, as returned by
	:func:`get_cursor_position`. Returns True when Windows accepted the event.
	"""
	i = INPUT()
	i.type = INPUT_MOUSE
	if x is not None and y is not None:
		# MOUSEEVENTF_VIRTUALDESK makes Windows spread the 0 to 65535 range over every
		# monitor instead of the primary one only, which is what our fractions mean.
		i.union.mi.dx = int(round(min(max(x, 0.0), 1.0) * ABSOLUTE_RANGE))
		i.union.mi.dy = int(round(min(max(y, 0.0), 1.0) * ABSOLUTE_RANGE))
		flags |= MOUSEEVENTF_MOVE | MOUSEEVENTF_ABSOLUTE | MOUSEEVENTF_VIRTUALDESK
	# mouseData is unsigned, so a wheel movement towards the user has to be wrapped.
	i.union.mi.mouseData = data & 0xFFFFFFFF
	i.union.mi.dwFlags = flags
	i.union.mi.dwExtraInfo = _injected_tag()
	sent = ctypes.windll.user32.SendInput(1, ctypes.byref(i), ctypes.sizeof(INPUT))
	return sent == 1


def move_mouse(x, y):
	"""Move the pointer to a position given as a fraction of the virtual desktop."""
	return _send_mouse_input(0, x, y)


def click_mouse(button="left", pressed=True, x=None, y=None):
	"""Press or release a mouse button, moving the pointer first when a position is given."""
	flags = MOUSE_BUTTONS.get(button)
	if flags is None:
		return False
	return _send_mouse_input(flags[0] if pressed else flags[1], x, y)


def scroll_mouse(delta=0, horizontal=False, x=None, y=None):
	"""Turn the mouse wheel by the given number of notches.

	A positive vertical delta scrolls away from the user, as Windows expects.
	"""
	if not delta:
		return False
	flags = MOUSEEVENTF_HWHEEL if horizontal else MOUSEEVENTF_WHEEL
	return _send_mouse_input(flags, x, y, data=int(delta) * WHEEL_DELTA)
