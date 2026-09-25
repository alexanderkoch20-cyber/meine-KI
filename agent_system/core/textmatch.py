"""Robuste Stichwortsuche fuer deutschsprachige Texte (Routing, Legal-Erkennung)."""

from __future__ import annotations

import re

_UMLAUTS = str.maketrans({"ä": "ae", "ö": "oe", "ü": "ue", "ß": "ss", "Ä": "ae", "Ö": "oe", "Ü": "ue"})


def normalize(text: str) -> str:
    """Kleinschreibung + Umlaute als ae/oe/ue/ss - damit 'Löschfrist' == 'loeschfrist'."""
    return (text or "").translate(_UMLAUTS).lower()


def keyword_matches(keyword: str, text: str) -> bool:
    """Ganzwort-Treffer; ``wort*`` trifft jeden Wortanfang (z.B. Plural, Komposita).

    ``text`` sollte bereits mit ``normalize`` aufbereitet sein.
    """
    keyword = normalize(keyword)
    if keyword.endswith("*"):
        pattern = r"(?<!\w)" + re.escape(keyword[:-1])
    else:
        pattern = r"(?<!\w)" + re.escape(keyword) + r"(?!\w)"
    return re.search(pattern, text) is not None
