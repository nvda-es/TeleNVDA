"""Check and install TeleNVDA updates published on GitHub."""

from __future__ import annotations

import hashlib
import hmac
import http.client
import json
import logging
import os
import re
import shutil
import socket
import ssl
import sys
import tempfile
import threading
from dataclasses import dataclass
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.parse import quote, urljoin, urlsplit
from urllib.request import HTTPRedirectHandler, ProxyHandler, Request, build_opener

import addonHandler
import buildVersion

from . import configuration, proxy_utils, sspi_proxy

log = logging.getLogger("TeleNVDA.updater")

REPOSITORY = "accessolutions/telenvda-accessolutions"
ADDON_NAME = "TeleNVDA"
RELEASES_URL = f"https://api.github.com/repos/{REPOSITORY}/releases?per_page=100"
_USER_AGENT = "TeleNVDA updater"
_NETWORK_TIMEOUT = 30
_MAX_RESPONSE_SIZE = 16 * 1024 * 1024
_MAX_REDIRECTS = 5
_VERSION_PATTERN = re.compile(r"^(?:\d{8}(?:\.\d+)*|\d{4}(?:\.\d+){1,5})$")
_ASSET_VERSION_PATTERN = re.compile(
	r"^telenvda-(?P<version>(?:\d{8}(?:\.\d+)*|\d{4}(?:\.\d+){1,5}))\.nvda-addon$",
	re.IGNORECASE,
)


@dataclass(frozen=True)
class UpdateInfo:
	version: str
	release_name: str
	release_url: str
	asset_name: str
	asset_url: str
	sha256: str
	sha256_url: str | None
	notes: str


class UpdateError(Exception):
	"""Raised when an update cannot be checked, downloaded or validated."""


def _version_key(version: str) -> tuple[int, ...]:
	"""Return a comparable key for TeleNVDA's date-based versions.

	Versions historically appeared as YYYYMMDD.0.0 and
	YYYY.M.DD.MMDD.HHMM. New builds use YYYY.MM.DD.HHMM. All forms are
	normalized to (year, month, day, build...).
	"""
	text = str(version or "").strip().lstrip("vV")
	parts = re.findall(r"\d+", text)
	if not parts:
		return (0,)
	numbers = [int(part) for part in parts]
	if len(parts[0]) == 8 and 1900 <= numbers[0] <= 99999999:
		date_part = parts[0]
		return (int(date_part[:4]), int(date_part[4:6]), int(date_part[6:8]), *numbers[1:])
	if len(parts) >= 5 and len(parts[0]) == 4 and numbers[0] >= 1900:
		month_day = f"{numbers[3]:04d}"
		if len(month_day) == 4 and 1 <= int(month_day[:2]) <= 12 and 1 <= int(month_day[2:]) <= 31:
			return (numbers[0], int(month_day[:2]), int(month_day[2:]), *numbers[4:])
	if len(parts) >= 3 and len(parts[0]) == 4 and numbers[0] >= 1900:
		return tuple(numbers)
	return tuple(numbers)


def is_newer_version(candidate: str, current: str) -> bool:
	return _version_key(candidate) > _version_key(current)


def _addons_dir() -> str | None:
	"""Return the directory that contains installed add-ons, if known."""
	try:
		addon = addonHandler.getCodeAddon()
	except (addonHandler.AddonError, AttributeError):
		return None
	return os.path.dirname(os.path.normpath(addon.path))


def pending_install_path() -> str | None:
	"""Return the path NVDA uses to stage a not-yet-completed TeleNVDA update."""
	addons_dir = _addons_dir()
	if not addons_dir:
		return None
	return os.path.join(addons_dir, ADDON_NAME + ".pendingInstall")


