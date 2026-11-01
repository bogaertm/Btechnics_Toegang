# Contract HA en tablet (Btechnics Deur)

Echte verzoeken en antwoorden van HA, gemaakt door `tests/test_contract.py`. De APK chat draait dezelfde bestanden in regressie.mjs.
Vergeleken worden de velden en hun types; ids, tijden en hashes verschillen per run.

| Bestand | Inhoud |
|---|---|
| hash.json | testvectoren voor pin en kaart (zout + 2000 rondes sha256) |
| koppel.json | POST /koppel, met `https` {poort, sha256} |
| lijst.json | GET /lijst, ETag en 304, persoon zonder en met gezicht |
| gezicht.json | gezichtspakket, ontsleutelbaar met testsleutel-tablet.pem |
| log.json | POST /log met toegang, geweigerd, vermoedelijke_foto (score) en systeem; verwachte historiek |
| foto.json | POST /foto: foto van de tablet bij een logregel (zelfde uid) |
| open.json | POST /open, 200 en 403 |
| status.json | POST /status met schema, personen, gezichten, laatste_lijst |
| testsleutel-tablet.pem | RSA sleutel enkel voor tests, nooit op een echte tablet |

Opnieuw maken: `CONTRACT_SCHRIJF=1 python -m pytest tests/test_contract.py`. Controle: `python -m pytest tests/test_contract.py`.

## Gezichtsserver (v0.10.0)

De gezichtsserver (container op Unraid) voor een deur met camera (Akuvox) gebruikt hetzelfde protocol als een tablet:

| Stap | Endpoint | Verschil met een tablet |
|---|---|---|
| Koppelen | POST /koppel | extra veld `"soort": "server"`, eigen RSA sleutel (SPKI DER base64), toestel_id `gezichtsserver-<deur>` |
| Lijst | GET /lijst | gezichtspakketten verpakt voor de sleutel van de server |
| Herkend | POST /open | `{"methode": "gezicht", "persoon_id": ..., "log": false}`: HA controleert uren en status, opent het relais van de Akuvox en logt met foto van de camera |
| Status | POST /status | zelfde velden, `gezichten` = aantal ontsleutelde personen |
| Foto | POST /foto | optioneel: beeld van het moment van herkennen bij de uid die /wacht vraagt |

| Doorzetten | GET /herverpak, POST /herverpak | enkel soort server: taken = afdrukken die de server kan lezen maar die ontbreken voor een toestel aan een deur van die persoon. De server pakt de AES sleutel uit en verpakt ze voor de doelen (RSA-OAEP SHA-1), HA ziet enkel verpakte sleutels |

Elke gezichtsserver krijgt bij een scan ook een verpakte sleutel als bewaarder (in het portaal als "Nieuwe toestellen"), ook voor wie geen toegang heeft tot zijn deur. Daardoor hoeft niemand opnieuw te scannen bij een nieuwe tablet of een extra deur. Per deur koppelt de server apart (eigen koppelcode). Toestemming v2 vermeldt de gezichtsserver; pakketten met v1 blijven geldig voor de tablets.

## Knop Toegang in Work (v0.12.0)

Work opent `https://ha.voorbeeld.be/api/btechnics_toegang/werklink?w=<werk_id>&t=<unix vervaltijd>&s=<hex HMAC-SHA256(geheim, "<werk_id>.<t>")>`.
t ligt hoogstens 10 minuten in de toekomst, elke link werkt een keer. HA stuurt door naar een portaalsessie van 30 minuten
waarin de medewerker zonder huidige code zijn code wijzigt (nieuwe code twee keer) en gezichtsherkenning instelt.
Het geheim (32 tot 128 tekens) maakt Work zelf en stuurt het een keer via het IOT kanaal: actie `geheim_zetten`, velden `{geheim}`.

## Audit 19/10 (v0.13.0)

