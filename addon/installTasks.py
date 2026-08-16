import gui
import wx
import addonHandler
import buildVersion
import configobj
import globalVars
import os
from config import conf as nvda_conf
from logHandler import log

addonHandler.initTranslation()

_NATIVE_REMOTE_SECTION = 'native_remote'


def _as_bool(value):
	if isinstance(value, bool):
		return value
	if isinstance(value, str):
		return value.strip().lower() in ('1', 'true', 'yes', 'on')
	return bool(value)


def _is_readonly():
	return bool(getattr(globalVars.appArgs, 'secure', False) or getattr(globalVars.appArgs, 'launcher', False))


def _load_addon_config():
	path = os.path.abspath(os.path.join(globalVars.appArgs.configPath, 'teleNVDA.ini'))
	return configobj.ConfigObj(infile=path, default_encoding='utf8', create_empty=True)


def _read_native_remote_state():
	config = _load_addon_config()
	state = config.get(_NATIVE_REMOTE_SECTION)
	if state is None:
		return False, True
	return _as_bool(state.get('managed', False)), _as_bool(state.get('original_enabled', True))


def _save_native_remote_state(original_enabled):
	if _is_readonly():
		return False
	config = _load_addon_config()
	if _NATIVE_REMOTE_SECTION not in config:
		config[_NATIVE_REMOTE_SECTION] = {}
	state = config[_NATIVE_REMOTE_SECTION]
	state['managed'] = True
	state['original_enabled'] = bool(original_enabled)
	state['restore_on_reactivation'] = False
	config.write()
	return True


def _clear_native_remote_state():
	if _is_readonly():
		return False
	config = _load_addon_config()
	if _NATIVE_REMOTE_SECTION not in config:
		return True
	state = config[_NATIVE_REMOTE_SECTION]
	state['managed'] = False
	state['original_enabled'] = True
	state['restore_on_reactivation'] = False
	config.write()
	return True


def _manage_native_remote_on_install():
	if _is_readonly():
		return
	try:
		remote = nvda_conf.get('remote')
		if remote is None or 'enabled' not in remote:
			return
		managed, original_enabled = _read_native_remote_state()
		if not managed:
			original_enabled = bool(remote['enabled'])
			if not _save_native_remote_state(original_enabled):
				return
		if remote['enabled'] is not False:
			remote['enabled'] = False
			nvda_conf.save()
	except Exception:
		log.exception("Unable to disable native NVDA Remote during TeleNVDA installation")


def _restore_native_remote_on_uninstall():
	if _is_readonly():
		return
	try:
		managed, original_enabled = _read_native_remote_state()
		if not managed:
			return
		remote = nvda_conf.get('remote')
		if remote is not None and 'enabled' in remote:
			if remote['enabled'] != original_enabled:
				remote['enabled'] = original_enabled
				nvda_conf.save()
		_clear_native_remote_state()
	except Exception:
		log.exception("Unable to restore native NVDA Remote during TeleNVDA uninstallation")


def onInstall():
	_manage_native_remote_on_install()
	if buildVersion.version_year >= 2026:
		return
	for addon in addonHandler.getAvailableAddons():
		if addon.name == "remote" and not addon.isDisabled:
			result = gui.messageBox(
				# Translators: message asking the user wether NVDA Remote whould be disabled or not
				_(
					"NVDA Remote has been detected on your NVDA installation. In order for TeleNVDA to work without conflicts, NVDA Remote must be disabled. Otherwise, TeleNVDA will refuse to work. Would you like to disable NVDA Remote now?"
				),
				# Translators: question title
				_("Running NVDA Remote detected"),
				wx.YES_NO | wx.ICON_QUESTION,
				gui.mainFrame,
			)
			if result == wx.YES:
				addon.enable(False)
			return


def onUninstall():
	_restore_native_remote_on_uninstall()
