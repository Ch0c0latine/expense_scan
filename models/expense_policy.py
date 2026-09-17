# -*- coding: utf-8 -*-
# Copyright 2026 Yves Vallée
# License LGPL-3.0 or later (https://www.gnu.org/licenses/lgpl-3.0).
"""Règles de dépenses : bonne conduite de l'entreprise, exigences des clients.

Un ensemble de règles vaut pour toutes les dépenses — plafonds de repas et
d'hôtel, dépenses personnelles à écarter — ou pour les missions de certains
clients, qui imposent souvent les leurs : classe de transport, catégorie de
véhicule de location, plafonds propres.

Le contrôle alerte, il ne bloque pas : un dépassement peut être justifié,
et une lecture de ticket peut se tromper. Deux niveaux donc, la
transgression (« dépasse ») et le soupçon (« à vérifier »), affichés en tête
de la dépense et filtrables dans la liste.
"""
import re

from odoo import _, api, fields, models

from ..ocr import lexicon

FAMILIES = [
    ('lodging', "Hébergement"),
    ('meal', "Repas"),
    ('train_air', "Train / avion"),
    ('car_rental', "Location de véhicule"),
    ('taxi', "Taxi / transports urbains"),
    ('fuel', "Carburant"),
    ('toll_parking', "Péages et parkings"),
    ('telecom', "Communication"),
]
MEAL_PERIODS = [
    ('breakfast', "Petit-déjeuner"),
    ('lunch', "Déjeuner"),
    ('dinner', "Dîner"),
    ('lunch_dinner', "Déjeuner et dîner du même jour"),
    ('day', "Tous les repas du même jour"),
]
#: Code ACRISS d'un véhicule de location : sa première lettre dit la
#: catégorie (M, N, E, H : mini et économique ; au-delà, plus cher).
ACRISS_RE = re.compile(r"\b([MNEHCDIJSRFGPULWOX])[BCDWVLSTFJXPQZEMRHYNGK][MNCABD][RNDQHIECLSABMFVZUX]\b")
ECONOMY_ACRISS = set("MNEH")
#: Mots qui, sur un contrat de location, trahissent une catégorie supérieure.
RENTAL_UPGRADE_WORDS = ("premium", "prestige", "luxe", "luxury", "suv", "full size", "fullsize")


class ExpenseScanPolicy(models.Model):
    _name = 'expense.scan.policy'
    _description = "Règles de dépenses"
    _order = 'name'

    name = fields.Char(string="Nom", required=True)
    active = fields.Boolean(default=True)
    apply_to_all = fields.Boolean(
        string="Toutes les dépenses",
        help="Contrôle chaque dépense, avec ou sans mission : les règles de "
             "bonne conduite de l'entreprise. Sinon, seules les missions des "
             "clients ou les missions désignées ci-dessous sont concernées.")
    company_id = fields.Many2one(
        'res.company', string="Société",
        help="Vide : les règles valent pour toutes les sociétés.")
    partner_ids = fields.Many2many(
        'res.partner', string="Clients",
        help="Les règles s'appliquent aux missions de ces clients, et de "
             "leurs établissements.")
    project_ids = fields.Many2many(
        'project.project', string="Missions",
        help="Missions soumises à ces règles quand leur client, dans Odoo, "
             "n'est pas le client final.")
    rule_ids = fields.One2many('expense.scan.policy.rule', 'policy_id', string="Règles")
    note = fields.Text(string="Notes")

    @api.model_create_multi
    def create(self, vals_list):
        policies = super().create(vals_list)
        policies._expense_scan_recheck()
        return policies

    def write(self, vals):
        result = super().write(vals)
        self._expense_scan_recheck()
        return result

    def _expense_scan_recheck(self):
        """Recalcule les alertes des dépenses encore en cours.

        Le chargement des exemples à l'installation le demande une seule
        fois, à la fin, plutôt qu'à chaque règle créée.
        """
        if self.env.context.get('expense_scan_no_recheck'):
            return
        self.env['expense.scan.policy']._expense_scan_recheck_all()

    @api.model
    def _expense_scan_recheck_all(self):
        expenses = self.env['hr.expense'].sudo().search([
            ('state', 'in', ('draft', 'submitted', 'approved')),
        ])
        expenses._expense_scan_recompute_policy()


