# Journal des changements

## 19.0.2.6.0 — Fiabilisation

Aucun changement de fonctionnement visible : ce qui pouvait faire tomber un
worker ou garder trop longtemps des données personnelles est corrigé.

- **Analyse interrompue** : une analyse « en cours » depuis plus de 15 minutes
  passe en erreur au lieu d'être relancée à l'infini par la fiche et par la
  tâche planifiée.
- **Plafonds d'entrée** : fichier de 25 Mo au plus, image réduite au décodage
  au-delà de 50 mégapixels (refusée au-delà de 250), PDF rendu à une
  résolution bornée et avec un délai.
- **Droits avant verrou** : le démarrage d'une analyse contrôle le droit
  d'écriture avant de verrouiller la ligne.
- **Données personnelles** : le texte lu sur un justificatif est effacé des
  dépenses soumises au bout de 365 jours (réglable par société, 0 = jamais).
  Le journal de test du corpus n'écrit plus les textes.
- **Historique des enseignes cloisonné par société.**
- **Banc d'évaluation** (`tools/bench.py`) : rejeu de l'analyse sur un instantané
  de textes lus, sans Odoo ; le calcul de catégorie vit désormais dans
  `ocr/categorize.py`, partagé avec le module.
- `tools/check_ocr.py` devient `tools/check.py` (syntaxe et imports).
- Avertissement de vue « lien sans role » supprimé.

## 19.0.2.5.x

Analyse en arrière-plan avec progression, indications par champ à la place du
bandeau, choix appareil photo / galerie / fichiers, menu « Fiches de frais »
par période, ordre chronologique des dépenses, « Retour brouillon », reprise
de la raison de déplacement, corrections de lecture (péages, factures en
ligne, TVA).
