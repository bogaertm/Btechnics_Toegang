"""Per deur: laatste toegang (zonder naam, zoals Btechnics VTO) en het aantal actieve codes."""
from homeassistant.components.sensor import SensorEntity, SensorStateClass

from .const import DOMAIN
from .entiteit import ToegangEntiteit
from .toegang import ts_lokaal


async def async_setup_entry(hass, entry, async_add_entities):
    d = hass.data[DOMAIN]
    ents = []
    for deur in d["register"].data["deuren"].values():
        ents += [LaatsteToegang(d["register"], d["toegang"], deur["id"]), Codes(d["register"], deur["id"])]
    async_add_entities(ents)


class LaatsteToegang(ToegangEntiteit, SensorEntity):
    _attr_translation_key = "laatste"
    _unrecorded_attributes = frozenset({"recent"})

    def __init__(self, reg, tg, deur_id: str) -> None:
        super().__init__(reg, deur_id, "Laatste", f"Deur {reg.data['deuren'][deur_id]['naam']}")
        self.tg, self.deur_id = tg, deur_id
        self._attr_unique_id = f"{deur_id}_laatste"

    def _laatste(self):
        r = self.tg.recent.get(self.deur_id) or []
        return r[0] if r else None

    @property
    def available(self) -> bool:
        return self.deur_id in self.reg.data["deuren"]

    @property
    def icon(self):
        r = self._laatste()
        return "mdi:door" if not r else "mdi:door-open" if r["opened"] else "mdi:door-closed-lock"

    @property
    def native_value(self):
        r = self._laatste()
        if not r:
            return None
        hoe = "op afstand" if str(r["method"]).startswith("op afstand") else f"via {r['method']}"
        return f"{'Geopend' if r['opened'] else 'Geweigerd'} {hoe}"

    @property
    def extra_state_attributes(self):
        r = self._laatste()
        if not r:
            return {}
        return {"door_id": self.deur_id, "door": r["door"], "method": r["method"], "opened": r["opened"],
                "time": ts_lokaal(r["ts"]).isoformat(), "ts": r["ts"],
                # verandert als een foto later binnenkomt, zodat de kaart Deuren zich meteen opnieuw tekent
                "fotos": sum(1 for x in (self.tg.recent.get(self.deur_id) or []) if x.get("photo"))}


class Codes(ToegangEntiteit, SensorEntity):
    _attr_translation_key = "codes"
    _attr_icon = "mdi:dialpad"
    _attr_state_class = SensorStateClass.MEASUREMENT

    def __init__(self, reg, deur_id: str) -> None:
        super().__init__(reg, deur_id, "Codes", f"Deur {reg.data['deuren'][deur_id]['naam']}")
        self.deur_id = deur_id
        self._attr_unique_id = f"{deur_id}_codes"

    @property
    def available(self) -> bool:
        return self.deur_id in self.reg.data["deuren"]

    @property
    def native_value(self):
        return sum(1 for p in self.reg.data["personen"].values() if p["status"] == "active" and self.deur_id in p["deuren"])
