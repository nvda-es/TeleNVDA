"""Proxy configuration helpers shared by relay and diagnostics."""

from dataclasses import dataclass, replace
from urllib.parse import urlsplit


PROXY_MODES = ("manual", "auto", "none")


SUPPORTED_PROXY_TYPES = ("http", "socks4", "socks4a", "socks5", "socks5h", "negotiate", "ntlm")


@dataclass(frozen=True)
class ProxySettings:
	host: str = ""
	port: int = 0
	type: str = "http"
	username: str = ""
	password: str = ""
	auto_detect: bool = False
	use_environment: bool = True

	@property
	def enabled(self):
		return bool(self.host and self.port)


def from_config(config):
	section = config.get("controlserver", config)
	mode = str(section.get("proxy_mode", "manual")).lower()
	if mode == "auto":
		return ProxySettings(auto_detect=True)
	if mode == "none":
		return ProxySettings(use_environment=False)
	return ProxySettings(
		host=section.get("proxy_host", ""),
		port=int(section.get("proxy_port", 0) or 0),
		type=str(section.get("proxy_type", "http")).lower(),
		username=section.get("proxy_username", ""),
		password=section.get("proxy_password", ""),
	)


def from_system(url):
	"""Read the proxy declared by Windows Internet settings or the environment.

	This is the fallback used when WinHTTP cannot resolve an automatic
	configuration. ``urllib`` reads the Windows registry as well as the
	``http_proxy`` / ``https_proxy`` variables, so a machine configured through
	Internet Options keeps working. Returns None when no proxy applies.
	"""
	try:
		from urllib.request import getproxies, proxy_bypass

		proxies = getproxies()
	except Exception:
		return None
	if not proxies:
		return None
	host = urlsplit(url).hostname or ""
	try:
		if host and proxy_bypass(host):
			return None
	except Exception:
		pass
	proxy_url = proxies.get("https") or proxies.get("http") or proxies.get("socks")
	if not proxy_url:
		return None
	parsed = urlsplit(proxy_url if "://" in proxy_url else f"http://{proxy_url}")
	try:
		proxy_host = parsed.hostname
		proxy_port = parsed.port
	except ValueError:
		return None
	if not proxy_host:
		return None
	scheme = (parsed.scheme or "http").lower()
	if scheme.startswith("socks"):
		proxy_type = scheme if scheme in SUPPORTED_PROXY_TYPES else "socks5h"
		default_port = 1080
	else:
		proxy_type = "http"
		default_port = 8080
	return ProxySettings(
		host=proxy_host,
		port=proxy_port or default_port,
		type=proxy_type,
		username=parsed.username or "",
		password=parsed.password or "",
		use_environment=False,
	)


def resolve_for_url(settings, url):
	"""Resolve automatic Windows proxy settings for one destination URL."""
	if not settings.auto_detect:
		return settings
	detected = None
	try:
		from .windows_proxy import detect_proxy

		detected = detect_proxy(url)
	except Exception:
		detected = None
	if detected is not None and detected.enabled:
		return ProxySettings(
			host=detected.host,
			port=detected.port,
			type=detected.type,
			use_environment=False,
		)
	# Windows reported a direct connection, or the detection is unavailable.
	# Fall back to the system-wide proxy so that machines configured only
	# through Internet Options or environment variables still connect.
	fallback = from_system(url)
	if fallback is not None:
		return fallback
	return replace(settings, auto_detect=False)


def uses_sspi(settings):
	"""Return whether the proxy requires the Windows SSPI CONNECT path."""
	return settings.enabled and settings.type in ("negotiate", "ntlm")


def needs_windows_authentication(settings, error):
	"""Return whether a failed CONNECT should be retried with Windows credentials.

	An HTTP proxy that answers 407 wants authentication we did not provide, so
	the SSPI tunnel (NTLM / Kerberos) is worth a try before giving up.
	"""
	if not settings.enabled or settings.type != "http" or settings.username:
		return False
	return "407" in str(error)


def with_windows_authentication(settings):
	"""Return the same proxy, negotiated through Windows integrated auth."""
	return replace(settings, type="negotiate")


def websocket_options(settings):
	"""Return websocket-client options for proxy types it supports natively."""
	if not settings.enabled:
		return {} if settings.use_environment else {"http_no_proxy": ["*"]}
	if settings.type in ("negotiate", "ntlm"):
		return {}
	options = {
		"http_proxy_host": settings.host,
		"http_proxy_port": settings.port,
		"proxy_type": settings.type,
	}
	if settings.username:
		options["http_proxy_auth"] = (settings.username, settings.password)
	return options
