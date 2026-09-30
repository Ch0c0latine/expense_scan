# -*- coding: utf-8 -*-
# Copyright 2026 Yves Vallée
# License LGPL-3.0 or later (https://www.gnu.org/licenses/lgpl-3.0).
"""Interchangeable text recognition engines.

Two implementations are provided, both local, free and without any token:

* ``rapidocr``: the PP-OCR networks run by ONNX Runtime. Clearly better than
  Tesseract on a thermal receipt (faded print, crumpled paper, condensed
  font), for a CPU cost of about a second.
* ``tesseract``: fallback engine, without a neural detection network.

To add an engine, write a subclass of :class:`ScanEngine` and register it in
``ENGINE_CLASSES``.
"""
import logging
import math
import os
import threading
import time

_logger = logging.getLogger(__name__)

# Import errors are kept: a missing dependency must be named to the user,
# not turn into a "required module" message that does not say what is
# actually missing.
try:
    import numpy as np
except Exception as error:  # noqa: BLE001
    np = None
    NUMPY_IMPORT_ERROR = error
else:
    NUMPY_IMPORT_ERROR = None

try:
    import cv2
except Exception as error:  # noqa: BLE001
    cv2 = None
    CV2_IMPORT_ERROR = error
else:
    CV2_IMPORT_ERROR = None

from .types import OcrWord

#: ONNX compute threads per worker, when the company sets none.
DEFAULT_THREADS = 4


def imaging_status():
    """State of the image processing libraries, with the exact cause."""
    problems = []
    if np is None:
        problems.append("numpy: %r" % (NUMPY_IMPORT_ERROR,))
    if cv2 is None:
        problems.append("cv2 (opencv-python): %r" % (CV2_IMPORT_ERROR,))
    if problems:
        return False, "Import failed: " + " / ".join(problems)
    return True, "numpy %s, OpenCV %s" % (np.__version__, cv2.__version__)


class ScanEngine(object):
    """Common interface: a BGR image goes in, positioned words come out."""

    code = ""
    label = ""

    def __init__(self, **options):
        self.options = options

    @property
    def working_side(self):
        """Longest side, in pixels, the engine scales the image down to.

        ``None``: the engine reads the image at its original size.
        """
        return None

    @classmethod
    def availability(cls):
        """Return (available, readable message)."""
        raise NotImplementedError

    def recognize(self, image, use_cls=None):
        """Return a list of ``OcrWord`` for the given BGR image."""
        raise NotImplementedError

    #: Lines of the control receipt used for warm-up and diagnosis.
    WARMUP_LINES = (
        "SUPERMARCHE TEST",
        "12 RUE DE LA PAIX",
        "04/09/2026 14:32",
        "TOTAL 12,34 EUR",
    )
    #: Fonts looked for in this order to draw that receipt.
    WARMUP_FONTS = (
        "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
        "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
        "/usr/share/fonts/truetype/liberation/LiberationSans-Bold.ttf",
        "/usr/share/fonts/dejavu/DejaVuSans.ttf",
    )

    @classmethod
    def _warmup_image(cls):
        """Control receipt, drawn with a real font.

        OpenCV's vector font (Hershey) is a stroke drawing, without glyph
        thickness: the PP-OCR models, trained on printed text, detect nothing
        in it. A control image drawn that way proves nothing about the real
        state of the engine.
        """
        if np is None or cv2 is None:
            return None
        try:
            from PIL import Image, ImageDraw, ImageFont
        except ImportError:
            return None

        height = 60 * len(cls.WARMUP_LINES) + 50
        image = Image.new("RGB", (620, height), "white")
        draw = ImageDraw.Draw(image)
        font = None
        for path in cls.WARMUP_FONTS:
            try:
                font = ImageFont.truetype(path, 34)
                break
            except OSError:
                continue
        if font is None:
            font = ImageFont.load_default()
        for index, line in enumerate(cls.WARMUP_LINES):
            draw.text((30, 25 + index * 60), line, fill="black", font=font)
        return cv2.cvtColor(np.array(image), cv2.COLOR_RGB2BGR)


