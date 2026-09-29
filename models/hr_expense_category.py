# -*- coding: utf-8 -*-
# Copyright 2026 Yves Vallée
# License LGPL-3.0 or later (https://www.gnu.org/licenses/lgpl-3.0).
"""Catégorie de la dépense, reconnue à l'enseigne ou aux mots du ticket.

Les indices, du plus sûr au moins sûr :

1. **L'historique partagé des enseignes.** Une dépense soumise porte son
   enseigne et sa catégorie, confirmées par le salarié. La dépense suivante
   de la même enseigne prend la même catégorie, quel que soit le salarié.
   L'enseigne telle que l'OCR l'a lue est conservée à part : un logo est
   mal lu, mais de façon stable, donc « Rchan » renvoie à Auchan dès qu'une
   personne l'a corrigé une fois. C'est l'indice de plus fort poids, sans
   être éliminatoire : un ticket qui indique nettement une autre catégorie
   l'emporte.
2. Indices complémentaires :

   * le **code d'activité** imprimé (APE, MCC) ou retrouvé dans la base
     Sirene grâce au SIRET ;
   * les **mots du ticket**, déclarés sur chaque catégorie, dans toutes les
     langues des déplacements ;
   * une **marque connue** en tête du ticket, d'après le Name Suggestion
     Index d'OpenStreetMap.

Sans certitude, aucune catégorie n'est choisie : la catégorie par défaut
est conservée et le champ est signalé à la vérification. Les forfaits
(kilométrage, barèmes) ne sont pas proposés : ils ne viennent pas d'un
ticket. Ils se reconnaissent à leur prix fixé ou à leur unité de distance ;
une catégorie simplement « sans TVA », comme l'hôtel, reste proposable.
"""
import base64
import logging
import re
from collections import Counter

from odoo import _, api, fields, models
from odoo.tools import file_open

from ..ocr import categorize, lexicon, parser
from ..ocr.types import OcrLine, OcrWord

_logger = logging.getLogger(__name__)

#: Part minimale des dépenses d'une enseigne classées dans une catégorie
#: pour qu'elle s'impose à la suivante.
HISTORY_MAJORITY = 0.6
#: Poids de l'historique d'une enseigne lue à l'identique, puis d'une
#: enseigne seulement ressemblante : suffisant seul, mais battu par un
#: ticket qui indique nettement une autre catégorie.
HISTORY_WEIGHT = 5.0
HISTORY_FUZZY_WEIGHT = 3.0
#: États où la catégorie a été confirmée par quelqu'un.
CONFIRMED_STATES = ('submitted', 'approved', 'posted', 'in_payment', 'paid')


