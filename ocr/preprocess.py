# -*- coding: utf-8 -*-
# Copyright 2026 Yves Vallée
# License LGPL-3.0 or later (https://www.gnu.org/licenses/lgpl-3.0).
"""Photo preprocessing: receipt detection, cropping, straightening.

This is the step with the most effect on how well a hand-held photo reads:
an OCR engine reads badly a receipt photographed at an angle, tilted or lost
in the middle of a table.

The heavy dependencies (OpenCV, Pillow, pdf2image) are imported defensively:
if they are missing, the module stays importable and the chain falls back on
the raw image, and says so.
"""
import io
import math
import logging

_logger = logging.getLogger(__name__)

# Import errors are kept rather than ignored: on a server, "missing module"
# and "missing system library" are fixed very differently, and only the
# original exception tells them apart.
_IMPORT_ERRORS = {}

try:
    import numpy as np
except Exception as error:  # noqa: BLE001 - depends on the server environment
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

#: Input limits. An attachment comes from anywhere (phone, mail gateway) and
#: what it declares says nothing about what it costs to decode: a 100
#: megapixel photo or a giant PDF page is enough to saturate a worker.
#:
#: File size, in bytes.
MAX_FILE_BYTES = 25 * 1024 * 1024
#: Beyond this number of pixels, the image is scaled down as it is decoded: a
#: receipt reads very well at 50 megapixels, and more only costs memory.
#: Beyond the hard limit, it is refused (decompression bomb).
MAX_PIXELS = 50_000_000
HARD_MAX_PIXELS = 250_000_000
#: PDF conversion timeout, in seconds.
PDF_TIMEOUT = 30

# A candidate quadrilateral must cover at least this share of the photo to
# count as "the receipt" and not a detail of the background.
MIN_QUAD_AREA_RATIO = 0.18
# Beyond this, the framing is already good: cropping would bring nothing and
# might cut an edge of the receipt.
SKIP_CROP_AREA_RATIO = 0.97
# A receipt is an elongated object; these bounds rule out false positives
# (table edge, reflection) without excluding short receipts.
MIN_ASPECT, MAX_ASPECT = 0.15, 20.0
# Working resolution for edge detection: no need to look for a quadrilateral
# on 12 megapixels, and it is 10 times faster.
DETECTION_MAX_SIDE = 900
# Largest residual tilt corrected (beyond it, it is more likely a detection
# error than a tilted photo).
MAX_DESKEW_ANGLE = 15.0
# Straightening measured on the recognised text is far more reliable than the
# morphological estimate: it can handle a receipt lying diagonally.
MAX_TEXT_DESKEW_ANGLE = 45.0
MIN_DESKEW_ANGLE = 0.3
# Largest gap to the median tilt for a box to count in the average. Beyond
# it, the box is background text, not a line of the receipt.
SKEW_OUTLIER_TOLERANCE = 8.0


def dependencies_status():
    """Return (ok, message) about the availability of the preprocessing."""
    if _IMPORT_ERRORS:
        return False, "Import failed: " + " / ".join(
            "%s: %r" % (name, error) for name, error in sorted(_IMPORT_ERRORS.items()))
    return True, "numpy %s, OpenCV %s" % (np.__version__, cv2.__version__)


def _pdf_dpi(data, dpi):
    """Rendering resolution that fits within the pixel limit.

    An A4 page at 200 dpi is 4 megapixels; a plan page several metres long,
    billions. The size is read from the PDF header, without rendering
    anything.
    """
    try:
        from pdf2image import pdfinfo_from_bytes
        size = pdfinfo_from_bytes(data, timeout=PDF_TIMEOUT).get('Page size', '')
        width, height = (float(part) for part in size.split(' pts')[0].split(' x '))
    except Exception:  # noqa: BLE001 - without the size, render at the requested resolution
        return dpi
    if width <= 0 or height <= 0:
        return dpi
    return max(10, min(dpi, int(72 * math.sqrt(MAX_PIXELS / (width * height)))))


