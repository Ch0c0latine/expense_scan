# -*- coding: utf-8 -*-
# Copyright 2026 Yves Vallée
# License LGPL-3.0 or later (https://www.gnu.org/licenses/lgpl-3.0).
"""Tickets typiques, tels que les lit l'OCR (enseignes réelles, coordonnées fictives).

Chacun a révélé un défaut : ils le gardent corrigé.
"""
from odoo.tests import common, tagged

from ..ocr import lexicon, parser
from .test_parser import words_from_text

MCDONALDS = """4562
SAT 122
NORD VILLEFRANCHE D'ORBEC CCIAL V2 Restaurant McDonald's
12 Boulevard des Exemples
99650 VILLEFRANCHE D'ORBEC
SIRET 123 456 782 00010-APE 5610C Te1.01.00.00.00.00
RCS Orbec -TVA INTRA FR11123456782
Restaurant 25000001
#CDE 62 Kiosk 45 09/06/2026 12:17:34
Qte Produit Unit Total
1 B0 Double Cheese 7.50 7.50
MX CBO CurryMan 13.30 13.30
oldseno S3  .0   0R Total commande 22.30 EUR
Merci de votre visite - A bientôt"""

LIDL = """← 18 novembre ooo
7 Rue de l'Exemple
FR-99650 VILLEFRANCHE D'ORBEC
Ticket de vente
Article P.U.EUR Qté EUR
Pipe Rigate 0,871 0,87 A T
Réduction Lidl Plus -0,87
Bière blonde Goudale 4,10 1 4,10 B
Lardons fumés 1,59 1 1,59 A T
Nombre de lignes: 7
A payer 18,21
16,79 HT
Total éligible TR (T) : 12,32
Carte 18,21
Total Promotion 0,87
TVA Taux MONT.TTC MONT.TVA TOTAL HT
A 5,5% 14,11 0,74 13,37
B 20% 4,10 0,68 3,42"""

POPEYES = """Ticket de Ventes No: 100201
ORBEC
Siret No:90123456700302
NAF：5610C
12 Jun'26 17:05
Qte Produit Total
1 3 BIC 12.49
Prix:12.49 TVA:D
Total €12.49
Carte Bancaire €12.49
Imposable TVA Montant
TVA 10.00%: 11.35 1.14 12.49 D
TVA 5.50%: 0.00 0.00 0.00 C
TOTAL 11.35 1.14 12.49"""

URBAN_TICKET = """ViLLEBuS LES TRONSPORTS DE L AGGLO
Automate:BRM-CS-SDB-01
09106/2026 21h19
Trajet unitaire x10 1 x 15,80 = 15,80
TOTAL EN EUROS : 15,80
Paiement en CB
TVA en Euros
Taux HT TVA TTC
10,00 14,36 1,44 15,80
Merci et bonne journée www.villebus.example"""


def reading(text):
    return parser.parse(words_from_text(text))


@tagged('post_install', '-at_install')
class TestRealReceipts(common.TransactionCase):

    def test_town_alone_does_not_make_the_same_merchant(self):
        """« Nord Villefranche d'Orbec » n'est pas « Dormizz Villefranche d'Orbec »."""
        lines = MCDONALDS.split("\n")
        self.assertIsNone(lexicon.match_merchant(lines, ["dormizz villefranche d orbec"]))
        self.assertEqual(lexicon.match_merchant(["DORMIZZ VILEFRANCHE D'ORBEC"],
                                                ["dormizz villefranche d orbec"]),
                         "dormizz villefranche d orbec")

    def test_long_brand_is_found_inside_a_line(self):
        family, brand = lexicon.brand_family(MCDONALDS.split("\n"))
        self.assertEqual(family, 'meal')
        self.assertEqual(lexicon.fold(brand), "mcdonald s")

    def test_mcdonalds_codes(self):
        result = reading(MCDONALDS)
        self.assertEqual(result.value('activity'), "NAF:5610C")
        self.assertEqual(result.value('company_number'), "12345678200010")
        self.assertEqual(result.value('total'), 22.30)

    def test_lidl(self):
        result = reading(LIDL)
        self.assertNotIn("novembre", (result.value('merchant') or "").lower())
        self.assertEqual(result.value('total'), 18.21)
        self.assertEqual(result.value('tax_amount'), 1.42)
        self.assertEqual(result.value('tax_rate_max'), 20.0)
        categories = {'meal': lexicon.split_keywords("\n".join(lexicon.DEFAULT_KEYWORDS['meal']))}
        self.assertEqual(lexicon.pick_category(
            lexicon.score_categories(LIDL.split("\n"), categories)), 'meal')

    def test_popeyes(self):
        result = reading(POPEYES)
        self.assertEqual(result.value('total'), 12.49)
        self.assertEqual(result.value('tax_rate'), 10.0)
        self.assertEqual(result.value('tax_amount'), 1.14)

    def test_urban_ticket(self):
        result = reading(URBAN_TICKET)
        self.assertEqual(result.value('total'), 15.80)
        self.assertEqual(result.value('tax_rate'), 10.0)
        self.assertEqual(result.value('tax_amount'), 1.44)


