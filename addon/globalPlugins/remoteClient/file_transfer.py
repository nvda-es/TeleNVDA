"""File transfer between two TeleNVDA clients.

Two formats coexist:

* The legacy ``file_transfer`` message, understood by the original TeleNVDA, which
  carries the whole file Base64 encoded in a single message. The original add-on
  refuses to send more than 10 MB that way.
* A chunked format, only used when the peer announced
  :data:`capabilities.FEATURE_CHUNKED_FILE_TRANSFER`. The file is split into
  acknowledged chunks, so memory use stays bounded, progress can be reported and
  the transfer can be cancelled from either side. Its only practical limits are
  the available disk space and the reliability of the connection.
"""

import base64
import hashlib
import os
import re
import shutil
import tempfile
import threading
import time
import uuid
from logging import getLogger

import globalVars
import gui
import ui
import wx

import addonHandler

from . import capabilities, configuration, cues
from .transport import TransportEvents

logger = getLogger("file_transfer")

try:
	addonHandler.initTranslation()
except addonHandler.AddonError:
	logger.warning("Unable to initialise translations. This may be because the addon is running from NVDA scratchpad.")

#: Largest file the original TeleNVDA accepts to send with the legacy message.
LEGACY_MAX_FILE_SIZE = 10 * 1024 * 1024

#: Amount of raw data carried by a single chunk, before Base64 encoding.
CHUNK_SIZE = 64 * 1024

#: Number of chunks that may travel without having been acknowledged yet.
#: Waiting for every single acknowledgement would limit the throughput to one
#: chunk per round trip, while a small window keeps the memory use bounded.
CHUNK_WINDOW = 8

#: How long the sender waits for the receiver to accept the transfer. The
#: receiver has to choose where the file will be saved, so this is generous.
START_ACK_TIMEOUT = 600.0

#: How long the sender waits for a chunk or completion acknowledgement.
ACK_TIMEOUT = 120.0

#: Progress is announced every time this many percent have been transferred.
ANNOUNCE_STEP = 10

#: Minimum delay, in seconds, between two refreshes of the progress dialog. A chunk
#: is small enough for a fast connection to send hundreds of them per second, which
#: would flood the user interface with useless refreshes.
PROGRESS_REFRESH_INTERVAL = 0.2

_INVALID_NAME_CHARACTERS = re.compile(r'[\\/:*?"<>|\x00-\x1f]')


def sanitize_file_name(name):
	"""Return a safe file name for a name received from another computer.

	The name is only used as the default name of a save dialog, but it must never
	be able to escape the folder chosen by the user.
	"""
	name = (name or "").replace("\\", "/")
	name = name.split("/")[-1]
	name = _INVALID_NAME_CHARACTERS.sub("_", name).strip().strip(".")
	if not name:
		# Translators: Default name used when a received file has no usable name.
		name = _("received file")
	return name[:200]


def format_size(size):
	"""Return a human readable file size."""
	size = float(max(0, size))
	# Translators: Units used to report the size of a file being transferred.
	units = (_("bytes"), _("KB"), _("MB"), _("GB"), _("TB"))
	index = 0
	while size >= 1024 and index < len(units) - 1:
		size /= 1024.0
		index += 1
	precision = 0 if index == 0 else 1
	return "{size:.{precision}f} {unit}".format(size=size, precision=precision, unit=units[index])


def format_speed(bytes_per_second):
	# Translators: Transfer speed, {size} is a formatted file size such as "1.2 MB".
	return _("{size}/s").format(size=format_size(bytes_per_second))


def format_duration(seconds):
	seconds = int(max(0, seconds))
	minutes, seconds = divmod(seconds, 60)
	hours, minutes = divmod(minutes, 60)
	if hours:
		# Translators: Remaining time of a file transfer, in hours and minutes.
		return _("{hours} h {minutes} min").format(hours=hours, minutes=minutes)
	if minutes:
		# Translators: Remaining time of a file transfer, in minutes and seconds.
		return _("{minutes} min {seconds} s").format(minutes=minutes, seconds=seconds)
	# Translators: Remaining time of a file transfer, in seconds.
	return _("{seconds} s").format(seconds=seconds)


