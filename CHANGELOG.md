# Journal des changements

## 19.0.2.6.4

- **Ticket à plusieurs taux de TVA** : la dépense retenait la taxe par
  défaut de la catégorie plutôt que celle du ticket. Elle prend désormais
  le taux le plus élevé effectivement imprimé (le montant de TVA, lui,
  était déjà juste — c'est la taxe associée qui était en cause).

## 19.0.2.6.3

- **TVA lue à côté de la plaque, sur les tickets d'automate** : l'en-tête
  d'un tableau de taxe se retrouve parfois, à cause de l'OCR, accolé à un
  total voisin sur la même ligne (« TOTAL EN EUROS : 15,80 HT TVA TTC »).
  Le module y lisait ce total comme s'il était la TVA elle-même (10 fois
  le montant réel). Corrigé, sans perdre la lecture des tickets à deux
  taux dont les lignes citent, elles aussi, HT/TVA/TTC mais entourés de
  montants.

## 19.0.2.6.2

- **Taux de TVA mal reconnu sur les tickets « Code Taux HT Montant TTC »**
  (caisses de restauration rapide notamment) : l'en-tête du tableau de taxe
  n'était pas reconnu, faute du mot « TVA » (remplacé par « Montant ») ; le
  taux retombait alors sur celui, par défaut, de la catégorie — potentiellement
  différent de celui imprimé sur le ticket. Corrigé.

## 19.0.2.6.1

- **Conservation du texte lu portée à 10 ans par défaut** (au lieu de 365 jours) :
  c'est la durée que le Code de commerce impose pour les pièces comptables
  (art. L123-22), tickets compris. Migration pour les sociétés restées sur
  l'ancien défaut.

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
  dépenses soumises au bout d'un délai réglable par société (0 = jamais).
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
