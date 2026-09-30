# -*- coding: utf-8 -*-
# Copyright 2026 T.T.C. SAS
# License LGPL-3.0 or later (https://www.gnu.org/licenses/lgpl-3.0).
"""Data structures shared by the scan chain.

Deliberately independent of Odoo: this package can be tested and tuned with
a plain Python interpreter, without a database.
"""
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Tuple


@dataclass
class OcrWord:
    """A group of characters read, with its position in the image."""

    text: str
    score: float
    left: float
    top: float
    right: float
    bottom: float
    #: Line tilt in degrees, when the engine gives it. PP-OCR detects
    #: oriented quadrilaterals; Tesseract only returns upright rectangles
    #: and leaves this value at zero.
    angle: float = 0.0

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
    """A line of the receipt, rebuilt by grouping the words by height."""

    words: List[OcrWord] = field(default_factory=list)

    @property
    def text(self) -> str:
        return " ".join(w.text for w in self.words).strip()

    @property
    def score(self) -> float:
        if not self.words:
            return 0.0
        # Weighted by length: a misread one-character word must not weigh as
        # much as a whole label.
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
    """A value extracted, its reliability and the text it came from."""

    value: Any
    confidence: float  # 0.0 -> 1.0
    source: str = ""

    def __bool__(self) -> bool:
        return self.value is not None


@dataclass
class PreprocessInfo:
    """What the preprocessing actually did to the image."""

    cropped: bool = False
    deskew_angle: float = 0.0
    rotated_quarters: int = 0
    original_size: Tuple[int, int] = (0, 0)
    final_size: Tuple[int, int] = (0, 0)
    changed: bool = False
    reread: bool = False  # a second reading, on the straightened or cropped image


@dataclass
class ScanResult:
    """Full result of a scan, ready to be written on an expense."""

    engine: str = ""
    duration: float = 0.0
    lines: List[OcrLine] = field(default_factory=list)
    fields: Dict[str, ExtractedField] = field(default_factory=dict)
    preprocess: Optional[PreprocessInfo] = None
    image_bytes: Optional[bytes] = None  # cropped/straightened image, as JPEG
    timer: Any = None  # duration of each step, for the log

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