class RapidOcrEngine(ScanEngine):
    """PP-OCR (detection + recognition) run by ONNX Runtime."""

    code = "rapidocr"
    label = "RapidOCR / PP-OCR (ONNX Runtime)"

    # Candidates tried in order, until one actually reads the control
    # receipt back.
    #
    # Measured on rapidocr 3.9.2: the "latin" recognition models (PP-OCRv5
    # as well as PP-OCRv3) load without error but recognise nothing at all,
    # while the default model reads accented French without a mistake
    # ("SUPERMARCHÉ", "NET À PAYER" at a score of 1.00). The default model
    # therefore comes first; the latin variants stay as a fallback in case a
    # later version fixes them.
    MODEL_CANDIDATES = [
        (None, None),  # the library's default configuration
        ("PP-OCRv5", "latin"),
        ("PP-OCRv4", "latin"),
    ]
    #: Thresholds for accepting a candidate on the control receipt.
    WARMUP_MIN_WORDS = 3
    WARMUP_MIN_SCORE = 0.5

    def __init__(self, **options):
        super().__init__(**options)
        self._engine = None
        self._description = ""
        self._lock = threading.Lock()

    @classmethod
    def availability(cls):
        try:
            import rapidocr  # noqa: F401
        except ImportError:
            return False, "Python package \"rapidocr\" not installed"
        try:
            import onnxruntime  # noqa: F401
        except ImportError:
            return False, "Python package \"onnxruntime\" not installed"
        return True, "rapidocr %s" % getattr(rapidocr, "__version__", "?")

    def _build_params(self, ocr_version, lang):
        from rapidocr import LangRec, ModelType, OCRVersion

        params = {
            # Text below this score is dropped. A thermal receipt produces
            # many fairly readable lines that are better kept: the parser
            # knows how to weigh them.
            "Global.text_score": float(self.options.get("text_score", 0.35)),
            "Global.max_side_len": int(self.options.get("max_side_len", 1800)),
        }
        model_dir = self.options.get("model_dir")
        if model_dir:
            params["Global.model_root_dir"] = model_dir
        # Odoo already runs several workers: letting ONNX open as many
        # threads as there are cores in each of them lowers the overall
        # throughput instead of raising it. Each extra thread also makes the
        # allocator (glibc) reserve its own virtual memory arena, which pushed
        # the workers past their limit on every scan (see MALLOC_ARENA_MAX in
        # the README). Four threads read a receipt just as fast, and the
        # operations follow one another without asking for more.
        threads = int(self.options.get("threads", 0) or 0) or min(DEFAULT_THREADS, os.cpu_count() or 1)
        params["EngineConfig.onnxruntime.intra_op_num_threads"] = threads
        params["EngineConfig.onnxruntime.inter_op_num_threads"] = 1
        if ocr_version and lang:
            params["Rec.ocr_version"] = OCRVersion(ocr_version)
            params["Rec.model_type"] = ModelType(self.options.get("model_type", "mobile"))
            params["Rec.lang_type"] = LangRec(lang)
        return params

    def _load(self):
        """Create the engine, load the models and check that they read.

        Creating it is not enough, for two reasons:

        * RapidOCR loads its models lazily: a language/version combination
          that does not exist would only fail on the first real receipt, out
          of reach of the fallback;
        * a set of models can load without error and recognise nothing.
          That is the case of ``latin/PP-OCRv5``: detection finds the text
          areas, recognition returns nothing and RapidOCR then drops the
          boxes, without raising an error. A candidate is therefore only
          kept if it reads the control receipt back.
        """
        from rapidocr import RapidOCR

        last_error = None
        warmup = self._warmup_image()
        for ocr_version, lang in self.MODEL_CANDIDATES:
            label = "RapidOCR / %s %s" % (ocr_version, lang) if ocr_version else self.label
            try:
                engine = RapidOCR(params=self._build_params(ocr_version, lang))
                if warmup is not None:
                    words = self._words_from_result(engine(warmup))
                    if not self._warmup_ok(words):
                        last_error = RuntimeError(
                            "%s loads but does not read the control receipt back "
                            "(%d words read)" % (label, len(words)))
                        _logger.warning("%s", last_error)
                        continue
                self._description = label
                _logger.info("RapidOCR scan engine loaded (%s)", label)
                return engine
            except Exception as error:  # noqa: BLE001
                last_error = error
                _logger.warning("RapidOCR models %s/%s unavailable: %s",
                                ocr_version, lang, error)
        raise RuntimeError("No usable set of RapidOCR models: %s" % last_error)

    @classmethod
    def _warmup_ok(cls, words):
        """Does the candidate read enough of the control receipt to be kept?"""
        if len(words) < cls.WARMUP_MIN_WORDS:
            return False
        return sum(word.score for word in words) / len(words) >= cls.WARMUP_MIN_SCORE

    def _get_engine(self):
        if self._engine is None:
            with self._lock:
                if self._engine is None:
                    self._engine = self._load()
        return self._engine

    @property
    def description(self):
        return self._description or self.label

    @property
    def working_side(self):
        return int(self.options.get("max_side_len", 1800))

    def recognize(self, image, use_cls=None):
        return self._words_from_result(self._get_engine()(image, use_cls=use_cls))

    @staticmethod
    def _words_from_result(result):
        """Turn the RapidOCR output into positioned words."""
        if result is None or not getattr(result, "txts", None):
            return []

        words = []
        boxes = result.boxes if result.boxes is not None else []
        scores = result.scores if result.scores is not None else []
        for index, text in enumerate(result.txts):
            text = (text or "").strip()
            if not text:
                continue
            angle = 0.0
            try:
                box = np.asarray(boxes[index], dtype="float32")
                left, top = float(box[:, 0].min()), float(box[:, 1].min())
                right, bottom = float(box[:, 0].max()), float(box[:, 1].max())
                # PP-OCR returns an oriented quadrilateral, not a rectangle:
                # the line direction is read on its longest side. The
                # measurement is direct and free, the data being already
                # there.
                if len(box) == 4:
                    # The detector orders its points from the top left corner
                    # of the image. The first side is therefore the length
                    # for a lying line, but the thickness for a standing one:
                    # the longer of the two is the only one that follows the
                    # writing direction.
                    edges = (box[1] - box[0], box[2] - box[1])
                    edge = max(edges, key=lambda side: float(side[0]) ** 2
                               + float(side[1]) ** 2)
                    angle = math.degrees(math.atan2(float(edge[1]), float(edge[0])))
                    # Brought back into (-90, 90]: a line and the same line
                    # read upside down have the same direction. The gap to
                    # the horizontal is kept, though, as it alone tells a
                    # lying receipt from a standing one (the engine reads
                    # just as well both ways).
                    angle = ((angle + 90.0) % 180.0) - 90.0
            except Exception:  # noqa: BLE001
                left = top = 0.0
                right = bottom = 1.0
            score = float(scores[index]) if index < len(scores) else 0.0
            words.append(OcrWord(text=text, score=score, angle=angle,
                                 left=left, top=top, right=right, bottom=bottom))
        return words


