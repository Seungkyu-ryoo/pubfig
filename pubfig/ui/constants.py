"""Shared UI defaults; no window or controller dependencies."""

import re

PREVIEW_DPI = 100

RECENT_FILES_LIMIT = 10

SAVED_STYLES_SETTINGS_KEY = "saved_styles_v1"

SAVED_STYLES_SCHEMA_VERSION = 1

AUTOSAVE_INTERVAL_MS = 120_000

MAX_UNDO_STACK_WEIGHT_BYTES = 256 * 1024 * 1024

DATA_FILE_SUFFIXES = {".csv", ".txt", ".tsv", ".dat"}

_SESSION_AUTOSAVE_RE = re.compile(r"\.pubfig_autosave\.(\d+)\.([0-9a-f]{32})\.json")
