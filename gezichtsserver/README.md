# Btechnics gezichtsserver

Container op Unraid (naast Frigate) die gezichten herkent op de camera van een deur met Akuvox en HA vraagt om te openen.
Gebruikt hetzelfde protocol als een deurtablet (soort server), zie `contract/README.md`. HA beslist en opent het relais.

Een container per deur. Beeld: `ghcr.io/bogaertm/btx-gezichtsserver`. Map `/data` (appdata) bewaart de sleutel en de koppeling.

| Variabele | Standaard | Uitleg |
|---|---|---|
| CAMERA | | RTSP, bv. rtsp://192.168.1.185:8554/VTO_Magazijn (go2rtc van Frigate) |
| HA_URL | https://192.168.1.117:8443 | https poort van Btechnics Toegang; moet https zijn bij elke start, anders stopt de container |
| KOPPELCODE | | enkel bij de eerste start (HA, Deuren, Tablet of gezichtsserver koppelen) |
| NAAM | Gezichtsserver | naam in HA |
| PROEF | 1 | 1 = enkel meten en melden in HA, 0 = deur openen |
| DREMPEL | 0.70 | cosinus met de afdruk (mediaan over 2 s) |
| MARGE | 0.10 | verschil met de tweede beste persoon |
| COHORTE | 0.30 | ondergrens voor die tweede (typische cosinus van een vreemde), ook als er maar een persoon in de lijst staat |
| ECHT_MIN / ECHT_HOOG | 0.85 / 0.97 | echtheid (MiniFASNet). Onder ECHT_MIN: vermoedelijke foto. Zonder 3D bewijs: onder ECHT_HOOG vermoedelijke foto, erboven onzeker. Nooit open zonder 3D |
| DIEPTE_MIN | 2.5 | vervorming bij naderen tegenover ruis; verplicht om te openen |
| OOG_MIN | 45 | pixels tussen de irissen (dichtbij) |
| HOEK_MAX | 15 | graden draai en kanteling |
| STIL_S | 2 | seconden stil en herkend |
| FPS / THREADS | 8 / 2 | belasting |

Opnieuw koppelen: `staat.json` in `/data` wissen en KOPPELCODE zetten. De sleutel (`sleutel.pem`) blijft dan dezelfde.
Er worden geen beelden bewaard; enkel als HA om een foto vraagt (openen op afstand) gaat er een beeld naar HA.

## Beslissen (v0.3.0)

| Regel | Uitleg |
|---|---|
| Sessie = spoor | een sessie is een ononderbroken spoor van het grootste gezicht. Nieuwe sessie bij een sprong van het midden groter dan 1 x de oogafstand, een schaalsprong groter dan 30 % of meer dan 1 s tussen twee beelden |
| Open | enkel als cos >= DREMPEL, cos min tweede >= MARGE, echt >= ECHT_MIN EN diepte >= DIEPTE_MIN (de persoon moet in hetzelfde spoor naderen). Een hoge echtheid alleen opent nooit meer |
| Diepte | vorm van ver tegenover dichtbij, enkel rigide punten (ooghoeken, neusbrug, neuspunt, neusvleugels, voorhoofd), restfout na een homografie: een foto of scherm, ook schuin gehouden, geeft geen diepte. Verre beelden met een afdruk van een andere persoon tellen niet |
| Een besluit per sessie | open, foto, onzeker of onbekend. Na 6 s dichtbij en recht zonder besluit: onbekend. Geen nieuwe kansen in dezelfde sessie |
| Meerdere gezichten | een tweede gezicht groter dan 0,6 x het grootste (oogafstand): geen besluit, sessie gemarkeerd als meerdere en vermeld in de proefmeting |
| Foto | enkel een melding (vermoedelijke_foto); niemand wordt daardoor geblokkeerd |

**Niet live zetten (PROEF=0) met DREMPEL onder 0,70, en niet zonder metingen van vreemden (onbekende gezichten, ook lijkende personen) en van spoofs (foto op papier, gsm en tablet scherm, recht en schuin, dag en nacht).** De standaarden zijn een vertrekpunt, geen gemeten grenswaarden. Een persoon in de lijst heeft geen echte tweede: daarom geldt COHORTE als ondergrens.

## Robuustheid

| Onderdeel | Gedrag |
|---|---|
| Camera | ffmpeg over tcp met timeout 10 s. Waakhond: meer dan 10 s geen nieuw beeld = sessie dicht, opnieuw verbinden, status camera_ok false met camera_laatst |
| HA | alle verzoeken zonder omleidingen. /open: weigering of fout als WARNING en beperkt als systeemmelding, HA onbereikbaar geeft geen crash |
| Doorzetten | enkel naar tablets met een RSA sleutel van minstens 2048 bit, nooit naar een andere server; een slechte doelsleutel slaat de rest niet over; elke doorzetting komt (zonder namen) in de meldingen |
| Fouten | elke fout in de verwerking wordt gevangen; dezelfde stacktrace hoogstens een keer per 60 s in het log |
| Bestanden | sleutel.pem en staat.json worden meteen met rechten 0600 aangemaakt |

Status naar HA (veld echtheid): proef, versie, sessies, geopend (live gelukt), proef_open, foto, onzeker, onbekend, meerdere, fouten,
ontsleutel_fouten (ook een keer per lijstversie gemeld), doorgezet, open_geweigerd, open_fout, camera_ok, camera_laatst, camera_beelden, camera_herstarts.

Proefmeting per sessie: duur, beelden, oog max, recht, beste persoon met cos max en de cos mediaan apart voor dichtbij (oog >= OOG_MIN) en ver, echtheid, scherpte en of er meerdere gezichten waren.

Tests: `python -m pytest -q tests` (in de map gezichtsserver).
