# -*- coding: utf-8 -*-
# Copyright 2026 Yves Vallée
# License LGPL-3.0 or later (https://www.gnu.org/licenses/lgpl-3.0).
"""Pré-traitement de la photo : détection du ticket, recadrage, redressage.

C'est l'étape qui a le plus d'effet sur la qualité de lecture d'une photo
prise à main levée : un moteur OCR lit mal un ticket photographié de biais,
penché ou noyé au milieu d'une table.

Les dépendances lourdes (OpenCV, Pillow, pdf2image) sont importées de façon
défensive : si elles manquent, le module reste importable et la chaîne se
rabat sur l'image brute, en le signalant.
"""
import io
import math
import logging

_logger = logging.getLogger(__name__)

# Les erreurs d'import sont conservées plutôt qu'ignorées : sur un serveur,
# « module manquant » et « bibliothèque système absente » se corrigent très
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

#: Plafonds d'entrée. Une pièce jointe arrive de n'importe où (téléphone,
#: passerelle e-mail) et ce qu'elle déclare n'indique pas ce qu'elle coûte à
#: décoder : une photo de 100 mégapixels ou une page PDF géante suffit à
#: saturer un worker.
#:
#: Taille du fichier, en octets.
MAX_FILE_BYTES = 25 * 1024 * 1024
#: Au-delà de ce nombre de pixels, l'image est réduite dès son décodage : un
#: ticket se lit très bien à 50 mégapixels, et plus n'apporte que de la
#: mémoire. Au-delà du plafond dur, elle est refusée (bombe de décompression).
MAX_PIXELS = 50_000_000
HARD_MAX_PIXELS = 250_000_000
#: Délai de conversion d'un PDF, en secondes.
PDF_TIMEOUT = 30

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
# l'estimation morphologique : il peut viser un ticket posé en diagonale.
MAX_TEXT_DESKEW_ANGLE = 45.0
MIN_DESKEW_ANGLE = 0.3
# Écart maximal à l'inclinaison médiane pour qu'une boîte compte dans la
# moyenne. Au-delà, c'est du texte d'arrière-plan, pas une ligne du ticket.
SKEW_OUTLIER_TOLERANCE = 8.0


def dependencies_status():
    """Renvoie (ok, message) sur la disponibilité du pré-traitement."""
    if _IMPORT_ERRORS:
        return False, "Import impossible — " + " / ".join(
            "%s : %r" % (name, error) for name, error in sorted(_IMPORT_ERRORS.items()))
    return True, "numpy %s, OpenCV %s" % (np.__version__, cv2.__version__)


def _pdf_dpi(data, dpi):
    """Résolution de rendu qui tient dans le plafond de pixels.

    Une page A4 à 200 dpi fait 4 mégapixels ; une page de plan de plusieurs
    mètres, des milliards. La taille se lit dans l'en-tête du PDF, sans rien
    rendre.
    """
    try:
        from pdf2image import pdfinfo_from_bytes
        size = pdfinfo_from_bytes(data, timeout=PDF_TIMEOUT).get('Page size', '')
        width, height = (float(part) for part in size.split(' pts')[0].split(' x '))
    except Exception:  # noqa: BLE001 - sans la taille, rendu à la résolution demandée
        return dpi
    if width <= 0 or height <= 0:
        return dpi
    return max(10, min(dpi, int(72 * math.sqrt(MAX_PIXELS / (width * height)))))


def pdf_page_count(data):
    """Nombre de pages d'un PDF. Renvoie 1 si le compte ne se lit pas."""
    try:
        from pdf2image import pdfinfo_from_bytes
        return max(1, int(pdfinfo_from_bytes(data, timeout=PDF_TIMEOUT).get('Pages', 1)))
    except Exception:  # noqa: BLE001 - un PDF illisible compte pour une seule page
        return 1


def pdf_page_to_image_bytes(data, page, dpi=200):
    """Convertit une page d'un PDF (1 = la première) en PNG. None si impossible."""
    try:
        from pdf2image import convert_from_bytes
    except ImportError:
        _logger.info("pdf2image absent : PDF non converti")
        return None
    options = dict(dpi=_pdf_dpi(data, dpi), first_page=page, last_page=page)
    try:
        try:
            pages = convert_from_bytes(data, timeout=PDF_TIMEOUT, **options)
        except TypeError:  # pdf2image ancien, sans délai
            pages = convert_from_bytes(data, **options)
    except Exception:
        _logger.warning("Conversion du PDF (page %s) impossible", page, exc_info=True)
        return None
    if not pages:
        return None
    buffer = io.BytesIO()
    pages[0].save(buffer, format="PNG")
    return buffer.getvalue()


