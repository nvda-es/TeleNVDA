This release brings the following changes:
* Add screen sharing: the controlling computer can display the screen of the controlled one with NVDA+Control+Shift+V, and move its mouse when the controlled computer allows it. The picture travels directly between the two computers rather than through the relay server. The controlled computer asks its user before anything is shared, mouse control is refused by default, and no keyboard input is ever sent this way. The feature requires a relay started with screen sharing enabled and is simply never offered otherwise.
* Add a chunked file transfer which is no longer limited to 10 MB when both computers run a version of TeleNVDA which supports it. Transfers work in both directions, report progress, transferred size, speed and estimated remaining time, can be cancelled from either end, and the received file is verified with a SHA-256 checksum before being saved.
* Negotiate the available features when the connection is established, so that computers running the original TeleNVDA or the standard NVDA Remote automatically fall back to the legacy 10 MB transfer.
* Add an option to send files larger than 10 MB to computers which only support the legacy transfer, and an option to limit the size of received files.
* Handle server connections which break when client sends ALPN information. This should restore connectivity to nvdaremote.com and similar servers.
* Add native Windows SSPI authentication for NTLM and Kerberos/Negotiate HTTP proxy CONNECT handshakes used by WebSocket relays.
* Check GitHub Releases for TeleNVDA stable updates at startup or on demand, with SHA-256 verification and confirmation before installation.
* Fix automatic updates that failed to complete with an "access denied" error by marking the previous version for removal before installing the downloaded package, and recover installations that were already stuck pending.
* Automatically trust and remember previously unknown server certificate fingerprints so manual and automatic connections are not blocked by a confirmation dialog.
* Add a configurable inactivity timeout for automatic connections and a configurable folder for received screenshots.
* Add keyboard gestures for remote screenshots: NVDA+Control+Shift+P for the native capture and Windows+Alt+P for the PowerShell capture. Both work from the controlling and the controlled computer.
* Fix the PowerShell screenshot request, which silently used the native capture method instead.
* Fix the PowerShell screenshot request, which did nothing when the controlled computer ran a standard TeleNVDA: the capture method is now sent as a parameter of the usual screenshot request, so those computers answer with their native capture instead of ignoring the request.
* Make the PowerShell screenshot work with a controlled computer running the NVDA Remote add-on shipped with NVDA or the original TeleNVDA: when nothing answers the screenshot request, the capture is driven through the clipboard and keyboard messages of the standard protocol. Known issue: this compatible capture does not work yet, the Run dialog is never opened on the controlled computer. It will be fixed in a later release.
* Fix the screenshot gestures, which were forwarded to the controlled computer instead of being handled locally while remote control was active.
* Fall back to the native capture when PowerShell is unavailable or blocked on the controlled computer, and run PowerShell from its absolute path so a restricted PATH no longer prevents the capture.
* Encode remote screenshots as JPEG instead of the raw bitmap before Base64 encoding, so that captures transfer faster.
* Add proxy settings to the connection dialog, with a proxy mode which can be manual, automatic Windows detection or no proxy. Existing configurations left in manual mode without a proxy address are switched to automatic detection.

Important: some anti-virus software may flag parts of this add-on as malicious. Specifically, `url_handler.exe`, which opens `remote://` and `tele://` links. If you don't use this feature, you can safely quarantine or delete the file. Otherwise, you must add it as an exception.

SHA256:
