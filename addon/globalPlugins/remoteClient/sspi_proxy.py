"""Windows SSPI authentication for HTTP proxy CONNECT tunnels.

The proxy authentication happens before TLS and WebSocket are established.
The implementation uses the Windows security package provider directly via
``secur32.dll`` so that the add-on does not need pywin32 or a Python package
with native dependencies.  The logged-on Windows credentials are used when
the proxy username is empty; explicit credentials are supported as well.
"""

import base64
import ctypes
import os
import re
import socket
from ctypes import wintypes


class SSPIProxyError(Exception):
	"""Raised when Windows cannot complete proxy authentication."""


class _CredHandle(ctypes.Structure):
	_fields_ = [("dwLower", ctypes.c_void_p), ("dwUpper", ctypes.c_void_p)]


class _CtxtHandle(ctypes.Structure):
	_fields_ = [("dwLower", ctypes.c_void_p), ("dwUpper", ctypes.c_void_p)]


class _TimeStamp(ctypes.Structure):
	_fields_ = [("LowPart", wintypes.DWORD), ("HighPart", wintypes.LONG)]


class _SecBuffer(ctypes.Structure):
	_fields_ = [
		("cbBuffer", wintypes.ULONG),
		("BufferType", wintypes.ULONG),
		("pvBuffer", ctypes.c_void_p),
	]


class _SecBufferDesc(ctypes.Structure):
	_fields_ = [
		("ulVersion", wintypes.ULONG),
		("cBuffers", wintypes.ULONG),
		("pBuffers", ctypes.POINTER(_SecBuffer)),
	]


class _SecWinntAuthIdentityW(ctypes.Structure):
	_fields_ = [
		("User", wintypes.LPWSTR),
		("UserLength", wintypes.ULONG),
		("Domain", wintypes.LPWSTR),
		("DomainLength", wintypes.ULONG),
		("Password", wintypes.LPWSTR),
		("PasswordLength", wintypes.ULONG),
		("Flags", wintypes.ULONG),
	]


_SECBUFFER_TOKEN = 2
_SECURITY_NETWORK_DREP = 0
_SECPKG_CRED_OUTBOUND = 2
_SEC_WINNT_AUTH_IDENTITY_UNICODE = 2
_ISC_REQ_CONNECTION = 0x00000800
_ISC_REQ_ALLOCATE_MEMORY = 0x00000100

_SEC_E_OK = 0x00000000
_SEC_I_CONTINUE_NEEDED = 0x00090312
_SEC_I_COMPLETE_NEEDED = 0x00090313
_SEC_I_COMPLETE_AND_CONTINUE = 0x00090314
_SEC_E_INCOMPLETE_MESSAGE = 0x80190318

_MAX_PROXY_HEADER_BYTES = 65536
_MAX_AUTH_ROUNDS = 8


def _status_code(status):
	return ctypes.c_ulong(status).value


def _status_is_error(status):
	return bool(_status_code(status) & 0x80000000)


def _sspi_error(operation, status):
	return SSPIProxyError(f"{operation} failed (SSPI status 0x{_status_code(status):08x})")