class TransferProgressDialog(wx.Dialog):
	"""Modeless dialog reporting the progress of a file transfer."""

	def __init__(self, title, file_name, total_size, on_cancel):
		super().__init__(gui.mainFrame, title=title)
		self._on_cancel = on_cancel
		self._closed = False
		self._last_announced = 0
		self.total_size = total_size
		main_sizer = wx.BoxSizer(wx.VERTICAL)
		main_sizer.Add(wx.StaticText(self, label=file_name), 0, wx.ALL, 10)
		self.gauge = wx.Gauge(self, range=100, size=(320, 25))
		# Translators: Name of the progress bar of the file transfer dialog.
		self.gauge.SetName(_("Progress"))
		main_sizer.Add(self.gauge, 0, wx.ALL | wx.EXPAND, 10)
		self.status = wx.StaticText(self, label="")
		main_sizer.Add(self.status, 0, wx.ALL, 10)
		# Translators: Button which interrupts a file transfer.
		self.cancel_button = wx.Button(self, wx.ID_CANCEL, _("&Cancel"))
		self.cancel_button.Bind(wx.EVT_BUTTON, self._on_cancel_button)
		main_sizer.Add(self.cancel_button, 0, wx.ALL | wx.ALIGN_CENTER, 10)
		self.SetSizerAndFit(main_sizer)
		self.Bind(wx.EVT_CLOSE, self._on_close)
		self.CentreOnScreen()
		self.Show()

	def _on_cancel_button(self, evt):
		self.Close()

	def _on_close(self, evt):
		if not self._closed:
			self._closed = True
			try:
				self._on_cancel()
			except Exception:
				logger.exception("Unable to cancel the transfer")
		self.Destroy()

	def update(self, transferred, elapsed):
		if self._closed or not self:
			return
		percent = int(transferred * 100 / self.total_size) if self.total_size else 100
		percent = min(100, max(0, percent))
		self.gauge.SetValue(percent)
		speed = transferred / elapsed if elapsed > 0 else 0
		if speed > 0 and self.total_size > transferred:
			remaining = format_duration((self.total_size - transferred) / speed)
		else:
			# Translators: Remaining time of a file transfer which cannot be estimated yet.
			remaining = _("unknown")
		self.status.SetLabel(
			# Translators: Status of a file transfer in progress.
			_("{percent}% - {transferred} of {total} - {speed} - {remaining} remaining").format(
				percent=percent,
				transferred=format_size(transferred),
				total=format_size(self.total_size),
				speed=format_speed(speed),
				remaining=remaining,
			)
		)
		if percent >= self._last_announced + ANNOUNCE_STEP and percent < 100:
			self._last_announced = percent - (percent % ANNOUNCE_STEP)
			# Translators: Progress of a file transfer, announced periodically.
			ui.message(_("{percent}%, {transferred} of {total}").format(
				percent=self._last_announced,
				transferred=format_size(transferred),
				total=format_size(self.total_size),
			))

	def finish(self):
		"""Close the dialog without reporting a cancellation."""
		if self._closed or not self:
			return
		self._closed = True
		self.Destroy()


