# -*- coding: utf-8 -*-
{
    'name': "Scan de tickets de caisse",
    'version': '19.0.1.0.0',
    'summary': "Saisir une dépense en photographiant un ticket : recadrage et "
               "redressage automatiques, pré-remplissage des champs par OCR "
               "local, vérification en vue scindée.",
    'description': """
Scan de tickets de caisse
=========================

Photographier un ticket depuis son téléphone et obtenir une note de frais
pré-remplie, vérifiable côte à côte avec l'image du ticket.

* Un seul bouton (« Scan » sur mobile, « Upload » sur ordinateur) ouvre
  directement l'appareil photo ou la photothèque.
* Le ticket est détecté dans la photo, découpé et redressé (correction de
  perspective + désinclinaison) avant lecture.
* Les champs Marchand, Date, Total, Devise et TVA sont extraits et écrits
  dans la dépense ; chaque champ douteux est signalé pour relecture.
* La vue de vérification affiche le ticket à gauche et les champs à droite
  (bandeau image en haut sur téléphone), pour corriger avant validation.

Le moteur OCR tourne **en local**, sans compte IAP, sans clé d'API, sans
jeton payant et sans accès réseau au moment du scan :

* RapidOCR / PP-OCR (réseaux de neurones exécutés par ONNX Runtime) — défaut
* Tesseract — repli

Aucun document ne quitte le serveur.
""",
    'author': "Yves Vallée",
    'website': "https://green-engine.eu",
    'category': 'Human Resources/Expenses',
    'license': 'LGPL-3',
    'depends': ['hr_expense'],
    'data': [
        'views/hr_expense_views.xml',
        'views/res_config_settings_views.xml',
    ],
    'assets': {
        'web.assets_backend': [
            'expense_scan/static/src/scss/expense_scan.scss',
            'expense_scan/static/src/js/**/*.js',
            'expense_scan/static/src/js/**/*.xml',
            'expense_scan/static/src/xml/**/*.xml',
        ],
    },
    'installable': True,
    'application': False,
    'auto_install': False,
}
