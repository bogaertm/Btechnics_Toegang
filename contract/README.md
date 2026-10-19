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

Work opent `https://iot.bogaert.me/api/btechnics_toegang/werklink?w=<werk_id>&t=<unix vervaltijd>&s=<hex HMAC-SHA256(geheim, "<werk_id>.<t>")>`.
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
