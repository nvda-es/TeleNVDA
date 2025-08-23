This release brings the following changes:

* Updated translations.
* `remote://` and `tele://` links won't work anymore if NVDA is not running.
* Added MiniUPNP library for Python 3.13 x86 and x64.
* Optimized `url_handler.exe` and removed unneeded functions. First attempt to remove it from anti-virus databases.

Important: some anti-virus software are flagging parts of this add-on as malicious. Specifically, `url_handler.exe`, which opens `remote://` and `tele://` links. If you don't use this feature, you can safely quarantine or delete the file. Otherwise, you must add it as an exception.

SHA256: 