@tagged('post_install', '-at_install')
class TestHistoryIsNotAVeto(common.TransactionCase):

    def test_printed_code_and_brand_beat_a_wrong_history(self):
        families = self.env['product.template']._expense_scan_family_templates()
        by_family = {family: template.product_variant_id
                     for template, family in families.items()}
        if not {'meal', 'lodging'} <= set(by_family):
            return
        employee = self.env['hr.employee'].create({'name': "Camille Historique"})
        default = self.env['product.product'].create(
            {'name': "Divers historique", 'can_be_expensed': True})
        self.env.company.expense_scan_product_id = default
        Expense = self.env['hr.expense']
        # Une enseigne lue à l'identique, classée à tort en hébergement ;
        # préfixée pour ne croiser aucun ticket réel de la base.
        receipt = MCDONALDS.replace("NORD VILLEFRANCHE", "ZZNORD VILLEFRANCHE")
        past = Expense.create({
            'name': "Passé", 'employee_id': employee.id,
            'product_id': by_family['lodging'].id, 'total_amount_currency': 20.0,
        })
        past.write({
            'expense_scan_merchant': "Nord Villefranche Zz",
            'expense_scan_merchant_read': lexicon.fold(
                "ZZNORD VILLEFRANCHE D'ORBEC CCIAL V2 Restaurant McDonald's"),
            'approval_state': 'submitted',
        })
        expense = Expense.create({
            'name': "Ticket", 'employee_id': employee.id, 'product_id': default.id,
        })
        values = expense._expense_scan_category_values(reading(receipt), self.env.company)
        self.assertEqual(values['product_id'], by_family['meal'].id)


@tagged('post_install', '-at_install')
class TestRescanAfterApproval(common.TransactionCase):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.employee = cls.env['hr.employee'].create({'name': "Alex Relance"})
        cls.default = cls.env['product.product'].create(
            {'name': "Divers relance", 'can_be_expensed': True})
        cls.chosen = cls.env['product.product'].create(
            {'name': "Choisie relance", 'can_be_expensed': True})
        cls.env.company.expense_scan_product_id = cls.default

    def approved(self, **values):
        expense = self.env['hr.expense'].create(dict({
            'name': "Relance", 'employee_id': self.employee.id,
            'product_id': self.chosen.id, 'total_amount_currency': 18.21,
        }, **values))
        expense.write({'approval_state': 'submitted'})
        return expense

    def test_an_expense_is_not_its_own_history(self):
        expense = self.approved()
        expense.write({'expense_scan_merchant': "← 18 novembre zz",
                       'expense_scan_merchant_read': "18 novembre zz"})
        values = expense._expense_scan_category_values(
            reading(LIDL.replace("18 novembre ooo", "18 novembre zz")), self.env.company)
        self.assertNotIn("novembre", values.get('expense_scan_merchant') or "")

    def test_stale_reason_is_cleared(self):
        expense = self.approved()
        expense.write({'expense_scan_category_reason': "d'après une vieille raison"})
        values = expense._expense_scan_category_values(reading(URBAN_TICKET), self.env.company)
        self.assertFalse(values['expense_scan_category_reason'])
        self.assertNotIn('product_id', values)

    def test_column_titles_are_not_a_merchant(self):
        result = reading(LIDL)
        merchant = (result.value('merchant') or "").lower()
        self.assertNotIn("article", merchant)
        self.assertNotIn("nombre", merchant)


