# -*- coding: utf-8 -*-
"""Moteurs de reconnaissance de texte, interchangeables.

Deux implémentations sont fournies, toutes deux 100 % locales, gratuites et
sans jeton :

* ``rapidocr`` — les réseaux PP-OCR exécutés par ONNX Runtime. Nettement
  meilleur que Tesseract sur un ticket thermique (impression pâle, papier
  froissé, police condensée) pour un coût CPU de l'ordre de la seconde.
* ``tesseract`` — repli historique, sans réseau de neurones de détection.

Ajouter un moteur revient à écrire une sous-classe de :class:`ScanEngine` et
à l'enregistrer dans ``ENGINE_CLASSES``.
"""
import logging
import math
import threading
import time

_logger = logging.getLogger(__name__)

# Les erreurs d'import sont conservées : une dépendance absente doit
# pouvoir être nommée à l'utilisateur, pas se traduire par un « module
# requis » qui n'apprend rien sur ce qui manque réellement.
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


def imaging_status():
    """État des bibliothèques de traitement d'image, avec la cause exacte."""
    problems = []
    if np is None:
        problems.append("numpy : %r" % (NUMPY_IMPORT_ERROR,))
    if cv2 is None:
        problems.append("cv2 (opencv-python) : %r" % (CV2_IMPORT_ERROR,))
    if problems:
        return False, "Import impossible — " + " / ".join(problems)
    return True, "numpy %s, OpenCV %s" % (np.__version__, cv2.__version__)


