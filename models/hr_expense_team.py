# -*- coding: utf-8 -*-
# Copyright 2026 Yves Vallée
# License LGPL-3.0 or later (https://www.gnu.org/licenses/lgpl-3.0).
"""Saisir pour un salarié de son équipe, sans se connecter à son compte.

Un chef d'équipe ou un administrateur ouvre la liste d'un salarié ; tout ce
qu'il y crée (saisie ou scan) l'est au nom du salarié. L'historique de la
dépense garde l'auteur réel.

Se connecter en tant que le salarié donnerait accès à tout ce qu'il voit
(congés, messages, fiche personnelle) et lui attribuerait chaque action.
"""
from odoo import _, api, models
from odoo.exceptions import AccessError


class HrExpense(models.Model):
    _inherit = 'hr.expense'

    @api.model
    def action_expense_scan_team(self):
        """Membres de l'équipe, pour choisir celui pour qui la saisie est faite."""
        team = self._expense_scan_team_employees()
        view = self.env.ref('expense_scan.hr_employee_public_view_kanban_expense_team')
        return {
            'type': 'ir.actions.act_window',
            'name': _("Dépenses de mon équipe"),
            # Chemin de l'action serveur : au rechargement, Odoo relance
            # l'action au lieu d'échouer.
            'path': 'my-team-expenses',
            'res_model': 'hr.employee.public',
            'view_mode': 'kanban',
            'views': [(view.id, 'kanban')],
            'domain': [('id', 'in', team.ids)],
            # Pas de « create » dans ce contexte : le bouton d'une carte le
            # transmet à l'action qu'il ouvre, et le bouton « Nouveau »
            # disparaissait des dépenses du salarié. La vue interdit déjà la
            # création de salariés par son propre attribut.
        }

    @api.model
    def _expense_scan_employee_expense_action(self, employee_id, employee_name):
        """Liste des dépenses d'un salarié, saisie en son nom.

        Utilisée par la carte de l'équipe et par les boutons de la fiche
        (Terminé, Supprimer) pour revenir à la liste du salarié et non à
        celle du manager.
        """
        # Action enregistrée, avec son chemin : l'adresse de la page garde le
        # salarié (active_id) et Odoo reconstruit la liste au rechargement ou
        # depuis le fil d'Ariane.
        action = self.env['ir.actions.actions']._for_xml_id(
            'expense_scan.action_expense_scan_employee_expenses')
        title = _("Dépenses de %s", employee_name)
        action.update({
            # Le client web affiche `display_name` quand il est présent.
            'name': title,
            'display_name': title,
            'domain': [('employee_id', '=', employee_id)],
            # Le salarié devient l'employé par défaut : la saisie comme le
            # scan passent par `create`, qui honore ce défaut.
            'context': {
                'active_id': employee_id,
                'active_model': 'hr.employee.public',
                'default_employee_id': employee_id,
                'expense_scan_acting_for': employee_id,
            },
        })
        return action

    def _expense_scan_expense_list(self):
        """Revient à la liste du salarié quand la saisie est faite pour lui.

        Sans cela, « Terminé » ramenait le manager à ses propres dépenses.
        """
        employee = self[:1].employee_id
        if not employee or employee in self.env.user.employee_ids:
            return super()._expense_scan_expense_list()
        action = self._expense_scan_employee_expense_action(employee.id, employee.name)
        action['target'] = 'main'
        return action

    @api.model
    def _expense_scan_team_employees(self):
        """Équipe de l'utilisateur, selon les règles d'accès d'Odoo.

        Mêmes critères que la règle « Team Approver Expense » : salariés dont
        il dirige le département, salariés placés sous lui dans la
        hiérarchie, salariés dont il approuve les notes de frais. Un
        approbateur de toutes les dépenses voit tout le monde. Ainsi, aucun
        salarié n'est proposé si la saisie serait ensuite refusée.
        """
        user = self.env.user
        Employee = self.env['hr.employee'].sudo()
        domain = [('company_id', 'in', self.env.companies.ids)]
        if not user.has_group('hr_expense.group_hr_expense_user'):
            if not user.has_group('hr_expense.group_hr_expense_team_approver'):
                return Employee.browse()
            domain += ['|', '|',
                       ('department_id.manager_id.user_id', '=', user.id),
                       ('id', 'child_of', user.employee_ids.ids),
                       ('expense_manager_id', '=', user.id)]
        # Ses propres frais ont déjà leur menu.
        return Employee.search(domain) - user.employee_ids

    @api.model_create_multi
    def create(self, vals_list):
        """Consigne sur la dépense qui l'a saisie, et pour qui.

        Odoo trace l'auteur de chaque modification, mais la création n'affiche
        qu'un « Dépense créée » sans distinction. Une note est ajoutée quand
        l'auteur n'est pas le salarié.
        """
        expenses = super().create(vals_list)
        author = self.env.user
        # Seul un utilisateur rattaché à un employé saisit « pour » quelqu'un ;
        # la passerelle de messagerie n'en est pas un.
        if author.employee_ids and not self.env.su:
            for expense in expenses:
                employee = expense.employee_id
                if employee and employee not in author.employee_ids:
                    expense.message_post(
                        body=_("Saisie par %(author)s pour %(employee)s.",
                               author=author.name, employee=employee.name),
                        subtype_xmlid='mail.mt_note',
                    )
        return expenses


class HrEmployeePublic(models.Model):
    _inherit = 'hr.employee.public'

    def action_expense_scan_open_expenses(self):
        """Liste des dépenses de ce salarié, saisie en son nom."""
        self.ensure_one()
        Expense = self.env['hr.expense']
        if self.id not in Expense._expense_scan_team_employees().ids:
            raise AccessError(_("%s ne fait pas partie de votre équipe.", self.name))
        return Expense._expense_scan_employee_expense_action(self.id, self.name)