def pdf_first_page_to_image_bytes(data, dpi=200):
    """Convertit la première page d'un PDF en PNG. Renvoie None en cas d'échec."""
    return pdf_page_to_image_bytes(data, 1, dpi=dpi)


def load_image(data):
    """Décode des octets en image BGR, en appliquant l'orientation EXIF.

    L'EXIF est essentiel : la plupart des téléphones enregistrent la photo
    dans le sens du capteur et indiquent la rotation en métadonnée. Sans
    cette correction, un ticket sur deux arrive couché.

    Le nombre de pixels se lit dans l'en-tête, avant tout décodage : une
    image au-delà de ``MAX_PIXELS`` est réduite pendant son décodage (un
    JPEG se décode directement à taille réduite), et au-delà de
    ``HARD_MAX_PIXELS`` elle est refusée.
    """
    if Image is None or np is None:
        raise RuntimeError("Pillow et numpy sont requis pour lire l'image")
    try:
        img = Image.open(io.BytesIO(data))
    except OSError:
        # Pillow compilé sans WebP (paquet de certaines distributions) :
        # OpenCV sait le lire. Les téléphones Android partagent souvent
        # leurs photos dans ce format.
        image = decode_with_opencv(data)
        if image is None:
            raise
        return image
    with img:
        pixels = img.width * img.height
        if pixels > HARD_MAX_PIXELS:
            raise ValueError("Image trop grande : %d mégapixels (plafond %d)." % (
                pixels // 1_000_000, HARD_MAX_PIXELS // 1_000_000))
        if pixels > MAX_PIXELS:
            ratio = math.sqrt(MAX_PIXELS / pixels)
            img.draft('RGB', (int(img.width * ratio) + 1, int(img.height * ratio) + 1))
        img = ImageOps.exif_transpose(img)
        img = img.convert("RGB")
        if img.width * img.height > MAX_PIXELS:
            ratio = math.sqrt(MAX_PIXELS / (img.width * img.height))
            img = img.resize((int(img.width * ratio), int(img.height * ratio)), Image.BILINEAR)
        array = np.array(img)
    if cv2 is not None:
        return cv2.cvtColor(array, cv2.COLOR_RGB2BGR)
    return array[:, :, ::-1].copy()


def decode_with_opencv(data):
    """Décode une image que Pillow ne reconnaît pas ; ``None`` si OpenCV échoue aussi.

    OpenCV lit l'image entière avant d'en connaître la taille : les
    plafonds de pixels s'appliquent après le décodage.
    """
    if cv2 is None:
        return None
    image = cv2.imdecode(np.frombuffer(data, dtype=np.uint8), cv2.IMREAD_COLOR)
    if image is None:
        return None
    height, width = image.shape[:2]
    pixels = width * height
    if pixels > HARD_MAX_PIXELS:
        raise ValueError("Image trop grande : %d mégapixels (plafond %d)." % (
            pixels // 1_000_000, HARD_MAX_PIXELS // 1_000_000))
    if pixels > MAX_PIXELS:
        ratio = math.sqrt(MAX_PIXELS / pixels)
        image = cv2.resize(image, (int(width * ratio), int(height * ratio)),
                           interpolation=cv2.INTER_AREA)
    return image


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
    # fond clair, le bord n'est pas détecté d'un seul tenant.
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


#: Une seconde zone claire compte comme un autre ticket à partir de cette
#: part de l'image, et de cette part de la plus grande.
SECOND_RECEIPT_AREA_RATIO = 0.10
SECOND_RECEIPT_RELATIVE_AREA = 0.40


def _has_several_receipts(gray, image_area):
    """Indique si la photo contient deux justificatifs côte à côte.

    Un ticket de borne et le reçu de carte de son paiement se photographient
    ensemble. Recadrer sur le plus grand rognerait l'autre, avec sa TVA ou
    son total. Le recadrage sur le texte reconnu garde tout ce qui est lu.
    """
    blurred = cv2.GaussianBlur(gray, (7, 7), 0)
    _, mask = cv2.threshold(blurred, 0, 255, cv2.THRESH_BINARY + cv2.THRESH_OTSU)
    kernel = cv2.getStructuringElement(cv2.MORPH_RECT, (9, 9))
    mask = cv2.morphologyEx(mask, cv2.MORPH_CLOSE, kernel, iterations=2)
    mask = cv2.morphologyEx(mask, cv2.MORPH_OPEN, kernel)
    contours, _ = cv2.findContours(mask, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
    areas = sorted((cv2.contourArea(contour) for contour in contours), reverse=True)
    if len(areas) < 2:
        return False
    return (areas[1] >= SECOND_RECEIPT_AREA_RATIO * image_area
            and areas[1] >= SECOND_RECEIPT_RELATIVE_AREA * areas[0])


def detect_receipt_quad(image):
    """Renvoie les 4 coins du ticket détecté, ou None."""
    height, width = image.shape[:2]
    scale = min(1.0, DETECTION_MAX_SIDE / float(max(height, width)))
    if scale < 1.0:
        small = cv2.resize(image, None, fx=scale, fy=scale, interpolation=cv2.INTER_AREA)
    else:
        small = image
    gray = cv2.cvtColor(small, cv2.COLOR_BGR2GRAY)
    small_area = small.shape[0] * small.shape[1]

    if _has_several_receipts(gray, small_area):
        return None

    quad = _find_quad_by_edges(gray, small_area)
    if quad is None:
        quad = _find_quad_by_brightness(gray, small_area)
    if quad is None:
        return None

    area_ratio = _quad_area(quad) / float(small_area)
    if area_ratio < MIN_QUAD_AREA_RATIO or area_ratio > SKIP_CROP_AREA_RATIO:
        return None
    # Les coordonnées ont été trouvées sur l'image réduite : elles sont
    # remises à l'échelle pour découper dans la pleine résolution.
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
    # Les caractères d'une même ligne sont soudés pour raisonner sur des
    # lignes entières plutôt que sur des lettres isolées.
    kernel = cv2.getStructuringElement(cv2.MORPH_RECT, (max(width // 30, 9), 3))
    merged = cv2.dilate(binary, kernel, iterations=1)

    contours, _ = cv2.findContours(merged, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
    angles = []
    for contour in contours:
        (_, _), (rect_width, rect_height), angle = cv2.minAreaRect(contour)
        # OpenCV décrit une même ligne tantôt couchée, tantôt debout, et sa
        # convention d'angle a changé d'une version à l'autre. Le calcul
        # porte donc sur le grand côté, quelle que soit la façon dont il est
        # rendu : c'est lui qui suit la ligne de texte. Filtrer sur la
        # « largeur » brute écartait à tort toute ligne décrite debout.
        length, thickness = max(rect_width, rect_height), min(rect_width, rect_height)
        if length < 0.15 * width or thickness < 4:
            continue
        if rect_width < rect_height:
            angle += 90.0
        angle = normalize_angle(angle)
        if -45.0 <= angle <= 45.0:
            angles.append(angle)

    if len(angles) < 3:
        return 0.0
    return float(np.median(angles))


def deskew_image(image):
    """Redresse l'image et vérifie que le résultat est meilleur.

    La convention de signe de ``cv2.minAreaRect`` a changé entre les
    versions d'OpenCV. La rotation est donc essayée dans les deux sens, et
    seule celle qui réduit réellement l'inclinaison mesurée est conservée.
    Si aucune n'améliore, l'image reste inchangée.

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


def rotate_words(words, matrix, angle=0.0):
    """Suit les boîtes de mots à travers la même rotation que l'image.

    Sans cela, le recadrage après un redressage se ferait sur des coordonnées
    périmées. Or c'est après le redressage qu'il faut recadrer, la rotation
    agrandissant le cadre pour ne rien couper.
    """
    moved = []
    for word in words:
        residual = normalize_angle(word.angle - angle)
        extents = _true_extents(word)
        if extents:
            # Le rectangle droit d'un mot penché déborde sur ses voisins ;
            # tourner ses coins le gonflerait encore et les lignes se
            # mêleraient. Le calcul repart de la vraie longueur et de la
            # vraie épaisseur du mot, replacées à son nouvel angle.
            length, thickness = extents
            cx, cy = (word.left + word.right) / 2.0, (word.top + word.bottom) / 2.0
            nx = matrix[0, 0] * cx + matrix[0, 1] * cy + matrix[0, 2]
            ny = matrix[1, 0] * cx + matrix[1, 1] * cy + matrix[1, 2]
            rad = math.radians(abs(residual))
            half_w = (length * math.cos(rad) + thickness * math.sin(rad)) / 2.0
            half_h = (length * math.sin(rad) + thickness * math.cos(rad)) / 2.0
            moved.append(OcrWord(text=word.text, score=word.score,
                                 left=nx - half_w, top=ny - half_h,
                                 right=nx + half_w, bottom=ny + half_h, angle=residual))
            continue
        corners = ((word.left, word.top), (word.right, word.top),
                   (word.right, word.bottom), (word.left, word.bottom))
        xs, ys = [], []
        for x, y in corners:
            xs.append(matrix[0, 0] * x + matrix[0, 1] * y + matrix[0, 2])
            ys.append(matrix[1, 0] * x + matrix[1, 1] * y + matrix[1, 2])
        moved.append(OcrWord(text=word.text, score=word.score,
                             left=min(xs), top=min(ys), right=max(xs), bottom=max(ys),
                             angle=residual))
    return moved


def _true_extents(word, max_angle=30.0):
    """Longueur et épaisseur d'un mot penché, d'après son rectangle droit.

    Un mot de longueur L et d'épaisseur T, penché de a, occupe un rectangle
    droit de L·cos a + T·sin a sur L·sin a + T·cos a. Ces deux relations
    sont inversées ici. Au-delà de 30°, l'inversion devient instable (elle
    divise par cos 2a) : la fonction renvoie ``None`` et les coins sont
    conservés.
    """
    tilt = abs(word.angle)
    if tilt < 0.5 or tilt > max_angle:
        return None
    rad = math.radians(tilt)
    width, height = word.right - word.left, word.bottom - word.top
    divisor = math.cos(2 * rad)
    length = (width * math.cos(rad) - height * math.sin(rad)) / divisor
    thickness = (height * math.cos(rad) - width * math.sin(rad)) / divisor
    if length <= 0 or thickness <= 0:
        return None
    return length, thickness


def rotate_words_quarters(words, quarters, width, height):
    """Fait subir aux boîtes le quart de tour appliqué à l'image.

    Le texte ne bouge pas : le moteur redresse chaque boîte détectée avant
    de la lire, quelle que soit son orientation dans la photo. Seuls les
    emplacements sont à corriger, ce qui permet de choisir le quart de tour
    après la lecture, sans en payer une seconde.

    L'inclinaison n'est pas touchée non plus : elle est définie modulo 90°,
    et un quart de tour la laisse donc inchangée.
    """
    quarters %= 4
    if not quarters:
        return list(words)

    turned = []
    for word in words:
        if quarters == 1:  # horaire : (x, y) -> (hauteur - y, x)
            box = (height - word.bottom, word.left, height - word.top, word.right)
        elif quarters == 2:
            box = (width - word.right, height - word.bottom,
                   width - word.left, height - word.top)
        else:  # anti-horaire : (x, y) -> (y, largeur - x)
            box = (word.top, width - word.right, word.bottom, width - word.left)
        turned.append(OcrWord(text=word.text, score=word.score,
                              left=box[0], top=box[1], right=box[2], bottom=box[3],
                              angle=normalize_angle(word.angle + 90.0 * quarters)))
    return turned


def normalize_angle(angle):
    """Ramène une direction dans (-90, 90].

    Une ligne et la même ligne parcourue à l'envers pointent dans la même
    direction : les angles de texte se comptent donc modulo 180°.
    """
    return ((angle + 90.0) % 180.0) - 90.0


def horizontal_text_score(words):
    """Positif si les lignes sont couchées, négatif si elles sont debout.

    C'est le seul indice qui distingue vraiment un quart de tour : le
    moteur redresse chaque boîte avant de la lire et lit donc aussi bien
    dans les quatre sens, si bien que comparer les scores de reconnaissance
    ne permet pas de trancher.

    La direction que le détecteur donne à chaque boîte est lue, pondérée par
    sa longueur : une ligne entière est un bien meilleur témoin qu'un
    fragment de quelques caractères. Une ligne à 45° pile ne vote pas.
    """
    total = 0.0
    for word in words:
        length = max(word.width, word.height)
        total += length * math.cos(math.radians(2.0 * word.angle))
    return total


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



def text_angle(word):
    """Inclinaison d'une boîte, ramenée modulo 90° dans (-45, 45].

    Les boîtes portent la direction du texte dans (-90, 90], ce qui indique
    si le ticket est couché ou debout. Pour le seul redressage, cette
    distinction est inutile (un quart de tour s'en charge) : seul le résidu
    compte.
    """
    return ((word.angle + 45.0) % 90.0) - 45.0


def text_inliers(words, tolerance=SKEW_OUTLIER_TOLERANCE):
    """Boîtes dont l'inclinaison suit celle de l'ensemble.

    Deux usages : calculer un angle de redressage, et délimiter le ticket.
    Dans les deux cas, les intrus faussent le résultat : texte imprimé au dos
    du ticket et vu par transparence, caractères du décor, lecture douteuse.
    Ils penchent n'importe comment, alors que les lignes d'un même ticket
    partagent une inclinaison à quelques degrés près.

    La médiane sert de repère, car elle est insensible à ces intrus, et
    l'écart à cette médiane les désigne.
    """
    if len(words) < 3:
        return list(words)
    angles = sorted(text_angle(word) for word in words)
    median = angles[len(angles) // 2]
    return [word for word in words
            if abs(text_angle(word) - median) <= tolerance] or list(words)


def skew_angle_from_words(words, min_width_ratio=0.25, min_boxes=2):
    """Inclinaison moyenne des lignes, lue sur les boîtes du détecteur.

    À préférer à :func:`skew_angle_from_lines` : PP-OCR ne détecte souvent
    qu'**une seule boîte par ligne de ticket**, ce qui ne laisse rien à
    régresser et faisait très largement sous-estimer l'angle. L'orientation
    de chaque boîte est une donnée du détecteur.

    La perspective fait varier l'angle d'une ligne à l'autre : la moyenne est
    donc préférée à une valeur unique. Elle est pondérée par la longueur des
    boîtes (une ligne entière donne un angle bien plus sûr qu'un fragment de
    quelques caractères) et débarrassée de ses intrus.
    """
    oriented = text_inliers(words)
    if len(oriented) < min_boxes:
        return 0.0
    # La longueur de la boîte, mesurée le long du texte : sur une ligne
    # penchée, la largeur du cadre englobant n'en dit plus rien.
    lengths = {id(word): max(word.width, word.height) for word in oriented}
    longest = max(lengths.values())
    kept = [word for word in oriented
            if lengths[id(word)] >= min_width_ratio * longest] or oriented
    total = sum(lengths[id(word)] for word in kept)
    if not total:
        return 0.0
    return sum(text_angle(word) * lengths[id(word)] for word in kept) / total


def skew_angle_from_lines(lines, min_words=3, min_lines=2):
    """Inclinaison des lignes de texte, mesurée sur les mots reconnus.

    Bien plus fiable que :func:`estimate_skew_angle`, qui travaille sur
    l'image : une fois le ticket recadré, ses bords de papier entrent dans
    le cadre et sont eux aussi des droites marquées, souvent inclinées
    autrement que l'impression. Ici, seul le texte est pris en compte.

    L'angle renvoyé se donne tel quel à :func:`rotate` : une rotation de
    ``a`` transforme une pente ``tan(θ)`` en ``tan(θ - a)``, donc corriger
    revient à tourner de l'angle mesuré. Le signe ne prête à aucune
    ambiguïté.
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


def shift_words(words, dy):
    """Décale des boîtes verticalement, sans toucher à leur orientation.

    Sert à mettre bout à bout les mots de plusieurs images distinctes (les
    pages d'un PDF) dans une seule liste, sans que leurs lignes se mêlent :
    chaque page reçoit un décalage assez grand pour rester sous la
    précédente.
    """
    if not dy:
        return words
    return [
        OcrWord(text=word.text, score=word.score, angle=word.angle,
                left=word.left, right=word.right,
                top=word.top + dy, bottom=word.bottom + dy)
        for word in words
    ]


def scale_words(words, factor):
    """Transpose des boîtes mesurées sur une image réduite vers la grande.

    L'orientation ne change pas : une homothétie n'incline pas le texte.
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

    Complète la détection de contours plutôt que de la remplacer : un
    ticket blanc posé sur une table claire n'a pas de bord détectable, mais
    la position du texte est connue sans ambiguïté une fois l'OCR passé.
    Recadrer sur le texte ne peut pas couper une information utile.

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

    Renvoie (image, PreprocessInfo). Ne lève pas d'exception pour une
    raison cosmétique : si le recadrage échoue, l'image d'origine est rendue
    et l'échec est indiqué dans PreprocessInfo.

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