def pdf_page_count(data):
    """Number of pages of a PDF. Returns 1 if the count cannot be read."""
    try:
        from pdf2image import pdfinfo_from_bytes
        return max(1, int(pdfinfo_from_bytes(data, timeout=PDF_TIMEOUT).get('Pages', 1)))
    except Exception:  # noqa: BLE001 - an unreadable PDF counts as a single page
        return 1


def pdf_page_to_image_bytes(data, page, dpi=200):
    """Convert a page of a PDF (1 = the first) to PNG. None if it cannot."""
    try:
        from pdf2image import convert_from_bytes
    except ImportError:
        _logger.info("pdf2image missing: PDF not converted")
        return None
    options = dict(dpi=_pdf_dpi(data, dpi), first_page=page, last_page=page)
    try:
        try:
            pages = convert_from_bytes(data, timeout=PDF_TIMEOUT, **options)
        except TypeError:  # old pdf2image, without a timeout
            pages = convert_from_bytes(data, **options)
    except Exception:
        _logger.warning("PDF conversion failed (page %s)", page, exc_info=True)
        return None
    if not pages:
        return None
    buffer = io.BytesIO()
    pages[0].save(buffer, format="PNG")
    return buffer.getvalue()


def pdf_first_page_to_image_bytes(data, dpi=200):
    """Convert the first page of a PDF to PNG. Returns None on failure."""
    return pdf_page_to_image_bytes(data, 1, dpi=dpi)


