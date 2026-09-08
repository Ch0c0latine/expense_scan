# Environnement de développement Odoo, isolé de la production

Procédure pour `green-engine.eu` : monter une instance Odoo de
développement à partir d'une sauvegarde de la production, sans que les
utilisateurs puissent l'atteindre ni même en soupçonner l'existence.

## Le problème à éviter

Restaurer une seconde base sur l'instance de production fait apparaître le
gestionnaire de bases à tous les visiteurs : Odoo voit plusieurs bases et
demande laquelle utiliser. C'est ce qui s'est produit avec la base
`sandbox`.

La cause n'est pas la seconde base, c'est l'absence de `dbfilter` dans
`/etc/odoo19.conf`. **Cette configuration doit être corrigée avant de créer
la base de développement**, sinon le symptôme revient à l'identique.

## L'architecture

Deux services Odoo sur la même machine, partageant le même serveur
PostgreSQL et rien d'autre.

| | Production | Développement |
|---|---|---|
| service systemd | `odoo19` | `odoo19-dev` (démarré à la demande) |
| configuration | `/etc/odoo19.conf` | `/etc/odoo19-dev.conf` |
| base | `greenengine` | `greenengine_dev` |
| écoute | 8069 sur `0.0.0.0`, derrière nginx | 8070 sur `127.0.0.1` |
| filestore | `data_dir` de production | `/opt/odoo/19/dev-data` |
| addons custom | `/opt/odoo/19/custom-addons` | `/opt/odoo/19/dev-addons`, puis `custom-addons` en lecture |
| accès | https://green-engine.eu | tunnel SSH vers `localhost:8070` |
| journal | `/var/log/odoo/odoo19.log` | `/var/log/odoo/odoo19-dev.log` |

L'instance de développement n'écoutant que sur la boucle locale, elle est
injoignable depuis Internet : ni nginx, ni certificat, ni sous-domaine à
prévoir, et aucun moyen pour un utilisateur d'y atterrir par accident.

---

## 1. Corriger la production (à faire en premier)

```bash
sudo cp /etc/odoo19.conf /etc/odoo19.conf.bak
```

```bash
sudo sed -i -E '/^\s*(db_name|dbfilter)\s*=/d' /etc/odoo19.conf
```

```bash
printf '\ndb_name = greenengine\ndbfilter = ^greenengine$\n' | sudo tee -a /etc/odoo19.conf
```

L'ancrage `^...$` est important : sans lui, `greenengine_dev` serait aussi
retenu par le filtre, et le sélecteur réapparaîtrait.

```bash
sudo systemctl restart odoo19
```

Vérifier que https://green-engine.eu ouvre directement Odoo, sans écran
intermédiaire, **avant de passer à la suite**.

### Renforcement facultatif

```bash
printf 'list_db = False\n' | sudo tee -a /etc/odoo19.conf
```

Cela désactive complètement le gestionnaire de bases, y compris
`/web/database/manager`. C'est la bonne pratique sur une instance exposée,
mais **on perd la sauvegarde et la restauration depuis l'interface web** :
il faut alors passer par `pg_dump` / `pg_restore` (§3 et §9). À n'activer
que si cette contrainte est acceptée.

---

## 2. Paramètres de ce serveur

Relevés en septembre 2026, et utilisés tels quels dans la suite :

| | |
|---|---|
| utilisateur et groupe système | `odoo:odoo` |
| interpréteur | `/opt/odoo/19/venv/bin/python3` |
| `data_dir` de production | `/opt/odoo/19/data` |
| filestore de production | `/opt/odoo/19/data/filestore/greenengine` |
| propriétaire PostgreSQL des bases | `odoo` |

Pour les revérifier si l'installation a changé :

```bash
sudo systemctl cat odoo19 | grep -iE "^User=|^Group=|^ExecStart="
```

```bash
sudo find /opt /var/lib /home -maxdepth 6 -type d -name filestore 2>/dev/null
```

```bash
sudo -u postgres psql -At -c "SELECT pg_get_userbyid(datdba) FROM pg_database WHERE datname='greenengine';"
```

---

## 3. Copier la base

```bash
sudo -u postgres pg_dump -Fc greenengine -f /tmp/greenengine.dump
```

```bash
sudo -u postgres createdb -T template0 -O odoo greenengine_dev
```

```bash
sudo -u postgres pg_restore -d greenengine_dev /tmp/greenengine.dump
```