def _configure_sspi_api():
	if os.name != "nt":
		raise SSPIProxyError("Windows SSPI is only available on Windows")
	try:
		secur32 = ctypes.WinDLL("secur32", use_last_error=True)
	except OSError as error:
		raise SSPIProxyError("The Windows SSPI library is unavailable") from error

	secur32.AcquireCredentialsHandleW.restype = wintypes.LONG
	secur32.AcquireCredentialsHandleW.argtypes = [
		wintypes.LPWSTR,
		wintypes.LPWSTR,
		wintypes.ULONG,
		ctypes.c_void_p,
		ctypes.c_void_p,
		ctypes.c_void_p,
		ctypes.c_void_p,
		ctypes.POINTER(_CredHandle),
		ctypes.POINTER(_TimeStamp),
	]
	secur32.FreeCredentialsHandle.restype = wintypes.LONG
	secur32.FreeCredentialsHandle.argtypes = [ctypes.POINTER(_CredHandle)]
	secur32.InitializeSecurityContextW.restype = wintypes.LONG
	secur32.InitializeSecurityContextW.argtypes = [
		ctypes.POINTER(_CredHandle),
		ctypes.POINTER(_CtxtHandle),
		wintypes.LPWSTR,
		wintypes.ULONG,
		wintypes.ULONG,
		wintypes.ULONG,
		ctypes.POINTER(_SecBufferDesc),
		wintypes.ULONG,
		ctypes.POINTER(_CtxtHandle),
		ctypes.POINTER(_SecBufferDesc),
		ctypes.POINTER(wintypes.ULONG),
		ctypes.POINTER(_TimeStamp),
	]
	secur32.CompleteAuthToken.restype = wintypes.LONG
	secur32.CompleteAuthToken.argtypes = [
		ctypes.POINTER(_CtxtHandle),
		ctypes.POINTER(_SecBufferDesc),
	]
	secur32.FreeContextBuffer.restype = wintypes.LONG
	secur32.FreeContextBuffer.argtypes = [ctypes.c_void_p]
	secur32.DeleteSecurityContext.restype = wintypes.LONG
	secur32.DeleteSecurityContext.argtypes = [ctypes.POINTER(_CtxtHandle)]
	return secur32


