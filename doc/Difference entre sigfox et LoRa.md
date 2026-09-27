# Différence entre sigfox et LoRa

## Utilisateur

différence entre sigfox et lora

## Assistant

### Sigfox vs LoRa : les grandes différences

Les deux sont des technologies **LPWAN** (réseaux bas débit, longue portée, faible consommation) sur bandes libres sub-GHz (868 MHz en Europe, 902-928 MHz aux US). Mais leur philosophie est très différente.

#### 1. Modèle économique — la différence la plus structurante

| | **Sigfox** | **LoRa / LoRaWAN** |
|---|---|---|
| Modèle | Opérateur unique (0G network), abonnement par objet | Vous pouvez **déployer votre propre réseau** privé, ou passer par un opérateur |
| Infrastructure | Vous ne possédez rien, vous achetez de la couverture | Vous achetez des gateways (quelques centaines d'€) et vous maîtrisez le réseau |
| Souveraineté des données | Les messages transitent par le back-end Sigfox | Données possibles 100 % en interne (serveur LoRaWAN privé type ChirpStack) |

C'est souvent le critère décisif : LoRa = autonomie, Sigfox = simplicité clé en main.

#### 2. Technologie radio

- **Sigfox** : modulation **UNB** (Ultra Narrow Band, ~100 Hz), DBPSK. Chaque message est émis 3 fois sur 3 fréquences différentes, sans accusé de réception par défaut.
- **LoRa** : modulation **CSS** (Chirp Spread Spectrum), brevetée par Semtech. Étalement de spectre avec facteurs d'étalement (SF7 à SF12) qui permettent d'arbitrer entre portée et débit.

Attention au vocabulaire : **LoRa** = la couche radio (Semtech, propriétaire). **LoRaWAN** = le protocole réseau au-dessus, spécifié par la LoRa Alliance (ouvert).

#### 3. Capacité et charge utile

| | Sigfox | LoRaWAN |
|---|---|---|
| Taille message montant | **12 octets** max | ~51 à 242 octets selon le SF |
| Nombre de messages/jour | **140 max** (uplink) | Limité seulement par le *duty cycle* (1 % en Europe) |
| Downlink | 4 messages/jour, 8 octets | Bidirectionnel, plus souple (classes A/B/C) |
| Débit | 100 bps | 300 bps à 50 kbps |

#### 4. Portée et consommation

Comparables dans les faits : 3-10 km en urbain, 15-40 km en rural dégagé. Sigfox a un léger avantage en budget de liaison brut (UNB très robuste) ; LoRa compense par l'adaptation dynamique du débit (ADR). Côté batterie, les deux permettent des capteurs qui tiennent 5 à 10 ans sur une pile.

#### 5. Écosystème et pérennité

- **Sigfox** : la société a été placée en redressement judiciaire en 2022 puis rachetée par le singapourien **UnaBiz**, qui a depuis ouvert une partie des spécifications. Le réseau reste opérationnel mais l'épisode a refroidi beaucoup d'industriels.
- **LoRaWAN** : écosystème très large (LoRa Alliance, The Things Network, Helium, opérateurs nationaux, déploiements privés massifs — Enedis Linky, agriculture, smart building). Dépendance résiduelle à Semtech pour les puces radio, mais des alternatives apparaissent.

#### En résumé — comment choisir

**Sigfox** convient si : vous avez beaucoup d'objets très simples, éparpillés géographiquement (logistique, traceurs, alarmes), peu de données, et que vous ne voulez gérer aucune infrastructure.

**LoRaWAN** convient si : vous couvrez un site ou une zone définie (usine, campus, exploitation agricole, ville), vous voulez maîtriser votre réseau et vos données, vous avez besoin de plus de messages, de charges utiles plus grosses ou de downlink fiable.

Et si vos objets sont peu nombreux mais doivent remonter plus de données, regardez aussi **NB-IoT / LTE-M** (réseaux opérateurs cellulaires), qui ont largement grignoté le terrain de Sigfox ces dernières années.


### Fréquences : Sigfox vs LoRaWAN

Les deux vivent dans les **mêmes bandes ISM sub-GHz**, donc la différence n'est pas la bande mais la **façon de l'occuper**.

#### Le point clé : largeur de canal

| | Sigfox | LoRa |
|---|---|---|
| Largeur d'un signal | **100 Hz** (UNB) — 600 Hz aux US | **125 / 250 / 500 kHz** (CSS) |
| Conséquence | Des milliers de "micro-canaux" tiennent dans 192 kHz | Peu de canaux, mais robustesse par étalement |

Un message Sigfox est 1000 fois plus étroit qu'un message LoRa. C'est toute la logique : Sigfox concentre l'énergie sur une porteuse quasi ponctuelle, LoRa l'étale sur une large bande.

#### Europe (EU868 / RC1)

**Sigfox**
- Uplink : **868,034 – 868,226 MHz** (bande de 192 kHz centrée sur 868,130), 100 bps, 14 dBm (25 mW), duty cycle 1 %
- Downlink : **869,525 MHz**, 600 bps, jusqu'à 27 dBm côté station de base
- Le device choisit sa fréquence **pseudo-aléatoirement** dans les 192 kHz, et répète le message 3 fois sur 3 fréquences — pas de plan de canaux à configurer

**LoRaWAN**
- 3 canaux obligatoires (join) : **868,1 / 868,3 / 868,5 MHz**, 125 kHz, 14 dBm, duty cycle 1 %
- Canaux additionnels courants : 867,1 / 867,3 / 867,5 / 867,7 / 867,9 MHz
- 868,3 MHz en 250 kHz (DR6) et 868,8 MHz en FSK 50 kbps
- **RX2 (downlink) : 869,525 MHz, SF12/SF9 selon le réseau** — même fréquence que Sigfox, car c'est la sous-bande 869,4–869,65 qui autorise **27 dBm et 10 % de duty cycle** en Europe

À retenir : les deux downlinks utilisent 869,525 MHz, pour la même raison réglementaire.

#### Autres régions

| Région | Sigfox (RC) | LoRaWAN |
|---|---|---|
| Europe | RC1 : 868 MHz | EU868 (863–870) |
| USA / Canada | RC2 : 902,2 MHz (uplink), 905,2 (downlink), 600 bps, 22 dBm | US915 : 64 canaux 125 kHz (902,3→914,9) + 8 canaux 500 kHz ; downlink 923,3→927,5 |
| Amérique latine / APAC | RC4 : 920–923 MHz | AU915, AS923 |
| Japon | RC3 : 923 MHz, **LBT obligatoire** | AS923 (LBT) |
| Corée | RC5 : 922–923 MHz | KR920 |
| Inde | RC6 : 865–867 MHz | IN865 |
| Russie | RC7 : 868,8–869,2 MHz | RU864 |
| Chine | — | CN470 (470–510 MHz) |

Conséquence pratique : un capteur Sigfox est lié à sa **RC zone** (un device RC1 ne fonctionne pas aux US sans reconfiguration/matériel adapté) — même contrainte en LoRaWAN, où la région est un paramètre de la stack.

#### Les règles européennes qui conditionnent tout (ETSI EN 300 220)

| Sous-bande | Puissance max | Duty cycle |
|---|---|---|
| 863,0 – 868,0 | 14 dBm | 1 % |
| 868,0 – 868,6 | 14 dBm | 1 % |
| 868,7 – 869,2 | 14 dBm | 0,1 % |
| **869,4 – 869,65** | **27 dBm** | **10 %** |
| 869,7 – 870,0 | 14 dBm | 1 % (ou 0,1 %) |

C'est ce 1 % qui limite en pratique le nombre de messages : à SF12 en LoRa, un paquet dure ~1,5 s d'air time, ce qui impose ~2,5 minutes de silence ensuite. Sigfox, lui, plafonne administrativement à 140 messages/jour, ce qui revient au même ordre de grandeur.

#### Et en dehors du sub-GHz

- **LoRa 2,4 GHz** (puce Semtech SX1280) : bande mondiale unique, **pas de duty cycle**, débits plus élevés, mais portée nettement réduite. Utile pour des déploiements internationaux ou indoor denses.
- Sigfox n'a pas d'équivalent.

**En résumé** : même terrain de jeu réglementaire, occupation opposée. Sigfox = ultra-étroit, fréquence tirée au hasard, zéro planification. LoRa = canaux larges définis explicitement, avec un plan de fréquences que vous configurez et maîtrisez — ce qui va de pair avec la possibilité d'avoir votre propre réseau.

## Utilisateur

et les portées?

## Assistant

### Portées : Sigfox vs LoRaWAN

Les deux technologies ont des portées **du même ordre**. L'écart vient surtout du terrain, de la hauteur des antennes et de la densité du réseau.

#### Ordres de grandeur

| Environnement | Sigfox | LoRaWAN |
|---|---|---|
| Urbain dense | 3 à 10 km | 2 à 5 km |
| Péri-urbain | 10 à 20 km | 5 à 15 km |
| Rural dégagé | 30 à 50 km | 15 à 30 km |
| Vue directe (mer, montagne, ballon) | > 100 km | > 100 km, record à plusieurs centaines de km depuis un ballon stratosphérique |
| Indoor profond (sous-sol, compteur en cave) | Difficile | Difficile, un peu mieux avec une gateway locale |

Sigfox a un **léger avantage en portée brute**. LoRaWAN compense par la possibilité de **poser une gateway là où on en a besoin**.

#### Pourquoi : le bilan de liaison (link budget)

La portée se déduit de la puissance d'émission moins la sensibilité du récepteur :

| | Sigfox | LoRa (SF12, 125 kHz) |
|---|---|---|
| Puissance device (EU) | 14 dBm | 14 dBm |
| Sensibilité station de base | environ −142 dBm | environ −137 à −141 dBm |
| **Bilan de liaison** | **environ 156 à 160 dB** | **environ 151 à 157 dB** |

La modulation UNB (100 Hz) de Sigfox capte très peu de bruit, d'où sa sensibilité excellente. LoRa atteint une sensibilité comparable grâce à l'étalement de spectre, mais seulement à SF12.

#### LoRa : la portée se règle avec le spreading factor

| SF | Sensibilité (125 kHz) | Débit | Temps d'antenne (≈10 octets) | Portée relative |
|---|---|---|---|---|
| SF7 | −123 dBm | 5,5 kbps | ~40 ms | 1× |
| SF9 | −129 dBm | 1,8 kbps | ~150 ms | ~1,5× |
| SF10 | −132 dBm | 1 kbps | ~300 ms | ~1,8× |
| SF12 | −137 dBm | 250 bps | ~1,2 à 1,5 s | ~2,5× |

C'est un **compromis** : plus de portée coûte plus de temps d'antenne, donc plus de batterie consommée et moins de messages autorisés par le duty cycle de 1 %. L'**ADR** (Adaptive Data Rate) ajuste le SF automatiquement : un capteur proche de la gateway passe en SF7, un capteur lointain en SF12.

Sigfox n'a pas ce réglage : un seul débit, toujours à portée maximale.

#### Ce qui compte vraiment sur le terrain

1. **La hauteur de l'antenne de la gateway ou station de base.** C'est le facteur n°1. Passer de 2 m à 30 m (toit, pylône) peut multiplier la portée par 3 à 5, parce qu'on dégage la zone de Fresnel.
2. **La pénétration dans les bâtiments.** Compter 10 à 20 dB de perte par mur porteur ou dalle béton, et 20 à 30 dB pour un sous-sol. En pratique, les deux technologies atteignent l'indoor profond grâce à la densité du réseau, pas à leur portée brute.
3. **La diversité spatiale.** Un message Sigfox est reçu en moyenne par environ 3 stations de base. En LoRaWAN, toutes les gateways à portée le reçoivent aussi. Plus le réseau est dense, meilleure est la fiabilité.
4. **La règle des 6 dB.** En espace libre, 6 dB de bilan en plus doublent la distance. En milieu urbain, les mêmes 6 dB ne donnent plus que +40 % environ.
5. **L'antenne du capteur.** Une antenne miniature mal accordée dans un boîtier métallique peut faire perdre 10 dB, c'est-à-dire l'essentiel de l'avantage théorique.

#### La liaison descendante (downlink)

Dans les deux cas, la station de base émet à 27 dBm sur 869,525 MHz. Le downlink porte donc généralement aussi loin que l'uplink. Ce qui le limite, c'est la sensibilité du device et la capacité du réseau, pas la portée.

#### En résumé

- **Sigfox** : portée maximale garantie à chaque message, mais on dépend de la couverture de l'opérateur. Si la zone est mal couverte, on ne peut rien y faire.
- **LoRaWAN** : portée brute un peu inférieure et réglable. Surtout, on peut **ajouter une gateway** pour couvrir un sous-sol, une vallée ou un site isolé. Sur un site maîtrisé (usine, campus, exploitation), c'est souvent plus décisif que quelques dB de bilan de liaison.

Pour un projet réel, faites une **campagne de mesures** avec un testeur de terrain (Adeunis FTD, RFTrack ou équivalent) avant de dimensionner. Les écarts entre la théorie et le terrain dépassent souvent un facteur 2.