class ProductTemplate(models.Model):
    _inherit = 'product.template'

    expense_scan_keywords = fields.Text(
        string="Mots du ticket",
        help="Mots qui, lus sur un ticket, désignent cette catégorie : un par "
             "ligne, dans toutes les langues utiles, enseignes comprises "
             "(« Ibis », « nocleg », « pedaggio »…). Accents et majuscules "
             "sont ignorés, et une faute de lecture est tolérée sur les mots "
             "de cinq lettres ou plus.\n\n"
             "Les enseignes déjà classées sont apprises d'elles-mêmes, à la "
             "soumission des dépenses : inutile de toutes les saisir ici.",
    )

    @api.model
    def _expense_scan_seed_keywords(self):
        """Propose des mots aux catégories qui n'en ont pas.

        Chaque famille de frais (hôtel, repas, carburant…) est affectée à une
        seule catégorie. Une fiche déjà remplie, même d'un seul mot, n'est
        pas modifiée.
        """
        to_fill = [(template, family)
                   for template, family in self._expense_scan_family_templates().items()
                   if not (template.expense_scan_keywords or '').strip()]
        for template, family in to_fill:
            template.expense_scan_keywords = "\n".join(
                dict.fromkeys(lexicon.DEFAULT_KEYWORDS[family]))
        return len(to_fill)

    @api.model
    def _expense_scan_icon_key(self, template):
        """Nom de l'icône fournie qui convient à cette catégorie, ou ``None``."""
        labels = (template.name, template.default_code)
        family = lexicon.family_of(*labels)
        if family:
            return {'lodging': 'lodging', 'meal': 'meal', 'train_air': 'train_air',
                    'car_rental': 'car_rental', 'taxi': 'taxi', 'fuel': 'fuel',
                    'toll_parking': 'toll_parking', 'telecom': 'telecom'}.get(family)
        words = lexicon.fold(" ".join(label for label in labels if label)).split()
        starts = lambda prefixes: any(w.startswith(p) for w in words for p in prefixes)  # noqa: E731
        if starts(('invit', 'affaire')):
            return 'invitation'
        if starts(('igd', 'bareme', 'forfait')):
            if starts(('logement', 'hebergement', 'nuit')):
                return 'house'
            if starts(('repas', 'meal')):
                return 'meal'
        return None

    @api.model
    def _expense_scan_seed_icons(self):
        """Pose une icône sur les catégories qui n'en ont pas.

        Mêmes couleurs et même trait que les icônes qu'Odoo fournit pour ses
        catégories d'origine. Une catégorie qui a déjà une image, choisie
        par un utilisateur ou livrée par Odoo, n'est pas modifiée.
        """
        seeded = 0
        for template in self.search([('can_be_expensed', '=', True), ('image_1920', '=', False)]):
            key = self._expense_scan_icon_key(template)
            if not key:
                continue
            try:
                with file_open('expense_scan/static/img/categories/%s.svg' % key, 'rb') as icon:
                    template.image_1920 = base64.b64encode(icon.read())
                seeded += 1
            except OSError:
                _logger.warning("Icône de catégorie introuvable : %s", key)
        return seeded

    @api.model
    def _expense_scan_add_keywords(self, additions):
        """Ajoute des mots, par famille, aux catégories qui les servent.

        Seuls les mots absents sont ajoutés, à la fin : les modifications
        déjà faites sur la fiche sont conservées.
        """
        for template, family in self._expense_scan_family_templates().items():
            words = additions.get(family)
            current = template.expense_scan_keywords or ''
            if not words or not current.strip():
                continue
            known = set(lexicon.split_keywords(current))
            missing = [word for word in words if lexicon.fold(word) not in known]
            if missing:
                template.expense_scan_keywords = current.rstrip('\n') + '\n' + '\n'.join(missing)

    def _expense_scan_remove_keywords(self, removals):
        """Retire des mots, par famille, des catégories qui les servent.

        Seuls ces mots sont retirés ; les autres gardent leur écriture et
        leur ordre.
        """
        for template, family in self._expense_scan_family_templates().items():
            words = {lexicon.fold(word) for word in removals.get(family, ())}
            current = template.expense_scan_keywords or ''
            if not words or not current.strip():
                continue
            chunks = [chunk.strip() for chunk in re.split(r"[\n,;]+", current) if chunk.strip()]
            kept = [chunk for chunk in chunks if lexicon.fold(chunk) not in words]
            if len(kept) != len(chunks):
                template.expense_scan_keywords = '\n'.join(kept)

    @api.model
    def _expense_scan_family_templates(self):
        """``{catégorie: famille}`` : la première catégorie active de chaque famille.

        Les catégories archivées sont ignorées : sinon l'ancienne
        « Travel & Accommodation » d'Odoo prendrait la place de l'hôtel.
        """
        distances = self.env['uom.uom']
        for xmlid in ('uom.product_uom_km', 'uom.product_uom_mile'):
            distances |= self.env.ref(xmlid, raise_if_not_found=False) or distances
        templates = self.search([('can_be_expensed', '=', True)], order='sequence, id')
        result = {}
        taken = set()
        for template in templates:
            # Forfait : prix fixé ; kilométrage : unité de distance. Une
            # catégorie « sans TVA » n'est pas un forfait (hôtel, train : pas
            # de TVA récupérable, mais un ticket).
            if template.standard_price or template.uom_id in distances:
                continue
            labels = [template.default_code, template.name]
            labels += [template.with_context(lang=lang).name
                       for lang in ('fr_FR', 'en_US')]
            family = lexicon.family_of(*labels)
            if not family or family in taken:
                continue
            taken.add(family)
            result[template] = family
        return result


