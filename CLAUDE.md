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

## Werkpunten (stand 17/10/2026, v0.8.1 live)

- Akuvox S532: live sinds 17/10 op deur Voordeur Akuvox (d_44385f). Live getest: testgebruiker toegevoegd en gewist, toestel terug op 10 gebruikers, 7 vreemde boven 700000 onaangeroerd. HTTP API laat enkel 192.168.1.117 toe.
- Voordeur Akuvox opent via relais 1 van de S532 (/api/relay/trig) als er geen schakelaar gekozen is; foto via camera.vto_magazijn bij elke toegang (deurlog elke 5 s).
- Foto van de tablet (POST /foto, afspraak 11.8): live sinds 17/10 met app v1.67 op Deur verdiep, bevestigd in HA.
- Openen op afstand in HA (v0.7.7): /wacht geeft "foto": [uid] aan de tablets van die deur (30 s geldig); werkt sinds 17/10 (bevestigd door de APK chat). Sensor Laatste heeft attribuut fotos zodat de kaart meteen hertekent als een foto later binnenkomt (v0.7.8). Sinds v0.7.9 gaat de fotovraag al vóór het schakelen van het relais naar de tablet. App v1.69: foto 480 px kwaliteit 60 (ca. 7 kB), 130 tot 190 ms tussen poging en 200 op /foto.
- Akuvox Action URL (v0.8.0): webhook /api/webhook/<reg.data.akuvox.webhook>; de S532 meldt elke poging, HA neemt meteen een foto en leest het deurlog. URL staat in het dashboard Deuren. Instellen op de S532: Setting, Action URL.
- Te veel foute codes (v0.8.0): 5 foute codes of badges zonder persoon binnen 120 s per deur: melding, event btechnics_toegang_te_veel_pogingen, lijst.deur.klavier_tot 60 s, /open met pin of kaart geeft 403 reden geblokkeerd. App moet klavier_tot tonen en blokkeren (bericht aan APK chat).
- Enkel deurtoegang: andere integraties in HA niet opkuisen of aanpassen.
- Backups: dagelijkse volledige backup 03:00, enkel lokaal (hassio.local) en NIET versleuteld; automatische backups van HA niet ingesteld. Beslissing Matthias.
- Token voor scripts staat in deur-config.json onder `ha` (cfg.ha.token). Nooit met een fout token proberen: HA blokkeert dan het IP (ip_bans).
- Work chat: mail met portaallink als `gezicht_ontbreekt` gevuld is (prompt gegeven).
- Enkel https staat aan sinds 17/10 (app v1.66 op 8443). Nieuwe tablet koppelt via 8443: tablet toont de eerste 16 tekens, Matthias vergelijkt met HA en tikt OK.
