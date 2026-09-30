# -*- coding: utf-8 -*-
# Copyright 2026 Yves Vallée
# License LGPL-3.0 or later (https://www.gnu.org/licenses/lgpl-3.0).
{
    'name': "Receipt Scanner for Expenses",
    'version': '19.0.2.8.6',
    'summary': "Create expenses by photographing receipts: automatic crop and "
               "straightening, fields filled in by a local OCR, side by side "
               "review. No IAP credits, no API key.",
    'description': """
Receipt Scanner for Expenses
============================

Photograph a receipt with your phone and get a filled-in expense, reviewed
side by side with the image of the receipt.

* One button ("Scan" on a phone, "Upload" on a computer) opens the camera or
  the photo library directly.
* The receipt is detected in the photo, cut out and straightened
  (perspective correction and deskewing) before it is read.
* Merchant, date, total, currency and tax are extracted and written on the
  expense; each doubtful field is flagged for review.
* The review screen shows the receipt next to the fields (an image strip at
  the top on a phone), to correct them before submitting.
* Category recognition, multi-piece receipts, manual retouch, projects and
  re-invoicing, expense sheets (PDF and Excel), expense rules, team entry.

The OCR engine runs **on your server**, without an IAP account, API key or
paid token, and without network access at scan time:

* RapidOCR / PP-OCR (neural networks run by ONNX Runtime), the default
* Tesseract, as a fallback

No document leaves the server.

Free module (LGPL-3). If it helps you, you can buy its author a coffee:
https://github.com/sponsors/Ch0c0latine
""",
    'author': "Yves Vallée",
    'website': "https://github.com/Ch0c0latine/expense_scan",
    'category': 'Human Resources/Expenses',
    'license': 'LGPL-3',
    'depends': ['hr_expense', 'project'],
    'data': [
        'security/ir.model.access.csv',
        'report/expense_sheet_report.xml',
        'data/export_templates.xml',
        'data/expense_policies.xml',
        'data/expense_scan_cron.xml',
        'data/menu_icon.xml',
        'views/hr_expense_views.xml',
        'views/hr_employee_views.xml',
        'views/expense_team_views.xml',
        'views/expense_batch_actions.xml',
        'views/expense_sheet_views.xml',
        'views/expense_policy_views.xml',
        'views/product_views.xml',
        'views/res_config_settings_views.xml',
    ],
    'assets': {
        'web.assets_backend': [
            'expense_scan/static/src/scss/expense_scan.scss',
            'expense_scan/static/src/css/control_panel_mobile.css',
            'expense_scan/static/src/js/**/*.js',
            'expense_scan/static/src/js/**/*.xml',
            'expense_scan/static/src/xml/**/*.xml',
        ],
    },
    'demo': [
        'demo/expense_policy_demo.xml',
    ],
    'post_init_hook': 'post_init_hook',
    'installable': True,
    'application': False,
    'auto_install': False,
}