class SSPIProxyAuthenticator:
	"""Stateful NTLM or Negotiate token generator backed by Windows SSPI."""

	def __init__(self, mechanism, username="", password="", target_host=""):
		mechanism = str(mechanism).lower()
		if mechanism not in ("negotiate", "ntlm"):
			raise ValueError(f"Unsupported SSPI mechanism: {mechanism}")
		if not username and password:
			raise SSPIProxyError("A proxy username is required when a proxy password is set")
		self.mechanism = mechanism
		self.complete = False
		self._api = _configure_sspi_api()
		self._credential = _CredHandle()
		self._context = _CtxtHandle()
		self._credential_acquired = False
		self._context_initialized = False
		self._identity = None
		self._identity_buffers = []
		self._target_name = f"HTTP/{target_host.strip('[]')}"
		self._acquire_credentials(username, password)

	def _acquire_credentials(self, username, password):
		auth_data = None
		if username:
			domain = ""
			user = username
			if "\\" in username:
				domain, user = username.split("\\", 1)
			elif "/" in username:
				domain, user = username.split("/", 1)
			user_buffer = ctypes.create_unicode_buffer(user)
			domain_buffer = ctypes.create_unicode_buffer(domain)
			password_buffer = ctypes.create_unicode_buffer(password)
			self._identity_buffers = [user_buffer, domain_buffer, password_buffer]
			self._identity = _SecWinntAuthIdentityW(
				User=ctypes.cast(user_buffer, wintypes.LPWSTR),
				UserLength=len(user),
				Domain=ctypes.cast(domain_buffer, wintypes.LPWSTR),
				DomainLength=len(domain),
				Password=ctypes.cast(password_buffer, wintypes.LPWSTR),
				PasswordLength=len(password),
				Flags=_SEC_WINNT_AUTH_IDENTITY_UNICODE,
			)
			auth_data = ctypes.cast(ctypes.pointer(self._identity), ctypes.c_void_p)

		package = "Negotiate" if self.mechanism == "negotiate" else "NTLM"
		expiry = _TimeStamp()
		status = self._api.AcquireCredentialsHandleW(
			None,
			package,
			_SECPKG_CRED_OUTBOUND,
			None,
			auth_data,
			None,
			None,
			ctypes.byref(self._credential),
			ctypes.byref(expiry),
		)
		if _status_is_error(status):
			raise _sspi_error(f"AcquireCredentialsHandleW ({package})", status)
		self._credential_acquired = True

	def _step(self, challenge):
		if self.complete:
			return None

		input_desc = None
		input_buffer = None
		challenge_buffer = None
		if challenge is not None:
			challenge_buffer = ctypes.create_string_buffer(challenge)
			input_buffer = _SecBuffer(
				cbBuffer=len(challenge),
				BufferType=_SECBUFFER_TOKEN,
				pvBuffer=ctypes.cast(challenge_buffer, ctypes.c_void_p),
			)
			input_desc = _SecBufferDesc(
				ulVersion=0,
				cBuffers=1,
				pBuffers=ctypes.pointer(input_buffer),
			)

		output_buffer = _SecBuffer(cbBuffer=0, BufferType=_SECBUFFER_TOKEN, pvBuffer=None)
		output_desc = _SecBufferDesc(
			ulVersion=0,
			cBuffers=1,
			pBuffers=ctypes.pointer(output_buffer),
		)
		context_attributes = wintypes.ULONG()
		expiry = _TimeStamp()
		new_context = _CtxtHandle()
		status = self._api.InitializeSecurityContextW(
			ctypes.byref(self._credential),
			ctypes.byref(self._context) if self._context_initialized else None,
			self._target_name,
			_ISC_REQ_CONNECTION | _ISC_REQ_ALLOCATE_MEMORY,
			0,
			_SECURITY_NETWORK_DREP,
			ctypes.byref(input_desc) if input_desc is not None else None,
			0,
			ctypes.byref(new_context),
			ctypes.byref(output_desc),
			ctypes.byref(context_attributes),
			ctypes.byref(expiry),
		)
		self._context = new_context
		self._context_initialized = True

		if status in (_SEC_I_COMPLETE_NEEDED, _SEC_I_COMPLETE_AND_CONTINUE):
			complete_status = self._api.CompleteAuthToken(
				ctypes.byref(self._context), ctypes.byref(output_desc)
			)
			if _status_is_error(complete_status):
				raise _sspi_error("CompleteAuthToken", complete_status)

		try:
			if output_buffer.pvBuffer and output_buffer.cbBuffer:
				token = ctypes.string_at(output_buffer.pvBuffer, output_buffer.cbBuffer)
			else:
				token = b""
		finally:
			if output_buffer.pvBuffer:
				free_status = self._api.FreeContextBuffer(output_buffer.pvBuffer)
				if _status_is_error(free_status):
					raise _sspi_error("FreeContextBuffer", free_status)

		if _status_is_error(status):
			if _status_code(status) == _SEC_E_INCOMPLETE_MESSAGE:
				raise SSPIProxyError("The proxy returned an incomplete SSPI challenge")
			raise _sspi_error("InitializeSecurityContextW", status)
		self.complete = status in (_SEC_E_OK, _SEC_I_COMPLETE_NEEDED)
		if not token and not self.complete:
			raise SSPIProxyError("Windows SSPI did not produce a proxy authentication token")
		return token or None

	def initial_token(self):
		return self._step(None)

	def next_token(self, challenge):
		return self._step(challenge)

	def close(self):
		if self._context_initialized:
			self._api.DeleteSecurityContext(ctypes.byref(self._context))
			self._context_initialized = False
		if self._credential_acquired:
			self._api.FreeCredentialsHandle(ctypes.byref(self._credential))
			self._credential_acquired = False


def _proxy_target(host, port):
	if ":" in host and not host.startswith("["):
		host = f"[{host}]"
	return f"{host}:{int(port)}"


def _read_proxy_response(sock):
	data = bytearray()
	while not data.endswith(b"\r\n\r\n"):
		if len(data) >= _MAX_PROXY_HEADER_BYTES:
			raise SSPIProxyError("The proxy response headers are too large")
		chunk = sock.recv(1)
		if not chunk:
			raise SSPIProxyError("The proxy closed the connection during authentication")
		data.extend(chunk)

	lines = bytes(data[:-4]).split(b"\r\n")
	try:
		status_line = lines.pop(0).decode("iso-8859-1")
		status = int(status_line.split(" ", 2)[1])
	except (IndexError, ValueError, UnicodeDecodeError) as error:
		raise SSPIProxyError("The proxy returned an invalid HTTP response") from error

	headers = {}
	for line in lines:
		if b":" not in line:
			continue
		name, value = line.split(b":", 1)
		key = name.decode("iso-8859-1").strip().lower()
		text = value.decode("iso-8859-1").strip()
		if key in headers:
			headers[key] += ", " + text
		else:
			headers[key] = text
	return status, headers


