# -*- coding: utf-8 -*-
"""Pré-traitement de la photo : détection du ticket, recadrage, redressage.

C'est l'étape qui fait la plus grande différence de qualité sur une photo
prise à main levée : un moteur OCR, aussi bon soit-il, lit mal un ticket
photographié de biais, penché et noyé au milieu d'une table.

Toutes les dépendances lourdes (OpenCV, Pillow, pdf2image) sont importées
de façon défensive : si elles manquent, le module reste importable et la
chaîne se rabat sur l'image brute, en le signalant.
"""
import io
import math
import logging

_logger = logging.getLogger(__name__)

# Les erreurs d'import sont conservées plutôt qu'avalées : sur un serveur,
# « module manquant » et « bibliothèque système absente » se soignent très
# différemment, et seule l'exception d'origine permet de les distinguer.
_IMPORT_ERRORS = {}

try:
    import numpy as np
except Exception as error:  # noqa: BLE001 - dépend de l'environnement serveur
    np = None
    _IMPORT_ERRORS['numpy'] = error

try:
    import cv2
except Exception as error:  # noqa: BLE001
    cv2 = None
    _IMPORT_ERRORS['cv2 (opencv-python)'] = error

try:
    from PIL import Image, ImageOps
except Exception as error:  # noqa: BLE001
    Image = ImageOps = None
    _IMPORT_ERRORS['Pillow'] = error

from .types import OcrWord, PreprocessInfo

# Un quadrilatère candidat doit couvrir au moins cette fraction de la photo
# pour être considéré comme « le ticket » et non un détail du décor.
MIN_QUAD_AREA_RATIO = 0.18
# Au-delà, le cadrage est déjà bon : recadrer n'apporterait rien et risquerait
# de rogner un bord du ticket.
SKIP_CROP_AREA_RATIO = 0.97
# Un ticket reste un objet allongé ; ces bornes écartent les faux positifs
# (bord de table, reflet) sans exclure les tickets courts.
MIN_ASPECT, MAX_ASPECT = 0.15, 20.0
# Résolution de travail pour la détection des contours : inutile de chercher
# un quadrilatère sur 12 mégapixels, et c'est 10 fois plus rapide.
DETECTION_MAX_SIDE = 900
# Inclinaison résiduelle corrigée (au-delà, c'est probablement une erreur
# d'analyse plutôt qu'une photo penchée).
MAX_DESKEW_ANGLE = 15.0
# Le redressage mesuré sur le texte reconnu est bien plus fiable que
# l estimation morphologique : il peut viser un ticket posé en diagonale.
MAX_TEXT_DESKEW_ANGLE = 45.0
MIN_DESKEW_ANGLE = 0.3


def dependencies_status():
    """Renvoie (ok, message) sur la disponibilité du pré-traitement."""
    if _IMPORT_ERRORS:
        return False, "Import impossible — " + " / ".join(
            "%s : %r" % (name, error) for name, error in sorted(_IMPORT_ERRORS.items()))
    return True, "numpy %s, OpenCV %s" % (np.__version__, cv2.__version__)


def pdf_first_page_to_image_bytes(data, dpi=200):
    """Convertit la première page d'un PDF en PNG. Renvoie None si impossible."""
    try:
        from pdf2image import convert_from_bytes
    except ImportError:
        _logger.info("pdf2image absent : PDF non converti")
        return None
    try:
        pages = convert_from_bytes(data, dpi=dpi, first_page=1, last_page=1)
    except Exception:
        _logger.warning("Conversion du PDF impossible", exc_info=True)
        return None
    if not pages:
        return None
    buffer = io.BytesIO()
    pages[0].save(buffer, format="PNG")
    return buffer.getvalue()


def load_image(data):
    """Décode des octets en image BGR, en appliquant l'orientation EXIF.

    L'EXIF est essentiel : la plupart des téléphones enregistrent la photo
    dans le sens du capteur et indiquent la rotation en métadonnée. Sans
    cette correction, un ticket sur deux arrive couché.
    """
    if Image is None or np is None:
        raise RuntimeError("Pillow et numpy sont requis pour lire l'image")
    with Image.open(io.BytesIO(data)) as img:
        img = ImageOps.exif_transpose(img)
        img = img.convert("RGB")
        array = np.array(img)
    if cv2 is not None:
        return cv2.cvtColor(array, cv2.COLOR_RGB2BGR)
    return array[:, :, ::-1].copy()


