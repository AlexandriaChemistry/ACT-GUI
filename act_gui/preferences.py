#
# This source file is part of the Alexandria Chemistry Toolkit.
#
# This program is free software; you can redistribute it and/or
# modify it under the terms of the GNU General Public License
# as published by the Free Software Foundation; either version 2
# of the License, or (at your option) any later version.
#
"""!
@file
@brief The preferences of the user for the page, such as the screen mode.

They are kept in the file with the settings of the user, under the key
`appearance`, next to the settings of the cluster:

    {"appearance": {"theme": "dark"}, "cluster": {...}}

A file with preferences that are not possible gives the defaults: the
preferences never stop the interface.
"""
from __future__ import annotations

from pathlib import Path
from typing import Any, Dict, Optional

from . import cluster

## The key of the preferences in the file with the settings
KEY = "appearance"
## The screen modes: light (the default), dark and hippy
THEMES = ("light", "dark", "hippy")
## The preferences when the user has chosen none
DEFAULTS: Dict[str, str] = {"theme": "light"}


class PreferenceError(ValueError):
    """!
    @brief Preferences that are not accepted. The message is for the user.
    """


def parse(given: Any) -> Dict[str, str]:
    """!
    @brief Check preferences that the page or the file gives.
    @param given A JSON object with the key theme, one of THEMES.
    @return The preferences.
    @throws PreferenceError if they are not possible.
    """
    if not isinstance(given, dict):
        raise PreferenceError("The preferences are not a JSON object")
    unknown = set(given) - set(DEFAULTS)
    if unknown:
        raise PreferenceError("Unknown preferences: %s" % ", ".join(sorted(unknown)))
    theme = given.get("theme")
    if theme not in THEMES:
        raise PreferenceError("The screen mode is one of %s" % ", ".join(THEMES))
    return {"theme": theme}


def load(path: Optional[Path]) -> Dict[str, str]:
    """!
    @brief Read the preferences when the interface starts.
    @param path The file with the settings, None for no file.
    @return The preferences, the defaults if there are none or if they
    cannot be used.
    """
    if path is None:
        return dict(DEFAULTS)
    try:
        return parse(cluster.read_settings(Path(path)).get(KEY))
    except (cluster.ClusterError, PreferenceError):
        return dict(DEFAULTS)


def save(path: Path, chosen: Dict[str, str]) -> None:
    """!
    @brief Write the preferences into the file with the settings. Other
    settings in the file are kept.
    @param path   The file. Its directory is made if needed.
    @param chosen The preferences, as parse() returns them.
    @throws ClusterError if the file cannot be written.
    """
    cluster.update_settings(Path(path), KEY, parse(chosen))
