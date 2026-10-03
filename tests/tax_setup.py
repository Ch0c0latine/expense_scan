# -*- coding: utf-8 -*-
# Copyright 2026 T.T.C. SAS
# License LGPL-3.0 or later (https://www.gnu.org/licenses/lgpl-3.0).
"""What a test that creates a tax needs, whatever the state of the database."""


def ensure_fiscal_country(env):
    """Give the company a fiscal country and the tax group of that country.

    A tax takes the country of its company, then the tax group of that
    country. On a database without a chart of accounts the company may have no
    country, or one without a tax group: creating a tax then fails.
    """
    company = env.company
    Group = env['account.tax.group']
    if not company.account_fiscal_country_id:
        country = Group.search([('company_id', '=', company.id)], limit=1).country_id
        company.account_fiscal_country_id = country or env.ref('base.fr')
    country = company.account_fiscal_country_id
    if not Group.search([('company_id', '=', company.id), ('country_id', '=', country.id)]):
        Group.create({'name': "Tax group (test)", 'company_id': company.id, 'country_id': country.id})