class _OutgoingTransfer:
	"""Send a file to the other computer, one acknowledged chunk at a time."""

	def __init__(self, manager, path, size):
		self.manager = manager
		self.path = path
		self.name = os.path.basename(path)
		self.size = size
		self.id = uuid.uuid4().hex
		self.chunk_size = CHUNK_SIZE
		self._condition = threading.Condition()
		self._acked_index = -1
		self._accepted = None
		self._completed = False
		self._aborted_reason = None
		self._cancelled = False
		self._started_at = 0.0
		self._last_report = 0.0
		self.dialog = None
		self._thread = threading.Thread(target=self._run, name="TeleNVDA file transfer", daemon=True)

	def start(self):
		self.dialog = TransferProgressDialog(
			# Translators: Title of the dialog reporting the progress of a file being sent.
			_("Sending a file"),
			self.name,
			self.size,
			self.cancel,
		)
		self._started_at = time.monotonic()
		self._thread.start()

	def cancel(self):
		with self._condition:
			if self._cancelled or self._completed:
				return
			self._cancelled = True
			self._condition.notify_all()
		self.manager.send(type="file_transfer_abort", id=self.id, reason="cancelled")

	def handle_ack(self, stage=None, index=None, accepted=True, reason=None, **kwargs):
		with self._condition:
			if stage == "start":
				self._accepted = bool(accepted)
				self._aborted_reason = reason
			elif stage == "chunk" and index is not None:
				self._acked_index = max(self._acked_index, int(index))
			elif stage == "complete":
				self._completed = True
			self._condition.notify_all()

	def handle_abort(self, reason=None, **kwargs):
		with self._condition:
			self._cancelled = True
			self._aborted_reason = reason
			self._condition.notify_all()

	def _wait(self, predicate, timeout):
		deadline = time.monotonic() + timeout
		with self._condition:
			while not predicate():
				if self._cancelled:
					return False
				remaining = deadline - time.monotonic()
				if remaining <= 0:
					return False
				self._condition.wait(remaining)
			return not self._cancelled

	def _report_progress(self, transferred):
		dialog = self.dialog
		if dialog is None:
			return
		now = time.monotonic()
		if transferred < self.size and now - self._last_report < PROGRESS_REFRESH_INTERVAL:
			return
		self._last_report = now
		wx.CallAfter(dialog.update, transferred, now - self._started_at)

	def _run(self):
		try:
			self._transfer()
		except Exception:
			logger.exception("Unable to send the file")
			self._finish_with_error(
				# Translators: Message reported when a file could not be sent.
				_("Unable to send the file.")
			)

	def _transfer(self):
		checksum = hashlib.sha256()
		self.manager.send(
			type="file_transfer_start",
			id=self.id,
			name=self.name,
			size=self.size,
			chunk_size=self.chunk_size,
		)
		if not self._wait(lambda: self._accepted is not None, START_ACK_TIMEOUT):
			self._finish_cancelled()
			return
		if not self._accepted:
			self._finish_with_error(
				# Translators: Message reported when the other computer refused a file transfer.
				_("The other computer refused the file transfer.")
			)
			return
		index = 0
		transferred = 0
		with open(self.path, "rb") as stream:
			while True:
				data = stream.read(self.chunk_size)
				if not data:
					break
				if not self._wait(
					lambda: index - self._acked_index <= CHUNK_WINDOW,
					ACK_TIMEOUT,
				):
					self._finish_cancelled()
					return
				checksum.update(data)
				self.manager.send(
					type="file_transfer_chunk",
					id=self.id,
					index=index,
					data=base64.b64encode(data).decode("ascii"),
				)
				index += 1
				transferred += len(data)
				self._report_progress(transferred)
		self.manager.send(
			type="file_transfer_complete",
			id=self.id,
			size=self.size,
			checksum=checksum.hexdigest(),
		)
		if not self._wait(lambda: self._completed, ACK_TIMEOUT):
			self._finish_cancelled()
			return
		self._finish_success()

	def _close_dialog(self):
		dialog = self.dialog
		self.dialog = None
		if dialog is not None:
			wx.CallAfter(dialog.finish)

	def _finish_success(self):
		self._close_dialog()
		self.manager.transfer_finished(self)
		wx.CallAfter(cues.clipboard_pushed)
		# Translators: Message reported when a file has been sent successfully.
		wx.CallAfter(ui.message, _("File sent"))

	def _finish_cancelled(self):
		self._close_dialog()
		self.manager.transfer_finished(self)
		# Translators: Message reported when a file transfer was interrupted.
		wx.CallAfter(ui.message, _("File transfer interrupted"))

	def _finish_with_error(self, message):
		self._close_dialog()
		self.manager.transfer_finished(self)
		wx.CallAfter(ui.message, message)


