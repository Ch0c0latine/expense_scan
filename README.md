# Scan de tickets de caisse — module Odoo 19

Photographier un ticket depuis son téléphone et obtenir une note de frais
pré-remplie, relue côte à côte avec l'image du ticket.

Tout tourne **en local** sur le serveur : pas de compte IAP, pas de clé
d'API, pas de jeton facturé, aucun document envoyé à un tiers.

---

## Ce que fait le module

| Étape | Détail |
|---|---|
| **1 appui** | Le bouton **Scan** (mobile) / **Upload** (bureau) de la liste des notes de frais ouvre directement l'appareil photo ou la photothèque. |
| **Recadrage** | Les bords du ticket sont détectés dans la photo, la perspective est corrigée, l'image est découpée. |
| **Redressage** | L'inclinaison résiduelle des lignes est mesurée puis annulée ; une photo prise de travers (quart de tour) est remise droite. |
| **Lecture** | Les réseaux PP-OCR lisent le ticket (détection des zones de texte + reconnaissance), en ~0,5 à 1,5 s sur un processeur récent. |
| **Extraction** | Marchand, date, total TTC, devise et TVA sont extraits, chacun avec un indice de fiabilité. |
| **Vérification** | La fiche s'ouvre avec le ticket d'un côté et les champs de l'autre, et un bandeau liste les champs à relire. |

Le module **ne valide jamais** une dépense automatiquement : il pré-remplit,
signale ce qui est douteux, et laisse la décision à l'utilisateur.

### Ce qu'il ajoute à Odoo 19

Odoo 19 Community fournit déjà le bouton d'envoi de justificatif et le volet
d'aperçu du reçu. Le module s'y greffe plutôt que de les réécrire, et
comble ce qui manque :

- la lecture OCR elle-même (réservée à l'offre Enterprise via IAP) ;
- le recadrage et le redressage de la photo ;
- l'ouverture directe de la fiche de vérification après un scan unique
  (Odoo renvoie sinon vers une liste) ;
- l'aperçu du ticket sur téléphone et sur écran de moins de 1400 px, où
  Odoo n'affiche aucun volet ;
- le filtre `image/*` sur le sélecteur de fichier, qui fait apparaître
  l'appareil photo en premier choix sur iOS et Android.

---

## Prérequis serveur

À installer dans l'environnement Python d'Odoo (voir `INSTALL.md` pour les
commandes exactes) :

| Paquet | Rôle | Obligatoire |
|---|---|---|
| `opencv-python` (ou `-headless`) | recadrage, redressage | oui |
| `numpy` | idem | oui (tiré par OpenCV) |
| `Pillow` | lecture EXIF | déjà présent dans Odoo |
| `rapidocr` + `onnxruntime` | moteur OCR recommandé | oui (sauf repli Tesseract) |
| `pytesseract` + `tesseract-ocr-fra` | moteur de repli | non |
| `pdf2image` + `poppler-utils` | justificatifs PDF | non |

Aucune de ces dépendances n'est déclarée dans le manifeste : le module
s'installe même si elles manquent, et l'écran de configuration indique
alors précisément ce qui fait défaut.

**Empreinte** : les modèles PP-OCR pèsent quelques dizaines de mégaoctets et
sont téléchargés une seule fois, au premier scan ou via le bouton *Tester et
précharger le moteur*. Ils sont rangés dans le répertoire de données d'Odoo,
pas dans `site-packages`.

---

## Configuration

**Paramètres → Notes de frais → Scan des tickets de caisse**

- **Moteur OCR** : `Automatique` prend RapidOCR s'il est disponible, sinon
  Tesseract.
- **Threads par worker** : 4 par défaut. Odoo fait déjà tourner plusieurs
  workers ; laisser ONNX ouvrir un thread par cœur dans chacun d'eux dégrade
  le débit au lieu de l'améliorer.
- **Traitement de la photo** : recadrage, redressage, correction des quarts
  de tour, conservation de la photo d'origine.
- **Report comptable** : application de la TVA lue et recherche du
  fournisseur, **désactivés par défaut**. Une lecture erronée y aurait des
  conséquences comptables, contrairement à un montant que l'on relit à
  l'écran.
- **Vue scindée dès 992 px** : affiche le ticket à côté du formulaire sur
  les écrans d'ordinateur portable, qu'Odoo laisse sinon sans aperçu.

Le bouton **Tester et précharger le moteur** charge les modèles et lit une
image de contrôle : à utiliser après chaque redémarrage du service pour que
le premier vrai scan ne paie pas le chargement.

---

## Architecture

```
expense_scan/
├── ocr/                     ← indépendant d'Odoo, testable seul
│   ├── types.py             structures partagées
│   ├── preprocess.py        EXIF, détection du ticket, perspective, redressage
│   ├── engines.py           moteurs interchangeables (RapidOCR, Tesseract)
│   └── parser.py            extraction des champs d'un ticket français
├── models/
│   ├── hr_expense.py        greffe sur create_expense_from_attachments
│   ├── res_company.py       réglages
│   ├── res_config_settings.py
│   └── ir_http.py           expose un réglage au client web
├── static/src/              bouton, aperçu mobile, vue scindée
└── tests/                   parseur et pré-traitement
```

Le dossier `ocr/` ne connaît pas Odoo : on peut y régler les mots-clés et
les seuils, et lancer les tests du parseur, sans base de données.

### Ajouter un moteur

Écrire une sous-classe de `ScanEngine` (`availability()` et `recognize()`),
l'enregistrer dans `ENGINE_CLASSES`, ajouter son code à la sélection de
`res.company`. Rien d'autre à toucher : le pré-traitement et le parseur
travaillent sur la même liste de mots situés.

---

## Limites connues

- **Choix du modèle de reconnaissance** : le module retient le premier jeu
  de modèles qui relit son ticket de contrôle. Sur `rapidocr` 3.9.2, les
  modèles `latin` se chargent mais ne reconnaissent rien ; ils sont donc
  écartés au profit du modèle d'usine PP-OCRv6, qui lit le français
  accentué sans difficulté. Si une version ultérieure de la bibliothèque
  corrige les modèles latins, ils seront à nouveau candidats sans qu'il y
  ait rien à changer.
- **Lignes d'articles** : le module extrait l'en-tête et le total, pas le
  détail des articles. Une note de frais Odoo n'a de toute façon qu'un seul
  montant.
- **Plusieurs taux de TVA** sur un même ticket : le module affiche la somme
  des TVA lues mais n'applique aucun taux, faute de pouvoir en choisir un.
- **Premier scan après redémarrage** : chaque worker Odoo charge les modèles
  de son côté, ce qui ajoute une à trois secondes. Le bouton de préchauffage
  ne couvre que le worker qui a traité la requête.
- **PDF** : seule la première page est lue.

---

## Tests

```bash
odoo -d <base> -i expense_scan --test-enable --test-tags /expense_scan --stop-after-init
```

Les tests de pré-traitement se désactivent d'eux-mêmes si OpenCV n'est pas
installé.

---

## Licence

LGPL-3, comme Odoo Community.
