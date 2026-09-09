# -*- coding: utf-8 -*-
"""Reconnaissance de l'expéditeur d'un justificatif envoyé par courriel.

Odoo ne compare l'adresse qu'à l'e-mail professionnel et à celui du compte
utilisateur. Un salarié qui transfère depuis son téléphone personnel n'est
donc pas reconnu, et la dépense se crée sans employé.
"""
from odoo.tests import common, tagged


@tagged('post_install', '-at_install')
class TestExpenseScanMailGateway(common.TransactionCase):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.employee = cls.env['hr.employee'].create({
            'name': "Camille Test",
            'work_email': "camille@societe.example",
            'expense_scan_emails': "camille.perso@exemple.fr\ncam@mobile.example",
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

    def test_additional_address_is_recognised(self):
        self.assertEqual(self.sender("camille.perso@exemple.fr"), self.employee)

    def test_second_additional_address(self):
        """La liste en accepte plusieurs, séparées comme on veut."""
        self.assertEqual(self.sender("cam@mobile.example"), self.employee)

    def test_address_is_matched_whole(self):
        """Un fragment ne désigne personne.

        La recherche se fait en « ilike » pour rester rapide, mais la
        comparaison finale porte sur l'adresse entière : sans quoi
        « perso@exemple.fr » ramènerait n'importe quel homonyme.
        """
        self.assertFalse(self.sender("perso@exemple.fr"))

    def test_unknown_sender_stays_unknown(self):
        self.assertFalse(self.sender("inconnu@ailleurs.example"))

    def test_no_address_at_all(self):
        self.assertFalse(self.sender(""))

    def test_case_and_spacing_are_ignored(self):
        """Une adresse se lit sans égard à la casse ni aux espaces."""
        self.employee.expense_scan_emails = "  Camille.PERSO@Exemple.FR , autre@x.example "
        self.assertEqual(self.sender("camille.perso@exemple.fr"), self.employee)
        self.assertEqual(self.sender("autre@x.example"), self.employee)