def encode_jpeg(image, quality=88):
    """Encode une image BGR en JPEG."""
    if cv2 is not None:
        ok, buffer = cv2.imencode(".jpg", image, [int(cv2.IMWRITE_JPEG_QUALITY), quality])
        if ok:
            return buffer.tobytes()
    if Image is None:
        raise RuntimeError("Aucun encodeur JPEG disponible")
    pil = Image.fromarray(image[:, :, ::-1])
    buffer = io.BytesIO()
    pil.save(buffer, format="JPEG", quality=quality)
    return buffer.getvalue()


def _order_points(points):
    """Range 4 points dans l'ordre haut-gauche, haut-droit, bas-droit, bas-gauche."""
    ordered = np.zeros((4, 2), dtype="float32")
    total = points.sum(axis=1)
    ordered[0] = points[np.argmin(total)]   # somme minimale -> haut-gauche
    ordered[2] = points[np.argmax(total)]   # somme maximale -> bas-droit
    diff = np.diff(points, axis=1)
    ordered[1] = points[np.argmin(diff)]    # ecart minimal -> haut-droit
    ordered[3] = points[np.argmax(diff)]    # ecart maximal -> bas-gauche
    return ordered


def _quad_area(quad):
    """Aire d'un quadrilatère par la formule du lacet."""
    x = quad[:, 0]
    y = quad[:, 1]
    return 0.5 * abs(np.dot(x, np.roll(y, 1)) - np.dot(y, np.roll(x, 1)))