class _IncomingTransfer:
	"""Receive a file sent by the other computer."""

	def __init__(self, manager, transfer_id, name, size, chunk_size):
		self.manager = manager
		self.id = transfer_id
		self.name = sanitize_file_name(name)
		self.size = size
		self.chunk_size = chunk_size
		self.destination = None
		self.dialog = None
		self._stream = None
		self._temporary_path = None
		self._checksum = hashlib.sha256()
		self._expected_index = 0
		self._received = 0
		self._started_at = time.monotonic()
		self._last_report = 0.0

	def accept(self, destination):
		"""Open the temporary file and tell the sender that it may start."""
		self.destination = destination
		directory = os.path.dirname(destination) or os.getcwd()
		descriptor, self._temporary_path = tempfile.mkstemp(prefix="teleNVDA-", suffix=".part", dir=directory)
		self._stream = os.fdopen(descriptor, "wb")
		self.dialog = TransferProgressDialog(
			# Translators: Title of the dialog reporting the progress of a file being received.
			_("Receiving a file"),
			self.name,
			self.size,
			self.cancel,
		)
		self.manager.send(type="file_transfer_ack", id=self.id, stage="start", accepted=True)

	def refuse(self, reason):
		self.manager.send(type="file_transfer_ack", id=self.id, stage="start", accepted=False, reason=reason)
		self.cleanup()

	def handle_chunk(self, index=None, data=None, **kwargs):
		if self._stream is None:
			return
		if index != self._expected_index:
			logger.error("Received chunk %r while expecting %r", index, self._expected_index)
			self.abort("out_of_order")
			# Translators: Message reported when a received file was corrupted during the transfer.
			ui.message(_("The file transfer failed because data was lost."))
			return
		try:
			decoded = base64.b64decode((data or "").encode("ascii"), validate=True)
		except Exception:
			logger.exception("Unable to decode a received chunk")
			self.abort("invalid_data")
			# Translators: Message reported when a received file was corrupted during the transfer.
			ui.message(_("The file transfer failed because data was lost."))
			return
		self._stream.write(decoded)
		self._checksum.update(decoded)
		self._received += len(decoded)
		self._expected_index += 1
		self._report_progress()
		self.manager.send(type="file_transfer_ack", id=self.id, stage="chunk", index=index)

	def _report_progress(self):
		if self.dialog is None:
			return
		now = time.monotonic()
		if self._received < self.size and now - self._last_report < PROGRESS_REFRESH_INTERVAL:
			return
		self._last_report = now
		self.dialog.update(self._received, now - self._started_at)

	def handle_complete(self, size=None, checksum=None, **kwargs):
		if self._stream is None:
			return
		self._stream.close()
		self._stream = None
		if self._received != size or (checksum and self._checksum.hexdigest() != checksum):
			logger.error("Checksum or size mismatch for the received file")
			self.abort("checksum_mismatch")
			# Translators: Message reported when a received file does not match what was sent.
			ui.message(_("The received file is corrupted and was discarded."))
			return
		try:
			os.replace(self._temporary_path, self.destination)
		except OSError:
			logger.exception("Unable to save the received file")
			self.abort("write_error")
			# Translators: Message reported when a received file could not be written to disk.
			ui.message(_("Unable to save the received file."))
			return
		self._temporary_path = None
		self.manager.send(type="file_transfer_ack", id=self.id, stage="complete")
		self.finish()
		cues.clipboard_received()
		# Translators: Message reported when a file has been received successfully.
		ui.message(_("File received"))

	def cancel(self):
		"""The user closed the progress dialog."""
		self.dialog = None
		self.abort("cancelled")

	def abort(self, reason):
		self.manager.send(type="file_transfer_abort", id=self.id, reason=reason)
		self.cleanup()

	def handle_abort(self, reason=None, **kwargs):
		self.cleanup()
		# Translators: Message reported when the other computer interrupted a file transfer.
		ui.message(_("The file transfer was interrupted by the other computer."))

	def finish(self):
		dialog = self.dialog
		self.dialog = None
		if dialog is not None:
			dialog.finish()
		self.manager.transfer_finished(self)

	def cleanup(self):
		if self._stream is not None:
			try:
				self._stream.close()
			except OSError:
				pass
			self._stream = None
		if self._temporary_path is not None:
			try:
				os.unlink(self._temporary_path)
			except OSError:
				pass
			self._temporary_path = None
		self.finish()