def has_pending_install() -> bool:
	"""Return whether a previous TeleNVDA update failed to complete.

	When NVDA cannot finish moving a downloaded update into place (for
	example because antivirus software is briefly locking the newly
	written files), the ``.pendingInstall`` staging folder is left behind
	and the installed add-on version never advances. Without this check,
	the updater would keep finding the same "newer" release and asking to
	download and install it again on every startup.
	"""
	path = pending_install_path()
	return bool(path) and os.path.isdir(path)


def _installed_addon():
	"""Return the currently installed TeleNVDA add-on, if available."""
	try:
		return addonHandler.getCodeAddon()
	except (addonHandler.AddonError, AttributeError):
		return None


def request_remove_installed_addon() -> bool:
	"""Mark the currently installed TeleNVDA add-on for removal on restart.

	NVDA's :func:`addonHandler.installAddonBundle` only stages the new
	version inside a ``.pendingInstall`` folder; it never removes the
	previous version. When an update is installed through the add-on store
	GUI, NVDA calls ``requestRemove()`` on the old version first, so its
	folder is deleted on restart and ``completeInstall`` can rename the
	staged folder into place. Installing a bundle directly (as this updater
	does) skips that step, leaving the old ``TeleNVDA`` folder behind. On
	the next start ``completeInstall`` then fails with a "permission denied"
	error because it cannot replace the still-present folder.

	Requesting the removal here reproduces the add-on store's behaviour.

	:return: ``True`` if a removal was newly requested, ``False`` if it was
		already pending or could not be performed.
	"""
	addon = _installed_addon()
	if addon is None:
		return False
	try:
		if getattr(addon, "isPendingRemove", False):
			return False
		addon.requestRemove()
		return True
	except Exception:
		log.error("Failed to mark the installed TeleNVDA add-on for removal", exc_info=True)
		return False


def _remove_stale_pending_install() -> None:
	"""Delete a leftover ``.pendingInstall`` folder before staging an update.

	A previous, unfinished update may have left a partial staging folder
	behind. Extracting a new bundle on top of it would merge the two file
	sets, so remove it first to guarantee a clean install.
	"""
	path = pending_install_path()
	if path and os.path.isdir(path):
		log.debug("Removing stale pending install folder %s", path)
		shutil.rmtree(path, ignore_errors=True)


def recover_pending_install() -> bool:
	"""Try to unblock a TeleNVDA update NVDA could not finalize.

	When a ``.pendingInstall`` folder is present but the previously
	installed version was never marked for removal, NVDA keeps failing to
	complete the installation on every restart. Marking the installed
	version for removal lets NVDA delete it and finish the pending update
	on the next restart.

	:return: ``True`` if a restart is required to finish the pending update.
	"""
	if not has_pending_install():
		return False
	addon = _installed_addon()
	if addon is None:
		return False
	if getattr(addon, "isPendingRemove", False):
		# Already scheduled for removal; a plain restart will finish it.
		return True
	return request_remove_installed_addon()


def _parse_sha256(data: bytes | str) -> str | None:
	text = data.decode("ascii", errors="ignore") if isinstance(data, bytes) else data
	match = re.search(r"(?i)\b([0-9a-f]{64})\b", text)
	return match.group(1).lower() if match else None


def _proxy_url(settings: proxy_utils.ProxySettings) -> str:
	credentials = ""
	if settings.username:
		credentials = quote(settings.username, safe="")
		if settings.password:
			credentials += ":" + quote(settings.password, safe="")
		credentials += "@"
	return f"http://{credentials}{settings.host}:{int(settings.port)}"


