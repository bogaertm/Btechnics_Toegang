"""v0.15.3: zelfcontrole van de deurtoegang in HA, zonder Mac of Claude.

- Elke dag om 07:45: alles nakijken (Akuvox sync en deurlog, tablets en gezichtsserver online, lijst up to date,
  geen sleutel die wacht, gezichten aanwezig). Iets mis = alarm in HA en op de gsm (melding.alarm).
- Elke 5 minuten: een toestel dat langer dan 30 minuten offline is, geeft meteen een alarm (één keer), en een
  herstelmelding als het terug online komt.
"""
from __future__ import annotations

import time
from datetime import timedelta

from homeassistant.components import persistent_notification
from homeassistant.core import HomeAssistant
from homeassistant.util import dt as dt_util

from .const import DOMAIN

OFFLINE_ALARM_S = 30 * 60


def zelfcontrole(hass: HomeAssistant) -> list[str]:
    """Lijst van problemen (leeg = alles in orde). Enkel lezen."""
    d = hass.data.get(DOMAIN) or {}
    reg = d.get("register")
    if reg is None:
        return ["Btechnics Toegang is niet geladen"]
    fout: list[str] = []
    for ak in d.get("akuvoxen", []):
        if not getattr(ak, "aan", False):
            continue
        st = ak.status or {}
        if st.get("ok") is not True:
            fout.append(f"Akuvox {ak.nr}: sync niet in orde ({st.get('fout') or 'nog geen geslaagde controle'})")
        if st.get("deurlog_ok") is False:
            fout.append(f"Akuvox {ak.nr}: deurlog niet leesbaar ({st.get('deurlog_fout') or '?'})")
    for t in reg.data["tablets"].values():
        naam = t["naam"]
        if not reg.tablet_online(t, OFFLINE_ALARM_S):
            fout.append(f"{naam} is offline (langer dan 30 minuten geen contact)")
            continue
        status_versie = (t.get("status") or {}).get("versie")
        if status_versie not in (None, reg.versie):
            laatst = t.get("laatste_lijst_ha") or ""
            if laatst < (dt_util.utcnow() - timedelta(minutes=10)).isoformat():
                fout.append(f"{naam} heeft een oude lijst (versie {status_versie}, HA {reg.versie})")
        if t.get("pubkey_wacht"):
            fout.append(f"{naam} wacht op goedkeuring van een nieuwe sleutel")
        verwacht = reg.gezichten_verwacht(t)
        heeft = (t.get("status") or {}).get("gezichten")
        if verwacht and isinstance(heeft, int) and heeft < verwacht:
            fout.append(f"{naam} heeft {heeft} van {verwacht} gezichten")
    return fout


async def async_dagcontrole(hass: HomeAssistant, _now=None) -> list[str]:
    from .melding import alarm
    fout = zelfcontrole(hass)
    nid = f"{DOMAIN}_dagcontrole"
    if fout:
        alarm(hass, "Dagelijkse controle van de deurtoegang: " + "; ".join(fout),
              title="Btechnics Toegang: controle niet in orde", notification_id=nid)
    else:
        persistent_notification.async_dismiss(hass, nid)
    d = hass.data.get(DOMAIN) or {}
    d["dagcontrole"] = {"tijd": dt_util.now().isoformat(), "fouten": fout}
    return fout


async def async_offline_bewaking(hass: HomeAssistant, _now=None) -> None:
    """Toestel langer dan 30 min offline: één alarm; terug online: herstelmelding (zonder gsm) en alarm weg."""
    from .melding import alarm
    d = hass.data.get(DOMAIN) or {}
    reg = d.get("register")
    if reg is None:
        return
    gemeld: dict = d.setdefault("offline_gemeld", {})
    opgestart = d.setdefault("bewaking_sinds", time.monotonic())
    if time.monotonic() - opgestart < OFFLINE_ALARM_S:
        return   # na een herstart van HA de toestellen eerst de kans geven om te verbinden
    for t in reg.data["tablets"].values():
        nid = f"{DOMAIN}_offline_{t['id']}"
        online = reg.tablet_online(t, OFFLINE_ALARM_S)
        if not online and not gemeld.get(t["id"]):
            gemeld[t["id"]] = True
            soort = "Gezichtsserver" if t.get("soort") == "server" else "Tablet"
            alarm(hass, f"{soort} **{t['naam']}** heeft al meer dan 30 minuten geen contact met HA. "
                        "Stroom, wifi of de app nakijken.", title="Btechnics Toegang: toestel offline", notification_id=nid)
        elif online and gemeld.pop(t["id"], None):
            persistent_notification.async_dismiss(hass, nid)
            persistent_notification.async_create(hass, f"**{t['naam']}** is terug online.",
                                                 title="Btechnics Toegang: toestel terug online", notification_id=f"{nid}_terug")