class ExpenseScanPolicyRule(models.Model):
    _name = 'expense.scan.policy.rule'
    _description = "Règle de frais"
    _order = 'sequence, id'

    policy_id = fields.Many2one('expense.scan.policy', required=True, ondelete='cascade')
    sequence = fields.Integer(default=10)
    name = fields.Char(
        string="Règle", required=True,
        help="Le texte rappelé au salarié en cas de dépassement.")
    family = fields.Selection(
        FAMILIES, string="Famille de frais",
        help="Les catégories de dépenses de cette famille, telles que le "
             "module les reconnaît. Ignorée si des catégories sont choisies.")
    product_ids = fields.Many2many(
        'product.product', string="Catégories",
        domain=[('can_be_expensed', '=', True)])
    rule_type = fields.Selection([
        ('max_amount', "Montant maximal par dépense"),
        ('daily_max', "Montant maximal par jour"),
        ('forbidden_words', "Mots interdits sur le justificatif"),
        ('rental_class', "Location : catégorie économique"),
    ], string="Contrôle", required=True, default='max_amount')
    meal_period = fields.Selection(MEAL_PERIODS, string="Repas")
    amount = fields.Float(string="Plafond TTC", digits='Account')
    per_night = fields.Boolean(
        string="Par nuitée", help="Le plafond vaut pour une nuit : le montant "
                                  "est divisé par le nombre de nuitées.")
    extra_amount = fields.Float(string="Supplément", digits='Account')
    extra_cities = fields.Char(
        string="Villes du supplément",
        help="Le supplément s'ajoute au plafond quand le justificatif cite "
             "l'une de ces villes.")
    words = fields.Text(
        string="Mots", help="Un par ligne. Leur présence sur le justificatif "
                            "ou dans la description déclenche l'alerte.")

    @api.model_create_multi
    def create(self, vals_list):
        rules = super().create(vals_list)
        rules.policy_id._expense_scan_recheck()
        return rules

    def write(self, vals):
        result = super().write(vals)
        self.policy_id._expense_scan_recheck()
        return result