def _select_proxy_challenge(value, mechanism):
	if not value:
		return None, None
	matches = []
	for match in re.finditer(
		r"(?i)(Negotiate|NTLM)(?:\s+([A-Za-z0-9+/]+={0,2}))?", value
	):
		scheme = match.group(1)
		token = match.group(2)
		if token and token.lower() in ("negotiate", "ntlm"):
			token = None
		matches.append((scheme, token))
	preferred = "Negotiate" if mechanism == "negotiate" else "NTLM"
	for scheme, token in matches:
		if scheme.lower() == preferred.lower() and token:
			return scheme, base64.b64decode(token)
	for scheme, token in matches:
		if scheme.lower() == preferred.lower():
			return scheme, None
	if mechanism == "negotiate":
		for scheme, token in matches:
			if scheme.lower() == "ntlm" and token:
				return scheme, base64.b64decode(token)
	return None, None


def open_sspi_proxy_tunnel(proxy, target_host, target_port, timeout=60):
	"""Open an HTTP CONNECT tunnel authenticated with NTLM or Negotiate.

	The returned socket is connected to the target through the proxy but is not
	wrapped in TLS.  The caller owns the socket and must wrap it before sending
	WebSocket traffic.
	"""
	if proxy.type not in ("negotiate", "ntlm"):
		raise ValueError(f"Unsupported SSPI proxy type: {proxy.type}")
	if not proxy.host or not proxy.port:
		raise SSPIProxyError("The SSPI proxy host and port must be configured")

	sock = None
	authenticator = SSPIProxyAuthenticator(
		proxy.type,
		username=proxy.username,
		password=proxy.password,
		target_host=target_host,
	)
	try:
		sock = socket.create_connection((proxy.host, int(proxy.port)), timeout=timeout or None)
		sock.settimeout(timeout or None)
		target = _proxy_target(target_host, target_port)
		scheme = "Negotiate" if proxy.type == "negotiate" else "NTLM"
		token = None
		started = False
		for _ in range(_MAX_AUTH_ROUNDS):
			request = [
				f"CONNECT {target} HTTP/1.1",
				f"Host: {target}",
				"Proxy-Connection: Keep-Alive",
				"Connection: Keep-Alive",
				"User-Agent: TeleNVDA",
			]
			if token:
				request.append(f"Proxy-Authorization: {scheme} {base64.b64encode(token).decode('ascii')}")
			request_bytes = ("\r\n".join(request) + "\r\n\r\n").encode("ascii")
			sock.sendall(request_bytes)
			status, headers = _read_proxy_response(sock)
			if status == 200:
				authenticator.close()
				return sock
			if status != 407:
				raise SSPIProxyError(f"The proxy rejected CONNECT with HTTP status {status}")
			selected_scheme, challenge = _select_proxy_challenge(
				headers.get("proxy-authenticate", ""), proxy.type
			)
			if not selected_scheme:
				raise SSPIProxyError("The proxy did not offer the selected SSPI authentication scheme")
			scheme = selected_scheme
			if challenge is None:
				if started:
					raise SSPIProxyError("The proxy requested another SSPI token without a challenge")
				token = authenticator.initial_token()
				started = True
			else:
				token = authenticator.next_token(challenge)
				started = True
			if not token:
				raise SSPIProxyError("Windows SSPI did not return another proxy authentication token")
		raise SSPIProxyError("The proxy authentication exchange exceeded the maximum number of rounds")
	except Exception:
		if sock is not None:
			sock.close()
		authenticator.close()
		raise
