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

[quel réglages MQTT ou autre faire pour cet usage?]

## Communication hors réseau

**Besoin** : garder un groupe en contact là où le réseau mobile ne passe
pas ou est saturé : montagne, mer, forêt, événements très fréquentés.

**Réponse de Meshtastic** :

- Chacun porte un nœud couplé à son téléphone ; les messages passent de
  nœud en nœud par LoRa, et le maillage contourne les obstacles (une
  crête, un bâtiment) par le nœud qui voit les deux côtés.
- Le couplage : Bluetooth.
- Le groupe partage ensuite son canal [il vient d'où? il est defini comment?]: un membre crée un canal privé
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

[quel réglages MQTT ou autre faire pour cet usage?]


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

[quel réglages MQTT ou autre faire pour cet usage?]

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

[quel réglages MQTT ou autre faire pour cet usage?]

[description des rôle `ROUTER` ou `ROUTER_LATE`]

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

[quel réglages MQTT ou autre faire pour cet usage?]

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

[quel réglages MQTT ou autre faire pour cet usage?]

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

[c'est quoi ce module Range Test ?]


