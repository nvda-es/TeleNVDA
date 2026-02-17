This release brings the following changes:

* When connecting, include the string "remote/2.0" in the ALPN protocols list.
* When Windows goes to lock screen, control is immediately returned to the local machine. Unfortunately, this feature is only available in NVDA 2022.4 or greather.

Important: some anti-virus software may fla parts of this add-on as malicious. Specifically, `url_handler.exe`, which opens `remote://` and `tele://` links. If you don't use this feature, you can safely quarantine or delete the file. Otherwise, you must add it as an exception.

SHA256: 