def _fetch_with_urllib(url: str, settings: proxy_utils.ProxySettings) -> bytes:
	if urlsplit(url).scheme.lower() != "https":
		raise UpdateError(f"Only HTTPS URLs are accepted: {url}")

	class HttpsRedirectHandler(HTTPRedirectHandler):
		def redirect_request(self, request, file, code, message, headers, new_url):
			if urlsplit(new_url).scheme.lower() != "https":
				raise UpdateError(f"Only HTTPS redirects are accepted: {new_url}")
			return super().redirect_request(request, file, code, message, headers, new_url)

	handlers = []
	if settings.enabled:
		if settings.type != "http":
			raise UpdateError(f"Unsupported urllib proxy type: {settings.type}")
		proxy = _proxy_url(settings)
		handlers.append(ProxyHandler({"http": proxy, "https": proxy}))
	elif settings.use_environment:
		handlers.append(ProxyHandler())
	else:
		handlers.append(ProxyHandler({}))
	handlers.append(HttpsRedirectHandler())
	opener = build_opener(*handlers)
	request = Request(
		url,
		headers={
			"Accept": "application/vnd.github+json",
			"User-Agent": _USER_AGENT,
		},
	)
	try:
		with opener.open(request, timeout=_NETWORK_TIMEOUT) as response:
			data = response.read(_MAX_RESPONSE_SIZE + 1)
	except (HTTPError, URLError, OSError) as error:
		raise UpdateError(f"Unable to retrieve {url}: {error}") from error
	if len(data) > _MAX_RESPONSE_SIZE:
		raise UpdateError(f"The response from {url} is too large")
	return data


def _python_socks_proxy(settings: proxy_utils.ProxySettings):
	library_dir = Path(__file__).parent / ("lib64" if buildVersion.version_year >= 2026 else "lib32")
	library_dir_text = str(library_dir)
	if library_dir_text not in sys.path:
		sys.path.insert(0, library_dir_text)
	try:
		from python_socks._types import ProxyType
		from python_socks.sync import Proxy
	finally:
		if sys.path and sys.path[0] == library_dir_text:
			sys.path.pop(0)
	proxy_type = {
		"socks4": ProxyType.SOCKS4,
		"socks4a": ProxyType.SOCKS4,
		"socks5": ProxyType.SOCKS5,
		"socks5h": ProxyType.SOCKS5,
	}[settings.type]
	return Proxy.create(
		proxy_type=proxy_type,
		host=settings.host,
		port=int(settings.port),
		username=settings.username or None,
		password=settings.password or None,
		rdns=settings.type in ("socks4a", "socks5h"),
	)


def _open_socket(settings: proxy_utils.ProxySettings, host: str, port: int) -> socket.socket:
	if not settings.enabled:
		return socket.create_connection((host, port), timeout=_NETWORK_TIMEOUT)
	if settings.type in ("socks4", "socks4a", "socks5", "socks5h"):
		return _python_socks_proxy(settings).connect(host, port, timeout=_NETWORK_TIMEOUT)
	if settings.type in ("negotiate", "ntlm"):
		return sspi_proxy.open_sspi_proxy_tunnel(settings, host, port, timeout=_NETWORK_TIMEOUT)
	raise UpdateError(f"Unsupported proxy type: {settings.type}")


def _fetch_over_socket(url: str, settings: proxy_utils.ProxySettings) -> tuple[int, dict[str, str], bytes]:
	parsed = urlsplit(url)
	if parsed.scheme.lower() != "https" or not parsed.hostname:
		raise UpdateError(f"Only HTTPS URLs are accepted: {url}")
	host = parsed.hostname
	port = parsed.port or 443
	path = parsed.path or "/"
	if parsed.query:
		path += "?" + parsed.query
	sock = _open_socket(settings, host, port)
	connection = None
	try:
		context = ssl.create_default_context()
		tls_socket = context.wrap_socket(sock, server_hostname=host)
		connection = http.client.HTTPSConnection(host, port, timeout=_NETWORK_TIMEOUT, context=context)
		connection.sock = tls_socket
		connection.request(
			"GET",
			path,
			headers={
				"Accept": "application/vnd.github+json",
				"User-Agent": _USER_AGENT,
			},
		)
		response = connection.getresponse()
		data = response.read(_MAX_RESPONSE_SIZE + 1)
		headers = {key.lower(): value for key, value in response.getheaders()}
		if len(data) > _MAX_RESPONSE_SIZE:
			raise UpdateError(f"The response from {url} is too large")
		return response.status, headers, data
	except (OSError, HTTPError, ssl.SSLError) as error:
		raise UpdateError(f"Unable to retrieve {url}: {error}") from error
	finally:
		if connection is not None:
			connection.close()
		else:
			sock.close()


