from urllib.parse import (
	urlparse,
	parse_qs,
	urlencode,
)
from . import socket_utils

URL_PREFIX = ["nvdaremote://", "telenvda://"]


class URLParsingError(Exception):
	"""Raised if it's impossible to parse out the URL"""


class ConnectionInfo:
	def __init__(self, hostname, mode, key, port=socket_utils.SERVER_PORT, encryption_key=None):
		self.hostname = hostname
		self.mode = mode
		self.key = key
		self.port = port or socket_utils.SERVER_PORT
		self.encryption_key = encryption_key

	@classmethod
	def from_url(cls, url):
		parsed_url = urlparse(url)
		parsed_query = parse_qs(parsed_url.query)
		hostname = parsed_url.hostname
		port = parsed_url.port
		key = parsed_query.get('key', [""])[0]
		encryption_key = parsed_query.get('encryption_key', [""])[0]
		mode = parsed_query.get('mode', [""])[0].lower()
		if not hostname:
			raise URLParsingError("No hostname provided")
		if not key:
			raise URLParsingError("No key provided")
		if not mode:
			raise URLParsingError("No mode provided")
		if mode not in ("master", "slave"):
			raise URLParsingError("Invalud mode provided: %r" % mode)
		if encryption_key:
			return cls(hostname=hostname, mode=mode, key=key, port=port, encryption_key=encryption_key)
		else:
			return cls(hostname=hostname, mode=mode, key=key, port=port)

	def __repr__(self):
		return "{classname} (hostname={hostname}, port={port}, mode={mode}, key={key}, encryption_key={encryption_key})".format(classname=self.__class__.__name__, hostname=self.hostname, port=self.port, mode=self.mode, key=self.key, encryption_key=self.encryption_key)

	def get_address(self):
		hostname = self.hostname if ":" not in self.hostname else "[" + self.hostname + "]"
		return "{hostname}:{port}".format(hostname=hostname, port=self.port)

	def get_url_to_connect(self, protocol):
		result = URL_PREFIX[protocol] + socket_utils.hostport_to_address((self.hostname, self.port))
		result += "?"
		mode = self.mode
		if mode == 'master':
			mode = 'slave'
		elif mode == 'slave':
			mode = 'master'
		if self.encryption_key:
			result += urlencode(dict(key=self.key, encryption_key=self.encryption_key, mode=mode))
		else:
			result += urlencode(dict(key=self.key, mode=mode))
		return result

	def get_url(self):
		result = URL_PREFIX[1] + socket_utils.hostport_to_address((self.hostname, self.port))
		result += "?"
		mode = self.mode
		if self.encryption_key:
			result += urlencode(dict(key=self.key, encryption_key=self.encryption_key, mode=mode))
		else:
			result += urlencode(dict(key=self.key, mode=mode))
		return result
