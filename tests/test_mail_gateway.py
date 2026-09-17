# -*- coding: utf-8 -*-
# Copyright 2026 Yves Vallée
# License LGPL-3.0 or later (https://www.gnu.org/licenses/lgpl-3.0).
"""Reconnaissance de l'expéditeur d'un justificatif envoyé par courriel.

Odoo ne compare l'adresse qu'à l'e-mail professionnel et à celui du compte
utilisateur. Un salarié qui transfère depuis son téléphone personnel n'est
donc pas reconnu, et la dépense se crée sans employé.
"""
from datetime import date

from odoo.tests import common, tagged


@tagged('post_install', '-at_install')
class TestExpenseScanMailGateway(common.TransactionCase):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.employee = cls.env['hr.employee'].create({
            'name': "Camille Test",
            'work_email': "camille@societe.example",
            'private_email': "camille.perso@exemple.fr",
        })
        cls.other = cls.env['hr.employee'].create({
            'name': "Dominique Test",
            'work_email': "dominique@societe.example",
        })

    def sender(self, address):
        return self.env['hr.expense']._get_employee_from_email(address)

    def test_work_email_still_wins(self):
        """Le comportement d'Odoo reste premier servi."""
        self.assertEqual(self.sender("camille@societe.example"), self.employee)

    def test_private_email_is_recognised(self):
        self.assertEqual(self.sender("camille.perso@exemple.fr"), self.employee)

    def test_address_is_matched_whole(self):
        """Un fragment ne désigne personne.

        La recherche se fait en « ilike » pour rester rapide, mais la
        comparaison finale porte sur l'adresse entière : sans quoi
        « perso@exemple.fr » ramènerait n'importe quel homonyme.
        """
        self.assertFalse(self.sender("perso@exemple.fr"))

    def test_case_and_display_name_are_ignored(self):
        """« Camille <CAMILLE.PERSO@Exemple.FR> » désigne bien Camille."""
        self.assertEqual(
            self.sender("Camille <CAMILLE.PERSO@Exemple.FR>"), self.employee)

    def test_unknown_sender_stays_unknown(self):
        self.assertFalse(self.sender("inconnu@ailleurs.example"))

    def test_no_address_at_all(self):
        self.assertFalse(self.sender(""))

    def test_alias_lets_private_address_through(self):
        """L'alias réservé aux employés ne renvoie plus l'adresse privée."""
        from email.message import EmailMessage
        alias = self.env['mail.alias'].new({'alias_contact': 'employees'})
        Expense = self.env['hr.expense']
        for address, refused in (("Camille <camille.perso@exemple.fr>", False),
                                 ("camille@societe.example", False),
                                 ("inconnu@ailleurs.example", True)):
            message = EmailMessage()
            message['From'] = address
            error = Expense._alias_get_error(message, {'email_from': address}, alias)
            self.assertEqual(bool(error), refused, address)


@tagged('post_install', '-at_install')
class TestExpenseScanMailSubject(common.TransactionCase):

    def clean(self, subject):
        return self.env['hr.expense']._expense_scan_mail_subject(subject)

    def test_prefixes_and_spaces_are_removed(self):
        self.assertEqual(self.clean("TR: RE:  Fwd:Hôtel Ibis\n  Lyon "), "Hôtel Ibis Lyon")
        self.assertEqual(self.clean("RE[2]: Taxi gare"), "Taxi gare")

    def test_words_starting_like_a_prefix_are_kept(self):
        self.assertEqual(self.clean("Restaurant du port"), "Restaurant du port")

    def test_empty_or_meaningless_subject(self):
        for subject in (None, "", "   ", "TR:", "Fwd: -- "):
            self.assertEqual(self.clean(subject), "", subject)

    def test_long_subject_is_cut_on_a_word(self):
        subject = "Hôtel " + " ".join(["mission"] * 30)
        cleaned = self.clean(subject)
        self.assertLessEqual(len(cleaned), 100)
        self.assertTrue(cleaned.endswith("mission…"), cleaned)

    def test_long_single_word_is_cut_anyway(self):
        cleaned = self.clean("x" * 300)
        self.assertEqual(len(cleaned), 100)

    def test_subject_becomes_the_description_and_survives_the_scan(self):
        employee = self.env['hr.employee'].create({
            'name': "Camille Objet", 'private_email': "camille.objet@exemple.fr",
        })
        expense = self.env['hr.expense'].message_new({
            'email_from': "camille.objet@exemple.fr",
            'subject': "TR: Hôtel Ibis Lyon, mission Enedis",
            'message_id': "<objet@exemple.fr>",
        })
        self.assertEqual(expense.employee_id, employee)
        self.assertEqual(expense.name, "Hôtel Ibis Lyon, mission Enedis")
        self.assertTrue(expense.expense_scan_keep_name)

        class Result:
            lines = []

            def value(self, name):
                return {'merchant': "IBIS", 'date': date(2026, 9, 1)}.get(name)

            def confidence(self, name):
                return 1.0

        values = expense._expense_scan_field_values(Result(), expense.company_id)
        self.assertNotIn('name', values)
