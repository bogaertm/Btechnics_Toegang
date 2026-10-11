DOMAIN = "btechnics_toegang"
STORAGE_KEY = f"{DOMAIN}_register"
STORAGE_VERSION = 1
SCHEMA = 1

API = "/api/btechnics_toegang"

HASH_ROUNDS = 2000
PIN_MIN = 4

MODI = ["open", "afwezig", "vergadering", "niet_storen"]
MODUS_STANDAARD = "open"

KOPPELCODE_GELDIG = 600       # seconden
KOPPELCODE_POGINGEN = 5       # foute pogingen voor alle openstaande codes vervallen
WACHT_MAX = 55                # seconden dat /wacht openblijft
OFFLINE_NA = 180              # seconden zonder status = offline

LOG_KEEP = 2000
LOG_SOORTEN = ["toegang", "fout", "geweigerd", "alarm", "systeem", "oproep"]
LOG_METHODES = ["pin", "kaart", "gezicht", "ha", "knop"]
LOG_REDENEN = ["onbekend", "buiten_schema", "verlopen"]

EVENT_TOEGANG = f"{DOMAIN}_toegang"
SIGNAL_UPDATE = f"{DOMAIN}_update"

# Akuvox (fase 2): eigen gebruikers krijgen een UserID vanaf dit nummer, de rest blijft onaangeroerd.
USER_ID_START = 700001
