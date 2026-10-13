"""Gezichtsafdrukken: HA bewaart enkel versleutelde pakketten en kan ze zelf niet lezen.

De gsm maakt per inschrijving een AES-256-GCM sleutel, versleutelt daarmee de afdrukken en verpakt die sleutel
per tablet met RSA-OAEP (SHA-1, MGF1 SHA-1) en de publieke sleutel van die tablet (Android Keystore).
"""
from __future__ import annotations

import base64
import binascii
import hashlib

from cryptography.hazmat.primitives.asymmetric import rsa
from cryptography.hazmat.primitives.serialization import load_der_public_key

from .const import BEDRIJF, GEZICHT_MAX, GEZICHT_MIN, GEZICHT_TOESTEMMING, GEZICHT_TOESTEMMING_VERSIE, GEZICHT_VERSIE


class GezichtFout(ValueError):
    pass


def b64(s, naam: str, minimum: int = 1, maximum: int = 1 << 20) -> bytes:
    if not isinstance(s, str):
        raise GezichtFout(f"{naam} ontbreekt")
    try:
        d = base64.b64decode(s, validate=True)
    except (binascii.Error, ValueError) as e:
        raise GezichtFout(f"{naam} is geen geldige base64") from e
    if not minimum <= len(d) <= maximum:
        raise GezichtFout(f"{naam} heeft een ongeldige lengte")
    return d


def publieke_sleutel(waarde) -> tuple[str, str]:
    """SPKI DER in base64 van een RSA sleutel van minstens 2048 bit. Geeft (base64, sha256 hex)."""
    der = b64(waarde, "pubkey", 200, 2000)
    try:
        k = load_der_public_key(der)
    except ValueError as e:
        raise GezichtFout("pubkey is geen geldige publieke sleutel (SPKI DER)") from e
    if not isinstance(k, rsa.RSAPublicKey) or k.key_size < 2048:
        raise GezichtFout("pubkey moet een RSA sleutel van minstens 2048 bit zijn")
    return base64.b64encode(der).decode(), hashlib.sha256(der).hexdigest()


def toestemming_tekst() -> str:
    return GEZICHT_TOESTEMMING.format(bedrijf=BEDRIJF["naam"], verantwoordelijke=BEDRIJF["verantwoordelijke"],
                                      adres=BEDRIJF["adres"], email=BEDRIJF["email"])


def toestemming_hash() -> str:
    return hashlib.sha256(toestemming_tekst().encode()).hexdigest()


def controleer_pakket(d: dict, sleutel_ids: set[str]) -> dict:
    """Pakket van de gsm nakijken. sleutel_ids: pubkey_id van de tablets waarvoor mag versleuteld worden.
    De verpakte sleutels staan per pubkey_id: wordt een tablet opnieuw gekoppeld met dezelfde Keystore sleutel,
    dan blijft de afdruk bruikbaar."""
    if d.get("versie") != GEZICHT_VERSIE:
        raise GezichtFout(f"versie moet {GEZICHT_VERSIE} zijn")
    if d.get("toestemming") != GEZICHT_TOESTEMMING_VERSIE or d.get("toestemming_hash") != toestemming_hash():
        raise GezichtFout("de toestemming ontbreekt of hoort bij een andere tekst; herlaad de pagina")
    aantal = d.get("aantal")
    if not isinstance(aantal, int) or not GEZICHT_MIN <= aantal <= GEZICHT_MAX:
        raise GezichtFout(f"{GEZICHT_MIN} tot {GEZICHT_MAX} afdrukken nodig")
    b64(d.get("iv"), "iv", 12, 12)
    b64(d.get("data"), "data", 16, 64 * 1024)
    sleutels = d.get("sleutels")
    if not isinstance(sleutels, dict) or not sleutels:
        raise GezichtFout("geen enkele tablet om voor te versleutelen")
    uit = {}
    for kid, s in sleutels.items():
        if kid not in sleutel_ids:
            raise GezichtFout("de sleutels van de tablets zijn intussen gewijzigd; herlaad de pagina")
        b64(s, "sleutel", 256, 512)
        uit[kid] = s
    return {"versie": GEZICHT_VERSIE, "iv": d["iv"], "data": d["data"], "aantal": aantal, "sleutels": uit,
            "toestemming": GEZICHT_TOESTEMMING_VERSIE, "toestemming_hash": d["toestemming_hash"]}
