"""File-ingestion subsystem (Módulo 1 do produto).

Pipeline:
    raw bytes → detector → BaseParser.parse() → DataFrame canônico → validator (Pandera)
                                                                  → BillingItem[] (DB)

Cada provedor implementa um parser isolado em `parsers/`. Adicionar nova cloud =
novo subpacote em `parsers/` + registrar em `detector.PARSERS`.
"""

from app.infrastructure.file_ingestion.base_parser import BaseParser
from app.infrastructure.file_ingestion.detector import detect_and_parse

__all__ = ["BaseParser", "detect_and_parse"]
