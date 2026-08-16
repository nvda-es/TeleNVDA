# Build customizations
# Change this file instead of sconstruct or manifest files, whenever possible.
from site_scons.site_tools.NVDATool.typings import AddonInfo, BrailleTables, SymbolDictionaries
from site_scons.site_tools.NVDATool.utils import _
from datetime import datetime

# Use a date-based version format for builds without an explicit release version.
# Stable release builds override this value from the Git tag in sconstruct.
_ADDON_VERSION = datetime.now().strftime("%Y.%m.%d.%H%M")

# Add-on information variables
addon_info = AddonInfo(
	# add-on Name, internal for nvda
	addon_name= "TeleNVDA",
	# Add-on summary, usually the user visible name of the addon.
	# Translators: Summary for this add-on to be shown on installation and add-on information.
	addon_summary= _("Tele NVDA remote assistance"),
	# Add-on description
	# Translators: Long description to be shown for this add-on on add-on information from add-ons manager
	addon_description= _("""Allows remote control of and remote access to another machine. This add-on is based on NVDA Remote."""),
	# version
	addon_version= _ADDON_VERSION,
	# Author(s)
	addon_author= "Accessolutions. Based on work by the Asociación Comunidad Hispanohablante de NVDA and other contributors. Original work by Tyler Spivey <tspivey@pcdesk.net> and Christopher Toth <q@q-continuum.net>",
	# URL for the add-on documentation support
	addon_url= "https://github.com/Accessolutions/telenvda-accessolutions",
	# Documentation file name
	addon_docFileName= "readme.html",
	# Minimum NVDA version supported (e.g. "2018.3.0", minor version is optional)
	addon_minimumNVDAVersion= "2019.3.0",
	# Last NVDA version supported/tested (e.g. "2018.4.0", ideally more recent than minimum version)
	addon_lastTestedNVDAVersion= "2026.1.0",
	# No alternate update channel: every published release is stable.
	addon_updateChannel= None,
	# Add-on license such as GPL 2
	addon_license= "GPL 2",
	# URL for the license document the ad-on is licensed under
	addon_licenseURL= "https://www.gnu.org/licenses/old-licenses/gpl-2.0.html",
	# URL for the add-on repository where the source code can be found
	addon_sourceURL= "https://github.com/Accessolutions/telenvda-accessolutions",
	# Brief changelog for this version
	# Translators: what's new content for the add-on version to be shown in the add-on store
	addon_changelog=_("""Fix automatic updates that failed to complete with an "access denied" error, recover installations stuck pending, add WebSocket relay connections over HTTPS, including port 443, add Windows SSPI authentication for NTLM and Kerberos HTTP proxies, improve proxy compatibility, and provide two remote screenshot methods."""),
)

import os.path

# Define the python files that are the sources of your add-on.
# You can use glob expressions here, they will be expanded.
pythonSources = [
	'addon/*.py',
	'addon/globalPlugins/*/*.py',
]

# Files that contain strings for translation. Usually your python sources
i18nSources = pythonSources + ["buildVars.py"]

# Files that will be ignored when building the nvda-addon file
# Paths are relative to the addon directory, not to the root directory of your addon sources.
excludedFiles = ['globalPlugins\\remoteClient\\url_handler.obj']

# Base language for the NVDA add-on
# If your add-on is written in a language other than english, modify this variable.
# For example, set baseLanguage to "es" if your add-on is primarily written in spanish.
baseLanguage = "en"

# Markdown extensions for add-on documentation
# Most add-ons do not require additional Markdown extensions.
# If you need to add support for markup such as tables, fill out the below list.
# Extensions string must be of the form "markdown.extensions.extensionName"
# e.g. "markdown.extensions.tables" to add tables.
markdownExtensions = []

# Custom braille translation tables
# If your add-on includes custom braille tables (most will not), fill out this dictionary.
# Each key is a dictionary named according to braille table file name,
# with keys inside recording the following attributes:
# displayName (name of the table shown to users and translatable),
# contracted (contracted (True) or uncontracted (False) braille code),
# output (shown in output table list),
# input (shown in input table list).
brailleTables = {}

# Custom speech symbol dictionaries
# Symbol dictionary files reside in the locale folder, e.g. `locale\en`, and are named `symbols-<name>.dic`.
# If your add-on includes custom speech symbol dictionaries (most will not), fill out this dictionary.
# Each key is the name of the dictionary,
# with keys inside recording the following attributes:
# displayName (name of the speech dictionary  shown to users and translatable),
# mandatory (True when always enabled, False when not.
symbolDictionaries = {}
