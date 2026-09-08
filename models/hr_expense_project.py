# -*- coding: utf-8 -*-
"""Rattachement d'un frais à une mission, et refacturation.

Séparé de ``hr_expense.py`` : la lecture du ticket et la refacturation sont
deux sujets distincts, et le second est facultatif — il ne s'active que si
la société le demande.
"""
import logging

from odoo import _, api, fields, models

_logger = logging.getLogger(__name__)


class HrExpense(models.Model):
    _inherit = 'hr.expense'

    reinvoice_mode = fields.Selection(
        selection=[
            # « Oui » d'abord : c'est la réponse la plus fréquente sur un
            # frais de mission, et celle qu'on veut atteindre sans lire.
            ('project', "Oui"),
            ('none', "Non"),
            ('todo', "À déterminer"),
        ],
        string="À refacturer",
        default='todo',
        required=True,
        help="« À déterminer » signale un frais dont le sort n'est pas "
             "tranché, et le bandeau de relecture le rappelle. « Non » est "
             "une décision, pas un oubli : le frais reste à la charge de la "
             "société.",
    )
    project_id = fields.Many2one(
        comodel_name='project.project',
        string="Mission",
        ondelete='restrict',
        help="Mission à laquelle rattacher ce frais. Le renseigner impute la "
             "dépense sur l'analytique de la mission et la porte sur la "
             "commande à refacturer, pour qu'elle ressorte sur la prochaine "
             "facture du projet.",
    )
    expense_scan_reinvoice = fields.Boolean(
        related='company_id.expense_scan_reinvoice',
        string="Rattachement aux missions actif",
    )

    @api.model
    def _get_view(self, view_id=None, view_type='form', **options):
        """Masque « Client à refacturer » : il découle de la mission.

        Ce champ vient de ``sale_expense``, par une vue sœur de la mienne :
        un xpath ne peut pas l'atteindre, puisqu'il n'est pas encore posé
        quand ma vue s'applique — et il ferait échouer la mise à jour sur
        une base où ``sale_expense`` n'est pas installé. Le retoucher ici,
        sur l'arbre déjà assemblé, marche dans les deux cas et n'impose
        aucune dépendance.
        """
        arch, view = super()._get_view(view_id, view_type, **options)
        if view_type == 'form' and self.env.company.expense_scan_reinvoice:
            for node in arch.xpath("//field[@name='sale_order_id']"):
                node.set('invisible', '1')
        return arch, view

    @api.onchange('reinvoice_mode')
    def _onchange_expense_scan_reinvoice_mode(self):
        """Choisir « Non » ou « À déterminer » libère la mission."""
        for expense in self:
            if expense.reinvoice_mode != 'project':
                expense.project_id = False

    @api.onchange('project_id')
    def _onchange_expense_scan_project(self):
        """Une mission choisie à la main entraîne les mêmes conséquences."""
        for expense in self:
            values = expense._expense_scan_project_values(expense.project_id)
            values.pop('project_id', None)
            for name, value in values.items():
                expense[name] = value

    def _expense_scan_project_values(self, project):
        """Ce qu'implique le rattachement à une mission.

        Deux conséquences, et seulement si les modules correspondants sont
        là : l'imputation analytique, qui fait remonter le coût dans la
        rentabilité du projet, et la commande à refacturer, qui produira la
        ligne sur la prochaine facture.
        """
        self.ensure_one()
        if not project:
            return {}

        values = {'project_id': project.id, 'reinvoice_mode': 'project'}
        if project.account_id:
            values['analytic_distribution'] = {str(project.account_id.id): 100.0}

        order = self.env['sale.order']
        if 'reinvoiced_sale_order_id' in project._fields:
            order = project.reinvoiced_sale_order_id
        if not order and 'sale_order_id' in project._fields:
            order = project.sale_order_id
        if order and 'sale_order_id' in self._fields:
            values['sale_order_id'] = order.id
        return values

    def _expense_scan_find_project(self, scan_date):
        """La mission du salarié en cours à la date du justificatif.

        Le module ``mission_report`` enregistre les missions comme des
        saisies d'absence portant un projet : c'est la seule source capable
        de dire sur quelle mission un salarié se trouvait un jour donné.
        Son champ ``entry_type`` étant calculé et non stocké, il n'est pas
        interrogeable — le vrai discriminant est la présence du projet.

        Une mission ambiguë ne vaut pas mieux qu'aucune : si plusieurs
        couvrent la date, on laisse le champ vide et on le signale.
        """
        self.ensure_one()
        employee = self.employee_id
        if not employee or not scan_date:
            return self.env['project.project']

        projects = self._expense_scan_missions_at(employee, scan_date)
        if len(projects) == 1:
            return projects
        if projects:
            return self.env['project.project']

        return self._expense_scan_only_project_of(employee)

    def _expense_scan_missions_at(self, employee, scan_date):
        """Projets des saisies de mission couvrant cette date."""
        if 'hr.leave' not in self.env:
            return self.env['project.project']
        Leave = self.env['hr.leave']
        if 'project_id' not in Leave._fields:
            return self.env['project.project']  # mission_report absent
        missions = Leave.search([
            ('employee_id', '=', employee.id),
            ('project_id', '!=', False),
            ('state', '!=', 'refuse'),
            ('request_date_from', '<=', scan_date),
            ('request_date_to', '>=', scan_date),
        ])
        return missions.mapped('project_id')

    def _expense_scan_only_project_of(self, employee):
        """Repli sans mission_report : le projet du salarié, s'il n'y en a qu'un.

        On s'aligne sur le critère de ``mission_report`` — les projets où le
        salarié a une tâche — mais on ne tranche pas à sa place dès qu'il y
        en a plusieurs.
        """
        user = employee.user_id
        if not user:
            return self.env['project.project']
        tasks = self.env['project.task'].search([('user_ids', 'in', user.id)])
        projects = tasks.project_id
        return projects if len(projects) == 1 else self.env['project.project']
