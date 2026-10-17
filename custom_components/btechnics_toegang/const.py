DOMAIN = "btechnics_toegang"
STORAGE_KEY = f"{DOMAIN}_register"
STORAGE_VERSION = 1
SCHEMA = 1

API = "/api/btechnics_toegang"

HASH_ROUNDS = 2000
PIN_MIN = 4
PIN_MAX = 8

MODI = ["open", "afwezig", "vergadering", "niet_storen"]
MODUS_STANDAARD = "open"

KOPPELCODE_GELDIG = 600       # seconden
KOPPELCODE_POGINGEN = 5       # foute pogingen voor alle openstaande codes vervallen
WACHT_MAX = 55                # seconden dat /wacht openblijft
OFFLINE_NA = 180              # seconden zonder status = offline

AUDIT_MAX = 1000            # wijzigingen (zoals Btechnics VTO)
GEBEURTENISSEN_MAX = 500    # meldingen van de tablets die geen toegang zijn (systeem, oproep, alarm, fout)
ARCHIEF_DAGEN = 400         # toegangen in het archief
PORTAAL_FOUTEN_MAX = 10     # foute pogingen per uitnodiging of portaal
UITNODIGING_DAGEN = 7
FOTO_MAX = 6 * 1024 * 1024
# gebruikers zonder beheerrechten: enkel eigen tijdelijke codes (zoals Btechnics VTO)
USER_MAX_ACTIVE = 10
USER_MAX_DAYS = 7
USER_MAX_AHEAD_DAYS = 31
LOG_SOORTEN = ["toegang", "fout", "geweigerd", "alarm", "systeem", "oproep"]
LOG_METHODES = ["pin", "kaart", "gezicht", "ha", "knop"]
LOG_REDENEN = ["onbekend", "buiten_schema", "verlopen", "vermoedelijke_foto"]

EVENT_TOEGANG = f"{DOMAIN}_toegang"
SIGNAL_UPDATE = f"{DOMAIN}_update"

# Akuvox (fase 2): eigen gebruikers krijgen een UserID vanaf dit nummer, de rest blijft onaangeroerd.
USER_ID_START = 700001

# gegevens voor het portaal (privacytekst en kop)
BEDRIJF = {
    "naam": "Btechnics",
    "verantwoordelijke": "Matthias Bogaert",
    "adres": "Nieuwstraat 79A, 9840 De Pinte",
    "email": "matthias@btechnics.be",
}
FOTO_MAP = "btechnics_toegang/fotos"

# ---------------------------------------------------------------- gezichtsherkenning (afdrukken van de gsm)
GEZICHT_VERSIE = "mfn112-arcface5-v1"   # MobileFaceNet 112x112, ArcFace 5 punten, 192 float32
GEZICHT_MIN, GEZICHT_MAX = 3, 8         # aantal afdrukken per inschrijving
GEZICHT_AAD = "btx-gezicht-v1:"         # + persoon_id, als extra gegevens bij AES-GCM
GEZICHT_TOESTEMMING_VERSIE = "v1"
GEZICHT_TOESTEMMING = (
    "Ik geef {bedrijf} de uitdrukkelijke toestemming om van mijn gezicht een gezichtsafdruk te maken, "
    "enkel om de deuren te openen waarvoor ik toegang heb. De afdruk wordt op mijn eigen gsm berekend en "
    "versleuteld zodat enkel de deurtablets ze kunnen lezen. Home Assistant en {bedrijf} kunnen ze niet bekijken. "
    "Van de scan wordt geen foto bewaard. Gezichtsherkenning is vrijwillig: mijn code blijft altijd werken. "
    "Ik kan mijn toestemming op elk moment intrekken op deze pagina; dan wordt de afdruk gewist, ook op de tablets. "
    "Ga ik uit dienst, dan wordt de afdruk ook gewist. Verantwoordelijke: {verantwoordelijke}, {adres}, {email}."
)

PULS_STANDAARD = 5  # seconden: een schakelaar of input_boolean zet HA na het openen zelf terug uit (0 = niet)
