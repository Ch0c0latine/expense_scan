# -*- coding: utf-8 -*-
# Copyright 2026 T.T.C. SAS
# License LGPL-3.0 or later (https://www.gnu.org/licenses/lgpl-3.0).
{
    'name': "Expense Receipt Scanner",
    'version': '20.0.3.0.1',
    'summary': "A local AI reads photographed receipts and fills in the expense: crop, straighten, "
               "OCR and parsing run on your own server. No API, no fee per scan.",
    'author': "T.T.C. SAS",
    'support': "ttc@green-engine.eu",
    'category': 'Human Resources/Expenses',
    'license': 'LGPL-3',
    'images': ['static/description/banner.png'],
    'depends': ['hr_expense', 'project'],
    # Tesseract, the fallback engine, stays optional.
    'external_dependencies': {
        'python': ['numpy', 'cv2', 'rapidocr', 'onnxruntime', 'pdf2image', 'openpyxl'],
        'bin': ['pdftoppm'],
    },
    'data': [
        'security/ir.access.csv',
        'report/expense_sheet_report.xml',
        'data/export_templates.xml',
        'data/expense_policies.xml',
        'data/expense_scan_cron.xml',
        'data/mail_activity_types.xml',
        'data/menu_icon.xml',
        'views/hr_expense_views.xml',
        'views/hr_employee_views.xml',
        'views/expense_team_views.xml',
        'views/expense_batch_actions.xml',
        'views/expense_sheet_views.xml',
        'views/expense_policy_views.xml',
        'views/expense_invoicing_views.xml',
        'views/expense_vat_views.xml',
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
    'uninstall_hook': 'uninstall_hook',
    'installable': True,
    'application': False,
    'auto_install': False,
}