def _find_quad_by_edges(gray, image_area):
    """Cherche le contour rectangulaire du ticket par détection de bords."""
    blurred = cv2.GaussianBlur(gray, (5, 5), 0)
    edges = cv2.Canny(blurred, 50, 150)
    # Ferme les interruptions du contour : sur un ticket clair posé sur un
    # fond clair, le bord n'est jamais détecté d'un seul tenant.
    kernel = cv2.getStructuringElement(cv2.MORPH_RECT, (7, 7))
    edges = cv2.morphologyEx(edges, cv2.MORPH_CLOSE, kernel)

    contours, _ = cv2.findContours(edges, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
    for contour in sorted(contours, key=cv2.contourArea, reverse=True)[:6]:
        if cv2.contourArea(contour) < MIN_QUAD_AREA_RATIO * image_area:
            break  # les suivants sont encore plus petits
        perimeter = cv2.arcLength(contour, True)
        approx = cv2.approxPolyDP(contour, 0.02 * perimeter, True)
        if len(approx) == 4 and cv2.isContourConvex(approx):
            return approx.reshape(4, 2).astype("float32")
    return None


def _find_quad_by_brightness(gray, image_area):
    """Repli : isole la zone claire du ticket et prend son rectangle englobant.

    Fonctionne là où la détection de bords échoue (ticket froissé, bord
    partiellement dans l'ombre), au prix d'un cadrage un peu plus large.
    """
    blurred = cv2.GaussianBlur(gray, (7, 7), 0)
    _, mask = cv2.threshold(blurred, 0, 255, cv2.THRESH_BINARY + cv2.THRESH_OTSU)
    kernel = cv2.getStructuringElement(cv2.MORPH_RECT, (9, 9))
    mask = cv2.morphologyEx(mask, cv2.MORPH_CLOSE, kernel, iterations=2)
    mask = cv2.morphologyEx(mask, cv2.MORPH_OPEN, kernel)

    contours, _ = cv2.findContours(mask, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
    if not contours:
        return None
    largest = max(contours, key=cv2.contourArea)
    if cv2.contourArea(largest) < MIN_QUAD_AREA_RATIO * image_area:
        return None
    box = cv2.boxPoints(cv2.minAreaRect(largest))
    return np.array(box, dtype="float32")


def detect_receipt_quad(image):
    """Renvoie les 4 coins du ticket dans l'image, ou None."""
    height, width = image.shape[:2]
    scale = min(1.0, DETECTION_MAX_SIDE / float(max(height, width)))
    if scale < 1.0:
        small = cv2.resize(image, None, fx=scale, fy=scale, interpolation=cv2.INTER_AREA)
    else:
        small = image
    gray = cv2.cvtColor(small, cv2.COLOR_BGR2GRAY)
    small_area = small.shape[0] * small.shape[1]

    quad = _find_quad_by_edges(gray, small_area)
    if quad is None:
        quad = _find_quad_by_brightness(gray, small_area)
    if quad is None:
        return None

    area_ratio = _quad_area(quad) / float(small_area)
    if area_ratio < MIN_QUAD_AREA_RATIO or area_ratio > SKIP_CROP_AREA_RATIO:
        return None
    # Les coordonnées ont été trouvées sur l'image réduite : on les remet à
    # l'échelle pour découper dans la pleine résolution.
    return quad / scale if scale < 1.0 else quad


def four_point_transform(image, quad):
    """Redresse la perspective : le quadrilatère devient un rectangle."""
    ordered = _order_points(quad)
    (top_left, top_right, bottom_right, bottom_left) = ordered

    width = int(max(np.linalg.norm(bottom_right - bottom_left),
                    np.linalg.norm(top_right - top_left)))
    height = int(max(np.linalg.norm(top_right - bottom_right),
                     np.linalg.norm(top_left - bottom_left)))
    if width < 40 or height < 40:
        return None

    aspect = height / float(width)
    if not (MIN_ASPECT <= aspect <= MAX_ASPECT):
        return None

    destination = np.array([
        [0, 0],
        [width - 1, 0],
        [width - 1, height - 1],
        [0, height - 1],
    ], dtype="float32")
    matrix = cv2.getPerspectiveTransform(ordered, destination)
    return cv2.warpPerspective(image, matrix, (width, height), flags=cv2.INTER_CUBIC,
                               borderMode=cv2.BORDER_REPLICATE)


def estimate_skew_angle(image):
    """Estime l'inclinaison résiduelle des lignes de texte, en degrés."""
    gray = cv2.cvtColor(image, cv2.COLOR_BGR2GRAY)
    height, width = gray.shape[:2]
    scale = min(1.0, DETECTION_MAX_SIDE / float(max(height, width)))
    if scale < 1.0:
        gray = cv2.resize(gray, None, fx=scale, fy=scale, interpolation=cv2.INTER_AREA)
        height, width = gray.shape[:2]

    binary = cv2.adaptiveThreshold(gray, 255, cv2.ADAPTIVE_THRESH_GAUSSIAN_C,
                                   cv2.THRESH_BINARY_INV, 25, 15)
    # Souder les caractères d'une même ligne pour raisonner sur des lignes
    # entières plutôt que sur des lettres isolées.
    kernel = cv2.getStructuringElement(cv2.MORPH_RECT, (max(width // 30, 9), 3))
    merged = cv2.dilate(binary, kernel, iterations=1)

    contours, _ = cv2.findContours(merged, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
    angles = []
    for contour in contours:
        (_, _), (rect_width, rect_height), angle = cv2.minAreaRect(contour)
        if rect_width < 0.15 * width or min(rect_width, rect_height) < 4:
            continue
        if rect_width < rect_height:  # rectangle décrit « debout » par OpenCV
            angle += 90.0
        if -45.0 <= angle <= 45.0:
            angles.append(angle)

    if len(angles) < 3:
        return 0.0
    return float(np.median(angles))


def deskew_image(image):
    """Redresse l'image et vérifie que le résultat est meilleur.

    La convention de signe de ``cv2.minAreaRect`` a changé entre les
    versions d'OpenCV : plutôt que de parier dessus, on essaie la rotation
    puis son opposée et on ne garde que celle qui réduit réellement
    l'inclinaison mesurée. Si aucune n'améliore, on ne touche à rien.

    Renvoie (image, angle appliqué).
    """
    angle = estimate_skew_angle(image)
    if not (MIN_DESKEW_ANGLE < abs(angle) <= MAX_DESKEW_ANGLE):
        return image, 0.0
    for candidate in (angle, -angle):
        corrected = rotate(image, candidate)
        if abs(estimate_skew_angle(corrected)) < abs(angle) * 0.5:
            return corrected, candidate
    return image, 0.0


def rotation_matrix(size, angle):
    """Matrice de rotation et taille du cadre agrandi pour ne rien rogner."""
    width, height = size
    center = (width / 2.0, height / 2.0)
    matrix = cv2.getRotationMatrix2D(center, angle, 1.0)
    cosine, sine = abs(matrix[0, 0]), abs(matrix[0, 1])
    new_width = int(height * sine + width * cosine)
    new_height = int(height * cosine + width * sine)
    matrix[0, 2] += (new_width / 2.0) - center[0]
    matrix[1, 2] += (new_height / 2.0) - center[1]
    return matrix, (new_width, new_height)


def rotate(image, angle):
    """Rotation autour du centre, fond blanc, sans rogner les coins."""
    height, width = image.shape[:2]
    matrix, new_size = rotation_matrix((width, height), angle)
    return cv2.warpAffine(image, matrix, new_size, flags=cv2.INTER_CUBIC,
                          borderMode=cv2.BORDER_CONSTANT, borderValue=(255, 255, 255))


def rotate_words(words, matrix):
    """Suit les boîtes de mots à travers la même rotation que l'image.

    Sans ça, recadrer après un redressage se ferait sur des coordonnées
    périmées — et c'est bien après le redressage qu'il faut recadrer, la
    rotation agrandissant le cadre pour ne rien couper.
    """
    moved = []
    for word in words:
        corners = ((word.left, word.top), (word.right, word.top),
                   (word.right, word.bottom), (word.left, word.bottom))
        xs, ys = [], []
        for x, y in corners:
            xs.append(matrix[0, 0] * x + matrix[0, 1] * y + matrix[0, 2])
            ys.append(matrix[1, 0] * x + matrix[1, 1] * y + matrix[1, 2])
        moved.append(OcrWord(text=word.text, score=word.score,
                             left=min(xs), top=min(ys), right=max(xs), bottom=max(ys)))
    return moved


def rotate_quarters(image, quarters):
    """Rotation par quarts de tour (1 = 90° horaire)."""
    quarters %= 4
    if quarters == 0:
        return image
    codes = {
        1: cv2.ROTATE_90_CLOCKWISE,
        2: cv2.ROTATE_180,
        3: cv2.ROTATE_90_COUNTERCLOCKWISE,
    }
    return cv2.rotate(image, codes[quarters])



def skew_angle_from_words(words, min_width_ratio=0.25, min_boxes=2):
    """Inclinaison médiane lue directement sur les boîtes du détecteur.

    À préférer à :func:`skew_angle_from_lines` : PP-OCR ne détecte souvent
    qu'**une seule boîte par ligne de ticket**, ce qui ne laisse rien à
    régresser et faisait très largement sous-estimer l'angle. L'orientation
    de chaque boîte, elle, est une donnée du détecteur.

    Les boîtes trop courtes sont écartées : sur quelques caractères,
    l'orientation est bruitée.
    """
    oriented = [word for word in words if 1e-6 < abs(word.angle) <= 45.0]
    if len(oriented) < min_boxes:
        return 0.0
    widest = max(word.width for word in oriented)
    kept = [word for word in oriented if word.width >= min_width_ratio * widest] or oriented
    angles = sorted(word.angle for word in kept)
    return angles[len(angles) // 2]


def skew_angle_from_lines(lines, min_words=3, min_lines=2):
    """Inclinaison des lignes de texte, mesurée sur les mots reconnus.

    Bien plus fiable que :func:`estimate_skew_angle`, qui travaille sur
    l'image : une fois le ticket recadré, ses bords de papier entrent dans
    le cadre et sont eux aussi des droites marquées, souvent inclinées
    autrement que l'impression. Ici on ne regarde que le texte.

    L'angle renvoyé se donne tel quel à :func:`rotate` : une rotation de
    ``a`` transforme une pente ``tan(θ)`` en ``tan(θ - a)``, donc corriger
    revient à tourner de l'angle mesuré. Aucune ambiguïté de signe.
    """
    angles = []
    for line in lines:
        if len(line.words) < min_words:
            continue
        xs = [(word.left + word.right) / 2.0 for word in line.words]
        ys = [word.center_y for word in line.words]
        count = len(xs)
        mean_x = sum(xs) / count
        mean_y = sum(ys) / count
        variance = sum((x - mean_x) ** 2 for x in xs)
        if variance <= 0:
            continue
        slope = sum((x - mean_x) * (y - mean_y) for x, y in zip(xs, ys)) / variance
        angles.append(math.degrees(math.atan(slope)))

    if len(angles) < min_lines:
        return 0.0
    angles.sort()
    return angles[len(angles) // 2]


def scale_words(words, factor):
    """Transpose des boîtes mesurées sur une image réduite vers la grande.

    L'orientation, elle, ne change pas : une homothétie ne fait pas pencher
    le texte.
    """
    if factor == 1.0:
        return words
    return [
        OcrWord(text=word.text, score=word.score, angle=word.angle,
                left=word.left * factor, top=word.top * factor,
                right=word.right * factor, bottom=word.bottom * factor)
        for word in words
    ]


def crop_to_text(image, words, margin_ratio=0.035, max_kept_ratio=0.94):
    """Recadre sur l'enveloppe du texte reconnu, avec une marge.

    Complète la détection de contours plutôt qu'elle ne la remplace : un
    ticket blanc posé sur une table claire n'a pas de bord détectable, mais
    la position du texte, elle, est connue sans ambiguïté une fois l'OCR
    passé. Et recadrer sur le texte ne peut pas couper une information
    utile, par construction.

    Renvoie (image, recadrée ou non).
    """
    boxes = [word for word in words if word.text.strip()]
    if cv2 is None or len(boxes) < 3:
        return image, False

    height, width = image.shape[:2]
    left = min(word.left for word in boxes)
    right = max(word.right for word in boxes)
    top = min(word.top for word in boxes)
    bottom = max(word.bottom for word in boxes)

    margin_x = (right - left) * margin_ratio + 8
    margin_y = (bottom - top) * margin_ratio + 8
    x0 = max(int(left - margin_x), 0)
    y0 = max(int(top - margin_y), 0)
    x1 = min(int(right + margin_x), width)
    y1 = min(int(bottom + margin_y), height)

    if x1 - x0 < 40 or y1 - y0 < 40:
        return image, False
    if (x1 - x0) * (y1 - y0) > max_kept_ratio * width * height:
        return image, False  # déjà cadré au plus juste
    return image[y0:y1, x0:x1], True


def limit_size(image, max_side):
    """Réduit l'image si son plus grand côté dépasse max_side."""
    height, width = image.shape[:2]
    longest = max(height, width)
    if max_side and longest > max_side:
        scale = max_side / float(longest)
        return cv2.resize(image, None, fx=scale, fy=scale, interpolation=cv2.INTER_AREA)
    return image


def prepare(data, autocrop=True, deskew=True, max_side=0):
    """Chaîne complète : octets -> image BGR prête pour l'OCR.

    Renvoie (image, PreprocessInfo). Ne lève jamais pour une raison
    cosmétique : si le recadrage échoue, on rend l'image d'origine et on le
    dit dans PreprocessInfo.

    ``max_side`` vaut zéro par défaut, donc aucune réduction : le moteur
    ramène lui-même l'image à sa taille de travail, et réduire ici avant de
    faire pivoter la photo ajouterait un rééchantillonnage qui coûte cher
    sur une impression thermique déjà pâle.
    """
    image = load_image(data)
    info = PreprocessInfo(original_size=(image.shape[1], image.shape[0]))

    if cv2 is None:
        info.final_size = info.original_size
        return image, info

    if autocrop:
        try:
            quad = detect_receipt_quad(image)
            if quad is not None:
                warped = four_point_transform(image, quad)
                if warped is not None:
                    image = warped
                    info.cropped = True
        except Exception:
            _logger.warning("Recadrage automatique impossible", exc_info=True)

    if deskew:
        try:
            image, info.deskew_angle = deskew_image(image)
        except Exception:
            _logger.warning("Redressage impossible", exc_info=True)

    image = limit_size(image, max_side)
    info.final_size = (image.shape[1], image.shape[0])
    info.changed = info.cropped or bool(info.deskew_angle) or info.final_size != info.original_size
    return image, info
