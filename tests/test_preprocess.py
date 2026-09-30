# -*- coding: utf-8 -*-
# Copyright 2026 T.T.C. SAS
# License LGPL-3.0 or later (https://www.gnu.org/licenses/lgpl-3.0).
"""Tests of the image preprocessing.

The tests build a fake receipt photo (a tilted white rectangle on a dark
background) and check that the chain finds it, cuts it out and straightens
it. They are skipped if OpenCV is not installed, so as not to fail the suite
on a server that only uses Tesseract.
"""
from odoo.tests import common, tagged

from ..ocr import preprocess
from ..ocr.types import OcrWord


def make_words(angle=0.0, count=5):
    """Five lines of text, all tilted by the same angle."""
    return [
        OcrWord(text="ligne", score=0.9, angle=angle,
                left=0.0, top=index * 30.0, right=200.0, bottom=index * 30.0 + 20.0)
        for index in range(count)
    ]


@tagged('post_install', '-at_install')
class TestPreprocess(common.TransactionCase):

    def setUp(self):
        super().setUp()
        ok, message = preprocess.dependencies_status()
        if not ok:
            self.skipTest(message)

    def _receipt_photo(self, angle=0.0, margin=120):
        """Fake photo: textured white receipt on a dark background."""
        import cv2
        import numpy as np

        photo = np.full((900, 700, 3), 40, dtype=np.uint8)
        ticket = np.full((640, 320, 3), 245, dtype=np.uint8)
        for row in range(60, 600, 40):
            cv2.line(ticket, (30, row), (290, row), (30, 30, 30), 3)
        if angle:
            ticket = preprocess.rotate(ticket, angle)
        height, width = ticket.shape[:2]
        top, left = margin, (photo.shape[1] - width) // 2
        photo[top:top + height, left:left + width] = ticket
        return photo

    def test_order_points(self):
        import numpy as np

        scrambled = np.array([[10, 90], [90, 10], [10, 10], [90, 90]], dtype="float32")
        ordered = preprocess._order_points(scrambled)
        self.assertEqual(ordered[0].tolist(), [10, 10])   # top left
        self.assertEqual(ordered[1].tolist(), [90, 10])   # top right
        self.assertEqual(ordered[2].tolist(), [90, 90])   # bottom right
        self.assertEqual(ordered[3].tolist(), [10, 90])   # bottom left

    def test_an_outline_far_from_a_rectangle_is_refused(self):
        """Receipt merged with a white menu at its top right corner."""
        import numpy as np

        perspective = np.array([[120, 80], [880, 60], [940, 1900], [60, 1880]], dtype="float32")
        self.assertTrue(preprocess._square_enough(perspective))
        merged = np.array([[371, 295], [1694, 688], [1340, 2385], [371, 2413]], dtype="float32")
        self.assertFalse(preprocess._square_enough(merged))

    def test_detect_and_crop_receipt(self):
        """The receipt is found in the photo and cut out."""
        photo = self._receipt_photo()
        quad = preprocess.detect_receipt_quad(photo)
        self.assertIsNotNone(quad, "the receipt was not detected in the photo")

        cropped = preprocess.four_point_transform(photo, quad)
        self.assertIsNotNone(cropped)
        # The result must be clearly smaller than the original photo and stay
        # taller than wide.
        self.assertLess(cropped.shape[0] * cropped.shape[1],
                        photo.shape[0] * photo.shape[1] * 0.75)
        self.assertGreater(cropped.shape[0], cropped.shape[1])

    def test_prepare_reports_what_it_did(self):
        import cv2

        photo = self._receipt_photo()
        ok, encoded = cv2.imencode(".jpg", photo)
        self.assertTrue(ok)

        image, info = preprocess.prepare(encoded.tobytes())
        self.assertTrue(info.cropped, "the crop should have happened")
        self.assertTrue(info.changed)
        self.assertEqual(info.original_size, (photo.shape[1], photo.shape[0]))
        self.assertEqual(info.final_size, (image.shape[1], image.shape[0]))

    def test_prepare_leaves_a_clean_scan_alone(self):
        """An image already framed on the receipt must not be cut."""
        import cv2
        import numpy as np

        scan = np.full((800, 400, 3), 245, dtype=np.uint8)
        for row in range(40, 760, 40):
            cv2.line(scan, (30, row), (370, row), (30, 30, 30), 3)
        ok, encoded = cv2.imencode(".png", scan)
        self.assertTrue(ok)

        image, info = preprocess.prepare(encoded.tobytes())
        self.assertFalse(info.cropped)
        self.assertEqual(image.shape[:2], scan.shape[:2])

    def test_skew_is_measured_and_corrected(self):
        """The tilt is measured, then really cancelled.

        The test checks the result and not the sign of the angle, which
        OpenCV's convention does not guarantee from one version to the next.
        """
        import cv2
        import numpy as np

        page = np.full((600, 600, 3), 255, dtype=np.uint8)
        for row in range(80, 520, 40):
            cv2.line(page, (80, row), (520, row), (20, 20, 20), 4)
        tilted = preprocess.rotate(page, -6.0)

        self.assertAlmostEqual(abs(preprocess.estimate_skew_angle(tilted)), 6.0, delta=1.0)

        corrected, applied = preprocess.deskew_image(tilted)
        self.assertTrue(applied, "no correction was applied")
        self.assertLess(abs(preprocess.estimate_skew_angle(corrected)), 1.5)

    def test_skew_angle_from_words(self):
        """The angle is read on the orientation of the detector's boxes.

        This is the main measure: PP-OCR often returns only one box per
        receipt line, which leaves nothing to regress.
        """
        self.assertAlmostEqual(
            preprocess.skew_angle_from_words(make_words(angle=6.0)), 6.0, places=3)
        self.assertAlmostEqual(
            preprocess.skew_angle_from_words(make_words(angle=-4.5)), -4.5, places=3)

    def test_skew_angle_without_orientation(self):
        """An engine that only returns upright rectangles allows no conclusion.

        The image is not rotated in that case; the regression on the lines is
        used instead.
        """
        self.assertEqual(preprocess.skew_angle_from_words(make_words(angle=0.0)), 0.0)

    def test_skew_ignores_outliers(self):
        """An outlier must not skew the average.

        Four lines at 6° and a single word at 40°: the median singles it out
        and the weighted average leaves it out.
        """
        words = make_words(angle=6.0, count=4)
        words.append(OcrWord(text="X", score=0.9, angle=40.0,
                             left=900.0, top=900.0, right=930.0, bottom=930.0))
        self.assertAlmostEqual(preprocess.skew_angle_from_words(words), 6.0, places=3)
        self.assertNotIn(words[-1], preprocess.text_inliers(words))

    # -- Orientation ------------------------------------------------------

    def test_horizontal_text_score_tells_lying_from_standing(self):
        """The only reliable clue to the quarter turn: the direction of the boxes.

        The engine straightens each box before reading it and so reads a
        standing receipt as well as a lying one: the reading quality says
        nothing about the orientation, the geometry of the boxes does.
        """
        self.assertGreater(
            preprocess.horizontal_text_score(make_words(angle=2.0)), 0.0)
        self.assertLess(
            preprocess.horizontal_text_score(make_words(angle=88.0)), 0.0)
        self.assertLess(
            preprocess.horizontal_text_score(make_words(angle=-88.0)), 0.0)

    def test_quarter_turn_uprights_a_standing_receipt(self):
        """A quarter turn brings standing lines back to horizontal."""
        standing = make_words(angle=90.0)
        turned = preprocess.rotate_words_quarters(standing, 1, 300.0, 300.0)
        self.assertGreater(preprocess.horizontal_text_score(turned), 0.0)

    def test_quarter_turn_moves_the_boxes(self):
        """The clockwise quarter turn sends the top of the photo to the right."""
        word = OcrWord(text="ASF", score=0.9, angle=0.0,
                       left=10.0, top=0.0, right=110.0, bottom=20.0)
        turned = preprocess.rotate_words_quarters([word], 1, 200.0, 300.0)[0]
        # (x, y) -> (height - y, x): the top line ends up on the right.
        self.assertAlmostEqual(turned.left, 280.0)
        self.assertAlmostEqual(turned.right, 300.0)
        self.assertAlmostEqual(turned.top, 10.0)
        self.assertAlmostEqual(turned.bottom, 110.0)

    def test_half_turn_keeps_the_direction(self):
        """A line read backwards has the same direction: 180° changes nothing."""
        turned = preprocess.rotate_words_quarters(make_words(angle=7.0), 2, 300.0, 300.0)
        self.assertAlmostEqual(turned[0].angle, 7.0)

    def test_deskew_brings_the_direction_back_to_zero(self):
        """Straightening by 6° flattens boxes tilted by 6°."""
        matrix, _size = preprocess.rotation_matrix((300, 300), 6.0)
        moved = preprocess.rotate_words(make_words(angle=6.0), matrix, 6.0)
        self.assertAlmostEqual(moved[0].angle, 0.0)


    def test_an_oversized_photo_is_reduced_while_decoding(self):
        """The pixel limit scales the image down instead of saturating the worker."""
        import io
        from unittest.mock import patch
        from PIL import Image
        buffer = io.BytesIO()
        Image.new('RGB', (400, 300), 'white').save(buffer, format='JPEG')
        with patch.object(preprocess, 'MAX_PIXELS', 30000):
            image = preprocess.load_image(buffer.getvalue())
        self.assertLessEqual(image.shape[0] * image.shape[1], 30000)
        # Within the limit, nothing changes.
        self.assertEqual(preprocess.load_image(buffer.getvalue()).shape[:2], (300, 400))

    def test_an_absurd_photo_is_refused(self):
        import io
        from unittest.mock import patch
        from PIL import Image
        buffer = io.BytesIO()
        Image.new('RGB', (400, 300), 'white').save(buffer, format='PNG')
        with patch.object(preprocess, 'HARD_MAX_PIXELS', 1000):
            with self.assertRaisesRegex(ValueError, "too large"):
                preprocess.load_image(buffer.getvalue())

    def test_an_image_pillow_cannot_open_is_read_by_opencv(self):
        """Pillow without WebP: the image is read by OpenCV, limits included."""
        import cv2
        from unittest.mock import patch
        ok, encoded = cv2.imencode('.webp', self._receipt_photo())
        self.assertTrue(ok)
        with patch.object(preprocess.Image, 'open', side_effect=OSError("cannot identify")):
            image = preprocess.load_image(encoded.tobytes())
            self.assertEqual(image.shape[:2], (900, 700))
            with patch.object(preprocess, 'MAX_PIXELS', 30000):
                image = preprocess.load_image(encoded.tobytes())
            self.assertLessEqual(image.shape[0] * image.shape[1], 30000)
            with self.assertRaises(OSError):
                preprocess.load_image(b"not an image at all")

    def test_pdf_resolution_falls_back_without_page_size(self):
        """A PDF whose size cannot be read is rendered at the requested resolution."""
        self.assertEqual(preprocess._pdf_dpi(b"not a pdf", 200), 200)
