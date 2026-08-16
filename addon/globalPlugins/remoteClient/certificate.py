"""Create and validate the private certificate used by the direct server.

The key is deliberately generated in the NVDA profile, never shipped with the
add-on.  The small X.509 encoder keeps the add-on self-contained and uses the
PyCryptodome copy bundled with TeleNVDA.
"""

import base64
import datetime
import os
import ssl
import sys
import tempfile

import buildVersion
import globalVars

sys.path.append(os.path.join(os.path.abspath(os.path.dirname(__file__)), "lib64" if buildVersion.version_year >= 2026 else "lib32"))
from Cryptodome.Hash import SHA256
from Cryptodome.PublicKey import RSA
from Cryptodome.Signature import pkcs1_15
sys.path.remove(sys.path[-1])


CERTIFICATE_FILENAME = "teleNVDA-server.pem"
_RSA_SHA256 = bytes.fromhex("300d06092a864886f70d01010b0500")
_RSA_ENCRYPTION = bytes.fromhex("300d06092a864886f70d0101010500")


def _length(length):
	if length < 128:
		return bytes([length])
	data = length.to_bytes((length.bit_length() + 7) // 8, "big")
	return bytes([0x80 | len(data)]) + data


def _der(tag, value):
	return bytes([tag]) + _length(len(value)) + value


def _integer(value):
	if isinstance(value, int):
		value = value.to_bytes(max(1, (value.bit_length() + 7) // 8), "big")
	value = value.lstrip(b"\0") or b"\0"
	if value[0] & 0x80:
		value = b"\0" + value
	return _der(0x02, value)


def _oid(parts):
	encoded = bytes([40 * parts[0] + parts[1]])
	for part in parts[2:]:
		chunks = [part & 0x7F]
		part >>= 7
		while part:
			chunks.append(0x80 | (part & 0x7F))
			part >>= 7
		encoded += bytes(reversed(chunks))
	return _der(0x06, encoded)


def _name(common_name):
	attribute = _der(0x30, _oid((2, 5, 4, 3)) + _der(0x0C, common_name.encode("utf-8")))
	return _der(0x30, _der(0x31, attribute))


def _utc_time(value):
	return _der(0x17, value.strftime("%y%m%d%H%M%SZ").encode("ascii"))


def _certificate_der(key):
	now = datetime.datetime.now(datetime.timezone.utc)
	valid_from = now - datetime.timedelta(minutes=5)
	valid_to = now + datetime.timedelta(days=3650)
	public_key = _der(0x30, _integer(key.n) + _integer(key.e))
	public_key_info = _der(0x30, _RSA_ENCRYPTION + _der(0x03, b"\0" + public_key))
	name = _name("TeleNVDA direct server")
	tbs = _der(
		0x30,
		_der(0xA0, _integer(2))
		+ _integer(int.from_bytes(os.urandom(16), "big"))
		+ _RSA_SHA256
		+ name
		+ _der(0x30, _utc_time(valid_from) + _utc_time(valid_to))
		+ name
		+ public_key_info,
	)
	signature = pkcs1_15.new(key).sign(SHA256.new(tbs))
	return _der(0x30, tbs + _RSA_SHA256 + _der(0x03, b"\0" + signature))


def _pem(label, data):
	encoded = base64.b64encode(data).decode("ascii")
	lines = [encoded[index:index + 64] for index in range(0, len(encoded), 64)]
	return f"-----BEGIN {label}-----\n" + "\n".join(lines) + f"\n-----END {label}-----\n"


def certificate_path():
	return os.path.join(os.path.abspath(globalVars.appArgs.configPath), CERTIFICATE_FILENAME)


def _is_valid(path):
	try:
		context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
		context.load_cert_chain(path, path)
		return True
	except (OSError, ssl.SSLError):
		return False


def ensure_certificate():
	"""Return a usable local certificate/key PEM path, creating it if needed."""
	path = certificate_path()
	os.makedirs(os.path.dirname(path), exist_ok=True)
	if _is_valid(path):
		return path
	key = RSA.generate(2048)
	content = _pem("RSA PRIVATE KEY", key.export_key(format="DER")) + _pem("CERTIFICATE", _certificate_der(key))
	fd, temporary_path = tempfile.mkstemp(prefix="teleNVDA-", suffix=".pem", dir=os.path.dirname(path))
	try:
		with os.fdopen(fd, "w", encoding="ascii", newline="\n") as stream:
			stream.write(content)
		os.replace(temporary_path, path)
	except Exception:
		try:
			os.unlink(temporary_path)
		except OSError:
			pass
		raise
	return path