`pg_restore` affiche des avertissements sur les extensions déjà présentes :
c'est normal et sans conséquence.

---

## 4. Copier le filestore

Sans cette étape, l'instance de développement perd toutes les pièces
jointes : la base ne stocke que leurs métadonnées.

```bash
sudo mkdir -p /opt/odoo/19/dev-data/filestore
```

```bash
sudo cp -a /opt/odoo/19/data/filestore/greenengine /opt/odoo/19/dev-data/filestore/greenengine_dev
```

```bash
sudo chown -R odoo:odoo /opt/odoo/19/dev-data
```

---

## 5. Configuration de l'instance de développement

On part de la configuration de production — elle porte déjà les
identifiants PostgreSQL — et on ne remplace que ce qui doit différer.

```bash
sudo cp /etc/odoo19.conf /etc/odoo19-dev.conf
```

```bash
sudo sed -i -E '/^\s*(db_name|dbfilter|list_db|http_port|http_interface|xmlrpc_port|xmlrpc_interface|gevent_port|longpolling_port|proxy_mode|data_dir|addons_path|logfile|workers|max_cron_threads)\s*=/d' /etc/odoo19-dev.conf
```

Cette liste doit couvrir **exactement** les options réécrites plus bas, y
compris les alias historiques `xmlrpc_port` et `longpolling_port` : le
lecteur de configuration de Python refuse un fichier contenant deux fois la
même option, et un alias survivant ferait écouter l'instance de
développement sur le port de la production.

```bash
sudo tee -a /etc/odoo19-dev.conf > /dev/null <<'EOF'

; --- instance de développement ---
db_name = greenengine_dev
dbfilter = ^greenengine_dev$
list_db = False
http_port = 8070
http_interface = 127.0.0.1
proxy_mode = False
data_dir = /opt/odoo/19/dev-data
addons_path = /opt/odoo/19/odoo/addons,/opt/odoo/19/dev-addons,/opt/odoo/19/custom-addons
logfile = /var/log/odoo/odoo19-dev.log
gevent_port = 8073
workers = 0
max_cron_threads = 0
EOF
```

Contrôle indispensable avant de démarrer quoi que ce soit — la sortie doit
être **vide** :

```bash
sudo grep -oE "^\s*[a-z_]+\s*=" /etc/odoo19-dev.conf | tr -d ' =' | sort | uniq -d
```

Toute option affichée ici est présente deux fois et empêchera Odoo de lire
le fichier.

- `http_interface = 127.0.0.1` : injoignable hors de la machine.
- `workers = 0` : mode multi-thread, plus léger et plus simple à déboguer ;
  une instance de développement n'a pas besoin de processus dédiés.
- `max_cron_threads = 0` : ceinture et bretelles avec la neutralisation,
  aucune tâche planifiée ne s'exécutera.
- `addons_path` place `dev-addons` **avant** `custom-addons`. Les deux sont
  nécessaires : la base copiée a `mission_report` et `auto_backup`
  installés, et sans leur code le registre se charge incomplet
  (`Some modules are not loaded`), ce qui fausse tout test. L'ordre fait le
  reste : un module présent dans les deux répertoires est chargé depuis
  `dev-addons`, donc on teste bien sa version de développement. L'instance
  ne fait que lire `custom-addons`, elle n'y écrit jamais.

```bash
sudo chown odoo:odoo /etc/odoo19-dev.conf && sudo chmod 640 /etc/odoo19-dev.conf
```

---

## 6. Neutraliser la copie

**Étape non facultative.** Une copie de production restaurée telle quelle
enverrait de vrais courriels à de vrais clients dès que ses tâches
planifiées se déclenchent. À lancer **avant le premier démarrage** du
service de développement (§7) : c'est le seul moment où c'est sûr.

```bash
sudo -u odoo /opt/odoo/19/venv/bin/python3 /opt/odoo/19/odoo/odoo-bin neutralize -c /etc/odoo19-dev.conf -d greenengine_dev
```

La commande `neutralize` d'Odoo désactive les serveurs de messagerie
sortante et efface leurs identifiants, en insère un factice pour bloquer
tout repli, désactive toutes les tâches planifiées sauf l'autovacuum,
neutralise les webhooks, réinitialise `database.secret` et pose le
marqueur `database.is_neutralized`.

Trois retouches qu'elle ne fait pas :

