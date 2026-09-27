# Usages de Meshtastic

Meshtastic transforme des cartes LoRa bon marché en un réseau maillé qui
transporte des messages texte courts, des positions et des relevés de
capteurs, sans réseau mobile, sans abonnement et sans infrastructure
propre. Chaque section ci-dessous pose un besoin, puis décrit les
fonctions de Meshtastic qui y répondent.

Les briques sur lesquelles reposent tous les usages :

- **La radio LoRa** : une modulation longue portée et basse consommation,
  dans des bandes libres (863–870 MHz en Europe, `EU_868`). Quelques
  kilomètres entre deux nœuds portés à la main au niveau du sol, des
  dizaines depuis un point haut, pour un débit de quelques centaines de
  bits à quelques kilobits par seconde. Les messages sont courts (environ
  200 caractères) : ni voix, ni image, ni fichier.
- **Le maillage** : chaque nœud qui entend un paquet encore inconnu le
  réémet, jusqu'à `hop_limit` fois (3 par défaut), si bien que le réseau
  porte plus loin qu'aucune radio seule. Aucun nœud ne dirige : les nœuds
  rejoignent le réseau, se déplacent ou tombent en panne sans
  configuration.
- **Les canaux et le chiffrement** : chaque canal a un nom et une clé AES ;
  seuls les nœuds qui ont la clé lisent ses paquets. Les messages directs
  sont chiffrés de bout en bout avec la clé publique de chaque nœud (PKI) :
  les relais les transportent sans pouvoir les lire.
- **MQTT, le pont par Internet** (facultatif) : un nœud passerelle relie le
  mesh à un broker MQTT, soit par son propre Wi-Fi, soit par l'app qui lui
  est connectée (proxy). Il publie ce qu'il entend en radio et réinjecte
  ce qui arrive du broker, ce qui relie des mesh que la radio seule
  n'atteint pas. Chaque canal choisit s'il monte vers Internet
  (`uplink_enabled`) et s'il en reçoit (`downlink_enabled`) ; les paquets
  restent chiffrés avec la clé du canal sur le broker. Le broker public
  (`mqtt.meshtastic.org`) sert aussi aux cartes publiques ; un broker
  privé garde le trafic pour soi et l'ouvre à Home Assistant ou à
  d'autres outils. Le mesh fonctionne sans MQTT.
- **Le téléphone ou l'ordinateur comme écran** : un nœud est en général
  couplé à un téléphone (Bluetooth) ou à un ordinateur (USB, Wi-Fi), ou
  fonctionne seul avec son propre écran et ses boutons. L'app affiche les
  messages, la liste des nœuds et une carte.

## Suivi de position

**Besoin** : savoir où se trouvent les membres d'un groupe, un véhicule ou
un objet, sans abonnement à un traceur mobile.

**Réponse de Meshtastic** :

- Un nœud équipé d'un GPS, ou un téléphone qui partage sa position, la
  diffuse régulièrement sur le mesh ; chaque app affiche toutes les
  positions sur sa carte.
- Le rôle `TRACKER` envoie les positions plus souvent et en priorité ;
  l'envoi « intelligent » en émet une dès que le nœud s'est assez déplacé,
  et se tait tant qu'il reste immobile.
- La précision des positions se règle par canal : exacte pour une équipe,
  arrondie à quelques kilomètres sur un canal public pour la vie privée.
- Pour un objet perdu, le rôle `LOST_AND_FOUND` envoie sa position en
  message texte toutes les cinq minutes ; un traceur sur un chien, un vélo
  ou un ballon se retrouve depuis n'importe quel nœud à portée.
- Les nœuds fixes (un relais sur un toit) reçoivent une position fixe une
  fois pour toutes, et apparaissent sur la carte sans GPS.

**Réglages** :