@tagged('post_install', '-at_install')
class TestOrientationFallback(common.TransactionCase):
    """Choix du quart de tour quand aucun sens de lecture ne s'impose.

    Cas réel : un justificatif de train édité en PDF (SNCF Connect), mis en
    page comme une facture plutôt qu'un ticket de caisse — l'enseigne en
    logo (pas en texte) et un pied de page légal (SIRET, adresse) en bas de
    page plutôt qu'en en-tête. Le solde de vote y est franchement négatif à
    l'endroit (le pied de page fait illusion en en-tête) sans jamais devenir
    franchement positif à l'envers, et l'ancien repli — le premier quart qui
    couche bien le texte, sans regarder son propre avis — le retournait.
    """

    def test_a_slightly_negative_original_orientation_is_kept(self):
        Expense = self.env['hr.expense']
        self.assertEqual(Expense._expense_scan_pick_quarter([(0, -1), (2, 0)]), 2)

    def test_a_clean_positive_verdict_wins_immediately(self):
        Expense = self.env['hr.expense']
        self.assertEqual(Expense._expense_scan_pick_quarter([(0, 1), (2, -1)]), 0)
        # L'ordre des candidats ne joue pas : un avis franc l'emporte
        # toujours, même s'il n'est pas testé en premier.
        self.assertEqual(Expense._expense_scan_pick_quarter([(0, -1), (2, 1)]), 2)

    def test_no_opinion_anywhere_keeps_the_first_candidate(self):
        Expense = self.env['hr.expense']
        self.assertEqual(Expense._expense_scan_pick_quarter([(0, 0), (2, 0)]), 0)

    def test_every_candidate_negative_keeps_the_first_by_default(self):
        """Cas dégénéré : aucune orientation n'est crédible. Faute de mieux."""
        Expense = self.env['hr.expense']
        self.assertEqual(Expense._expense_scan_pick_quarter([(0, -1), (2, -1)]), 0)

    class _FakeImage:
        """De quoi satisfaire `image.shape[:2]`, sans dépendre d'OpenCV."""
        def __init__(self, width, height):
            self.shape = (height, width, 3)

    def test_decide_180_false_never_flips_upside_down_or_not(self):
        """L'essai basse résolution ne corrige que debout/couché.

        Sur le même texte qui fait pencher `reading_direction` vers -1 à
        l'endroit (voir test_reading_direction_upside_down dans
        test_parser.py), l'appeler avec ``decide_180=False`` ne bouge pas
        le quart — c'est tout l'objet du repli introduit pour les PDF.
        """
        upside_down_text = words_from_text("""
6,80 euros PRIX TTC
1,13 euros TVA 20,00%
5,67 euros PRIX HT
ASF Lieu-dit Les Pins BP 10017
""")
        image = self._FakeImage(300, 100)
        Expense = self.env['hr.expense']
        self.assertEqual(
            Expense._expense_scan_quarters(upside_down_text, image, decide_180=False), 0)
        # À décider (le comportement d'une photo), le même texte tranche
        # bien pour le quart 2 : le repli n'aveugle pas la méthode, il la
        # rend seulement muette à ce stade.
        self.assertEqual(
            Expense._expense_scan_quarters(upside_down_text, image, decide_180=True), 2)

    def test_pdf_attachments_are_recognised(self):
        Expense = self.env['hr.expense']
        pdf = self.env['ir.attachment'].create(
            {'name': "x.pdf", 'raw': b'%PDF-1.4', 'mimetype': 'application/pdf'})
        photo = self.env['ir.attachment'].create(
            {'name': "x.jpg", 'raw': b'\xff\xd8', 'mimetype': 'image/jpeg'})
        self.assertTrue(Expense._expense_scan_is_pdf(pdf))
        self.assertFalse(Expense._expense_scan_is_pdf(photo))


