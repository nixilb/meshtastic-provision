

Mashtastic Notes
=================


## Usages

Voici les usages courants de Meshtastic : des messages texte courts et des positions, sans réseau mobile ni abonnement.

**Communication hors réseau**
- **Randonnée, montagne, trail, ski de rando** : rester en contact dans un groupe dispersé là où le téléphone ne passe pas.
- **Voile et kayak** : entre bateaux, ou avec la terre, à quelques kilomètres.
- **Événements** (festivals, courses, rassemblements) : coordonner les bénévoles quand le réseau mobile est saturé.
- **Airsoft, paintball, chasse** : positions et messages d'équipe.

**Suivi de position**
- **Suivre les membres d'un groupe sur une carte** (rôle Tracker).
- **Retrouver un objet perdu** : un traceur sur un vélo, un chien, un drone ou un ballon-sonde.
- **Suivre un véhicule** sur un grand terrain : ferme, chantier.

**Secours et résilience**
- **Réseau de secours de quartier ou de commune** pour une panne, une tempête ou une inondation, quand les antennes relais tombent.
- **Préparation aux urgences** (« preppers »), réseaux de clubs de radioamateurs.
- **Appui à des équipes de secours ou de bénévoles** : Croix-Rouge locale, recherche de personnes.

**Capteurs et télémétrie**
- **Relevés à distance** : station météo, niveau d'une cuve, humidité d'une serre, température d'un rucher (rôle Sensor).
- **Surveillance** de la batterie et de la charge solaire d'un site isolé.
- **Domotique** via MQTT : Home Assistant, Node-RED.

**Infrastructure communautaire**
- **Relais fixes sur des points hauts** (toits, pylônes, collines) pour étendre le mesh d'une région (rôles Router).
- **Passerelles MQTT** qui relient des zones éloignées par Internet : ton cas.
- **Cartes publiques** et observation du réseau.

**Loisirs et technique**
- **Expérimenter la radio LoRa** : portée, antennes, tests de couverture (module Range Test).
- **Développement** : clients, bots, intégrations par l'API série, TCP ou MQTT (comme `meshtastic-desktop`).
- **Contacts à longue distance** : records de portée depuis des sommets ou des avions.

**Usages particuliers**
- **TAK / ATAK** : interfaçage avec les outils tactiques utilisés par les secours et l'airsoft.
- **Store & Forward** : un nœud garde les messages pour ceux qui étaient hors ligne.
- **Canned messages** : messages préenregistrés pour les appareils sans téléphone, avec clavier ou molette.

Dans tous les cas, les messages sont courts (environ 200 caractères) et le débit est faible. On ne transmet ni voix, ni image, ni fichier.

## MQTT


Recevoir depuis MQTT (`downlink_enabled`) permet à ton nœud d'entendre ce qui arrive d'Internet sur ce canal. Sans ça, il ne connaît que ce qu'il entend en radio, dans un rayon de quelques kilomètres.

**Ce que ça apporte :**
- **Les messages de nœuds hors de portée radio.** Un message envoyé à Lyon sur LongFast, publié sur le broker par une passerelle de là-bas, arrive jusqu'à ton nœud et donc jusqu'à toi.
- **Les messages directs (DM) venus d'Internet.** Le firmware écoute le topic « PKI » des DM chiffrés dès qu'un canal a la réception activée (`MQTT.cpp:87`). Sans réception, un DM envoyé via MQTT par un nœud éloigné ne t'arrive pas.
- **Un pont entre deux zones.** C'est le seul moyen de relier par Internet deux groupes de nœuds, par exemple deux maisons ou toi et un ami éloigné.

**Ce que ça coûte :**
- **Du temps d'antenne local, probablement.** Les paquets reçus d'Internet entrent dans le routeur comme des paquets radio (`router->enqueueReceivedMessage`, `MQTT.cpp:151`). À ma connaissance, ils sont donc réémis en LoRa, marqués « venu de MQTT », et encombrent la radio de tes voisins. C'est pour ça que `ignore_mqtt` existe. Je n'ai pas suivi le code jusqu'à la réémission.
- **De la charge sur le nœud** : sur le broker public, c'est le flux de ~25 msg/s dont on a parlé.

**Sans réception**, le nœud reste un « émetteur » vers Internet. Il publie ce qu'il entend en radio, il apparaît sur la carte et les autres voient ses messages. Mais les réponses envoyées via Internet ne lui parviennent pas.

Pour toi, qui passes par l'app et son filtre, garder la réception activée est raisonnable. Pour un nœud autonome sur le broker public, la couper est le choix prudent, au prix des messages venant d'Internet.

### Topics

Le topic root n'est pas un topic complet mais le préfixe commun à tous les topics du nœud, en publication comme en abonnement. Le firmware y ajoute un suffixe selon le type de message (`MQTT.h:104`, `MQTT.cpp:433`).

Avec `root: msh/EU_868` :