class FileTransferManager:
	"""Send and receive files, choosing the best format supported by the peer."""

	def __init__(self, transport, negotiator):
		self.transport = transport
		self.negotiator = negotiator
		self.outgoing = None
		self.incoming = None
		callbacks = transport.callback_manager
		callbacks.register_callback("msg_file_transfer_start", self.handle_start)
		callbacks.register_callback("msg_file_transfer_chunk", self.handle_chunk)
		callbacks.register_callback("msg_file_transfer_complete", self.handle_complete)
		callbacks.register_callback("msg_file_transfer_ack", self.handle_ack)
		callbacks.register_callback("msg_file_transfer_abort", self.handle_abort)
		callbacks.register_callback(TransportEvents.CLOSING, self.handle_closing)
		callbacks.register_callback(TransportEvents.DISCONNECTED, self.handle_closing)

	def send(self, **kwargs):
		self.transport.send(**kwargs)

	def transfer_finished(self, transfer):
		if self.outgoing is transfer:
			self.outgoing = None
		if self.incoming is transfer:
			self.incoming = None

	def handle_closing(self, *args, **kwargs):
		if self.outgoing is not None:
			self.outgoing.handle_abort(reason="disconnected")
		if self.incoming is not None:
			self.incoming.cleanup()

	def supports_chunked_transfer(self):
		"""Whether the chunked format may be used with the current peers.

		Every chunk is broadcast to the whole channel, so the chunked format is only
		used when a single other computer is connected and announced the feature.
		"""
		return (
			self.negotiator.peer_count == 1
			and self.negotiator.all_peers_support(capabilities.FEATURE_CHUNKED_FILE_TRANSFER)
		)

	def max_send_size(self):
		"""Return the largest file that may be sent to the current peers.

		None means that no limit applies other than the available disk space.
		"""
		if self.supports_chunked_transfer():
			return self.negotiator.negotiated_max_file_size()
		return self.legacy_max_size()

	def legacy_max_size(self):
		"""Return the size limit used with clients which only know the legacy format.

		The 10 MB limit of the original TeleNVDA is enforced by the sender, not by the
		receiver, so a larger file can still be delivered to an original client. This
		is disabled by default because the whole file is held in memory on both sides
		and the session is blocked until the transfer completes.
		"""
		section = configuration.get_config()["file_transfer"]
		if not section["allow_large_legacy_transfers"]:
			return LEGACY_MAX_FILE_SIZE
		return max(LEGACY_MAX_FILE_SIZE, int(section["legacy_max_size_mb"]) * 1024 * 1024)

	def max_receive_size(self):
		"""Return the largest file this client accepts to receive, or None when unlimited."""
		megabytes = int(configuration.get_config()["file_transfer"]["max_received_size_mb"])
		return megabytes * 1024 * 1024 if megabytes > 0 else None

	def send_file(self, path):
		"""Send the given file, using the best format supported by the peer."""
		if globalVars.appArgs.secure:
			return
		if self.outgoing is not None:
			gui.messageBox(
				# Translators: Message shown when a file transfer is already in progress.
				message=_("A file transfer is already in progress. Please wait until it completes."),
				# Translators: Title of an error dialog.
				caption=_("Error"),
				style=wx.ICON_ERROR,
			)
			return
		try:
			size = os.path.getsize(path)
		except OSError:
			logger.exception("Unable to read the specified file")
			gui.messageBox(
				# Translators: Message shown when the file to send could not be read.
				message=_("Unable to read the specified file."),
				caption=_("Error"),
				style=wx.ICON_ERROR,
			)
			return
		if self.supports_chunked_transfer():
			self._send_chunked(path, size)
		else:
			self._send_legacy(path, size)

	def _send_chunked(self, path, size):
		limit = self.negotiator.negotiated_max_file_size()
		if limit and size > limit:
			gui.messageBox(
				# Translators: Message shown when the other computer refuses files of this size.
				message=_("This file is too large. The other computer only accepts files up to {size}.").format(
					size=format_size(limit)
				),
				caption=_("Error"),
				style=wx.ICON_ERROR,
			)
			return
		configuration.record_activity()
		self.outgoing = _OutgoingTransfer(self, path, size)
		self.outgoing.start()

	def _send_legacy(self, path, size):
		limit = self.legacy_max_size()
		if size > limit:
			gui.messageBox(
				# Translators: Message shown when a file is too large for the legacy transfer.
				message=_("This file is too large. The other computer only supports transfers up to {size}.").format(
					size=format_size(limit)
				),
				caption=_("Error"),
				style=wx.ICON_ERROR,
			)
			return
		if gui.messageBox(
			# Translators: Question asked before starting a transfer which blocks the session.
			message=_("The session will be blocked until the transfer is complete. Are you sure you want to continue?"),
			# Translators: Title of a warning dialog.
			caption=_("Warning!"),
			style=wx.YES | wx.NO | wx.ICON_WARNING,
		) != wx.YES:
			return
		try:
			with open(path, "rb") as stream:
				content = base64.b64encode(stream.read()).decode("ascii")
		except OSError:
			logger.exception("Unable to read the specified file")
			gui.messageBox(
				# Translators: Message shown when the file to send could not be read.
				message=_("Unable to read the specified file."),
				caption=_("Error"),
				style=wx.ICON_ERROR,
			)
			return
		self.send(type="file_transfer", name=os.path.basename(path), content=content)
		configuration.record_activity()
		cues.clipboard_pushed()
		# Translators: Message reported when a file has been sent successfully.
		ui.message(_("File sent"))

	def handle_start(self, id=None, name=None, size=None, chunk_size=CHUNK_SIZE, **kwargs):
		if globalVars.appArgs.secure:
			return
		if id is None or not isinstance(size, int) or size < 0:
			return
		transfer = _IncomingTransfer(self, id, name, size, chunk_size or CHUNK_SIZE)
		if self.incoming is not None:
			transfer.refuse("busy")
			return
		self.incoming = transfer
		limit = self.max_receive_size()
		if limit and size > limit:
			transfer.refuse("too_large")
			# Translators: Message reported when an incoming file is larger than the configured limit.
			ui.message(_("A file transfer was refused because the file is larger than the configured limit."))
			return
		configuration.record_activity()
		destination = self._ask_destination(transfer)
		if destination is None:
			transfer.refuse("declined")
			return
		free_space = self._free_space(destination)
		if free_space is not None and free_space < size:
			transfer.refuse("no_space")
			gui.messageBox(
				# Translators: Message shown when there is not enough free space to receive a file.
				message=_("There is not enough free disk space to receive this file."),
				caption=_("Error"),
				style=wx.ICON_ERROR,
			)
			return
		try:
			transfer.accept(destination)
		except OSError:
			logger.exception("Unable to prepare the received file")
			transfer.refuse("write_error")

	def _ask_destination(self, transfer):
		dialog = wx.FileDialog(
			gui.mainFrame,
			# Translators: Message displayed in the transfer dialog when receiving a file.
			message=_("Choose where to save the received file ({name}, {size})").format(
				name=transfer.name,
				size=format_size(transfer.size),
			),
			defaultDir=os.environ["userprofile"],
			defaultFile=transfer.name,
			# Translators: Supported file types when sending or receiving files.
			wildcard=_("All files (*.*)") + "|*.*",
			style=wx.FD_SAVE | wx.FD_OVERWRITE_PROMPT,
		)
		try:
			if dialog.ShowModal() != wx.ID_OK:
				return None
			return dialog.GetPath()
		finally:
			dialog.Destroy()

	def _free_space(self, destination):
		try:
			return shutil.disk_usage(os.path.dirname(destination) or os.getcwd()).free
		except OSError:
			logger.debug("Unable to determine the available disk space", exc_info=True)
			return None

	def _incoming_for(self, transfer_id):
		if self.incoming is not None and self.incoming.id == transfer_id:
			return self.incoming
		return None

	def handle_chunk(self, id=None, **kwargs):
		transfer = self._incoming_for(id)
		if transfer is not None:
			transfer.handle_chunk(**kwargs)

	def handle_complete(self, id=None, **kwargs):
		transfer = self._incoming_for(id)
		if transfer is not None:
			transfer.handle_complete(**kwargs)

	def handle_ack(self, id=None, **kwargs):
		if self.outgoing is not None and self.outgoing.id == id:
			self.outgoing.handle_ack(**kwargs)

	def handle_abort(self, id=None, **kwargs):
		if self.outgoing is not None and self.outgoing.id == id:
			self.outgoing.handle_abort(**kwargs)
			return
		transfer = self._incoming_for(id)
		if transfer is not None:
			transfer.handle_abort(**kwargs)
