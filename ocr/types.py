# -*- coding: utf-8 -*-
"""Structures de données partagées par la chaîne de scan.

Volontairement indépendantes d'Odoo : ce paquet peut être testé et mis au
point avec un simple interpréteur Python, sans base de données.
"""
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Tuple


@dataclass
class OcrWord:
    """Un groupe de caractères reconnu, avec sa position dans l'image."""

    text: str
    score: float
    left: float
    top: float
    right: float
    bottom: float

    @property
    def height(self) -> float:
        return max(self.bottom - self.top, 1.0)

    @property
    def width(self) -> float:
        return max(self.right - self.left, 1.0)

    @property
    def center_y(self) -> float:
        return (self.top + self.bottom) / 2.0


@dataclass
class OcrLine:
    """Une ligne du ticket, reconstruite en regroupant les mots par hauteur."""

    words: List[OcrWord] = field(default_factory=list)

    @property
    def text(self) -> str:
        return " ".join(w.text for w in self.words).strip()

    @property
    def score(self) -> float:
        if not self.words:
            return 0.0
        # Pondérée par la longueur : un mot d'un caractère mal lu ne doit pas
        # peser autant qu'un libellé complet.
        total = sum(len(w.text) for w in self.words) or 1
        return sum(w.score * len(w.text) for w in self.words) / total

    @property
    def top(self) -> float:
        return min((w.top for w in self.words), default=0.0)

    @property
    def bottom(self) -> float:
        return max((w.bottom for w in self.words), default=0.0)

    @property
    def center_y(self) -> float:
        return (self.top + self.bottom) / 2.0


@dataclass
class ExtractedField:
    """Une valeur extraite, sa fiabilité et le texte qui l'a produite."""

    value: Any
    confidence: float  # 0.0 -> 1.0
    source: str = ""

    def __bool__(self) -> bool:
        return self.value is not None


@dataclass
class PreprocessInfo:
    """Ce que le pré-traitement a effectivement fait à l'image."""

    cropped: bool = False
    deskew_angle: float = 0.0
    rotated_quarters: int = 0
    original_size: Tuple[int, int] = (0, 0)
    final_size: Tuple[int, int] = (0, 0)
    changed: bool = False


@dataclass
class ScanResult:
    """Résultat complet d'un scan, prêt à être reporté sur une dépense."""

    engine: str = ""
    duration: float = 0.0
    lines: List[OcrLine] = field(default_factory=list)
    fields: Dict[str, ExtractedField] = field(default_factory=dict)
    preprocess: Optional[PreprocessInfo] = None
    image_bytes: Optional[bytes] = None  # image recadrée/redressée, en JPEG

    @property
    def raw_text(self) -> str:
        return "\n".join(line.text for line in self.lines)

    @property
    def mean_score(self) -> float:
        words = [w for line in self.lines for w in line.words]
        if not words:
            return 0.0
        return sum(w.score for w in words) / len(words)

    def get(self, name: str) -> Optional[ExtractedField]:
        found = self.fields.get(name)
        return found if found else None

    def value(self, name: str, default: Any = None) -> Any:
        found = self.fields.get(name)
        return found.value if found and found.value is not None else default

    def confidence(self, name: str) -> float:
        found = self.fields.get(name)
        return found.confidence if found else 0.0
