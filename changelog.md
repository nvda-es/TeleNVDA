This release brings the following changes:

* Fixed CVE-2025-26326, thanks to @RafaelFernandesBR. Now, TeleNVDA will fail to connect or save settings when attempting to use passwords with less than 6 characters or easily discoverable.
* Last tested NVDA version is now 2024.4.2.

Important: some anti-virus software are flagging parts of this add-on as malicious. Specifically, `url_handler.exe`, which opens `remote://` and `tele://` links. If you don't use this feature, you can safely quarantine or delete the file. Otherwise, you must add it as an exception.

SHA256: 