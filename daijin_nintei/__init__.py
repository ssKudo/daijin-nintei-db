"""Zero-dependency core Python library for decoding, searching, and batch-screening
Japanese MLIT Minister Certification numbers (大臣認定番号).
"""

from .db import REPO_URL, CertDatabase, resolve_data_dir
from .decoder import (
    DATA_DIR_ENV_VAR,
    SEINOKYO_SEARCH_URL,
    CertDecoder,
    DecodeResult,
    decode_cert_no,
    format_load_bearing_label,
    normalize_cert_no,
)
from .screener import (
    LEGAL_DISCLAIMER,
    STATUS_FOUND,
    STATUS_INVALID_FORMAT,
    STATUS_LABELS,
    STATUS_UNCHECKED,
    STATUS_UNLISTED_VALID_FORMAT,
    CertScreener,
    ContradictionWarning,
    InventoryItem,
    ScreeningOccurrence,
    ScreeningReport,
    build_legal_disclaimer,
)

__version__ = "0.2.0"

__all__ = [
    "__version__",
    "DATA_DIR_ENV_VAR",
    "REPO_URL",
    "SEINOKYO_SEARCH_URL",
    "LEGAL_DISCLAIMER",
    "build_legal_disclaimer",
    "STATUS_FOUND",
    "STATUS_UNLISTED_VALID_FORMAT",
    "STATUS_UNCHECKED",
    "STATUS_INVALID_FORMAT",
    "STATUS_LABELS",
    "CertDecoder",
    "DecodeResult",
    "decode_cert_no",
    "format_load_bearing_label",
    "normalize_cert_no",
    "CertDatabase",
    "resolve_data_dir",
    "CertScreener",
    "ContradictionWarning",
    "ScreeningOccurrence",
    "InventoryItem",
    "ScreeningReport",
]
