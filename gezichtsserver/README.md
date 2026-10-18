# Btechnics gezichtsserver

Container op Unraid (naast Frigate) die gezichten herkent op de camera van een deur met Akuvox en HA vraagt om te openen.
Gebruikt hetzelfde protocol als een deurtablet (soort server), zie `contract/README.md`. HA beslist en opent het relais.

Een container per deur. Beeld: `ghcr.io/bogaertm/btx-gezichtsserver`. Map `/data` (appdata) bewaart de sleutel en de koppeling.

| Variabele | Standaard | Uitleg |
|---|---|---|
| CAMERA | | RTSP, bv. rtsp://192.168.1.185:8554/VTO_Magazijn (go2rtc van Frigate) |
| HA_URL | https://192.168.1.117:8443 | https poort van Btechnics Toegang |
| KOPPELCODE | | enkel bij de eerste start (HA, Deuren, Tablet of gezichtsserver koppelen) |
| NAAM | Gezichtsserver | naam in HA |
| PROEF | 1 | 1 = enkel meten en melden in HA, 0 = deur openen |
| DREMPEL | 0.70 | cosinus met de afdruk |
| ECHT_MIN / ECHT_HOOG | 0.85 / 0.97 | echtheid (MiniFASNet); zonder 3D bewijs bij naderen is ECHT_HOOG nodig |
| DIEPTE_MIN | 2.5 | vervorming bij naderen tegenover ruis |
| OOG_MIN | 45 | pixels tussen de irissen (dichtbij) |
| HOEK_MAX | 15 | graden draai en kanteling |
| STIL_S | 2 | seconden stil en herkend |
| FPS / THREADS | 8 / 2 | belasting |

Opnieuw koppelen: `staat.json` in `/data` wissen en KOPPELCODE zetten. De sleutel (`sleutel.pem`) blijft dan dezelfde.
Er worden geen beelden bewaard; enkel als HA om een foto vraagt (openen op afstand) gaat er een beeld naar HA.
