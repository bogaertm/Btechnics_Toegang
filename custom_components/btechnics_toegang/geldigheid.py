"""Geldigheid van een persoon: van/tot datum en weekschema, zoals afgesproken in schema 1.

Regels (afsprakendocument punt 3):
- van en tot zijn datums, beide inclusief; None betekent geen grens.
- schema is een lijst blokken {dagen, van, tot}; een lege lijst betekent altijd.
- dagen: 1 is maandag tot 7 is zondag, de dag waarop het blok start.
- tot kleiner dan van: het blok loopt over middernacht tot de volgende dag.
- van gelijk aan tot: 24 uur vanaf van.
"""
from __future__ import annotations

from datetime import date, datetime, time, timedelta


def _tijd(s: str) -> time:
    u, m = s.split(":")
    return time(int(u), int(m))


def controleer_blok(blok: dict) -> dict:
    """Controleert en normaliseert een schemablok. Gooit ValueError bij fouten."""
    dagen = blok.get("dagen")
    if not isinstance(dagen, list) or not dagen or any(not isinstance(d, int) or d < 1 or d > 7 for d in dagen):
        raise ValueError("dagen moet een lijst van 1 tot 7 zijn")
    van, tot = str(blok.get("van", "")), str(blok.get("tot", ""))
    try:
        _tijd(van)
        _tijd(tot)
    except (ValueError, TypeError) as e:
        raise ValueError(f"uren als HH:MM, kreeg {van} en {tot}") from e
    return {"dagen": sorted(set(dagen)), "van": f"{_tijd(van):%H:%M}", "tot": f"{_tijd(tot):%H:%M}"}


def _in_blok(blok: dict, nu: datetime) -> bool:
    van, tot = _tijd(blok["van"]), _tijd(blok["tot"])
    for startdag in (nu.date(), nu.date() - timedelta(days=1)):
        if startdag.isoweekday() not in blok["dagen"]:
            continue
        begin = datetime.combine(startdag, van, nu.tzinfo)
        if tot > van:
            einde = datetime.combine(startdag, tot, nu.tzinfo)
        else:  # over middernacht, of 24 uur bij van gelijk aan tot
            einde = datetime.combine(startdag + timedelta(days=1), tot, nu.tzinfo)
        if begin <= nu < einde:
            return True
    return False


def reden_geweigerd(persoon: dict, nu: datetime) -> str | None:
    """None als de persoon nu toegang heeft, anders 'verlopen' of 'buiten_schema'."""
    vandaag = nu.date()
    van = persoon.get("van")
    tot = persoon.get("tot")
    if van and vandaag < date.fromisoformat(van):
        return "buiten_schema"
    if tot and vandaag > date.fromisoformat(tot):
        return "verlopen"
    schema = persoon.get("schema") or []
    if schema and not any(_in_blok(b, nu) for b in schema):
        return "buiten_schema"
    return None