```bash
sudo -u postgres psql -d greenengine_dev -c "UPDATE ir_config_parameter SET value='http://localhost:8070' WHERE key='web.base.url';"
```

```bash
sudo -u postgres psql -d greenengine_dev -c "INSERT INTO ir_config_parameter(key,value) VALUES('web.base.url.freeze','True') ON CONFLICT (key) DO UPDATE SET value='True';"
```

```bash
sudo -u postgres psql -d greenengine_dev -c "UPDATE ir_config_parameter SET value = gen_random_uuid()::text WHERE key='database.uuid';"
```

Les deux premières évitent que la copie fabrique des liens pointant vers la
production. La troisième lui donne une identité propre (PostgreSQL 13 ou
plus récent pour `gen_random_uuid()`).

Pour voir ce que la neutralisation va exécuter sans l'appliquer :

```bash
sudo -u odoo /opt/odoo/19/venv/bin/python3 /opt/odoo/19/odoo/odoo-bin neutralize -c /etc/odoo19-dev.conf -d greenengine_dev --stdout
```

---

## 7. Service systemd

```bash
sudo sed -e 's#/etc/odoo19.conf#/etc/odoo19-dev.conf#' -e 's#^Description=.*#Description=Odoo 19 (developpement)#' /etc/systemd/system/odoo19.service | sudo tee /etc/systemd/system/odoo19-dev.service > /dev/null
```

```bash
sudo systemctl daemon-reload
```

Le service n'est volontairement **pas** activé au démarrage : on le lance
quand on en a besoin, et il ne consomme rien le reste du temps.

### Démarrer, arrêter, surveiller

La base `greenengine_dev` existe en permanence dans PostgreSQL ; elle ne se
démarre pas. Ce que l'on démarre et arrête, c'est le service Odoo qui la
sert.

```bash
sudo systemctl start odoo19-dev
```

```bash
sudo systemctl stop odoo19-dev
```

```bash
sudo systemctl restart odoo19-dev
```

```bash
sudo systemctl status odoo19-dev --no-pager
```

```bash
sudo tail -f /var/log/odoo/odoo19-dev.log
```

Pour qu'il remonte automatiquement après un redémarrage du serveur :

```bash
sudo systemctl enable odoo19-dev
```

Trois indépendances à garder en tête : arrêter `odoo19-dev` n'affecte jamais
`odoo19`, ce sont deux services distincts ; fermer le tunnel SSH (§8)
n'arrête pas le service ; et arrêter le service ne détruit rien, la base et
le filestore restent en place.

### Répertoire des addons de développement

```bash
sudo mkdir -p /opt/odoo/19/dev-addons && sudo chown odoo:odoo /opt/odoo/19/dev-addons
```

```bash
sudo -u odoo git clone https://github.com/Ch0c0latine/expense_scan.git /opt/odoo/19/dev-addons/expense_scan
```

C'est ici qu'on teste une branche avant de la fusionner, puis de la déployer
dans `custom-addons`.

---

## 8. Accès

Le serveur n'expose pas SSH sur Internet : `green-engine.eu` ne répond que
sur 80 et 443. L'accès se fait par l'adresse locale de la machine,
`192.168.1.35`, donc **depuis le réseau du bureau**.

```bash
ssh -N -L 8070:127.0.0.1:8070 <utilisateur>@192.168.1.35
```

`-N` n'ouvre pas de shell : la commande ne renvoie rien et reste bloquée,
c'est le signe qu'elle fonctionne. Laisser la fenêtre ouverte et se rendre
sur `http://localhost:8070` ; `Ctrl+C` referme le tunnel sans arrêter le
service.

Deux repères pour être certain de ne pas être sur la production : l'adresse
est en `localhost:8070`, et Odoo affiche la bannière de neutralisation en
haut de l'écran.

Pour un accès depuis l'extérieur, la réponse est un VPN (Tailscale,
WireGuard) — pas l'ouverture du port SSH ni l'exposition du port 8070.

### Tester depuis un téléphone

Le parcours de scan — bouton unique, appareil photo, bandeau d'aperçu — ne
s'éprouve que sur un vrai téléphone, qui ne peut pas emprunter le tunnel
SSH. Il faut exposer l'instance au réseau local, le temps du test.

```bash
sudo sed -i 's/^http_interface = 127.0.0.1/http_interface = 0.0.0.0/' /etc/odoo19-dev.conf && sudo systemctl restart odoo19-dev
```