def load_image(data):
    """Decode bytes into a BGR image, applying the EXIF orientation.

    EXIF matters: most phones record the photo in the sensor's orientation
    and give the rotation as metadata. Without this correction, one receipt
    in two arrives lying down.

    The number of pixels is read from the header, before any decoding: an
    image beyond ``MAX_PIXELS`` is scaled down while decoding (a JPEG decodes
    directly at a reduced size), and beyond ``HARD_MAX_PIXELS`` it is refused.
    """
    if Image is None or np is None:
        raise RuntimeError("Pillow and numpy are required to read the image")
    try:
        img = Image.open(io.BytesIO(data))
    except OSError:
        # Pillow built without WebP (package of some distributions): OpenCV
        # can read it. Android phones often share their photos in this
        # format.
        image = decode_with_opencv(data)
        if image is None:
            raise
        return image
    with img:
        pixels = img.width * img.height
        if pixels > HARD_MAX_PIXELS:
            raise ValueError("Image too large: %d megapixels (limit %d)." % (
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
    """Decode an image Pillow does not recognise; ``None`` if OpenCV fails too.

    OpenCV reads the whole image before knowing its size: the pixel limits
    apply after decoding.
    """
    if cv2 is None:
        return None
    image = cv2.imdecode(np.frombuffer(data, dtype=np.uint8), cv2.IMREAD_COLOR)
    if image is None:
        return None
    height, width = image.shape[:2]
    pixels = width * height
    if pixels > HARD_MAX_PIXELS:
        raise ValueError("Image too large: %d megapixels (limit %d)." % (
            pixels // 1_000_000, HARD_MAX_PIXELS // 1_000_000))
    if pixels > MAX_PIXELS:
        ratio = math.sqrt(MAX_PIXELS / pixels)
        image = cv2.resize(image, (int(width * ratio), int(height * ratio)),
                           interpolation=cv2.INTER_AREA)
    return image


def encode_jpeg(image, quality=88):
    """Encode a BGR image as JPEG."""
    if cv2 is not None:
        ok, buffer = cv2.imencode(".jpg", image, [int(cv2.IMWRITE_JPEG_QUALITY), quality])
        if ok:
            return buffer.tobytes()
    if Image is None:
        raise RuntimeError("No JPEG encoder available")
    pil = Image.fromarray(image[:, :, ::-1])
    buffer = io.BytesIO()
    pil.save(buffer, format="JPEG", quality=quality)
    return buffer.getvalue()


def _order_points(points):
    """Order 4 points as top left, top right, bottom right, bottom left."""
    ordered = np.zeros((4, 2), dtype="float32")
    total = points.sum(axis=1)
    ordered[0] = points[np.argmin(total)]   # smallest sum -> top left
    ordered[2] = points[np.argmax(total)]   # largest sum -> bottom right
    diff = np.diff(points, axis=1)
    ordered[1] = points[np.argmin(diff)]    # smallest difference -> top right
    ordered[3] = points[np.argmax(diff)]    # largest difference -> bottom left
    return ordered


def _quad_area(quad):
    """Area of a quadrilateral, by the shoelace formula."""
    x = quad[:, 0]
    y = quad[:, 1]
    return 0.5 * abs(np.dot(x, np.roll(y, 1)) - np.dot(y, np.roll(x, 1)))


def _find_quad_by_edges(gray, image_area):
    """Look for the rectangular outline of the receipt by edge detection."""
    blurred = cv2.GaussianBlur(gray, (5, 5), 0)
    edges = cv2.Canny(blurred, 50, 150)
    # Close the gaps in the outline: on a light receipt on a light
    # background, the edge is not detected in one piece.
    kernel = cv2.getStructuringElement(cv2.MORPH_RECT, (7, 7))
    edges = cv2.morphologyEx(edges, cv2.MORPH_CLOSE, kernel)

    contours, _ = cv2.findContours(edges, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
    for contour in sorted(contours, key=cv2.contourArea, reverse=True)[:6]:
        if cv2.contourArea(contour) < MIN_QUAD_AREA_RATIO * image_area:
            break  # the next ones are even smaller
        perimeter = cv2.arcLength(contour, True)
        approx = cv2.approxPolyDP(contour, 0.02 * perimeter, True)
        if len(approx) == 4 and cv2.isContourConvex(approx):
            return approx.reshape(4, 2).astype("float32")
    return None


def _find_quad_by_brightness(gray, image_area):
    """Fallback: isolate the light area of the receipt and take its bounding box.

    Works where edge detection fails (crumpled receipt, edge partly in the
    shade), at the cost of a slightly wider frame.
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


#: A second light area counts as another receipt from this share of the
#: image, and this share of the largest one.
SECOND_RECEIPT_AREA_RATIO = 0.10
SECOND_RECEIPT_RELATIVE_AREA = 0.40


def _has_several_receipts(gray, image_area):
    """Tell whether the photo holds two receipts side by side.

    A pay station receipt and the card slip of its payment get photographed
    together. Cropping to the larger one would cut the other, with its VAT or
    its total. Cropping to the recognised text keeps everything read.
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
    """Return the 4 corners of the detected receipt, or None."""
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
    # The coordinates were found on the reduced image: they are scaled back
    # up to cut in the full resolution.
    return quad / scale if scale < 1.0 else quad


def four_point_transform(image, quad):
    """Correct the perspective: the quadrilateral becomes a rectangle."""
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
    """Estimate the residual tilt of the text lines, in degrees."""
    gray = cv2.cvtColor(image, cv2.COLOR_BGR2GRAY)
    height, width = gray.shape[:2]
    scale = min(1.0, DETECTION_MAX_SIDE / float(max(height, width)))
    if scale < 1.0:
        gray = cv2.resize(gray, None, fx=scale, fy=scale, interpolation=cv2.INTER_AREA)
        height, width = gray.shape[:2]

    binary = cv2.adaptiveThreshold(gray, 255, cv2.ADAPTIVE_THRESH_GAUSSIAN_C,
                                   cv2.THRESH_BINARY_INV, 25, 15)
    # The characters of a line are merged, to reason on whole lines rather
    # than on single letters.
    kernel = cv2.getStructuringElement(cv2.MORPH_RECT, (max(width // 30, 9), 3))
    merged = cv2.dilate(binary, kernel, iterations=1)

    contours, _ = cv2.findContours(merged, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
    angles = []
    for contour in contours:
        (_, _), (rect_width, rect_height), angle = cv2.minAreaRect(contour)
        # OpenCV describes the same line sometimes lying, sometimes standing,
        # and its angle convention changed from one version to another. The
        # computation therefore works on the long side, however it is
        # returned: that side follows the text line. Filtering on the raw
        # "width" wrongly dropped every line described as standing.
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
    """Straighten the image and check that the result is better.

    The sign convention of ``cv2.minAreaRect`` changed between OpenCV
    versions. The rotation is therefore tried both ways, and only the one
    that really reduces the measured tilt is kept. If neither improves, the
    image stays unchanged.

    Returns (image, applied angle).
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
    """Rotation matrix and size of the frame enlarged so as to cut nothing."""
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
    """Rotation about the centre, white background, without cutting the corners."""
    height, width = image.shape[:2]
    matrix, new_size = rotation_matrix((width, height), angle)
    return cv2.warpAffine(image, matrix, new_size, flags=cv2.INTER_CUBIC,
                          borderMode=cv2.BORDER_CONSTANT, borderValue=(255, 255, 255))


def rotate_words(words, matrix, angle=0.0):
    """Take the word boxes through the same rotation as the image.

    Without it, cropping after straightening would use stale coordinates.
    Yet cropping must come after straightening, as the rotation enlarges the
    frame so as to cut nothing.
    """
    moved = []
    for word in words:
        residual = normalize_angle(word.angle - angle)
        extents = _true_extents(word)
        if extents:
            # The upright rectangle of a tilted word spills over its
            # neighbours; rotating its corners would inflate it further and
            # the lines would mix. The computation starts again from the real
            # length and thickness of the word, placed at its new angle.
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
    """Length and thickness of a tilted word, from its upright rectangle.

    A word of length L and thickness T, tilted by a, fills an upright
    rectangle of L·cos a + T·sin a by L·sin a + T·cos a. Both relations are
    inverted here. Beyond 30°, the inversion becomes unstable (it divides by
    cos 2a): the function returns ``None`` and the corners are kept.
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
    """Apply to the boxes the quarter turn applied to the image.

    The text does not change: the engine straightens each detected box
    before reading it, whatever its orientation in the photo. Only the
    positions need correcting, which lets the quarter turn be chosen after
    the reading, without paying for a second one.

    The tilt is not touched either: it is defined modulo 90°, so a quarter
    turn leaves it unchanged.
    """
    quarters %= 4
    if not quarters:
        return list(words)

    turned = []
    for word in words:
        if quarters == 1:  # clockwise: (x, y) -> (height - y, x)
            box = (height - word.bottom, word.left, height - word.top, word.right)
        elif quarters == 2:
            box = (width - word.right, height - word.bottom,
                   width - word.left, height - word.top)
        else:  # anticlockwise: (x, y) -> (y, width - x)
            box = (word.top, width - word.right, word.bottom, width - word.left)
        turned.append(OcrWord(text=word.text, score=word.score,
                              left=box[0], top=box[1], right=box[2], bottom=box[3],
                              angle=normalize_angle(word.angle + 90.0 * quarters)))
    return turned


def normalize_angle(angle):
    """Bring a direction back into (-90, 90].

    A line and the same line followed backwards point in the same
    direction: text angles are therefore counted modulo 180°.
    """
    return ((angle + 90.0) % 180.0) - 90.0


def horizontal_text_score(words):
    """Positive if the lines are lying, negative if they are standing.

    This is the only clue that really tells a quarter turn: the engine
    straightens each box before reading it and so reads just as well in all
    four directions, so comparing recognition scores cannot decide.

    The direction the detector gives each box is read, weighted by its
    length: a whole line is a far better witness than a fragment of a few
    characters. A line at exactly 45° does not vote.
    """
    total = 0.0
    for word in words:
        length = max(word.width, word.height)
        total += length * math.cos(math.radians(2.0 * word.angle))
    return total


def rotate_quarters(image, quarters):
    """Rotation by quarter turns (1 = 90° clockwise)."""
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
    """Tilt of a box, brought back modulo 90° into (-45, 45].

    The boxes carry the text direction in (-90, 90], which tells whether the
    receipt is lying or standing. For straightening alone, that distinction
    is useless (a quarter turn handles it): only the remainder counts.
    """
    return ((word.angle + 45.0) % 90.0) - 45.0


def text_inliers(words, tolerance=SKEW_OUTLIER_TOLERANCE):
    """Boxes whose tilt follows that of the whole.

    Two uses: computing a straightening angle, and framing the receipt. In
    both cases, the intruders skew the result: text printed on the back of
    the receipt and seen through it, characters of the background, doubtful
    readings. They lean any which way, while the lines of one receipt share a
    tilt within a few degrees.

    The median is the reference, as these intruders do not affect it, and the
    gap to that median singles them out.
    """
    if len(words) < 3:
        return list(words)
    angles = sorted(text_angle(word) for word in words)
    median = angles[len(angles) // 2]
    return [word for word in words
            if abs(text_angle(word) - median) <= tolerance] or list(words)


def skew_angle_from_words(words, min_width_ratio=0.25, min_boxes=2):
    """Average tilt of the lines, read on the detector's boxes.

    Preferred to :func:`skew_angle_from_lines`: PP-OCR often detects only
    **a single box per receipt line**, which leaves nothing to regress and
    made the angle largely underestimated. The orientation of each box is
    given by the detector.

    Perspective makes the angle vary from one line to the next: the average
    is therefore preferred to a single value. It is weighted by the box
    length (a whole line gives a far safer angle than a fragment of a few
    characters) and cleared of its intruders.
    """
    oriented = text_inliers(words)
    if len(oriented) < min_boxes:
        return 0.0
    # The box length, measured along the text: on a tilted line, the width
    # of the bounding frame no longer says anything about it.
    lengths = {id(word): max(word.width, word.height) for word in oriented}
    longest = max(lengths.values())
    kept = [word for word in oriented
            if lengths[id(word)] >= min_width_ratio * longest] or oriented
    total = sum(lengths[id(word)] for word in kept)
    if not total:
        return 0.0
    return sum(text_angle(word) * lengths[id(word)] for word in kept) / total


def skew_angle_from_lines(lines, min_words=3, min_lines=2):
    """Tilt of the text lines, measured on the recognised words.

    Far more reliable than :func:`estimate_skew_angle`, which works on the
    image: once the receipt is cropped, its paper edges come into the frame
    and are strong straight lines too, often tilted differently from the
    print. Here, only the text counts.

    The angle returned is given as is to :func:`rotate`: a rotation of ``a``
    turns a slope ``tan(θ)`` into ``tan(θ - a)``, so correcting means
    rotating by the measured angle. The sign leaves no ambiguity.
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
    """Move boxes vertically, without touching their orientation.

    Used to put the words of several separate images (the pages of a PDF)
    end to end in a single list, without their lines mixing: each page gets
    an offset large enough to stay below the previous one.
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
    """Carry boxes measured on a reduced image over to the large one.

    The orientation does not change: scaling does not tilt the text.
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
    """Crop to the outline of the recognised text, with a margin.

    Complements edge detection rather than replacing it: a white receipt on a
    light table has no detectable edge, but the position of the text is known
    for certain once the OCR has run. Cropping to the text cannot cut useful
    information.

    Returns (image, cropped or not).
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
        return image, False  # already tightly framed
    return image[y0:y1, x0:x1], True


def limit_size(image, max_side):
    """Scale the image down if its longest side exceeds max_side."""
    height, width = image.shape[:2]
    longest = max(height, width)
    if max_side and longest > max_side:
        scale = max_side / float(longest)
        return cv2.resize(image, None, fx=scale, fy=scale, interpolation=cv2.INTER_AREA)
    return image


def prepare(data, autocrop=True, deskew=True, max_side=0):
    """Whole chain: bytes -> BGR image ready for the OCR.

    Returns (image, PreprocessInfo). Raises no exception for a cosmetic
    reason: if cropping fails, the original image is returned and the failure
    is noted in PreprocessInfo.

    ``max_side`` is zero by default, so no reduction: the engine scales the
    image to its working size itself, and scaling here before rotating the
    photo would add a resampling that costs a lot on thermal print that is
    already faded.
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
            _logger.warning("Automatic crop failed", exc_info=True)

    if deskew:
        try:
            image, info.deskew_angle = deskew_image(image)
        except Exception:
            _logger.warning("Straightening failed", exc_info=True)

    image = limit_size(image, max_side)
    info.final_size = (image.shape[1], image.shape[0])
    info.changed = info.cropped or bool(info.deskew_angle) or info.final_size != info.original_size
    return image, info