class HrExpense(models.Model):
    _inherit = 'hr.expense'

    expense_scan_merchant = fields.Char(
        string="Enseigne",
        copy=False,
        help="Commerce qui a émis le ticket. Corrigez-la si l'analyse l'a mal "
             "lue : à la soumission, l'enseigne et la catégorie servent à "
             "classer les tickets suivants, pour toute l'équipe.",
    )
    expense_scan_merchant_key = fields.Char(
        string="Clé de l'enseigne",
        compute='_compute_expense_scan_merchant_key',
        store=True,
        index=True,
    )
    expense_scan_merchant_read = fields.Char(
        string="Enseigne lue",
        readonly=True,
        copy=False,
        index=True,
        help="L'enseigne telle que l'OCR l'a lue, avant toute correction. "
             "Un logo se lit mal, mais de la même façon à chaque ticket : "
             "c'est ce qui permet de le reconnaître la fois suivante.",
    )
    expense_scan_guessed_product_id = fields.Many2one(
        comodel_name='product.product',
        string="Catégorie proposée",
        readonly=True,
        copy=False,
        ondelete='set null',
    )
    expense_scan_category_reason = fields.Char(
        string="Catégorie reconnue",
        readonly=True,
        copy=False,
    )

    @api.depends('expense_scan_merchant')
    def _compute_expense_scan_merchant_key(self):
        for expense in self:
            expense.expense_scan_merchant_key = (
                lexicon.merchant_key(expense.expense_scan_merchant) or False)

    # ------------------------------------------------------------------
    # Historique partagé
    # ------------------------------------------------------------------

    def _expense_scan_known_merchants(self):
        """Enseignes déjà classées : ``{clé: {'names': …, 'products': …}}``.

        Lu en droits élevés : l'historique est commun à l'équipe, alors qu'un
        salarié ne voit que ses propres dépenses. Seuls des noms d'enseignes
        et des catégories sont renvoyés.

        Limité à la société : l'historique d'une société n'influence pas le
        classement d'une autre, bien que le filtre de droits soit levé ici.

        La dépense analysée est exclue : relancée après approbation, elle
        se reconnaîtrait elle-même et ne corrigerait plus rien.
        """
        companies = self.company_id or self.env.company
        groups = self.sudo()._read_group(
            [('state', 'in', CONFIRMED_STATES),
             ('company_id', 'in', companies.ids),
             ('id', 'not in', self.ids),
             ('product_id', '!=', False),
             '|', ('expense_scan_merchant_key', '!=', False),
                  ('expense_scan_merchant_read', '!=', False)],
            groupby=['expense_scan_merchant_key', 'expense_scan_merchant_read',
                     'expense_scan_merchant', 'product_id'],
            aggregates=['__count'],
        )
        known = {}
        for key, read_key, name, product, count in groups:
            for found in {key, read_key} - {False, None, ''}:
                entry = known.setdefault(found, {'names': Counter(), 'products': Counter()})
                if name:
                    entry['names'][name] += count
                entry['products'][product.id] += count
        return known

    # ------------------------------------------------------------------
    # Reconnaissance
    # ------------------------------------------------------------------

    def _expense_scan_category_is_free(self, company):
        """Indique si la catégorie peut encore être choisie par l'analyse.

        Vrai tant que personne ne l'a choisie : vide, catégorie par défaut,
        ou celle que l'analyse a proposée.
        """
        self.ensure_one()
        product = self.product_id
        return (not product
                or product == company.expense_scan_product_id
                or product == self.expense_scan_guessed_product_id)

    def _expense_scan_target_product(self, values):
        """La catégorie que la dépense aura une fois ces valeurs écrites."""
        if values.get('product_id'):
            return self.env['product.product'].browse(values['product_id'])
        return self.product_id

    def _expense_scan_product_no_vat(self, product):
        """Indique si cette catégorie exclut toute TVA récupérable."""
        if not product:
            return False
        distances = self.env['uom.uom']
        for xmlid in ('uom.product_uom_km', 'uom.product_uom_mile'):
            distances |= self.env.ref(xmlid, raise_if_not_found=False) or distances
        return bool(product.expense_scan_no_vat) or product.uom_id in distances

    def _expense_scan_guessable(self, product, company):
        """Une catégorie qu'un ticket peut désigner."""
        if not product or not product.can_be_expensed:
            return False
        if product.company_id and product.company_id != company:
            return False
        if product.standard_price:
            # Forfait ou barème : son prix est fixé, un ticket n'y mène pas.
            # Une catégorie « sans TVA » reste proposable : l'hôtel ou le
            # train ont un ticket, seule leur TVA n'est pas récupérable.
            return False
        distances = self.env['uom.uom']
        for xmlid in ('uom.product_uom_km', 'uom.product_uom_mile'):
            distances |= self.env.ref(xmlid, raise_if_not_found=False) or distances
        return product.uom_id not in distances

    def _expense_scan_keyword_categories(self, company):
        """``{id de catégorie: mots repliés}`` des catégories qui en déclarent."""
        products = self.env['product.product'].sudo().search([
            ('can_be_expensed', '=', True),
            ('expense_scan_keywords', '!=', False),
            ('company_id', 'in', (False, company.id)),
        ])
        return {
            product.id: lexicon.split_keywords(product.expense_scan_keywords)
            for product in products
            if self._expense_scan_guessable(product, company)
        }

    def _expense_scan_reason(self, reason, number=None):
        """Phrase qui explique l'indice de catégorie retenu.

        ``reason`` : ``(nature, détail)``, tel que le rend ``categorize.score``.
        """
        kind, detail = reason
        if kind == categorize.ACTIVITY:
            return _("d'après le code d'activité %s imprimé", detail)
        if kind == categorize.SIRET:
            return _("d'après le SIRET %(number)s (activité %(naf)s)", number=number, naf=detail)
        if kind == categorize.UNIT:
            return _("d'après un prix au litre ou au kWh")
        if kind == categorize.BRAND:
            return _("d'après la marque « %s »", detail)
        return _("d'après les mots du ticket")

    def _expense_scan_recognize(self, result, company):
        """Enseigne et catégorie du ticket.

        Renvoie ``(enseigne, clé lue, catégorie, raison)`` ; la catégorie et
        la raison sont vides quand rien n'est sûr.
        """
        self.ensure_one()
        lines = [line.text for line in result.lines]
        read_name = result.value('merchant')
        read_key = lexicon.merchant_key(read_name) or False

        known = self._expense_scan_known_merchants()
        key = read_key if read_key in known else lexicon.match_merchant(lines, known)
        merchant = read_name
        if key and known[key]['names']:
            merchant = known[key]['names'].most_common(1)[0][0]

        Product = self.env['product.product'].sudo()
        # Tous les indices s'additionnent : historique de l'enseigne, mots
        # du ticket, code d'activité imprimé ou retrouvé par le SIRET,
        # marque connue.
        family_products = {}
        for template, family in self.env['product.template'].sudo() \
                ._expense_scan_family_templates().items():
            product = template.product_variant_id
            if self._expense_scan_guessable(product, company):
                family_products[family] = product.id
        number = result.value('company_number')
        scores, signals, brand = categorize.score(
            lines, self._expense_scan_keyword_categories(company), family_products,
            activity=result.value('activity'),
            naf_of=lambda: self.env['expense.scan.sirene']._expense_scan_activity(number))
        reasons = {product_id: [(weight, self._expense_scan_reason(reason, number))
                                for weight, reason in found]
                   for product_id, found in signals.items()}

        # La catégorie par défaut n'indique rien sur l'enseigne : c'est celle
        # d'une dépense non classée. La compter dans l'historique la ferait
        # se reproduire (un restaurant scanné pendant un échec de
        # reconnaissance resterait « Dépenses »).
        history = Counter(known[key]['products']) if key else Counter()
        history.pop(company.expense_scan_product_id.id, None)
        if history:
            products = history
            product_id, count = products.most_common(1)[0]
            product = Product.browse(product_id).exists()
            share = count / sum(products.values())
            if share >= HISTORY_MAJORITY and self._expense_scan_guessable(product, company):
                # L'enseigne lue à l'identique pèse plus qu'une ressemblance.
                # Dans les deux cas, un ticket qui indique nettement une autre
                # catégorie (code APE, marque, mots) l'emporte.
                weight = (HISTORY_WEIGHT if key == read_key else HISTORY_FUZZY_WEIGHT) * share
                scores[product.id] = scores.get(product.id, 0.0) + weight
                reasons.setdefault(product.id, []).append((weight, _(
                    "d'après l'enseigne « %s », déjà classée ainsi", merchant or key)))
        if brand and not key:
            # La marque reconnue est préférée à la première ligne du ticket
            # (souvent un logo mal lu ou une adresse).
            merchant = brand

        product_id = lexicon.pick_category(scores)
        # Une ligne de log par scan, pour diagnostiquer le choix d'une
        # catégorie ou l'absence de choix.
        _logger.info(
            "expense_scan : catégorie scores=%s activité=%s enseigne=%s retenue=%s",
            {Product.browse(pid).display_name: round(score, 1) for pid, score in scores.items()},
            result.value('activity'), read_key, product_id)
        if product_id:
            reason = max(reasons[product_id], key=lambda item: item[0])[1]
            return merchant, read_key, Product.browse(product_id), reason
        return merchant, read_key, Product, False

    def _expense_scan_category_values(self, result, company):
        """Valeurs d'enseigne et de catégorie à écrire sur la dépense."""
        self.ensure_one()
        merchant, read_key, product, reason = self._expense_scan_recognize(result, company)
        values = {'expense_scan_merchant_read': read_key}
        # Une enseigne corrigée à la main reste celle du salarié, même si
        # l'analyse est relancée.
        corrected = (self.expense_scan_merchant and self.expense_scan_merchant_key
                     != (self.expense_scan_merchant_read or False)
                     and self.expense_scan_merchant_key != lexicon.merchant_key(merchant))
        if not corrected:
            # Sans enseigne lisible, l'ancienne lecture automatique est
            # effacée.
            values['expense_scan_merchant'] = merchant or False
        if self._expense_scan_category_is_free(company):
            if product:
                values.update({
                    'product_id': product.id,
                    'expense_scan_guessed_product_id': product.id,
                    'expense_scan_category_reason': reason,
                })
            else:
                values.update({
                    'expense_scan_guessed_product_id': False,
                    'expense_scan_category_reason': False,
                })
                if not self.product_id and company.expense_scan_product_id:
                    values['product_id'] = company.expense_scan_product_id.id
        else:
            # Catégorie choisie manuellement : la raison d'un ancien choix
            # automatique ne s'applique plus.
            values['expense_scan_category_reason'] = False
        return values

    @api.model
    def _expense_scan_backfill_merchants(self):
        """Extrait l'enseigne du texte déjà lu des dépenses passées.

        L'historique est ainsi alimenté par toutes les dépenses scannées
        depuis l'installation du module.
        """
        expenses = self.sudo().search([
            ('scan_raw_text', '!=', False),
            ('expense_scan_merchant_read', '=', False),
        ])
        for expense in expenses:
            lines = [
                OcrLine(words=[OcrWord(text=text, score=1.0, left=0.0,
                                       top=index * 20.0, right=10.0 * len(text),
                                       bottom=index * 20.0 + 14.0)])
                for index, text in enumerate(
                    line for line in expense.scan_raw_text.splitlines() if line.strip())
            ]
            name = parser.extract_merchant(lines).value
            if not name:
                continue
            values = {'expense_scan_merchant_read': lexicon.merchant_key(name)}
            if not expense.expense_scan_merchant:
                values['expense_scan_merchant'] = name
            expense.write(values)
        return len(expenses)
