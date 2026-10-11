# Btechnics Toegang

Home Assistant integratie voor codebeheer van de deurtablets (app Btechnics Deur).
Home Assistant is de baas over personen, pincodes, deuren en modus. De tablets halen hun lijst op, controleren zelf (ook offline) en sturen hun logboek terug.

Het contract tussen HA en de tablets staat in het afsprakendocument, schema 1.

## Installeren

1. HACS, Integraties, drie puntjes, Aangepaste repositories: `https://github.com/bogaertm/Btechnics_Toegang`, type Integratie.
2. Btechnics Toegang installeren en Home Assistant herstarten.
3. Instellingen, Apparaten en diensten, Integratie toevoegen, Btechnics Toegang.

## Dashboard

Kaart `custom:btechnics-toegang` met `view: deuren`, `personen` of `logboek`. De kaart wordt automatisch geladen (geen resource toevoegen). Enkel beheerders.

```yaml
type: custom:btechnics-toegang
view: personen
```

## Acties (Ontwikkelhulpmiddelen, Acties, enkel beheerders)

| Actie | Wat |
|---|---|
| `btechnics_toegang.deur_toevoegen` | Deur met naam en de schakelaar of het script dat ze opent |
| `btechnics_toegang.persoon_toevoegen` | Persoon met pincode, deuren, van/tot en weekschema |
| `btechnics_toegang.persoon_wijzigen` / `persoon_verwijderen` | Aanpassen of weg |
| `btechnics_toegang.tablet_koppelcode` | Koppelcode van 6 cijfers, 10 minuten geldig |
| `btechnics_toegang.tablet_intrekken` | Tablet ontkoppelen |
| `btechnics_toegang.modus_zetten` | open, afwezig, vergadering, niet_storen |
| `btechnics_toegang.overzicht` | Alles op een rij, zonder pincodes |

Per deur: `select.deur_<naam>_modus` en `sensor.deur_<naam>_laatste_toegang`. Per tablet: `binary_sensor.tablet_<naam>_online`.
Elke toegang vuurt het event `btechnics_toegang_toegang`.

## Endpoints voor de tablets

Onder `/api/btechnics_toegang/`: `koppel`, `wacht`, `lijst`, `log`, `modus`, `status`, `open`. Authenticatie met de eigen tabletsleutel (`Authorization: Bearer`), niet met een HA token.

## Nog niet in deze versie

Gezichten via HA, kaarten (USB NFC lezer) en synchronisatie met de Akuvox S532.

## Tests

```
pip install pytest-homeassistant-custom-component
pytest
```