# Devis PDF mis en colonnes : le total HT et la TVA partagent des lignes,
# et le taux n'est écrit que dans le détail, sans le mot « TVA ».
DEVIS = """DOMICILIATION EXEMPLE
Siège social et Gestion du courrier DEVIS
Date d'émission : 08/09/2026
Libellé Qté PU HT Montant HT TVA
AR01027 -Domiciliation commerciale. 1,00 30,00 € 30,00 € 20,00%
AR00826 -Numérisation du courrier. 1,00 20,00 € 20,00 € 20,00%
Détail de la TVA Total HT 50,00 €
Code Base HT Taux Montant TVA 10,00 €
Normale 50,00 € 20,00% 10,00 € Total TTC 60,00 €
Règlement Chèque ou Virement
Code NAF (APE) 4321A - SARL au capital social de 3000 €
N° TVA FR00000000000"""


@tagged('post_install', '-at_install')
class TestColumnInvoice(common.TransactionCase):

    def test_quote_with_columns(self):
        result = reading(DEVIS)
        self.assertEqual(result.value('total'), 60.00)
        self.assertEqual(result.value('tax_amount'), 10.00)
        self.assertEqual(result.value('tax_rate'), 20.0)
        self.assertEqual(result.value('tax_rate_max'), 20.0)


# Restaurant à caisse enregistreuse : chaque article porte « (c° tva: 2) », un
# renvoi au tableau des taux, suivi de son prix — qu'on prenait pour de la TVA.
RESTAURANT_CODES = """LE DRAGON GOURMAND
12 RUE DE L'EXEMPLE
VILLEFRANCHE D'ORBEC 99650 France
Siret:12345678200010
Code NAF: 5610A
N°TVA:FR11123456782
TEL:0100000000
Ticket C1T00001
Vente
Num commande: 23
Nb personnes: 2
Impression Ticket: 23/09/2026 21:58:57
Création commande: 23/09/2026 21:21:18
Opérateur: Vendeur(1)
N° Caisse: 1
Qté Désignation P.U Total
1 Takoaki (c° tva: 2) 6,90 6.90
1 huimian sauté boeuf (c° tva: 2) 14,90 14,90
1 porc aigre doux a la 16,00 16,00
vantonaise (c° tva: 2)
1 riz (c° tva: 2) 2,00 2,00
Nombre de ligne: 4
Total HT: 36,18€
Total TTC: 39,80€
Code TAUX QTE HT TVA TTC
2 10,00 4 36,18 3,62 39,80
Carte Bleue : 39,80€
Total: 39,80€
Impression N°1
L'équipe du LE DRAGON GOURMAND vous remercie de
votre visite, à bientôt!
(NF525) B 0000 AAAA - MyCaisse 3.0.10.0"""


@tagged('post_install', '-at_install')
class TestRestaurantCodes(common.TransactionCase):

    def test_item_tax_codes_are_not_tax_amounts(self):
        result = reading(RESTAURANT_CODES)
        self.assertEqual(result.value('total'), 39.80)
        self.assertEqual(result.value('tax_amount'), 3.62)
        self.assertEqual(result.value('tax_rate'), 10.0)
        self.assertEqual(result.value('activity'), "NAF:5610A")

    def test_a_real_rate_after_tva_still_counts(self):
        result = reading("BOUTIQUE\nTVA: 5,50 1,10\nTOTAL 21,10")
        self.assertEqual(result.value('tax_amount'), 1.10)

    def test_the_printed_activity_code_picks_the_meal_category(self):
        expense = self.env['hr.expense'].new({'name': "Ticket"})
        company = self.env.company
        _merchant, _key, product, _reason = expense._expense_scan_recognize(
            reading(RESTAURANT_CODES), company)
        families = self.env['product.template']._expense_scan_family_templates()
        meal = [t.product_variant_id for t, family in families.items() if family == 'meal']
        if not meal:
            self.skipTest("aucune catégorie de repas dans cette base")
        self.assertEqual(product, meal[0])