class TesseractEngine(ScanEngine):
    """Fallback: Tesseract, without neural detection of the text areas."""

    code = "tesseract"
    label = "Tesseract (local)"

    @classmethod
    def availability(cls):
        try:
            import pytesseract
        except ImportError:
            return False, "Python package \"pytesseract\" not installed"
        try:
            version = pytesseract.get_tesseract_version()
        except Exception as error:  # noqa: BLE001
            return False, "tesseract binary not found (%s)" % error
        return True, "tesseract %s" % version

    @property
    def description(self):
        return self.label

    def recognize(self, image, use_cls=None):
        import pytesseract
        from PIL import Image

        lang = self.options.get("lang") or "eng"
        # --psm 6: the receipt is handled as a single block of text. The
        # automatic mode readily splits a receipt into columns and mixes up
        # the reading order of the lines.
        config = self.options.get("config") or "--psm 6"
        rgb = image[:, :, ::-1] if image.ndim == 3 else image
        data = pytesseract.image_to_data(Image.fromarray(rgb), lang=lang, config=config,
                                         output_type=pytesseract.Output.DICT)

        words = []
        for index, text in enumerate(data.get("text", [])):
            text = (text or "").strip()
            if not text:
                continue
            try:
                confidence = float(data["conf"][index])
            except (KeyError, ValueError, TypeError):
                confidence = -1.0
            if confidence < 0:
                continue
            left = float(data["left"][index])
            top = float(data["top"][index])
            words.append(OcrWord(
                text=text,
                score=confidence / 100.0,
                left=left,
                top=top,
                right=left + float(data["width"][index]),
                bottom=top + float(data["height"][index]),
            ))
        return words