- Nœuds suivis : rôle `TRACKER` (`config.device.role`), GPS activé
  (`config.position.gps_mode: ENABLED`), envoi intelligent activé
  (`position_broadcast_smart_enabled: true`, avec
  `broadcast_smart_minimum_distance` en mètres) et intervalle de base
  court (`position_broadcast_secs`, 15 min par exemple). Sans GPS, c'est
  le téléphone couplé qui fournit la position (partage de position dans
  l'app).
- Précision : sur le canal privé du groupe, précision exacte
  (`module_settings.position_precision: 32`) ; sur le canal public, garder
  une précision grossière (13 bits, environ 2,9 km) ou 0 pour ne rien
  partager.
- Nœud fixe : position fixe (`fixed_position`, ce que pose
  meshtastic-provision), GPS désactivé et intervalle long (12 h).
- MQTT, seulement pour suivre les positions depuis Internet : une
  passerelle avec la montée MQTT activée sur le canal du groupe
  (`uplink_enabled: true`). Les positions montent avec la précision du
  canal : sur le broker public, celles d'un canal à clé connue sont
  lisibles par tous. Pour un suivi précis hors radio, un canal privé, et
  de préférence un broker privé. Les rapports de carte
  (`map_reporting_enabled`, `should_report_location`) ne concernent que
  la carte publique, avec une précision de 12 à 15 bits.

## Communication hors réseau

**Besoin** : garder un groupe en contact là où le réseau mobile ne passe
pas ou est saturé : montagne, mer, forêt, événements très fréquentés.

**Réponse de Meshtastic** :

- Chacun porte un nœud couplé à son téléphone ; les messages passent de
  nœud en nœud par LoRa, et le maillage contourne les obstacles (une
  crête, un bâtiment) par le nœud qui voit les deux côtés.
- Le couplage : Bluetooth.
- Le groupe partage ensuite son canal. Un nœud a jusqu'à 8 canaux : le
  canal primaire (index 0), par défaut `LongFast` avec une clé publique
  connue de tous, et jusqu'à 7 canaux secondaires. Un canal se définit
  par un nom et une clé AES de 256 bits que l'app tire au hasard. Le nom
  du canal primaire fixe aussi la fréquence utilisée dans la bande : on
  garde donc `LongFast` en primaire, pour rester sur la fréquence et le
  canal public de la région, et on ajoute le canal du groupe en
  secondaire. Concrètement, un membre crée ce canal privé
  (nom et clé) et l'app en tire un QR code ou un lien ; les autres le
  scannent et leur nœud rejoint le canal. C'est tout : le téléphone sert
  d'écran et de clavier, la radio est dans le nœud, et le téléphone peut
  rester en mode avion, Bluetooth activé.
- Le canal principal partagé touche tout le groupe ; un message direct
  n'atteint qu'une personne, avec un accusé de réception à la livraison.
- Un nœud tient plusieurs jours sur une petite batterie, bien plus
  longtemps qu'un téléphone qui cherche du réseau.
- Les messages préenregistrés, l'écran et les boutons du nœud permettent
  à quelqu'un sans téléphone de lire et de répondre.
- Pour un événement, quelques nœuds placés en hauteur (toit, mât) comme
  relais couvrent tout le site pour les nœuds de chaque bénévole.

**Réglages** :

- Tous les nœuds : même région (`EU_868`), même preset radio
  (`LONG_FAST` par défaut), rôle `CLIENT`.
- Un canal secondaire privé commun (index 1, rôle `SECONDARY`), le
  canal primaire restant `LongFast` (voir plus haut).
- Relais d'événement : rôle `ROUTER_LATE` ou `ROUTER` (voir
  « Infrastructure communautaire »), alimentation secteur ou batterie
  avec panneau.
- Rien à régler côté MQTT : cet usage fonctionne sans Internet. Si une
  passerelle existe, laisser la montée MQTT coupée sur le canal privé
  (`uplink_enabled: false`) garde les échanges du groupe hors
  d'Internet.


## Secours et résilience

**Besoin** : permettre à un quartier, une commune ou une équipe de secours
de communiquer quand le réseau mobile, le courant ou Internet tombent
(tempête, inondation, panne générale).

**Réponse de Meshtastic** :

- Le mesh ne dépend que des nœuds : ni antenne relais, ni serveur, ni
  Internet. Les nœuds tiennent plusieurs jours sur batterie et petits
  panneaux solaires.
- Des relais installés à l'avance sur des points hauts (toits, châteaux
  d'eau) couvrent toute une zone ; un nœud perdu dans la catastrophe est
  contourné automatiquement par les autres.
- Des canaux privés séparent le trafic d'une équipe du canal public ;
  toute personne équipée d'un nœud peut toujours joindre tout le monde sur
  le canal public.
- Les nœuds Store & Forward gardent les messages récents et les
  retransmettent aux nœuds qui étaient hors de portée ou éteints.
- Les nœuds peuvent être configurés à l'avance et distribués prêts à
  l'emploi (ce que fait meshtastic-provision).

- **Store & Forward** : un nœud doté de plus de mémoire enregistre le
  trafic du canal et le rejoue à la demande aux nœuds qui étaient hors
  ligne.

**Réglages** :

