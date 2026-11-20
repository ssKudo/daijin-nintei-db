"""Zero-dependency core Python library for decoding, searching, and batch-screening
Japanese MLIT Minister Certification numbers (大臣認定番号).
"""

from .db import CertDatabase
from .decoder import (
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
    STATUS_UNLISTED_VALID_FORMAT,
    CertScreener,
    ContradictionWarning,
    InventoryItem,
    ScreeningOccurrence,
    ScreeningReport,
)

__version__ = "0.1.0"

__all__ = [
    "__version__",
    "SEINOKYO_SEARCH_URL",
    "LEGAL_DISCLAIMER",
    "STATUS_FOUND",
    "STATUS_UNLISTED_VALID_FORMAT",
    "STATUS_INVALID_FORMAT",
    "STATUS_LABELS",
    "CertDecoder",
    "DecodeResult",
    "decode_cert_no",
    "format_load_bearing_label",
    "normalize_cert_no",
    "CertDatabase",
    "CertScreener",
    "ContradictionWarning",
    "ScreeningOccurrence",
    "InventoryItem",
    "ScreeningReport",
]