class ScanEngine(object):
    """Interface commune : une image BGR entre, des mots situés sortent."""

    code = ""
    label = ""

    def __init__(self, **options):
        self.options = options

    @classmethod
    def availability(cls):
        """Renvoie (disponible, message lisible)."""
        raise NotImplementedError

    def recognize(self, image, use_cls=None):
        """Renvoie une liste d'``OcrWord`` pour l'image BGR fournie."""
        raise NotImplementedError

    #: Lignes du ticket de contrôle servant à la chauffe et au diagnostic.
    WARMUP_LINES = (
        "SUPERMARCHE TEST",
        "12 RUE DE LA PAIX",
        "04/09/2026 14:32",
        "TOTAL 12,34 EUR",
    )
    #: Polices cherchées dans cet ordre pour dessiner ce ticket.
    WARMUP_FONTS = (
        "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
        "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
        "/usr/share/fonts/truetype/liberation/LiberationSans-Bold.ttf",
        "/usr/share/fonts/dejavu/DejaVuSans.ttf",
    )

    @classmethod
    def _warmup_image(cls):
        """Ticket de contrôle, dessiné avec une véritable police.

        La police vectorielle d'OpenCV (Hershey) est un tracé au trait, sans
        épaisseur de glyphe : les modèles PP-OCR, entraînés sur du texte
        imprimé, n'y détectent rien. Une image de contrôle dessinée ainsi ne
        prouve donc rien sur l'état réel du moteur.
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
    """PP-OCR (détection + reconnaissance) exécuté par ONNX Runtime."""

    code = "rapidocr"
    label = "RapidOCR / PP-OCR (ONNX Runtime)"

    # Essayés dans l'ordre, jusqu'à en trouver un qui relit réellement le
    # ticket de contrôle.
    #
    # Mesuré sur rapidocr 3.9.2 : les modèles de reconnaissance « latin »
    # (PP-OCRv5 comme PP-OCRv3) se chargent sans erreur mais ne
    # reconnaissent rien du tout, tandis que le modèle d'usine lit le
    # français accentué sans faute — « SUPERMARCHÉ », « NET À PAYER » à 1,00
    # de score. L'usine passe donc en premier ; les variantes latines
    # restent en repli au cas où une version ultérieure les corrigerait.
    MODEL_CANDIDATES = [
        (None, None),  # configuration d'usine de la bibliothèque
        ("PP-OCRv5", "latin"),
        ("PP-OCRv4", "latin"),
    ]
    #: Seuils de validation d'un candidat sur le ticket de contrôle.
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
            return False, "Paquet Python « rapidocr » non installé"
        try:
            import onnxruntime  # noqa: F401
        except ImportError:
            return False, "Paquet Python « onnxruntime » non installé"
        return True, "rapidocr %s" % getattr(rapidocr, "__version__", "?")

    def _build_params(self, ocr_version, lang):
        from rapidocr import LangRec, ModelType, OCRVersion

        params = {
            # En dessous de ce score, le texte reconnu est écarté. Un ticket
            # thermique produit beaucoup de lignes moyennement lisibles qu'il
            # vaut mieux garder : le parseur, lui, sait les pondérer.
            "Global.text_score": float(self.options.get("text_score", 0.35)),
            "Global.max_side_len": int(self.options.get("max_side_len", 1800)),
        }
        model_dir = self.options.get("model_dir")
        if model_dir:
            params["Global.model_root_dir"] = model_dir
        threads = int(self.options.get("threads", 0) or 0)
        if threads > 0:
            # Odoo fait déjà tourner plusieurs workers ; laisser ONNX ouvrir
            # autant de threads que de cœurs dans chacun d'eux dégrade le
            # débit global au lieu de l'améliorer.
            params["EngineConfig.onnxruntime.intra_op_num_threads"] = threads
        if ocr_version and lang:
            params["Rec.ocr_version"] = OCRVersion(ocr_version)
            params["Rec.model_type"] = ModelType(self.options.get("model_type", "mobile"))
            params["Rec.lang_type"] = LangRec(lang)
        return params

    def _load(self):
        """Instancie le moteur, charge les modèles et vérifie qu'ils lisent.

        Deux raisons de ne pas se contenter d'instancier :

        * le chargement des modèles est paresseux dans RapidOCR, donc une
          combinaison langue/version inexistante n'échouerait qu'au premier
          vrai ticket, hors de portée du repli ;
        * surtout, un jeu de modèles peut se charger sans erreur et ne rien
          reconnaître du tout. C'est arrivé avec ``latin/PP-OCRv5`` : la
          détection trouvait bien les zones de texte, la reconnaissance ne
          rendait rien, et RapidOCR éliminait alors les boîtes — panne
          parfaitement silencieuse. Un candidat n'est donc retenu que s'il
          relit le ticket de contrôle.
        """
        from rapidocr import RapidOCR

        last_error = None
        warmup = self._warmup_image()
        for ocr_version, lang in self.MODEL_CANDIDATES:
            label = "PP-OCR %s / %s" % (ocr_version or "défaut", lang or "défaut")
            try:
                engine = RapidOCR(params=self._build_params(ocr_version, lang))
                if warmup is not None:
                    words = self._words_from_result(engine(warmup))
                    if not self._warmup_ok(words):
                        last_error = RuntimeError(
                            "%s se charge mais ne relit pas le ticket de contrôle "
                            "(%d mots reconnus)" % (label, len(words)))
                        _logger.warning("%s", last_error)
                        continue
                self._description = label
                _logger.info("Moteur de scan RapidOCR chargé (%s)", label)
                return engine
            except Exception as error:  # noqa: BLE001
                last_error = error
                _logger.warning("Modèles RapidOCR %s/%s indisponibles : %s",
                                ocr_version, lang, error)
        raise RuntimeError("Aucun jeu de modèles RapidOCR utilisable : %s" % last_error)

    @classmethod
    def _warmup_ok(cls, words):
        """Le candidat relit-il assez du ticket de contrôle pour être retenu ?"""
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

    def recognize(self, image, use_cls=None):
        return self._words_from_result(self._get_engine()(image, use_cls=use_cls))

    @staticmethod
    def _words_from_result(result):
        """Traduit la sortie de RapidOCR en mots situés."""
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
                # PP-OCR renvoie un quadrilatère orienté, pas un rectangle :
                # l'inclinaison de la ligne se lit sur son arête supérieure.
                # C'est la mesure de biais la plus directe qui soit, et elle
                # ne coûte rien puisque la donnée est déjà là.
                if len(box) == 4:
                    angle = math.degrees(math.atan2(float(box[1][1] - box[0][1]),
                                                    float(box[1][0] - box[0][0])))
                    if abs(angle) > 45.0:
                        angle = 0.0
            except Exception:  # noqa: BLE001
                left = top = 0.0
                right = bottom = 1.0
            score = float(scores[index]) if index < len(scores) else 0.0
            words.append(OcrWord(text=text, score=score, angle=angle,
                                 left=left, top=top, right=right, bottom=bottom))
        return words


class TesseractEngine(ScanEngine):
    """Repli : Tesseract, sans détection neuronale des zones de texte."""

    code = "tesseract"
    label = "Tesseract (local)"

    @classmethod
    def availability(cls):
        try:
            import pytesseract
        except ImportError:
            return False, "Paquet Python « pytesseract » non installé"
        try:
            version = pytesseract.get_tesseract_version()
        except Exception as error:  # noqa: BLE001
            return False, "Binaire tesseract introuvable (%s)" % error
        return True, "tesseract %s" % version

    @property
    def description(self):
        return self.label

    def recognize(self, image, use_cls=None):
        import pytesseract
        from PIL import Image

        lang = self.options.get("lang") or "fra"
        # --psm 6 : le ticket est traité comme un bloc de texte homogène.
        # Le mode automatique découpe volontiers un ticket en colonnes et
        # mélange l'ordre de lecture des lignes.
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
# Ordre de préférence quand la configuration est sur « automatique ».
AUTO_ORDER = [RapidOcrEngine.code, TesseractEngine.code]

# Les modèles pèsent plusieurs dizaines de mégaoctets et leur chargement
# coûte 1 à 3 secondes : on garde une instance par jeu d'options et par
# processus worker.
_ENGINE_CACHE = {}
_CACHE_LOCK = threading.Lock()


def _cache_key(code, options):
    return (code,) + tuple(sorted((k, v) for k, v in options.items()))


def get_engine(code, **options):
    """Renvoie l'instance partagée du moteur ``code`` pour ces options."""
    if code not in ENGINE_CLASSES:
        raise ValueError("Moteur de scan inconnu : %s" % code)
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
    """Choisit un moteur disponible, en partant du moteur préféré.

    ``preferred`` vaut ``auto`` ou un code de moteur. Renvoie l'instance ;
    lève ``RuntimeError`` si aucun moteur n'est utilisable, avec le détail
    de ce qui manque pour chacun.
    """
    candidates = AUTO_ORDER if preferred in (None, "", "auto") else [preferred]
    problems = []
    for code in candidates:
        engine_class = ENGINE_CLASSES.get(code)
        if engine_class is None:
            problems.append("%s : moteur inconnu" % code)
            continue
        available, message = engine_class.availability()
        if available:
            return get_engine(code, **options)
        problems.append("%s : %s" % (engine_class.label, message))
    raise RuntimeError("Aucun moteur OCR disponible. " + " / ".join(problems))


def engines_status():
    """État de chaque moteur, pour l'écran de configuration."""
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
    """Charge le moteur et lit une image de test. Renvoie un dictionnaire.

    Sert autant de diagnostic que de préchauffage : c'est ici que les
    modèles sont téléchargés la première fois, plutôt que devant
    l'utilisateur qui vient de photographier son ticket.
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
