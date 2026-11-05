"""Spreadsheet export entry points using shared document artifact routing."""
from ...config import DATA_ROOT  # existing test/runtime configuration hook
from ..artifacts import export_dest, make_download_url, resolve_export_file