- Relais sur points hauts : rôle `ROUTER` ou `ROUTER_LATE`, position
  fixe, écran et Bluetooth coupés si personne n'y touche, panneau solaire
  dimensionné pour plusieurs jours sans soleil.
- Nœuds portatifs : rôle `CLIENT`, économie d'énergie
  (`config.power.is_power_saving`) sur ceux qui tournent sur batterie.
- Canal d'équipe : canal secondaire privé, préparé à l'avance et
  distribué par QR code (ou écrit par meshtastic-provision).
- Store & Forward sur un nœud ESP32 doté de PSRAM (le module ne démarre
  pas sans) : `store_forward.enabled: true`, `is_server: true`,
  `heartbeat: true` pour que les autres nœuds le découvrent ;
  `history_return_max` et `history_return_window` bornent ce qu'il
  renvoie à la demande.
- MQTT : facultatif, et utile seulement là où Internet survit (un site
  avec liaison satellite ou fibre secourue) : une passerelle relie alors
  les îlots du mesh entre eux. Sur un broker privé, avec la montée et la
  réception activées sur le canal d'équipe ; laisser la réception coupée
  sur `LongFast` pour ne pas inonder la radio locale avec le trafic
  d'ailleurs.

## Infrastructure communautaire

**Besoin** : donner à toute une région un réseau que chacun peut
rejoindre, et relier des régions que la radio seule n'atteint pas.

**Réponse de Meshtastic** :

- Les nœuds bien placés prennent le rôle `ROUTER` ou `ROUTER_LATE` : ils
  relaient le trafic de tous en priorité (ou en dernier recours), coupent
  ce dont ils n'ont pas besoin (écran, Wi-Fi) et fonctionnent sans
  surveillance.
- Sur le canal par défaut (`LongFast` avec la clé connue de tous), tout le
  monde peut parler à tout le monde, sans inscription.
- Les passerelles MQTT publient ce qu'elles entendent sur un broker
  Internet et injectent ce qui en vient : des mesh éloignés se rejoignent
  par Internet. Chaque canal choisit s'il monte (`uplink_enabled`) ou
  descend (`downlink_enabled`), et chaque nœud si ses paquets peuvent être
  montés (`config_ok_to_mqtt`) ou s'il ignore ceux qui sont descendus
  (`ignore_mqtt`).
- Les rapports de carte alimentent les cartes publiques, qui montrent où
  passe le réseau et qui le relaie.
- Les règles de réémission (nombre de sauts, rôles, modes de réémission)
  maîtrisent le temps d'antenne à mesure que le réseau grandit.

**Réglages** :

- Relais : rôle `ROUTER` ou `ROUTER_LATE` selon l'emplacement (voir
  ci-dessous), `hop_limit` à 3, position fixe, intervalle de position et
  de télémétrie longs.
- Passerelle MQTT vers le broker public : `mqtt.enabled: true`, adresse
  `mqtt.meshtastic.org` avec les identifiants publics, racine
  `msh/EU_868` (ou un sous-niveau local convenu, `msh/EU_868/FR/...`),
  `encryption_enabled: true`. Sur `LongFast` : montée activée
  (`uplink_enabled: true`) ; réception (`downlink_enabled`) à activer
  seulement si la communauté veut recevoir d'Internet, car elle réinjecte
  le trafic d'ailleurs sur la radio locale.
- Passerelle sans Wi-Fi : `proxy_to_client_enabled: true`, l'app
  connectée faisant la liaison (voir « MQTT » plus haut).
- Carte publique : `map_reporting_enabled: true` et
  `should_report_location: true`, précision 12 à 15 bits.
- Pour chaque nœud, `config_ok_to_mqtt: true` autorise les passerelles à
  monter ses paquets sur le broker public ; `ignore_mqtt: true` lui fait
  ignorer les paquets venus d'Internet.

**Les rôles de relais** :

- `ROUTER` : nœud d'infrastructure prioritaire. Il réémet sans attendre,
  avant les autres nœuds, ce qui en fait l'épine dorsale du réseau. Il
  coupe l'écran et le Wi-Fi, envoie sa télémétrie toutes les 12 h, ne
  réémet que les types de paquets essentiels et se déclare injoignable
  par message direct. À réserver à un point très dégagé (sommet, pylône)
  convenu avec la communauté locale : mal placé, il consomme des sauts
  et dégrade le réseau, raison pour laquelle la communauté en limite
  l'usage.
- `ROUTER_LATE` : réémet toujours, mais après tous les autres nœuds, donc
  seulement quand personne n'a mieux relayé. Pour couvrir une zone mal
  desservie ou contourner un relief sans prendre la priorité aux
  routeurs existants ni gaspiller de sauts.
