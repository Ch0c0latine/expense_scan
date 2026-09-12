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