ENGINE_CLASSES = {
    RapidOcrEngine.code: RapidOcrEngine,
    TesseractEngine.code: TesseractEngine,
}
# Order of preference when the setting is "automatic".
AUTO_ORDER = [RapidOcrEngine.code, TesseractEngine.code]

# The models weigh several tens of megabytes and take 1 to 3 seconds to load:
# one instance is kept per set of options and per worker process.
_ENGINE_CACHE = {}
_CACHE_LOCK = threading.Lock()


def _cache_key(code, options):
    return (code,) + tuple(sorted((k, v) for k, v in options.items()))


def get_engine(code, **options):
    """Return the shared instance of engine ``code`` for these options."""
    if code not in ENGINE_CLASSES:
        raise ValueError("Unknown scan engine: %s" % code)
    key = _cache_key(code, options)
    engine = _ENGINE_CACHE.get(key)
    if engine is None:
        with _CACHE_LOCK:
            engine = _ENGINE_CACHE.get(key)
            if engine is None:
                engine = ENGINE_CLASSES[code](**options)
                _ENGINE_CACHE[key] = engine
    return engine


def resolve_engine(preferred, **options):
    """Choose an available engine, starting from the preferred one.

    ``preferred`` is ``auto`` or an engine code. Returns the instance; raises
    ``RuntimeError`` if no engine can be used, with what is missing for each.
    """
    candidates = AUTO_ORDER if preferred in (None, "", "auto") else [preferred]
    problems = []
    for code in candidates:
        engine_class = ENGINE_CLASSES.get(code)
        if engine_class is None:
            problems.append("%s: unknown engine" % code)
            continue
        available, message = engine_class.availability()
        if available:
            return get_engine(code, **options)
        problems.append("%s: %s" % (engine_class.label, message))
    raise RuntimeError("No OCR engine available. " + " / ".join(problems))


def engines_status():
    """State of each engine, for the settings screen."""
    status = []
    for code in AUTO_ORDER:
        engine_class = ENGINE_CLASSES[code]
        available, message = engine_class.availability()
        status.append({
            "code": code,
            "label": engine_class.label,
            "available": available,
            "message": message,
        })
    return status


def self_test(preferred="auto", **options):
    """Load the engine and read a test image. Returns a dictionary.

    Serves both as a diagnosis and as a warm-up: the models are downloaded
    here the first time, not when the user has just photographed a receipt.
    """
    started = time.time()
    engine = resolve_engine(preferred, **options)
    image = ScanEngine._warmup_image()
    if image is None:
        raise RuntimeError(imaging_status()[1])
    words = engine.recognize(image)
    return {
        "engine": getattr(engine, "description", engine.label),
        "duration": time.time() - started,
        "text": " ".join(word.text for word in words),
        "word_count": len(words),
    }