| Usage | Topic | Sens |
|---|---|---|
| Paquets d'un canal (chiffrés) | `msh/EU_868/2/e/LongFast/!acaa8b40` | publication |
| Écoute d'un canal | `msh/EU_868/2/e/LongFast/+` | abonnement, si la réception MQTT du canal est activée |
| DM chiffrés | `msh/EU_868/2/e/PKI/+` | abonnement, si un canal a la réception activée |
| Rapport de carte | `msh/EU_868/2/map/` | publication |
| JSON, si `json_enabled` | `msh/EU_868/2/json/LongFast/…` | publication et abonnement |

Deux nœuds ne se parlent via MQTT que s'ils ont la même racine, sur le même canal (même nom et même clé). Sur le broker public, la convention est `msh/<région>`, parfois avec un sous-niveau local (`msh/EU_868/FR/...`). C'est pourquoi la racine se déduit presque toujours de la région.

Sur un broker perso, tu choisis la racine librement ; il suffit que tous tes nœuds utilisent la même. Laissée vide, elle vaut `msh`.

### Settings MQTT

#### proxy_to_client_enabled

"Proxy through the app" (proxy_to_client_enabled) décide qui se connecte au broker MQTT : le nœud lui-même, ou l'app qui lui est reliée. Je l'ai vérifié dans le firmware (src/mqtt/MQTT.cpp) et dans le code de meshtastic-desktop.

Proxy désactivé. Le nœud ouvre lui-même sa connexion au broker, ce qui suppose qu'il ait un réseau (Wi-Fi ou Ethernet). Il publie ce qu'il entend en radio et reçoit ce que le broker lui envoie.

Proxy activé. Le nœud n'ouvre jamais de connexion au broker, même s'il a du Wi-Fi. Chaque publication part, emballée dans un message, vers l'app connectée en USB, BLE ou TCP (MQTT::publish, ligne 510). C'est l'app qui se connecte au broker avec la config du nœud (adresse, identifiants, racine) et fait l'aller-retour (mqtt_proxy.rs côté desktop).

Ce que ça implique pour toi :

L'app doit rester connectée au nœud. Si elle est fermée, le nœud perd MQTT : ni envoi ni réception via Internet, et pas de rapport de carte.
Avec le proxy activé, le Wi-Fi ne sert plus à MQTT. Il ne sert plus qu'à l'heure (NTP) et à l'API TCP. Ton profil active les deux, donc c'est ton cas.
Le proxy protège le nœud. L'app filtre ce qui descend du broker (DownlinkFilter). Sans ce filtre, le trafic du broker public sur LongFast (~25 msg/s, dont ~95 % inutile au nœud) saturait l'ESP32 jusqu'à le faire redémarrer.

Sans proxy, le nœud recevrait sans filtre tout le trafic auquel il s'abonne. Je suppose qu'il redémarrerait comme sans filtre, mais je ne l'ai pas vérifié en direct Wi-Fi.

Avec ton profil actuel (proxy activé), un nœud laissé seul sans l'app continue de fonctionner en radio, mais perd tout ce qui passe par Internet. Je l'ai vérifié dans le code du firmware 2.7.26.

**Ce qui continue de marcher :**
- Le mesh LoRa : le nœud relaie, émet et reçoit comme avant.
- Le Wi-Fi reste connecté, mais ne sert qu'à l'heure (NTP) et à l'API TCP.

**Ce qui est perdu :**
- **Rien ne part vers le broker, même avec le Wi-Fi.** Le nœud ne s'y connecte jamais lui-même en mode proxy : chaque publication va dans une file destinée à l'app.
- **La file garde les 8 derniers messages.** Au-delà, le plus ancien est jeté (`MeshService.cpp:344`). Quand l'app se reconnecte, elle ne récupère que ces 8, parfois périmés.
- **Rien n'arrive d'Internet**, donc rien de ce que les autres nœuds envoient via MQTT.
- **Aucun rapport de carte** : le nœud finit par disparaître de la carte publique.

**Pour un nœud vraiment autonome**, il faut le Wi-Fi activé et « Proxy through the app » désactivé. Le nœud se connecte alors lui-même au broker.

Il perd dans ce cas le filtre de l'app. Il s'abonne au trafic de chaque canal dont la réception MQTT (`downlink_enabled`) est activée (`MQTT.cpp:586`). Sur le broker public, ça représente jusqu'à ~25 msg/s sur LongFast, le débit qui faisait redémarrer l'ESP32 quand il arrivait sans filtre par TCP. Je n'ai pas vérifié ce qui se passe avec une connexion Wi-Fi directe. Deux façons de rester prudent :
- **Garder l'envoi et couper la réception** (« Receive from MQTT » décoché) : le nœud publie ce qu'il entend et reste sur la carte, sans s'abonner à rien.
- **Utiliser un broker perso peu chargé**, où la réception ne pose pas de problème.

Si c'est un usage que tu vises, je peux ajouter une ligne d'aide sous « Proxy through the app » qui résume ce choix.