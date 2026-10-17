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
