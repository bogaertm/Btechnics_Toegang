# Btechnics Toegang

Home Assistant integratie voor codebeheer van de deurtablets (app Btechnics Deur).
Home Assistant is de baas over personen, pincodes, deuren en modus. De tablets halen hun lijst op, controleren zelf (ook offline) en sturen hun logboek terug.

Het contract tussen HA en de tablets staat in het afsprakendocument, schema 1.

## Installeren

1. HACS, Integraties, drie puntjes, Aangepaste repositories: `https://github.com/bogaertm/Btechnics_Toegang`, type Integratie.
2. Btechnics Toegang installeren en Home Assistant herstarten.
3. Instellingen, Apparaten en diensten, Integratie toevoegen, Btechnics Toegang.

## Dashboard

Het dashboard **Deurtoegang** heeft vijf tabbladen, met dezelfde werking als Btechnics VTO (Trefpunt):

| Tabblad | Kaart |
|---|---|
| Overzicht | `custom:btechnics-toegang-overzicht` |
| Codes | `custom:btechnics-toegang-codes` (tijdelijke codes, vaste codes, uitnodigingen, portaallinks, wijzigingen) |
| Historiek | `custom:btechnics-toegang-historiek` (400 dagen, per persoon, per dag, CSV) |
| Deuren | `custom:btechnics-toegang` met `view: deuren` (modus, openen, tablets koppelen) |
| Handleiding | `custom:btechnics-toegang-handleiding` |

De kaarten worden automatisch geladen. Gebruikers zonder beheerrechten zien enkel hun eigen tijdelijke codes (maximaal 7 dagen, 10 tegelijk).

## Portaal

Een uitnodiging geeft een link naar `/api/btechnics_toegang/portaal/<sleutel>`. De medewerker vult voornaam, naam, gsm, e-mail, een eigen code (4 tot 8 cijfers, geen reeksen) en een foto in. Daarna krijgt hij een vaste persoonlijke link; wijzigen vraagt altijd zijn huidige code. Foto's staan in `config/btechnics_toegang/fotos`.

## Acties (enkel beheerders)

`deur_toevoegen`, `deur_wijzigen`, `deur_verwijderen`, `deur_openen`, `modus_zetten`, `persoon_toevoegen`, `tijdelijke_code`, `persoon_wijzigen`, `persoon_blokkeren`, `persoon_deblokkeren`, `persoon_uit_dienst`, `persoon_herstellen`, `persoon_verwijderen`, `overzicht`, `tablet_koppelcode`, `tablet_intrekken`, `uitnodiging_maken`.

## Endpoints voor de tablets

Onder `/api/btechnics_toegang/`: `koppel`, `wacht`, `lijst`, `log`, `modus`, `status`, `open`. Authenticatie met de eigen tabletsleutel (`Authorization: Bearer`), niet met een HA token.

## Nog niet in deze versie

Gezichten via HA, kaarten (USB NFC lezer) en synchronisatie met de Akuvox S532.

## Tests

```
pip install pytest-homeassistant-custom-component
pytest
```
