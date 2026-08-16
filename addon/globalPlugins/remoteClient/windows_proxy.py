"""Windows proxy auto-detection for a destination URL."""

from __future__ import annotations

import ctypes
import fnmatch
import os
from ctypes import wintypes
from dataclasses import dataclass
from urllib.parse import urlsplit, urlunsplit


_WINHTTP_ACCESS_TYPE_NO_PROXY = 1
_WINHTTP_AUTO_PROXY_FLAG_AUTO_DETECT = 0x00000001
_WINHTTP_AUTO_PROXY_FLAG_CONFIG_URL = 0x00000002
_WINHTTP_AUTO_DETECT_TYPE_DHCP = 0x00000001
_WINHTTP_AUTO_DETECT_TYPE_DNS_A = 0x00000002


@dataclass(frozen=True)
class DetectedProxy:
	"""Proxy selected by Windows for a destination URL."""

	host: str = ""
	port: int = 0
	type: str = "negotiate"

	@property
	def enabled(self):
		return bool(self.host and self.port)


class _CurrentUserIEProxyConfig(ctypes.Structure):
	_fields_ = [
		("fAutoDetect", wintypes.BOOL),
		("lpszAutoConfigUrl", ctypes.c_void_p),
		("lpszProxy", ctypes.c_void_p),
		("lpszProxyBypass", ctypes.c_void_p),
	]


class _AutoProxyOptions(ctypes.Structure):
	_fields_ = [
		("dwFlags", wintypes.DWORD),
		("dwAutoDetectFlags", wintypes.DWORD),
		("lpszAutoConfigUrl", ctypes.c_wchar_p),
		("lpvReserved", ctypes.c_void_p),
		("dwReserved", wintypes.DWORD),
		("fAutoLogonIfChallenged", wintypes.BOOL),
	]


class _ProxyInfo(ctypes.Structure):
	_fields_ = [
		("dwAccessType", wintypes.DWORD),
		("lpszProxy", ctypes.c_void_p),
		("lpszProxyBypass", ctypes.c_void_p),
	]


def _read_string(pointer):
	return ctypes.wstring_at(pointer) if pointer else ""


def _free_string(kernel32, pointer):
	if pointer:
		kernel32.GlobalFree(pointer)


def _load_api():
	if os.name != "nt":
		return None
	try:
		winhttp = ctypes.WinDLL("winhttp", use_last_error=True)
		kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
	except OSError:
		return None

	winhttp.WinHttpGetIEProxyConfigForCurrentUser.argtypes = [
		ctypes.POINTER(_CurrentUserIEProxyConfig),
	]
	winhttp.WinHttpGetIEProxyConfigForCurrentUser.restype = wintypes.BOOL
	winhttp.WinHttpOpen.argtypes = [
		ctypes.c_wchar_p,
		wintypes.DWORD,
		ctypes.c_wchar_p,
		ctypes.c_wchar_p,
		wintypes.DWORD,
	]
	winhttp.WinHttpOpen.restype = ctypes.c_void_p
	winhttp.WinHttpSetTimeouts.argtypes = [
		ctypes.c_void_p,
		ctypes.c_int,
		ctypes.c_int,
		ctypes.c_int,
		ctypes.c_int,
	]
	winhttp.WinHttpSetTimeouts.restype = wintypes.BOOL
	winhttp.WinHttpGetProxyForUrl.argtypes = [
		ctypes.c_void_p,
		ctypes.c_wchar_p,
		ctypes.POINTER(_AutoProxyOptions),
		ctypes.POINTER(_ProxyInfo),
	]
	winhttp.WinHttpGetProxyForUrl.restype = wintypes.BOOL
	winhttp.WinHttpCloseHandle.argtypes = [ctypes.c_void_p]
	winhttp.WinHttpCloseHandle.restype = wintypes.BOOL
	kernel32.GlobalFree.argtypes = [ctypes.c_void_p]
	kernel32.GlobalFree.restype = ctypes.c_void_p
	return winhttp, kernel32


def _is_bypassed(host, bypass_list):
	if not bypass_list:
		return False
	host = host.lower().rstrip(".")
	for raw_pattern in bypass_list.replace(";", " ").split():
		pattern = raw_pattern.strip().lower()
		if not pattern:
			continue
		if pattern == "<local>" and "." not in host:
			return True
		if fnmatch.fnmatchcase(host, pattern):
			return True
	return False


def _to_winhttp_url(url):
	"""Map the WebSocket schemes to HTTP ones.

	WinHttpGetProxyForUrl only understands http and https; it fails with
	ERROR_WINHTTP_UNRECOGNIZED_SCHEME for a ``wss`` URL, which would silently
	disable proxy detection for WebSocket connections.
	"""
	parts = urlsplit(url)
	scheme = parts.scheme.lower()
	if scheme == "wss":
		scheme = "https"
	elif scheme == "ws":
		scheme = "http"
	return urlunsplit((scheme, parts.netloc, parts.path or "/", parts.query, ""))