def _fetch_bytes(url: str, settings: proxy_utils.ProxySettings) -> bytes:
	"""Fetch an HTTPS resource, including GitHub's asset redirects."""
	for _ in range(_MAX_REDIRECTS + 1):
		resolved_settings = proxy_utils.resolve_for_url(settings, url)
		if not resolved_settings.enabled or resolved_settings.type == "http":
			return _fetch_with_urllib(url, resolved_settings)
		status, headers, data = _fetch_over_socket(url, resolved_settings)
		if status in (301, 302, 303, 307, 308):
			location = headers.get("location")
			if not location:
				raise UpdateError(f"Redirect without a destination for {url}")
			url = urljoin(url, location)
			continue
		if status < 200 or status >= 300:
			raise UpdateError(f"GitHub returned HTTP status {status} for {url}")
		return data
	raise UpdateError(f"Too many redirects while retrieving {url}")


def _release_version(release: dict, addon_name: str = "") -> str:
	for value in (release.get("tag_name"), release.get("name")):
		version = str(value or "").strip().lstrip("vV")
		if _VERSION_PATTERN.fullmatch(version):
			return version
	match = _ASSET_VERSION_PATTERN.fullmatch(addon_name.strip())
	return match.group("version") if match else ""


def _find_asset(release: dict) -> tuple[dict, dict | None]:
	assets = [asset for asset in release.get("assets") or [] if isinstance(asset, dict)]
	addon_assets = [
		asset for asset in assets
		if str(asset.get("name", "")).lower().endswith(".nvda-addon")
		and str(asset.get("browser_download_url", "")).startswith("https://")
	]
	if not addon_assets:
		raise UpdateError("The GitHub release has no NVDA add-on asset")
	addon_asset = next(
		(asset for asset in addon_assets if str(asset.get("name", "")).lower().startswith("telenvda")),
		addon_assets[0],
	)
	addon_name = str(addon_asset.get("name", ""))
	hash_asset = next(
		(
			asset for asset in assets
			if str(asset.get("name", "")).lower() in (addon_name.lower() + ".sha256", addon_name.lower() + ".sha256.txt")
			and str(asset.get("browser_download_url", "")).startswith("https://")
		),
		None,
	)
	return addon_asset, hash_asset


def check_for_update(current_version: str) -> UpdateInfo | None:
	"""Return the newest verified stable release."""
	settings = proxy_utils.from_config(configuration.get_config())
	try:
		releases_data = _fetch_bytes(RELEASES_URL, settings)
		releases = json.loads(releases_data.decode("utf-8"))
	except (ValueError, UnicodeDecodeError) as error:
		raise UpdateError(f"Invalid GitHub releases response: {error}") from error
	if not isinstance(releases, list):
		raise UpdateError("GitHub returned an invalid releases list")

	candidates = []
	for release in releases:
		if not isinstance(release, dict):
			continue
		if release.get("draft") or release.get("prerelease"):
			continue
		try:
			addon_asset, hash_asset = _find_asset(release)
		except UpdateError:
			continue
		version = _release_version(release, str(addon_asset.get("name", "")))
		if version and is_newer_version(version, current_version):
			candidates.append((
				_version_key(version),
				release,
				version,
				addon_asset,
				hash_asset,
			))
	for _, release, version, addon_asset, hash_asset in sorted(candidates, key=lambda item: item[0], reverse=True):
		try:
			hash_url = hash_asset.get("browser_download_url") if hash_asset else None
			sha256 = None
			if hash_url:
				sha256 = _parse_sha256(_fetch_bytes(hash_url, settings))
			if not sha256:
				sha256 = _parse_sha256(release.get("body", ""))
			if not sha256:
				raise UpdateError("The release does not publish a SHA-256 hash")
			return UpdateInfo(
				version=version,
				release_name=str(release.get("name") or version),
				release_url=str(release.get("html_url") or ""),
				asset_name=str(addon_asset.get("name")),
				asset_url=str(addon_asset.get("browser_download_url")),
				sha256=sha256,
				sha256_url=hash_url,
				notes=str(release.get("body") or ""),
			)
		except UpdateError:
			log.warning("Ignoring unusable GitHub release %s", version, exc_info=True)
	return None