| Regel | Waar |
|---|---|
| Toestel-endpoints (koppel, wacht, lijst, log, foto, modus, status, open, herverpak) weigeren verkeer via de tunnel (Cf-Connecting-Ip, Cf-Ray, X-Forwarded-For) | api.py |
| Het soort (tablet of server) kiest de beheerder bij de koppelcode; een toestel dat iets anders beweert, wordt geweigerd | register.koppelen |
| Een nieuwe publieke sleutel van een gekoppeld toestel wacht op goedkeuring (Deuren, Sleutel goedkeuren); /status geeft pubkey_fout tot dan | register._pubkey_zetten |
| De gezichtsserver opent enkel met methode gezicht en enkel als de deur gezicht_live heeft (dashboard, deur aanpassen); anders 403 reden proef | api.OpenView |
| Akuvox: na elke sync worden de gebruikers op het toestel opnieuw gelezen en gecontroleerd (naam, code, ScheduleRelay met 1001 en relais 1, activatierecht). ok is enkel waar als die controle slaagt | akuvox.async_sync |
| Akuvox: een UserID wordt enkel gewijzigd of gewist als naam of toestel-ID nog van ons is | akuvox._is_van_ons |
| Akuvox: codes van vreemde gebruikers op het toestel (als hash) mogen niet gekozen worden | register.controleer_pin |
| Akuvox: weigert het toestel een eigen gebruiker (Status Failed), dan volgt een melding en een nieuwe controle | akuvox.async_deurlog |
| Echt toestelgedrag staat in contract/akuvox_s532.json; NepAkuvox in de tests volgt dat | tests |

## Schema 2 (v0.14.0, 01/11/2026)

Tablet app v1.72 aanvaardt schema 1 en 2. Eerst de app bijwerken, dan HA.

| Wat | Waar | Inhoud |
|---|---|---|
| `gezicht_kdf` | GET /lijst | weg (was altijd null) |
| `deur.gezicht_drempel` | GET /lijst | getal 0,50 tot 0,95 of null. Null: de tablet houdt zijn eigen strengheid. Instellen in HA bij Deur aanpassen of dienst deur_wijzigen `gezicht_drempel` ("" = terug naar de tablet) |
| `app` | GET /lijst | `{version_code, versie, sha256, grootte}` of null; nooit voor een gezichtsserver. Is version_code hoger dan de eigen versie, dan haalt de tablet GET /apk |
| GET /apk | tablet (sleutel, https 8443) | het APK bestand (application/vnd.android.package-archive). De tablet controleert grootte, sha256, pakketnaam, versie en handtekening en installeert stil als toestelbeheerder, nooit tijdens een oproep |
| POST /apk_upload | beheerder (HA token, enkel lokaal) | `?version_code=66&versie=1.72&sha256=<hex>` met de APK als body. HA rekent de sha256 zelf na en weigert een oudere versie (409) |
| `geopend` | GET /wacht | `[{"uid": "ha-...", "wie": "<voornaam of leeg>"}]` naast `foto` bij openen op afstand. De tablet toont "Deur open" met het deurgeluid, elke uid een keer |

## Akuvox schema's (v0.15.0, 01/11/2026)

Uren en einddatum van een persoon staan als eigen schema's op de S532, zodat ze ook gelden als HA uitvalt.
Formaat in `akuvox_s532.json` (schema_formaat). Regels:

- Eigen schema's heten `BTX <UserID of laatste 6 tekens van het persoon-ID>-<n>`, staan in de HA opslag (akuvox.schemas) en worden enkel op dat ID en die naam gewijzigd of gewist. SmartPlus (Source Cloud), Always en Never blijven onaangeroerd.
- Zonder uren en zonder datums: `1001-1;` (Always), zoals voorheen.
- Met uren: Type 1 (Week + Daily). Met datums: Type 0 met Date. Over middernacht: twee schema's (tot 23:59 en vanaf 00:00 de dag erna).
- Einddatum per dag op het toestel; HA zet de persoon nog altijd op het exacte uur uit dienst.
- Eerst schema's zetten en terug lezen, dan de gebruiker (ScheduleRelay), pas daarna ongebruikte eigen schema's wissen.
- Staat een schema niet goed op het toestel: wie al op werkende eigen schema's stond, blijft onaangeroerd; wie nog geen werkend schema heeft, krijgt Always (1001) enkel binnen de uren en Never (1002) erbuiten, door HA bewaakt. Nooit 'altijd' buiten de uren door een fout. (v0.15.1)
- Eigen schema's worden nooit gewist zolang een gebruiker op het toestel ernaar verwijst; na een wissing volgt een tweede ronde die alle verwijzingen opnieuw zet en terug leest (voor als het toestel DisplayID's verschuift). (v0.15.1)
- Einduur exact ook zonder HA: eindigt de geldigheid niet om 23:59, dan loopt het bereik tot de dag ervoor en krijgt de laatste dag eigen stukken tot het einduur. (v0.15.1)
- Laat het toestel iemand binnen die volgens HA geen toegang heeft, dan volgt een melding (v0.15.1).
- Een weigering in het deurlog buiten de uren of datums is terecht: geen alarm, in de historiek "(buiten de uren)".