```bash
sudo ufw allow from 192.168.1.0/24 to any port 8070 proto tcp
```

Le pare-feu n'ouvre par défaut que le 8069, celui de la production : sans
cette règle, le téléphone attend indéfiniment. Elle est restreinte au sous-
réseau local, comme la règle existante.

Le téléphone, sur le même Wi-Fi, se rend alors sur `http://192.168.1.35:8070`.
Le HTTP simple suffit : Odoo ouvre l'appareil photo par un champ fichier
classique, qui n'exige pas de contexte sécurisé — c'est `getUserMedia`, le
flux vidéo en direct, qui réclamerait du HTTPS.

**À défaire dès le test terminé.** Pendant ce temps, n'importe quel appareil
du réseau atteint une base contenant de vraies données clients.

```bash
sudo ufw delete allow from 192.168.1.0/24 to any port 8070 proto tcp
```

```bash
sudo sed -i 's/^http_interface = 0.0.0.0/http_interface = 127.0.0.1/' /etc/odoo19-dev.conf && sudo systemctl restart odoo19-dev
```

---

## 9. Utilisation courante

### Avant chaque mise à jour : le contrôle du paquet `ocr`

```bash
cd /opt/odoo/19/dev-addons/expense_scan && sudo -u odoo /opt/odoo/19/venv/bin/python3 tools/check_ocr.py
```

Une seconde, aucune base de données touchée. Il attrape les erreurs
d'import et de syntaxe, celles qui ne se voient qu'à l'exécution (un champ
de dataclass mal placé, par exemple), et vérifie que le parseur lit encore
correctement un ticket de référence.

Sans ce contrôle, ces erreurs ne se manifestent qu'au chargement du
registre Odoo — et laissent alors l'instance entière hors service, avec un
« Internal Server Error » sur toutes les pages.

### Après chaque `git pull`

**Toujours mettre à jour le module, jamais seulement redémarrer.** Un
redémarrage recharge le code mais ne touche pas au schéma : un nouveau
champ Python reste sans colonne en base, et toute requête sur le modèle
échoue alors avec `UndefinedColumn`. Les vues et les données XML sont dans
le même cas.

```bash
sudo systemctl stop odoo19-dev && sudo -u odoo /opt/odoo/19/venv/bin/python3 /opt/odoo/19/odoo/odoo-bin -c /etc/odoo19-dev.conf -d greenengine_dev -u expense_scan --stop-after-init && sudo systemctl start odoo19-dev
```

Première installation : remplacer `-u` par `-i`.

```bash
sudo systemctl stop odoo19-dev && sudo -u odoo /opt/odoo/19/venv/bin/python3 /opt/odoo/19/odoo/odoo-bin -c /etc/odoo19-dev.conf -d greenengine_dev -i expense_scan --stop-after-init
```

Le journal de cette commande est l'endroit où apparaissent les erreurs
d'import ou de vue, avant qu'elles n'aient la moindre chance d'atteindre la
production.

Suivre le journal de l'instance :

```bash
sudo tail -f /var/log/odoo/odoo19-dev.log
```

### Repartir d'une production fraîche

```bash
sudo systemctl stop odoo19-dev
```

```bash
sudo -u postgres dropdb greenengine_dev && sudo rm -rf /opt/odoo/19/dev-data/filestore/greenengine_dev
```

Puis reprendre aux étapes 3, 4 et 5.

---

## Points de vigilance

- **La base de développement contient de vraies données clients.** La
  neutralisation coupe les envois, pas la confidentialité : c'est une raison
  de plus de ne l'exposer que sur la boucle locale.
- **Les deux instances partagent le processeur et la mémoire.** Une mise à
  jour de module côté développement ralentit la production le temps qu'elle
  dure. Sur ce serveur (6 cœurs, 16 Go) c'est sans gravité, mais mieux vaut
  éviter les heures ouvrées pour les opérations longues.
- **Ne jamais éditer un fichier de `custom-addons` pour les besoins d'un
  test.** L'instance de développement lit ce répertoire, mais c'est le même
  code que sert la production : toute modification y est immédiate et non
  testée. Le travail se fait dans `dev-addons`, qui prime dans
  l'`addons_path`.
- **Après chaque modification de `/etc/odoo19.conf`**, vérifier que le site
  public ouvre bien Odoo directement. La sauvegarde `odoo19.conf.bak` de
  l'étape 1 permet de revenir en arrière.
