"""Laatste toegang per deur, met de laatste 20 regels van het logboek als attribuut."""
from homeassistant.components.sensor import SensorEntity

from .const import DOMAIN
from .entiteit import ToegangEntiteit


async def async_setup_entry(hass, entry, async_add_entities):
    reg = hass.data[DOMAIN]["register"]
    async_add_entities(LaatsteToegang(reg, d["id"]) for d in reg.data["deuren"].values())


class LaatsteToegang(ToegangEntiteit, SensorEntity):
    _attr_translation_key = "laatste"
    _attr_icon = "mdi:account-key"

    def __init__(self, reg, deur_id: str) -> None:
        d = reg.data["deuren"][deur_id]
        super().__init__(reg, deur_id, "Laatste", f"Deur {d['naam']}")
        self.deur_id = deur_id
        self._attr_unique_id = f"{deur_id}_laatste"

    def _regels(self):
        return [r for r in self.reg.data["log"] if r.get("deur_id") == self.deur_id]

    def _naam(self, r):
        p = self.reg.data["personen"].get(r.get("persoon_id") or "")
        return p["naam"] if p else None

    @property
    def native_value(self):
        regels = self._regels()
        if not regels:
            return None
        r = regels[-1]
        return (f"{self._naam(r) or 'Onbekend'} ({r['soort']})")[:255]

    @property
    def extra_state_attributes(self):
        return {"logboek": [{"tijd": r.get("tijd"), "soort": r.get("soort"), "methode": r.get("methode"),
                             "reden": r.get("reden"), "persoon": self._naam(r), "tekst": r.get("tekst")}
                            for r in reversed(self._regels()[-20:])]}
