# -*- coding: utf-8 -*-
"""Tests du pré-traitement d'image.

Ils construisent une fausse photo de ticket — un rectangle blanc incliné sur
fond sombre — et vérifient que la chaîne le retrouve, le découpe et le
redresse. Ils s'effacent d'eux-mêmes si OpenCV n'est pas installé, pour ne
pas faire échouer la suite de tests sur un serveur qui n'utiliserait que
Tesseract.
"""
from odoo.tests import common, tagged

from ..ocr import preprocess
from ..ocr.types import OcrWord


def make_words(angle=0.0, count=5):
    """Cinq lignes de texte, toutes inclinées du même angle."""
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
        """Fausse photo : ticket blanc texturé sur fond sombre."""
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
        self.assertEqual(ordered[0].tolist(), [10, 10])   # haut-gauche
        self.assertEqual(ordered[1].tolist(), [90, 10])   # haut-droit
        self.assertEqual(ordered[2].tolist(), [90, 90])   # bas-droit
        self.assertEqual(ordered[3].tolist(), [10, 90])   # bas-gauche

    def test_detect_and_crop_receipt(self):
        """Le ticket est retrouvé dans la photo et découpé."""
        photo = self._receipt_photo()
        quad = preprocess.detect_receipt_quad(photo)
        self.assertIsNotNone(quad, "le ticket n'a pas été détecté dans la photo")

        cropped = preprocess.four_point_transform(photo, quad)
        self.assertIsNotNone(cropped)
        # Le résultat doit être nettement plus petit que la photo d'origine
        # et rester un objet plus haut que large.
        self.assertLess(cropped.shape[0] * cropped.shape[1],
                        photo.shape[0] * photo.shape[1] * 0.75)
        self.assertGreater(cropped.shape[0], cropped.shape[1])

    def test_prepare_reports_what_it_did(self):
        import cv2

        photo = self._receipt_photo()
        ok, encoded = cv2.imencode(".jpg", photo)
        self.assertTrue(ok)

        image, info = preprocess.prepare(encoded.tobytes())
        self.assertTrue(info.cropped, "le recadrage aurait dû se déclencher")
        self.assertTrue(info.changed)
        self.assertEqual(info.original_size, (photo.shape[1], photo.shape[0]))
        self.assertEqual(info.final_size, (image.shape[1], image.shape[0]))

    def test_prepare_leaves_a_clean_scan_alone(self):
        """Une image déjà cadrée sur le ticket ne doit pas être rognée."""
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
        """L'inclinaison est mesurée, puis réellement annulée.

        Le test porte sur le résultat et non sur le signe de l'angle : c'est
        justement ce que la convention d'OpenCV ne garantit pas d'une
        version à l'autre.
        """
        import cv2
        import numpy as np

        page = np.full((600, 600, 3), 255, dtype=np.uint8)
        for row in range(80, 520, 40):
            cv2.line(page, (80, row), (520, row), (20, 20, 20), 4)
        tilted = preprocess.rotate(page, -6.0)

        self.assertAlmostEqual(abs(preprocess.estimate_skew_angle(tilted)), 6.0, delta=1.0)

        corrected, applied = preprocess.deskew_image(tilted)
        self.assertTrue(applied, "aucune correction n'a été appliquée")
        self.assertLess(abs(preprocess.estimate_skew_angle(corrected)), 1.5)

    def test_skew_angle_from_words(self):
        """L'angle se lit sur l'orientation des boîtes du détecteur.

        C'est la mesure principale : PP-OCR ne rend souvent qu'une boîte par
        ligne de ticket, ce qui ne laisse rien à régresser.
        """
        self.assertAlmostEqual(
            preprocess.skew_angle_from_words(make_words(angle=6.0)), 6.0, places=3)
        self.assertAlmostEqual(
            preprocess.skew_angle_from_words(make_words(angle=-4.5)), -4.5, places=3)

    def test_skew_angle_without_orientation(self):
        """Un moteur qui ne rend que des rectangles droits ne conclut rien.

        Mieux vaut ne pas tourner l'image que la tourner au hasard ; c'est
        alors la régression sur les lignes qui prend le relais.
        """
        self.assertEqual(preprocess.skew_angle_from_words(make_words(angle=0.0)), 0.0)

    def test_skew_ignores_outliers(self):
        """Un caractère du décor ne doit pas emporter la moyenne.

        Quatre lignes du ticket à 6°, un intrus à 40° : la médiane le
        désigne, et la moyenne pondérée n'en tient plus compte.
        """
        words = make_words(angle=6.0, count=4)
        words.append(OcrWord(text="X", score=0.9, angle=40.0,
                             left=900.0, top=900.0, right=930.0, bottom=930.0))
        self.assertAlmostEqual(preprocess.skew_angle_from_words(words), 6.0, places=3)
        self.assertNotIn(words[-1], preprocess.text_inliers(words))