def download_update(update: UpdateInfo) -> str:
	settings = proxy_utils.from_config(configuration.get_config())
	data = _fetch_bytes(update.asset_url, settings)
	digest = hashlib.sha256(data).hexdigest()
	if not hmac.compare_digest(digest.lower(), update.sha256.lower()):
		raise UpdateError("The downloaded add-on failed SHA-256 verification")
	fd, path = tempfile.mkstemp(prefix="TeleNVDA-", suffix=".nvda-addon")
	try:
		with os.fdopen(fd, "wb") as output:
			output.write(data)
	except Exception:
		try:
			os.unlink(path)
		except OSError:
			pass
		raise
	return path


def install_package(path: str):
	"""Install a validated package using the NVDA add-on API."""
	bundle_type = getattr(addonHandler, "AddonBundle", None)
	installer = getattr(addonHandler, "installAddonBundle", None)
	if bundle_type is not None and installer is not None:
		bundle = bundle_type(path)
		if bundle.manifest.get("name") != ADDON_NAME:
			raise UpdateError("The downloaded package is not a TeleNVDA add-on")
		# Mirror the add-on store: remove any leftover staging folder and
		# mark the currently installed version for removal so NVDA can
		# replace it with the staged update on restart instead of failing
		# with a "permission denied" error.
		_remove_stale_pending_install()
		request_remove_installed_addon()
		if installer(bundle) is None:
			raise UpdateError("NVDA could not install the add-on package")
		return
	legacy_installer = getattr(addonHandler, "installAddonPackage", None)
	if legacy_installer is not None:
		legacy_installer(path)
		return
	raise UpdateError("This version of NVDA does not expose an add-on installation API")


class UpdateManager:
	"""Run network operations away from NVDA's main thread."""

	def __init__(self):
		self._lock = threading.Lock()
		self._workers: set[threading.Thread] = set()
		self._stopped = threading.Event()

	def _start(self, target, callback) -> bool:
		with self._lock:
			if self._stopped.is_set():
				return False
			self._workers = {worker for worker in self._workers if worker.is_alive()}
			if self._workers:
				return False
			worker = threading.Thread(target=target, args=(callback,), name="TeleNVDA updater", daemon=True)
			self._workers.add(worker)
		worker.start()
		return True

	def check_async(self, current_version: str, callback, manual: bool = False) -> bool:
		def run(done):
			try:
				result = check_for_update(current_version)
			except Exception as error:
				log.debug("Update check failed", exc_info=True)
				with self._lock:
					self._workers.discard(threading.current_thread())
				done(None, error, manual)
			else:
				with self._lock:
					self._workers.discard(threading.current_thread())
				done(result, None, manual)
		return self._start(run, callback)

	def download_async(self, update: UpdateInfo, callback) -> bool:
		def run(done):
			try:
				path = download_update(update)
			except Exception as error:
				log.debug("Update download failed", exc_info=True)
				with self._lock:
					self._workers.discard(threading.current_thread())
				done(None, error)
			else:
				with self._lock:
					self._workers.discard(threading.current_thread())
				done(path, None)
		return self._start(run, callback)

	def terminate(self):
		self._stopped.set()
