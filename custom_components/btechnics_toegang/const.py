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
LOG_REDENEN = ["onbekend", "buiten_schema", "verlopen"]

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
