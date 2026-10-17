"""Vaste testdeur voor tests (HA chat, APK chat, scripts). Nooit de koppeling van een echte tablet gebruiken.

testdeur_klaarzetten: maakt (eenmalig) de deur "Testdeur (tests)" met een testpersoon, en geeft een nieuwe koppelcode.
testdeur_opruimen: ontkoppelt alle tablets van de testdeur en wist de testpersonen. De deur zelf blijft.
"""
from __future__ import annotations

import secrets

from .register import Register, botst, zwak

NAAM = "Testdeur (tests)"


def _deur(reg: Register) -> dict:
    for d in reg.data["deuren"].values():
        if d.get("test"):
            return d
    d = reg.deur_toevoegen(NAAM, wie="testset")
    d["test"] = True
    return d


def klaarzetten(reg: Register) -> dict:
    d = _deur(reg)
    personen = [p for p in reg.data["personen"].values() if p.get("bron") == "test"]
    if not personen:
        bezet = reg._pins()  # noqa: SLF001
        while True:
            pin = "".join(secrets.choice("0123456789") for _ in range(8))
            if not zwak(pin) and not any(pin == b or botst(pin, b) for b in bezet):
                break
        p = reg.toevoegen("testset", naam="Testpersoon (tests)", pin=pin, deuren=[d["id"]], bron="test")
        personen = [p]
    code = reg.koppelcode_maken(d["id"], "Testtablet")
    return {"deur_id": d["id"], "deur": d["naam"], "koppelcode": code, "geldig_s": 600,
            "personen": [{"id": p["id"], "naam": p["naam"], "pin": p["pin"]} for p in personen]}


def opruimen(reg: Register) -> dict:
    d = next((x for x in reg.data["deuren"].values() if x.get("test")), None)
    tablets = [t["id"] for t in reg.data["tablets"].values() if d and t["deur_id"] == d["id"]]
    for tid in tablets:
        reg.tablet_intrekken(tid, wie="testset")
    personen = [p["id"] for p in reg.data["personen"].values() if p.get("bron") == "test"]
    for pid in personen:
        reg.verwijderen("testset", pid)
    return {"tablets": len(tablets), "personen": len(personen)}
