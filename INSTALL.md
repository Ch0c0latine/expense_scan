# Installation sur le serveur

Procédure pour `green-engine.eu` (Odoo 19, service `odoo19`, addons custom
dans `/opt/odoo/19/custom-addons/`, base `sandbox`).

Chaque bloc est à exécuter en SSH sur le serveur.

---

## 1. Désinstaller les trois modules OCR achetés

⚠️ **À lire avant** : les modules `account_ai_ocr`, `tus_odoo_ocr_ai_base` et
`tus_odoo_ocr_ai_expense` dépendent de `account`, `mail` et `hr_expense`,
qui sont des applications **Odoo standard**. Il ne faut surtout pas les
désinstaller : `hr_expense` est indispensable au nouveau module, et
désinstaller `account` supprimerait toute la comptabilité. Seuls les trois
modules achetés sont à retirer.

### 1.1 Depuis l'interface (recommandé)

`Apps` → retirer le filtre **Apps** de la barre de recherche → chercher
chaque module → menu ⋮ → **Uninstall**, dans cet ordre :

1. `Odoo OCR Using AI - Expense` (`tus_odoo_ocr_ai_expense`)
2. `Odoo OCR Using AI Base` (`tus_odoo_ocr_ai_base`)
3. `AI/OCR Invoice & Bill Digitization` (`account_ai_ocr`)

Odoo affiche la liste des modules entraînés par chaque désinstallation :
vérifier qu'elle ne contient aucune application standard avant de confirmer.

### 1.2 Ou en ligne de commande

```bash
sudo systemctl stop odoo19
```

```bash
sudo -u odoo /opt/odoo/19/venv/bin/python /opt/odoo/19/odoo/odoo-bin -c /etc/odoo19.conf -d sandbox --uninstall tus_odoo_ocr_ai_expense,tus_odoo_ocr_ai_base,account_ai_ocr --stop-after-init
```

> Si le chemin du virtualenv n'est pas celui-là, le retrouver avec :
> `systemctl cat odoo19 | grep -i exec`

### 1.3 Retirer les répertoires des modules

Une fois la désinstallation confirmée dans Odoo :

```bash
sudo mv /opt/odoo/19/custom-addons/account_ai_ocr /opt/odoo/19/custom-addons/tus_odoo_ocr_ai_base /opt/odoo/19/custom-addons/tus_odoo_ocr_ai_expense /root/ocr_modules_retires/
```

(Un `mv` plutôt qu'un `rm` : si un enregistrement résiduel réapparaît, les
fichiers sont encore là pour rejouer une désinstallation propre.)

### 1.4 Paquets Python laissés en place

`account_ai_ocr` avait fait installer `pytesseract`, `pdf2image` et
`Pillow`. **Ne pas les désinstaller** :

- `Pillow` est une dépendance d'Odoo lui-même ;
- `pytesseract` et `pdf2image` servent de repli au nouveau module
  (moteur Tesseract, justificatifs PDF).

---

## 2. Installer les dépendances du nouveau module

```bash
sudo apt update && sudo apt install -y libgl1 libglib2.0-0
```

`rapidocr` s'appuie sur `opencv-python`, qui réclame ces deux bibliothèques
système même sans interface graphique.

```bash
sudo -u odoo /opt/odoo/19/venv/bin/pip install rapidocr onnxruntime
```

Vérification rapide, hors Odoo :

```bash
sudo -u odoo /opt/odoo/19/venv/bin/python -c "import cv2, onnxruntime, rapidocr; print(cv2.__version__, onnxruntime.__version__)"
```

<details>
<summary>Variante sans bibliothèques graphiques système</summary>

Pour éviter `libgl1`, on peut remplacer OpenCV par sa variante headless
après installation :

```bash
sudo -u odoo /opt/odoo/19/venv/bin/pip install rapidocr onnxruntime && sudo -u odoo /opt/odoo/19/venv/bin/pip uninstall -y opencv-python && sudo -u odoo /opt/odoo/19/venv/bin/pip install opencv-python-headless
```

`pip` signalera une dépendance non satisfaite : c'est attendu, les deux
paquets fournissent le même module `cv2`.
</details>

Optionnel, pour garder Tesseract en repli et lire les PDF :

```bash
sudo apt install -y tesseract-ocr tesseract-ocr-fra poppler-utils
```

---

## 3. Déployer le module

```bash
sudo -u odoo git clone <URL_DU_DEPOT> /opt/odoo/19/custom-addons/expense_scan
```

Puis, à chaque mise à jour ultérieure :

```bash
cd /opt/odoo/19/custom-addons/expense_scan && sudo -u odoo git pull
```

---

## 4. Installer dans Odoo

```bash
update-odoo-modules
```

Puis `Apps` → **Update Apps List** → chercher *Scan de tickets de caisse* →
**Install**.

---

## 5. Premier réglage

`Paramètres` → `Notes de frais` → **Scan des tickets de caisse**

1. Vérifier que l'**état du moteur** affiche `RapidOCR ... : disponible`.
2. Cliquer **Tester et précharger le moteur**. Le premier appel télécharge
   les modèles (quelques dizaines de Mo) : compter 10 à 60 s selon la
   connexion. Les suivants sont instantanés.
3. Le message doit indiquer le texte lu sur l'image de contrôle
   (`TOTAL 12,34 EUR`).

Le dossier des modèles est créé automatiquement dans le répertoire de
données d'Odoo. Pour le retrouver :

```bash
grep -i data_dir /etc/odoo19.conf
```

---

## 6. Vérifier de bout en bout

1. Depuis un téléphone, ouvrir Odoo → **Notes de frais** → **Mes frais**.
2. Appuyer sur **Scan** : la feuille système doit proposer *Appareil photo*
   et *Photothèque*.
3. Photographier un ticket. Après une à deux secondes, la fiche s'ouvre
   avec le ticket en haut et les champs remplis en dessous.
4. Sur ordinateur, ouvrir la même dépense : le ticket doit s'afficher dans
   un volet à gauche du formulaire.

---

## En cas de problème

Suivre le journal pendant le scan :

```bash
sudo tail -f -n0 /var/log/odoo/odoo19.log
```

| Symptôme | Piste |
|---|---|
| « Aucun moteur OCR disponible » | `pip install rapidocr onnxruntime` non fait, ou fait dans le mauvais environnement Python. |
| « ImportError: libGL.so.1 » | `sudo apt install libgl1`, ou passer à `opencv-python-headless`. |
| Le préchauffage échoue sur un téléchargement | Le service n'a pas accès à Internet, ou le dossier des modèles n'est pas inscriptible. Renseigner un chemin explicite dans les réglages. |
| Le ticket est mal recadré | Désactiver **Recadrer le ticket** dans les réglages et relancer l'analyse pour comparer. |
| Rien ne se passe au clic sur *Scan* | Vider le cache des assets : `Paramètres` → mode développeur → *Regenerate Assets Bundles*. |