def _parse_proxy_endpoint(value, scheme):
	value = value.strip()
	if not value:
		return None
	is_socks = scheme.lower() == "socks"
	parsed = urlsplit(value if "://" in value else f"//{value}")
	try:
		host = parsed.hostname
		port = parsed.port or (1080 if is_socks else 8080)
	except ValueError:
		return None
	if not host:
		return None
	# A plain HTTP CONNECT tunnel is attempted first; the transport falls back to
	# Windows integrated authentication only when the proxy answers 407.
	proxy_type = "socks5" if is_socks else "http"
	return DetectedProxy(host=host, port=port, type=proxy_type)


def _parse_proxy_list(proxy_text, url):
	preferred_scheme = urlsplit(url).scheme.lower()
	if preferred_scheme == "wss":
		preferred_scheme = "https"
	elif preferred_scheme == "ws":
		preferred_scheme = "http"
	specs = {}
	for item in proxy_text.split(";"):
		item = item.strip()
		if not item:
			continue
		if "=" in item:
			scheme, value = item.split("=", 1)
			specs[scheme.strip().lower()] = value.strip()
		else:
			specs.setdefault("default", item)
	for scheme in (preferred_scheme, "https", "http", "default", "socks"):
		if scheme in specs:
			proxy = _parse_proxy_endpoint(specs[scheme], scheme)
			if proxy:
				return proxy
	return None


def _query_automatic_proxy(winhttp, kernel32, session, url, config):
	flags = 0
	auto_detect_flags = 0
	if config.fAutoDetect:
		flags |= _WINHTTP_AUTO_PROXY_FLAG_AUTO_DETECT
		auto_detect_flags = _WINHTTP_AUTO_DETECT_TYPE_DHCP | _WINHTTP_AUTO_DETECT_TYPE_DNS_A
	auto_config_url = _read_string(config.lpszAutoConfigUrl)
	if auto_config_url:
		flags |= _WINHTTP_AUTO_PROXY_FLAG_CONFIG_URL
	if not flags:
		return None

	options = _AutoProxyOptions(
		dwFlags=flags,
		dwAutoDetectFlags=auto_detect_flags,
		lpszAutoConfigUrl=auto_config_url or None,
		fAutoLogonIfChallenged=True,
	)
	proxy_info = _ProxyInfo()
	if not winhttp.WinHttpGetProxyForUrl(session, url, ctypes.byref(options), ctypes.byref(proxy_info)):
		return None
	try:
		if proxy_info.dwAccessType == _WINHTTP_ACCESS_TYPE_NO_PROXY:
			return DetectedProxy()
		return _parse_proxy_list(_read_string(proxy_info.lpszProxy), url) or DetectedProxy()
	finally:
		_free_string(kernel32, proxy_info.lpszProxy)
		_free_string(kernel32, proxy_info.lpszProxyBypass)


def detect_proxy(url):
	"""Return the Windows-selected proxy, direct mode, or None if unavailable."""
	api = _load_api()
	if api is None:
		return None
	url = _to_winhttp_url(url)
	winhttp, kernel32 = api
	winhttp._kernel32 = kernel32
	config = _CurrentUserIEProxyConfig()
	if not winhttp.WinHttpGetIEProxyConfigForCurrentUser(ctypes.byref(config)):
		return None

	session = winhttp.WinHttpOpen("TeleNVDA proxy detection", _WINHTTP_ACCESS_TYPE_NO_PROXY, None, None, 0)
	if not session:
		_free_string(kernel32, config.lpszAutoConfigUrl)
		_free_string(kernel32, config.lpszProxy)
		_free_string(kernel32, config.lpszProxyBypass)
		return None
	try:
		winhttp.WinHttpSetTimeouts(session, 5000, 5000, 5000, 5000)
		auto_flags_present = bool(config.fAutoDetect or config.lpszAutoConfigUrl)
		if auto_flags_present:
			automatic = _query_automatic_proxy(winhttp, kernel32, session, url, config)
			if automatic is not None:
				return automatic

		proxy_text = _read_string(config.lpszProxy)
		host = urlsplit(url).hostname or ""
		bypass = _read_string(config.lpszProxyBypass)
		if proxy_text and not _is_bypassed(host, bypass):
			return _parse_proxy_list(proxy_text, url)
		if not auto_flags_present:
			return DetectedProxy()
		return None
	finally:
		winhttp.WinHttpCloseHandle(session)
		_free_string(kernel32, config.lpszAutoConfigUrl)
		_free_string(kernel32, config.lpszProxy)
		_free_string(kernel32, config.lpszProxyBypass)