class HrExpense(models.Model):
    _inherit = 'hr.expense'

    expense_scan_policy_alert = fields.Text(
        string="Règles de dépenses", compute='_compute_expense_scan_policy',
        store=True, readonly=True)
    expense_scan_policy_breach = fields.Boolean(
        string="Hors règles", compute='_compute_expense_scan_policy',
        store=True, readonly=True)

    #: Champs dont dépend le contrôle d'une dépense.
    POLICY_FIELDS = ('product_id', 'total_amount', 'total_amount_currency', 'date',
                     'scan_time', 'name', 'expense_scan_nights', 'scan_raw_text',
                     'project_id', 'employee_id', 'expense_scan_merchant')

    @api.depends(*POLICY_FIELDS)
    def _compute_expense_scan_policy(self):
        for expense in self:
            findings = expense._expense_scan_policy_findings()
            expense.expense_scan_policy_alert = "\n".join(
                ("⚠ " if breach else "? ") + message for breach, message in findings) or False
            expense.expense_scan_policy_breach = bool(findings)

    def _expense_scan_recompute_policy(self):
        if not self:
            return
        for name in ('expense_scan_policy_alert', 'expense_scan_policy_breach'):
            self.env.add_to_compute(self._fields[name], self)
        self.flush_recordset(['expense_scan_policy_alert', 'expense_scan_policy_breach'])

    @api.model_create_multi
    def create(self, vals_list):
        expenses = super().create(vals_list)
        expenses._expense_scan_policy_siblings()._expense_scan_recompute_policy()
        return expenses

    def write(self, vals):
        checked = any(name in vals for name in self.POLICY_FIELDS)
        # Le plafond journalier dépend des autres repas du jour : ceux du
        # jour quitté, si la date ou le salarié change, comme ceux du nouveau.
        before = self._expense_scan_policy_siblings() if checked else self.browse()
        result = super().write(vals)
        if checked:
            (before | self._expense_scan_policy_siblings())._expense_scan_recompute_policy()
        return result

    def _expense_scan_policy_siblings(self):
        """Autres dépenses du même salarié, les mêmes jours."""
        expenses = self.filtered(lambda e: e.employee_id and e.date)
        if not expenses:
            return self.browse()
        domain = ['|'] * (len(expenses) - 1)
        for expense in expenses:
            domain += ['&', ('employee_id', '=', expense.employee_id.id),
                       ('date', '=', expense.date)]
        return self.sudo().search(domain) - expenses

    # ------------------------------------------------------------------
    # Contrôle
    # ------------------------------------------------------------------

    def _expense_scan_policies(self):
        self.ensure_one()
        project = self.sudo().project_id
        domain = [('apply_to_all', '=', True)]
        if project:
            domain = ['|', ('project_ids', 'in', project.id)] + domain
            if project.partner_id:
                domain = ['|', ('partner_ids', 'parent_of', project.partner_id.id)] + domain
        company = self.company_id or self.env.company
        domain += ['|', ('company_id', '=', False), ('company_id', '=', company.id)]
        return self.env['expense.scan.policy'].sudo().search(domain)

    def _expense_scan_family(self):
        self.ensure_one()
        template = self.product_id.product_tmpl_id
        families = self.env['product.template'].sudo()._expense_scan_family_templates()
        return families.get(template)

    def _expense_scan_meal_period(self):
        """Petit-déjeuner, déjeuner, dîner — d'après la description, puis l'heure."""
        self.ensure_one()
        text = lexicon.fold(self.name)
        if re.search(r"\bpetit\s*dej|\bbreakfast\b|\bcolazione\b|\bfruhstuck", text):
            return 'breakfast'
        if re.search(r"\bdejeuner\b|\bmidi\b|\blunch\b|\bpranzo\b|\bmittag", text):
            return 'lunch'
        if re.search(r"\bdiner\b|\bsoir\b|\bdinner\b|\bcena\b|\babend", text):
            return 'dinner'
        match = re.match(r"(\d{1,2}):(\d{2})", self.scan_time or '')
        if match:
            moment = int(match.group(1)) + int(match.group(2)) / 60.0
            if moment < 10.5:
                return 'breakfast'
            if moment < 16:
                return 'lunch'
            if moment >= 17:
                return 'dinner'
        return None

    def _expense_scan_rule_applies(self, rule, family):
        if rule.product_ids:
            return self.product_id in rule.product_ids
        # Ni famille ni catégorie : la règle vaut pour toutes les dépenses.
        return not rule.family or rule.family == family

    def _expense_scan_policy_findings(self):
        """``[(transgression, message)]`` des règles qui visent cette dépense."""
        self.ensure_one()
        if not self.product_id:
            return []
        policies = self._expense_scan_policies()
        if not policies:
            return []
        family = self._expense_scan_family()
        period = self._expense_scan_meal_period()
        amount = self.total_amount
        currency = self.company_currency_id
        text = lexicon.fold(" ".join(filter(None, (
            self.name, self.expense_scan_merchant, self.scan_raw_text))))
        findings = []
        undecided_limits = []

        def money(value):
            return "%s %s" % (("%.2f" % value).replace('.', ','), currency.symbol or '€')

        # Un plafond journalier respecté couvre les repas pris ce jour-là :
        # un dîner un peu cher après un déjeuner léger reste dans les clous.
        applicable = policies.rule_ids.filtered(
            lambda rule: self._expense_scan_rule_applies(rule, family))
        daily_rules = applicable.filtered(lambda rule: rule.rule_type == 'daily_max')
        day_within_limit = bool(daily_rules) and all(
            (total := self._expense_scan_daily_total(rule, family)) is None
            or currency.compare_amounts(total, rule.amount) <= 0
            for rule in daily_rules)

        for rule in applicable:
            if rule.rule_type == 'max_amount':
                if rule.meal_period and day_within_limit:
                    continue
                if rule.meal_period and rule.meal_period not in ('lunch_dinner', 'day'):
                    if period is None:
                        undecided_limits.append((rule.amount, rule.name))
                        continue
                    if rule.meal_period != period:
                        continue
                value = amount
                if rule.per_night:
                    value = amount / max(self.expense_scan_nights or 1, 1)
                limit = rule.amount
                cities = [lexicon.fold(city) for city in (rule.extra_cities or '').split(',')]
                if rule.extra_amount and any(city and re.search(r"\b%s\b" % re.escape(city), text)
                                             for city in cities):
                    limit += rule.extra_amount
                if currency.compare_amounts(value, limit) > 0:
                    findings.append((True, _(
                        "%(rule)s — %(value)s%(unit)s pour %(limit)s autorisés.",
                        rule=rule.name, value=money(value),
                        unit=_(" par nuit") if rule.per_night else "", limit=money(limit))))
            elif rule.rule_type == 'daily_max':
                total = self._expense_scan_daily_total(rule, family)
                if total is not None and currency.compare_amounts(total, rule.amount) > 0:
                    findings.append((True, _(
                        "%(rule)s — %(value)s ce jour-là pour %(limit)s autorisés.",
                        rule=rule.name, value=money(total), limit=money(rule.amount))))
            elif rule.rule_type == 'forbidden_words':
                hits = [word for word in lexicon.split_keywords(rule.words)
                        if re.search(r"\b%s\b" % re.escape(word), text)]
                if hits:
                    findings.append((False, _(
                        "%(rule)s — le justificatif mentionne « %(words)s ».",
                        rule=rule.name, words=", ".join(hits))))
            elif rule.rule_type == 'rental_class':
                codes = [m.group(0) for m in ACRISS_RE.finditer((self.scan_raw_text or '').upper())
                         if m.group(1) not in ECONOMY_ACRISS]
                words = [word for word in RENTAL_UPGRADE_WORDS
                         if re.search(r"\b%s\b" % re.escape(word), text)]
                if codes or words:
                    findings.append((False, _(
                        "%(rule)s — catégorie à vérifier (%(hints)s).",
                        rule=rule.name, hints=", ".join(codes + words))))

        if undecided_limits and period is None:
            low = min(undecided_limits)
            high = max(undecided_limits)
            if currency.compare_amounts(amount, high[0]) > 0:
                findings.append((True, _(
                    "Repas de %(value)s : au-delà de tous les plafonds (%(limit)s au plus).",
                    value=money(amount), limit=money(high[0]))))
            elif currency.compare_amounts(amount, low[0]) > 0:
                findings.append((False, _(
                    "Repas de %(value)s sans heure lisible : au-delà de « %(rule)s » "
                    "s'il s'agit de ce repas. Précisez-le dans la description.",
                    value=money(amount), rule=low[1])))
        return findings

    def _expense_scan_daily_total(self, rule, family):
        """Total des repas du jour visés par la règle, ou ``None``."""
        periods = ('lunch', 'dinner') if rule.meal_period == 'lunch_dinner' else None
        if periods and self._expense_scan_meal_period() not in periods:
            return None
        same_day = self.sudo().search([
            ('employee_id', '=', self.employee_id.id),
            ('date', '=', self.date),
            ('id', '!=', self._origin.id or 0),  # 0 : dépense pas encore créée
            ('state', '!=', 'refused'),
        ])
        total = self.total_amount
        for other in same_day:
            if not other._expense_scan_rule_applies(rule, other._expense_scan_family()):
                continue
            if periods and other._expense_scan_meal_period() not in periods:
                continue
            total += other.total_amount
        return total
