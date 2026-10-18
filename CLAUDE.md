# Btechnics_Toegang (HA integratie btechnics_toegang)

Home Assistant integratie voor deurtoegang bij Btechnics: personen, codes, badges, gezichten, deuren, tablets (app Btechnics Deur),
portaal voor medewerkers en sync naar de Akuvox S532. HA (Btechnics HA, 192.168.1.117, extern https://iot.bogaert.me) is de baas.

## Samenwerking met de APK chat

Sinds 17/10/2026 19u: NIET meer via de postbus. Antwoorden voor de APK chat geven als bericht dat Matthias kopieert (venster om te kopiëren).
Oude werkwijze hieronder enkel ter info.

Map op de Mac: `~/Documents/Claude/Projects/Postbus Deurtoegang` (iCloud, op beide Macs).

| Bestand | Wie leest | Wie schrijft |
|---|---|---|
| naar-ha.md | deze chat (HA, Btechnics_Toegang) | APK chat |
| naar-app.md | APK chat (Btechnics Deur) | deze chat |

Regels:
1. Bij het begin van elke beurt eerst `naar-ha.md` lezen.
2. Afgehandeld bericht: verplaatsen naar `archief/` met datum en uur in de naam (bv. `archief/2026-10-17_1630_naar-ha.md`).
3. Iets voor de app: onderaan `naar-app.md` een blok toevoegen met kop (datum, uur, afzender HA, versie), daarna vraag of antwoord
   met verwijzing naar het punt in het afsprakendocument.
4. Nooit een bericht van de andere chat wijzigen of wissen.
5. Formaten staan enkel in het afsprakendocument en in de map `contract/` van deze repo. De postbus is voor vragen en meldingen.
6. Typt Matthias "postbus": postbus lezen, afwerken, antwoord voor de app in `naar-app.md`, kort melden wat er gebeurd is (versie, testresultaat).

## Werkwijze

- Releases: commit in de container, bundle naar `~/BtxBuild` op de Mac, `git pull --ff-only <bundle> HEAD` in
  `~/Documents/Claude/Projects/Btechnics_Toegang`, push, `gh release create vX.Y.Z --target main`, daarna
  `node toegang-ha.mjs download vX.Y.Z` in `~/BtxBuild` en HA herstarten via REST. Altijd een nieuwe bundelnaam (iCloud cache).
- Na elke uitrol het scherm zelf nakijken (headless Chrome op de Mac, schermafdrukken), niet enkel tests.
- Nooit testen op de koppeling van de echte tablet (Deur verdiep, t_85ba78). Gebruik de testdeur via de diensten testdeur_klaarzetten en testdeur_opruimen ("Testdeur (tests)") en een
  testtablet met eigen sleutel; ruim testpersonen op.
- Akuvox S532 (192.168.1.153): enkel eigen gebruikers vanaf UserID 700001, nooit SmartPlus of bestaande gebruikers aanpassen.
- Wijzigingen aan de Work app of Btechnics IOT nooit zelf doen: prompt geven voor die chat (zie `claude/afspraak-work-toegang.md`
  in het claude.ai project).

## Werkpunten (stand 18/10/2026, v0.10.0 live)

- Akuvox S532: live sinds 17/10 op deur Voordeur Akuvox (d_44385f). Live getest: testgebruiker toegevoegd en gewist, toestel terug op 10 gebruikers, 7 vreemde boven 700000 onaangeroerd. HTTP API laat enkel 192.168.1.117 toe.
- Voordeur Akuvox opent via relais 1 van de S532 (/api/relay/trig) als er geen schakelaar gekozen is; foto via camera.vto_magazijn bij elke toegang (deurlog elke 5 s).
- Foto van de tablet (POST /foto, afspraak 11.8): live sinds 17/10 met app v1.67 op Deur verdiep, bevestigd in HA.
- Openen op afstand in HA (v0.7.7): /wacht geeft "foto": [uid] aan de tablets van die deur (30 s geldig); werkt sinds 17/10 (bevestigd door de APK chat). Sensor Laatste heeft attribuut fotos zodat de kaart meteen hertekent als een foto later binnenkomt (v0.7.8). Sinds v0.7.9 gaat de fotovraag al vóór het schakelen van het relais naar de tablet. App v1.69: foto 480 px kwaliteit 60 (ca. 7 kB), 130 tot 190 ms tussen poging en 200 op /foto.
- Akuvox Action URL (v0.8.0): webhook /api/webhook/<reg.data.akuvox.webhook>; de S532 meldt elke poging, HA neemt meteen een foto en leest het deurlog. URL staat in het dashboard Deuren. Instellen op de S532: Setting, Action URL.
- Te veel foute codes (v0.8.0): 5 foute codes of badges zonder persoon binnen 120 s per deur: melding, event btechnics_toegang_te_veel_pogingen, lijst.deur.klavier_tot 60 s, /open met pin of kaart geeft 403 reden geblokkeerd. App moet klavier_tot tonen en blokkeren (bericht aan APK chat).
- Action URL op de S532 staat sinds 18/10 bij Valid/Invalid Code en Card Entered. Make Call, RelayA en RelayB Triggered hebben andere webhooks: nooit aanpassen.
- S532 firmware 532.30.10.315 heeft lokaal GEEN gezichtsbeheer (enkel via SmartPlus, niet aanraken).
- Beslist 18/10: gezichtsherkenning voor Akuvox deuren via eigen container op Unraid naast Frigate (zelfde pipeline als de tablet, met echtheid). Volgorde: 1) back-ups versleutelen, 2) meerdere Akuvox toestellen (max 2), 3) server als eigen toestel met sleutel + toestemmingstekst v2, 4) container, 5) nachtmeting infrarood. Openen enkel bij groot recht gezicht, 2 s stil.
- Twee Akuvox toestellen (v0.9.0): opties akuvox_* (toestel 1) en akuvox2_* (toestel 2), opslag reg.data akuvox en akuvox2, elk eigen webhook, mapping vanaf 700001 en relais. hass.data[DOMAIN]["akuvoxen"], helper akuvox_voor_deur().
- Gezichtsserver (v0.10.0): koppelt als tablet met soort server aan een Akuvox deur (koppelcode, eigen RSA sleutel), gebruikt /lijst, /wacht, /status, /open methode gezicht. Toestemming v2 vermeldt de gezichtsserver. Telt niet mee als tablet op de deurkaart. Zie contract/README.md. Container: map gezichtsserver/ (Python, MediaPipe + MobileFaceNet tflite + MiniFASNet ONNX + 3D bij naderen), beeld ghcr.io/bogaertm/btx-gezichtsserver via GitHub Actions bij tag gezicht-vX.Y.Z. Start in PROEF=1 (meet enkel, meldt in HA bij de deur). Camera rtsp://192.168.1.185:8554/VTO_Magazijn (go2rtc van Frigate). Draait sinds 18/10 op Unraid als container btx-gezicht-magazijn (appdata /mnt/user/appdata/btx-gezicht, gekoppeld als t_630a02 aan Voordeur Akuvox, PROEF=1, --memory=1g --cpus=2). Camera levert 704x576 (H264 level 3.0), OOG_MIN 45. In proefmodus komt per sessie een 'Proef meting' bij de meldingen van de deur. Nog nodig voor kalibratie: iemand met toegang tot Voordeur Akuvox en een nieuwe scan (toestemming v2), APK chat bevestigde 18/10: app controleert toestemming niet (wel versie, pubkey_id, aad), opnieuw scannen mag. Matthias (p_f2e67b) heeft sinds 18/10 toegang tot Voordeur Akuvox (ook op de S532). Logs: Unraid logterminal of /webGui/include/OpenTerminal.php?tag=docker&name=btx-gezicht-magazijn&more=.log.
- Enkel deurtoegang: andere integraties in HA niet opkuisen of aanpassen.
- Backups (18/10): automatische HA back-up dagelijks, versleuteld, 3 kopieën per plek, lokaal + netwerkopslag unraid_backups (share backups op 192.168.1.185, gebruiker btechnics). Eerste kopie op Unraid 18/10 18u27 bevestigd protected (905 MB). Unraid max ca. 3 GB, oudste wordt automatisch gewist. Script ~/BtxBuild/backup-unraid.mjs (info, zet, maak). 13 oude onversleutelde lokale back-ups (12 GB, oude add-on) gewist op 18/10 met akkoord. Add-on Home Assistant Google Drive Backup gestopt en boot manueel (maakte onversleutelde back-ups naar Google Drive). Oude kopieën op Drive wist Matthias zelf.
- Token voor scripts staat in deur-config.json onder `ha` (cfg.ha.token). Nooit met een fout token proberen: HA blokkeert dan het IP (ip_bans).
- Work chat: mail met portaallink als `gezicht_ontbreekt` gevuld is (prompt gegeven).
- Enkel https staat aan sinds 17/10 (app v1.66 op 8443). Nieuwe tablet koppelt via 8443: tablet toont de eerste 16 tekens, Matthias vergelijkt met HA en tikt OK.