@tagged('post_install', '-at_install')
class TestCorpusFindings(common.TransactionCase):
    """Défauts trouvés en passant des justificatifs réels dans la chaîne."""

    def test_tax_row_led_by_a_code_in_brackets(self):
        result = reading("""
KIOSQUE DE GARE
Total TTC EUR 7,20
Tx TVA HT TVA TTC
(10) 10.00% 6,55 0,65 7,20
TOTAUX 6,55 0,65 7,20
7 CB SANS CONTACT 7,20""")
        self.assertEqual(result.value('tax_amount'), 0.65)
        self.assertEqual(result.value('tax_rate'), 10.0)

    def test_two_rates_with_mixed_columns_keep_the_consistent_triple(self):
        """Photo de biais : la ligne du second taux traîne les colonnes du total."""
        result = reading("""
MAISON EXEMPLE
HT TVA TTC
TVA 10% (10%) 32,09 € 3,21 € 35,30 €
TVA 5,5% (5.5%) 2,84 € Total 34,93 € 0,16 € 3,37 € 38,30 € 3,00 €
Total 38,30 €""")
        self.assertEqual(result.value('total'), 38.30)
        self.assertAlmostEqual(result.value('tax_amount'), 3.37, places=2)

    def test_total_with_vat_included_beats_a_kwh_figure(self):
        result = reading("""
Facture
Sessions de chargement
1 15/09/2026 18:22:55
kWh: 27,45
Sous-totaux: Énergie: EUR 12,82
Montant final (hors TVA) EUR 12,82
TVA 20,00 % EUR 2,56
Montant final (TVA incluse) EUR 15,38""")
        self.assertEqual(result.value('total'), 15.38)
        self.assertEqual(result.value('tax_amount'), 2.56)

    def test_merchant_label_wins_over_a_logo_and_a_title(self):
        result = reading("""
logo Justificatif d'achat
Résumé de la commande
Montant total (TTC) : 13,20 €
Détail des transactions
Date de transaction : 22/09/2026 à 17:26:10
Transaction n° 770717
Commerçant : GARE EXEMPLE, 93212 La Plaine
Montant de la transaction (TTC) : 13,20 €""")
        self.assertEqual(result.value('merchant'), "GARE EXEMPLE")

    def test_online_receipt_headings_are_not_a_merchant(self):
        result = reading("""
Voici votre reçu
Vos coordonnées
Prénom et nom Camille Exemple
Nom de l'établissement Hôtel du Parc
Montant payé 163,90 €""")
        merchant = (result.value('merchant') or "").lower()
        self.assertNotIn("coordonn", merchant)
        self.assertNotIn("voici", merchant)

    def test_a_merchant_may_carry_a_digit(self):
        result = reading("""
Distribo Tower 3 CS00000
53 Bd Exemple
93200 St Denis
Café au Lait 2,70 €
Total TTC 2,70 €""")
        self.assertTrue((result.value('merchant') or "").startswith("Distribo"))

    def test_a_short_word_does_not_change_its_first_letter(self):
        """« Selecta » n'est pas « electra » : un distributeur n'est pas une borne."""
        scores = lexicon.score_categories(["SELECTA PLEYAD 3"], {1: ["electra"]})
        self.assertEqual(scores, {})
        misread = lexicon.score_categories(["ELECTRA PLEYAD"], {1: ["electra"]})
        self.assertTrue(misread)

    def test_default_category_is_not_history(self):
        """Ce qu'on n'a pas su classer ne devient pas le classement d'une enseigne."""
        Expense = self.env['hr.expense']
        default = self.env.company.expense_scan_product_id or self.env['product.product'].create(
            {'name': "Divers historique zz", 'can_be_expensed': True})
        self.env.company.expense_scan_product_id = default
        employee = self.env['hr.employee'].create({'name': "Camille Histoire"})
        past = Expense.create({'name': "Passé", 'employee_id': employee.id,
                               'product_id': default.id, 'total_amount_currency': 35.0})
        past.write({'expense_scan_merchant': "Brasserie Zzexemple",
                    'expense_scan_merchant_read': lexicon.fold("Brasserie Zzexemple"),
                    'approval_state': 'submitted'})
        expense = Expense.create({'name': "Ticket", 'employee_id': employee.id,
                                  'product_id': default.id})
        result = reading("BRASSERIE ZZEXEMPLE\nTable 5\nRepas complet 35,00\nTOTAL 35,00 EUR")
        _merchant, _key, product, _reason = expense._expense_scan_recognize(
            result, self.env.company)
        self.assertNotEqual(product, default)

    def test_toll_ticket_without_a_total_word(self):
        """Ticket de péage italien : le prix du passage est le seul montant."""
        result = reading("""
Autostrade Exemplo S.p.A.
Tronco X1 Nord-Sud
Direzione e Coordinamento EXEMPLO
Via Esempio 1
00100 Città (XX)
ATTESTATO DI TRANSITO
CARTA DI CREDITO
USCITA: CASELLO NORD 100
DATA e ORA: 02/09/2026 10:15
ENTRATA: CASELLO SUD 200
DATA e ORA: 02/09/2026 09:05
CLASSE DI PEDAGGIO: A
PEDAGGIO € 9,40
TESSERA 0000""")
        self.assertEqual(result.value('total'), 9.40)
        self.assertNotIn(parser.FIELD_LABELS['total'], parser.fields_to_check(result))
        self.assertEqual(result.value('merchant'), "Autostrade Exemplo S.p.A.")

    def test_booking_receipt_names_the_establishment(self):
        result = reading("""
Voici votre reçu
Numéro De Réservation 1234567890
VOS COORDONNÉES
Prénom et nom Camille Exemple
Nom de l'établissement Hôtel Exemple Centre 3 étoiles
Montant payé le 02 sept. 2026 € 98,40""")
        self.assertEqual(result.value('merchant'), "Hôtel Exemple Centre")

    def test_merchant_is_cut_before_a_glued_lieu_dit(self):
        result = reading("""
ASFLieu-ditExemple 12
RECU
Date 02/09/26
PRIX TTC 4,20 euros""")
        self.assertEqual(result.value('merchant'), "ASF")

    def test_the_buyer_printed_on_an_invoice_is_not_the_merchant(self):
        """Facture de recharge : le client en tête, le vendeur en pied de page."""
        result = parser.parse(words_from_text("""
Facture
Spécification N° de facture: FRXX000001 Voltix>
CAMILLE EXEMPLE
Sessions de chargement
Voltix – Parking Exemple: Rue des Usines 00000 Exempleville
CAMILLE EXEMPLE
1 02/09/2026 18:00:00
Montant final (TVA incluse) EUR 12,00
Voltix Innovations B.V. – Voorbeeldstraat 1, 0000 XX Voorbeeld"""),
            buyers=["Camille Exemple", "Green Exemple"])
        self.assertEqual(result.value('merchant'), "Voltix Innovations B.V.")

    def test_web_page_headings_are_not_a_merchant(self):
        result = reading("""
Fermer ×
PAIEMENTS EFFECTUÉS
De Villeun à Villedeux
02 septembre, 1 passager
TOTAL PAYÉ À CE JOUR
42,00€
VOLS
Vol aller V x 1 passager 41,05€
TOTAL 42,00€""")
        self.assertIsNone(result.value('merchant'))
        self.assertEqual(result.value('total'), 42.0)

    def test_tax_table_with_rate_tax_net_gross_columns(self):
        """« TVA% TVA Net Brut » : la taxe en première colonne."""
        result = reading("""
SUPERMARCHE EXEMPLE
*CAFE MOULU 5,05
*BISCUITS 2*2,73 5,46
Total 10,51
TVA% TVA Net Brut
5,50 0,54 9,97 10,51
Brut 0,54 9,97 10,51
Reçu CARTE BANCAIRE 10.51""")
        self.assertEqual(result.value('tax_amount'), 0.54)
        self.assertEqual(result.value('tax_rate'), 5.5)

    def test_buyer_glued_to_the_seller_by_two_columns(self):
        result = parser.parse(words_from_text("""
L Institut Le 02.09.2026
Exemple Facture n°000000001
INSTITUT EXEMPLE GREEN EXEMPLE
1 rue de l'Exemple 2 CHEMIN DES PRES
120h x 11 €/h = 1 320,00 € HT"""), buyers=["Camille Exemple", "GreenExemple"])
        self.assertEqual(result.value('merchant'), "Institut Exemple")

    def test_english_net_total_is_before_tax(self):
        """« Net Total » est le hors-taxe d'un taux ; le montant payé suit."""
        result = reading("""
Exemple Food & Beverage
CHK 5785
28 Jul'26 15:40 PM
1 Viennois Exemple 8,50
1 Croissant Exemple 4,80
0,77 France-VAT 10% Tak 8,50
Net Total: €7,73
0,25 France-VAT 5.5% Ta 4,80
Net Total: €4,55
Food €13,30
Paiement €13,30
Change Due €0,00""")
        self.assertEqual(result.value('total'), 13.30)
        self.assertEqual(str(result.value('date')), "2026-07-28")
        self.assertGreaterEqual(result.fields['date'].confidence, parser.LOW_CONFIDENCE)

    def test_abbreviated_tot_ttc(self):
        result = reading("""
STATION EXEMPLE A62
TICKET CLIENT
Donut 3.70
Moka 5.95
TOT TTC € 9.65
Visa 635
Montant 10.00""")
        self.assertEqual(result.value('total'), 9.65)

    def test_german_net_turnover_and_tip_are_not_the_total(self):
        result = reading("""
Flughafen Exemple
Rechnung Nr. 1128
1 × 6,20 EUR 6,20 EUR
Bowl Exemple 10,90 EUR
Summe Nettoumsatz 15,98 EUR 1,12 EUR
Steuersumme Verkäufe 7% inkl. 17,10 EUR 1,12 EUR
Summe 17,10 EUR""")
        self.assertEqual(result.value('total'), 17.10)
        tipped = reading("""
Exemple Frühstück
1x Frühstück 5,50
1x Espresso 3,70
Summe Trinkgeld 9,20 0,46
Gesamt 9,66""")
        self.assertEqual(tipped.value('total'), 9.66)

    def test_ocr_reads_tva_as_tua(self):
        result = reading("""
CAFETERIA EXEMPLE
1 x PLAT DU JOUR 8.60
TOTAL 14.95
HT TUA TTC
E TUA 10.00 13.59 1.36 14.95
CARTE BLEUE 14.95""")
        self.assertEqual(result.value('tax_amount'), 1.36)
        self.assertEqual(result.value('tax_rate'), 10.0)

    def test_tax_named_taxe_after_a_rate(self):
        result = reading("""
RESTAURANT RAPIDE EXEMPLE
Sous-total 18,09
10% Taxe 1,81
Total taxes 1,81
Sur Place Total 19,90
Taxe de séjour 1,65""")
        self.assertEqual(result.value('tax_amount'), 1.81)
        self.assertEqual(result.value('total'), 19.90)

    def test_tax_line_with_tax_then_gross(self):
        result = reading("""
EXEMPLE FOOD
0,77 France-VAT 10% Tak 8,50
0,25 France-VAT 5.5% Ta 4,80
Paiement €13,30""")
        self.assertAlmostEqual(result.value('tax_amount'), 1.02, places=2)

    def test_total_line_with_columns_in_reverse(self):
        """Facture de parking : « TTC TVA HT Total », dans cet ordre."""
        result = reading("""
PARKING AEROPORT EXEMPLE
PROXIPARC P1
68,60 € 11,43 € 57,17 € Total""")
        self.assertEqual(result.value('total'), 68.60)

    def test_net_total_under_each_rate_is_before_tax(self):
        result = reading("""
TACOS EXEMPLE
1 TACOS XL 14.30
2.27 T.V.A. 10% AE 25.00
Total net : 22.73
0.12 T.V.A. 5.5% AE 2.25
Total net : 2.13
SOUS-TOTAL 27.25
PAIEMENT 27.25""")
        self.assertEqual(result.value('total'), 27.25)

    def test_german_tax_sum(self):
        result = reading("""
Flughafen Exemple
Bowl Exemple 17,10 EUR
Steuersumme Verkäufe 7% inkl. 17,10 EUR 1,12 EUR
MwSt 7% EC 17,10 EUR
Sie haben 17,10 EUR bezahlt""")
        self.assertEqual(result.value('tax_amount'), 1.12)
        self.assertEqual(result.value('total'), 17.10)

    def test_dates_with_month_names_glued_or_slashed(self):
        glued = reading("CAFE EXEMPLE\nFct 7868 N :0001-0462003\nMai12'25 10:35AM\nTOTAL 6,95")
        self.assertEqual(str(glued.value('date')), "2025-05-12")
        slashed = reading("HOTEL EXEMPLE\nDate: 14/May/2025\nTotal 6.37")
        self.assertEqual(str(slashed.value('date')), "2025-05-14")
        ambiguous = reading("CAFE EXEMPLE\nJui08'25 08:55AM\nTOTAL 9,00")
        self.assertIsNone(ambiguous.value('date'))  # juin ou juillet ?