- Pour un nœud de toit qui sert surtout ses propres nœuds intérieurs, le
  rôle `CLIENT_BASE` est plus adapté : il se comporte en `ROUTER_LATE`
  pour les nœuds favoris et en `CLIENT` pour les autres.

## Capteurs et télémétrie

**Besoin** : relever des mesures dans des lieux sans courant ni réseau :
station météo isolée, cuve d'eau, serre, rucher, site solaire.

**Réponse de Meshtastic** :

- Le module télémétrie lit des capteurs I²C branchés sur le nœud
  (température, humidité, pression, qualité de l'air, courant et tension)
  et diffuse leurs valeurs à l'intervalle choisi.
- Le rôle `SENSOR` donne la priorité à la télémétrie et, avec l'économie
  d'énergie, endort le nœud entre deux relevés pour ménager une batterie
  ou un petit panneau.
- Chaque nœud publie aussi son niveau de batterie, sa tension et la charge
  du canal : l'état d'un site isolé se lit depuis n'importe quel point du
  mesh.
- Par une passerelle MQTT, les relevés arrivent dans Home Assistant,
  Node-RED ou n'importe quelle base de données, en protobuf ou en JSON.
- Le module série relie d'autres équipements qui parlent déjà sur une
  liaison série (une station météo, un régulateur de charge solaire).

**Réglages** :

- Nœud capteur : rôle `SENSOR`, module télémétrie avec
  `environment_measurement_enabled: true` et
  `environment_update_interval` (en secondes, 15 à 60 min selon la
  mesure) ; `device_update_interval` pour la batterie. Sur batterie :
  `config.power.is_power_saving: true`.
- Canal : un canal privé pour les relevés, qui n'encombrent pas le canal
  public.
- MQTT vers Home Assistant ou Node-RED : un broker privé (Mosquitto sur
  la machine de Home Assistant, par exemple) et une passerelle ESP32 (le
  JSON n'existe pas sur nRF52) avec `json_enabled: true` : elle publie
  alors chaque paquet déchiffré en JSON sous
  `<racine>/2/json/<canal>/<nœud>`, que Home Assistant lit directement.
  Montée activée sur le canal des relevés ; `encryption_enabled: false`
  si l'on veut aussi les paquets protobuf en clair. Réception inutile :
  les capteurs n'attendent rien d'Internet.

## Loisirs et expérimentation

**Besoin** : découvrir la radio, tester des antennes et la portée,
construire ses propres outils.

**Réponse de Meshtastic** :

- Les cartes coûtent quelques dizaines d'euros et le firmware est libre ;
  il se flashe depuis un navigateur ou un script.
- Le module Range Test envoie des paquets numérotés à intervalles
  réguliers et enregistre ce qui arrive, avec la force du signal (RSSI,
  SNR) et la position, pour cartographier la couverture réelle.
- Chaque paquet reçu indique la force de son signal et le nombre de sauts
  parcourus : l'effet d'un changement d'antenne ou d'emplacement se
  mesure au lieu de se deviner.
- Les interfaces série, TCP et MQTT, avec les définitions protobuf et les
  bibliothèques officielles (Python, JavaScript…), permettent d'écrire
  clients, bots et intégrations (comme meshtastic-desktop).
- Les liaisons à longue distance depuis des sommets ou des avions sont un
  sport en soi ; les presets de modem échangent du débit contre de la
  portée (`LONG_SLOW`, `VERY_LONG_SLOW`).

**Le module Range Test** : il mesure la portée réelle entre deux nœuds.

- L'émetteur (`range_test.enabled: true`, `sender` = intervalle en
  secondes, 60 par exemple) envoie un message « seq 1 », « seq 2 »… à
  intervalle fixe, seulement tant que le canal est occupé à moins de
  25 %, et s'arrête de lui-même après 8 heures.
- Le récepteur (`enabled: true`, `sender: 0`) reçoit ces messages ; avec
  `save: true` (ESP32 seulement), il enregistre chacun dans un fichier
  CSV sur le nœud (`rangetest.csv`) avec l'heure, la position de
  l'émetteur, le RSSI et le SNR. On se promène avec l'émetteur, puis on
  récupère le fichier par l'interface web du nœud pour tracer la carte
  de couverture.
- Les messages partent sur le canal primaire : pour ne pas encombrer le
  canal public, on fait le test sur des nœuds dont le canal primaire est
  privé, le temps de l'essai. Le firmware ne monte jamais ces messages
  vers le broker MQTT public.


