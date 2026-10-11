"""Hash van codes en kaartnummers, identiek aan Config.hash in de Btechnics Deur app.

Ronde 1: sha256(zout + "zout:waarde"). Daarna telkens sha256(zout + vorige digest), 2000 rondes in totaal.
De digest gaat als 32 ruwe bytes mee, niet als hex. Uitkomst: hex in kleine letters.
Testvector (afgesproken met de app): zout "test", waarde "1234"
-> 78ddd1e09efdd4c9c9b044ffee6ea076b05e95441fc2ead652e0c46809e31824
"""
import hashlib
import secrets

from .const import HASH_ROUNDS


def zout() -> str:
    """12 willekeurige bytes als 24 hex tekens."""
    return secrets.token_hex(12)


def hash_waarde(zout_: str, waarde: str) -> str:
    z = zout_.encode()
    h = (zout_ + ":" + waarde).encode()
    for _ in range(HASH_ROUNDS):
        h = hashlib.sha256(z + h).digest()
    return h.hex()


def pin_norm(pin: str) -> str:
    """Enkel cijfers, zoals de app ze leest."""
    return "".join(ch for ch in str(pin) if ch.isdigit())


def kaart_norm(kaart: str) -> str:
    """Kaartnummer als hoofdletter hex zonder scheidingstekens."""
    return "".join(ch for ch in str(kaart).upper() if ch in "0123456789ABCDEF")


def sleutel_hash(sleutel: str) -> str:
    """Hash van een tabletsleutel. Sleutels zijn 32 willekeurige bytes, dus 1 ronde volstaat."""
    return hashlib.sha256(sleutel.encode()).hexdigest()
