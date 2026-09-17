# -*- coding: utf-8 -*-
# Copyright 2026 Yves Vallée
# License LGPL-3.0 or later (https://www.gnu.org/licenses/lgpl-3.0).
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
    expense_scan_task_id = fields.Many2one(
        comodel_name='project.task',
        string="Tâche",
        domain="[('project_id', '=', project_id)]",
        ondelete='set null',
        help="Tâche de la mission concernée, pour le suivi. Le coût remonte "
             "dans le budget de la mission, que le frais soit refacturé ou non.",
    )
    expense_scan_reinvoice = fields.Boolean(
        related='company_id.expense_scan_reinvoice',
        string="Rattachement aux missions actif",
    )

    expense_scan_has_tasks = fields.Boolean(
        string="Mission avec tâches",
        compute='_compute_expense_scan_has_tasks',
        help="Le champ « Tâche » n'apparaît que s'il y a un choix à faire.",
    )

    @api.depends('project_id')
    def _compute_expense_scan_has_tasks(self):
        for expense in self:
            expense.expense_scan_has_tasks = bool(
                expense.project_id and self._expense_scan_open_tasks(expense.project_id))

    @api.model
    def _expense_scan_open_tasks(self, project):
        """Tâches ouvertes d'une mission, dans l'ordre fixé par l'utilisateur."""
        return self.env['project.task'].sudo().search([
            ('project_id', '=', project._origin.id or project.id),
            ('state', 'not in', ('1_done', '1_canceled')),
        ], order='sequence, id')

    @api.model_create_multi
    def create(self, vals_list):
        expenses = super().create(vals_list)
        expenses._expense_scan_sync_analytic()
        return expenses

    def write(self, vals):
        syncing = 'project_id' in vals and not self.env.context.get('expense_scan_syncing')
        # L'imputation posée d'office pour l'ancienne mission, relevée avant
        # qu'elle ne change : c'est elle, et elle seule, qu'on remplacera.
        previous = {expense.id: self._expense_scan_auto_distribution(expense.project_id)
                    for expense in self.sudo()} if syncing else {}
        result = super().write(vals)
        if syncing:
            self._expense_scan_sync_analytic(previous)
        return result

    @api.model
    def _expense_scan_auto_distribution(self, project):
        """La répartition qu'implique une mission : tout sur son compte."""
        project = project.sudo()
        if not project or 'account_id' not in project._fields or not project.account_id:
            return {}
        return {str(project.account_id.id): 100.0}

    def _expense_scan_sync_analytic(self, previous=None):
        """Impute sur la mission les dépenses qui y sont rattachées.

        Le coût d'une mission ne remonte dans son tableau de bord qu'au
        travers de son compte analytique. Une mission qui n'en a pas en
        reçoit un, comme Odoo le fait pour les feuilles de temps.

        ``previous`` donne, par dépense, l'imputation d'office de la mission
        qu'elle quitte : celle-là suit le changement de mission (ou disparaît
        avec elle) ; une répartition saisie à la main n'est jamais touchée.
        """
        previous = previous or {}
        for expense in self.sudo():
            current = expense.analytic_distribution or {}
            automatic = not current or (
                previous.get(expense.id) and self._same_distribution(current, previous[expense.id]))
            if not automatic:
                continue
            project = expense.project_id
            if project and 'account_id' in project._fields and not project.account_id:
                try:
                    with self.env.cr.savepoint():
                        project._create_analytic_account()
                except Exception:  # noqa: BLE001 - plan analytique absent
                    _logger.warning("Compte analytique impossible pour la mission %s",
                                    project.display_name, exc_info=True)
            wanted = self._expense_scan_auto_distribution(project)
            if not self._same_distribution(current, wanted):
                expense.with_context(expense_scan_syncing=True).write({
                    'analytic_distribution': wanted or False,
                })

    @staticmethod
    def _same_distribution(left, right):
        """Deux répartitions identiques, aux écarts d'écriture près."""
        left, right = left or {}, right or {}
        return set(left) == set(right) and all(
            abs(float(left[key]) - float(right[key])) < 0.01 for key in left)

    expense_scan_project_domain = fields.Char(
        string="Missions proposées",
        compute='_compute_expense_scan_project_domain',
        help="Domaine appliqué au champ « Mission ». Il restreint le choix "
             "d'un salarié à ses propres missions ; un chef de projet ou un "
             "administrateur garde la liste entière.",
    )

    @api.depends_context('uid')
    @api.depends('employee_id')
    def _compute_expense_scan_project_domain(self):
        """Restreint le choix de mission à celles du salarié de la dépense.

        Sans cela, n'importe qui rattachait son frais à n'importe quel
        projet, y compris ceux auxquels il n'a jamais participé — et cette
        imputation remonte dans la rentabilité du projet et sur la facture
        du client. Un chef de projet ou un administrateur, lui, a de bonnes
        raisons de voir toute la liste.

        Ce sont les missions du **salarié** qui comptent, pas celles de qui
        regarde : un chef d'équipe qui saisit pour Léon doit voir les
        missions de Léon.

        Ce domaine borne ce qui est **proposé**, ce qui n'est pas une
        barrière de sécurité : il filtre l'interface, pas les écritures.
        """
        viewer = self.env.user
        if viewer.has_group('project.group_project_manager') \
                or viewer.has_group('base.group_system'):
            for expense in self:
                expense.expense_scan_project_domain = "[]"
            return

        for expense in self:
            employee = expense.employee_id or viewer.employee_id
            user = employee.user_id
            # Le second terme couvre les projets dont le salarié est
            # responsable : il peut n'y avoir aucune tâche ni mission dessus.
            expense.expense_scan_project_domain = str([
                '|', ('id', 'in', self._expense_scan_employee_project_ids(employee)),
                ('user_id', '=', user.id or False),
            ])

    @api.model
    def _expense_scan_employee_project_ids(self, employee):
        """Les missions d'un salarié, au sens large.

        Trois sources, réunies : les projets dont il est responsable, ceux
        où une tâche lui est assignée, et ceux de ses missions passées ou à
        venir.

        Recherche en droits élevés : un chef d'équipe n'a pas forcément
        accès aux tâches ni aux absences du salarié pour qui il saisit. On
        n'en retire que des identifiants de projet.
        """
        if not employee:
            return []
        sudo = self.sudo()
        user = employee.user_id
        projects = sudo.env['project.project']
        if user:
            projects |= sudo.env['project.project'].search([('user_id', '=', user.id)])
            tasks = sudo.env['project.task'].search([('user_ids', 'in', user.id)])
            projects |= tasks.project_id

        # Les missions sont des absences portant un projet : il faut les
        # Congés (hr_holidays) et un champ « project_id » sur les absences,
        # qu'ajoute un module de suivi des missions.
        if 'hr.leave' in sudo.env:
            Leave = sudo.env['hr.leave']
            if 'project_id' in Leave._fields:
                missions = Leave.search([
                    ('employee_id', '=', employee.id),
                    ('project_id', '!=', False),
                ])
                projects |= missions.project_id
        return projects.ids

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
        """« À déterminer » libère la mission ; « Non » la garde, sans commande.

        Un frais non refacturé peut rester rattaché à sa mission : son coût
        remonte alors dans le budget de la mission, sans aller sur la
        facture du client.
        """
        for expense in self:
            if expense.reinvoice_mode == 'todo':
                expense.project_id = False
                expense.expense_scan_task_id = False
            if expense.reinvoice_mode != 'project' and 'sale_order_id' in expense._fields:
                expense.sale_order_id = False
            if expense.reinvoice_mode in ('project', 'none') and expense.project_id:
                for name, value in expense._expense_scan_project_values(
                        expense.project_id, reinvoice=expense.reinvoice_mode == 'project').items():
                    if name not in ('project_id', 'reinvoice_mode'):
                        expense[name] = value

    @api.onchange('project_id')
    def _onchange_expense_scan_project(self):
        """Une mission choisie à la main entraîne les mêmes conséquences."""
        for expense in self:
            if expense.expense_scan_task_id.sudo().project_id != expense.project_id:
                expense.expense_scan_task_id = False
            # « Non » est une décision : choisir la mission ensuite ne la
            # renverse pas, elle sert alors au seul suivi du budget.
            reinvoice = expense.reinvoice_mode != 'none'
            values = expense._expense_scan_project_values(expense.project_id, reinvoice=reinvoice)
            values.pop('project_id', None)
            for name, value in values.items():
                expense[name] = value

    def _expense_scan_project_values(self, project, reinvoice=True):
        """Ce qu'implique le rattachement à une mission.

        Deux conséquences, et seulement si les modules correspondants sont
        là : l'imputation analytique, qui fait remonter le coût dans la
        rentabilité du projet, et — pour un frais refacturé seulement — la
        commande à refacturer, qui produira la ligne sur la prochaine
        facture.
        """
        self.ensure_one()
        if not project:
            return {}

        values = {'project_id': project.id,
                  'reinvoice_mode': 'project' if reinvoice else 'none'}

        # Lecture en droits élevés, et c'est indispensable : le compte
        # analytique d'un projet et sa commande à refacturer sont réservés
        # aux groupes Analytique et Ventes. Un salarié ordinaire qui
        # photographie son ticket n'en fait partie d'aucun, et l'analyse
        # échouait pour lui seul — « vous ne disposez pas des droits
        # suffisants pour accéder au champ reinvoiced_sale_order_id ».
        #
        # Rien n'est divulgué au passage : on ne retient que des
        # identifiants, portés sur la dépense de ce salarié, et déduits de
        # la mission sur laquelle il se trouvait ce jour-là.
        project = project.sudo()
        distribution = self._expense_scan_auto_distribution(project)
        if distribution:
            values['analytic_distribution'] = distribution
        elif self._same_distribution(self.analytic_distribution,
                                     self._expense_scan_auto_distribution(self._origin.project_id)):
            # Mission sans compte (il sera créé à l'enregistrement) : l'imputation
            # de la précédente ne doit pas lui survivre.
            values['analytic_distribution'] = False

        # La tâche la plus haut placée par l'utilisateur, si celle déjà
        # choisie n'appartient pas à cette mission.
        if self.expense_scan_task_id.sudo().project_id != project:
            task = self._expense_scan_open_tasks(project)[:1]
            values['expense_scan_task_id'] = task.id or False

        # La commande à refacturer n'existe qu'avec les Ventes : sans elles,
        # ni le modèle ni ces champs ne sont là.
        if reinvoice and 'sale.order' in self.env:
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

        Un module de suivi des missions peut les enregistrer comme des
        saisies d'absence portant un projet : c'est la seule source capable
        de dire sur quelle mission un salarié se trouvait un jour donné. On
        les reconnaît à la présence du projet sur l'absence.

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
        """Projets des saisies de mission couvrant cette date.

        En droits élevés : un chef d'équipe qui scanne pour un salarié ne
        voit pas forcément ses absences. On n'en retient que les projets.
        """
        if 'hr.leave' not in self.env:
            return self.env['project.project']
        Leave = self.env['hr.leave'].sudo()
        if 'project_id' not in Leave._fields:
            return self.env['project.project']  # aucun module de missions
        missions = Leave.search([
            ('employee_id', '=', employee.id),
            ('project_id', '!=', False),
            ('state', '!=', 'refuse'),
            ('request_date_from', '<=', scan_date),
            ('request_date_to', '>=', scan_date),
        ])
        return missions.project_id.with_env(self.env)

    def _expense_scan_only_project_of(self, employee):
        """Repli sans missions datées : le projet du salarié, s'il n'y en a qu'un.

        Le critère retenu : les projets où le salarié a une tâche — mais on
        ne tranche pas à sa place dès qu'il y en a plusieurs.
        """
        user = employee.user_id
        if not user:
            return self.env['project.project']
        tasks = self.env['project.task'].sudo().search([('user_ids', 'in', user.id)])
        projects = tasks.project_id.with_env(self.env)
        return projects if len(projects) == 1 else self.env['project.project']
