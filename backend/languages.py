"""Languages the Writing engine can translate into.

The desktop translate menu shows six built-ins plus whatever the user saved as
their writing default. Picking one of those extras is meant to happen on the
website, not in the app — but the website had no list to build a picker from, so
in practice the only way to discover a language was to open the desktop app,
right-click, and use "More languages…" to be sent to the settings page.

This is that list, served over the API so the settings page can render a proper
selector without hardcoding anything or waiting on the desktop.
"""
from __future__ import annotations

# Shown first in the desktop menu without any configuration.
BUILT_IN = (
    "English", "Spanish", "French", "German", "Japanese", "Hindi",
)

# Everything else that can be selected on the website. Names, not ISO codes: the
# value is passed to the translation model as plain text and is also what the
# desktop menu displays, so a human-readable name is the right storage form.
ADDITIONAL = (
    "Arabic", "Assamese", "Bengali", "Bulgarian", "Burmese", "Catalan",
    "Chinese (Simplified)", "Chinese (Traditional)", "Croatian", "Czech",
    "Danish", "Dutch", "Estonian", "Filipino", "Finnish", "Greek", "Gujarati",
    "Hebrew", "Hungarian", "Icelandic", "Indonesian", "Italian", "Kannada",
    "Kazakh", "Khmer", "Korean", "Latvian", "Lithuanian", "Malay", "Malayalam",
    "Marathi", "Nepali", "Norwegian", "Odia", "Persian", "Polish",
    "Portuguese", "Punjabi", "Romanian", "Russian", "Serbian", "Sinhala",
    "Slovak", "Slovenian", "Swahili", "Swedish", "Tamil", "Telugu", "Thai",
    "Turkish", "Ukrainian", "Urdu", "Vietnamese", "Welsh",
)

ALL_LANGUAGES = tuple(BUILT_IN) + tuple(ADDITIONAL)

# Lower-cased name → canonical name, so "kannada" and "KANNADA" both store as
# "Kannada". The desktop compares case-insensitively too, but storing a single
# canonical spelling keeps the menu tidy and de-duplication reliable.
_CANONICAL = {name.lower(): name for name in ALL_LANGUAGES}

# How many extras one user may save. Guards against a client posting an enormous
# string into a column the desktop renders as a menu.
MAX_SELECTED = 12


def canonical(name: str) -> str | None:
    """Return the canonical spelling of a language name, or None if unknown."""
    return _CANONICAL.get((name or "").strip().lower())


def normalise_selection(value: str) -> tuple[str, list[str]]:
    """Normalise a comma-separated language selection.

    Returns (stored_value, unknown_names). Unknown names are dropped rather than
    rejecting the whole save — one bad entry should not lose the user's other
    choices — but they are reported so the caller can log or surface them.
    """
    names, unknown, seen = [], [], set()
    for raw in (value or "").split(","):
        raw = raw.strip()
        if not raw:
            continue
        canon = canonical(raw)
        if canon is None:
            unknown.append(raw)
            continue
        if canon.lower() in seen:
            continue
        seen.add(canon.lower())
        names.append(canon)
    return ", ".join(names[:MAX_SELECTED]), unknown
