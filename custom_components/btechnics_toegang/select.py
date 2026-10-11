"""Modus per deur: open (Beschikbaar), afwezig, vergadering, niet_storen."""
from homeassistant.components.select import SelectEntity

from .const import DOMAIN, MODI
from .entiteit import ToegangEntiteit


async def async_setup_entry(hass, entry, async_add_entities):
    reg = hass.data[DOMAIN]["register"]
    async_add_entities(ModusSelect(reg, d["id"]) for d in reg.data["deuren"].values())


class ModusSelect(ToegangEntiteit, SelectEntity):
    _attr_options = MODI
    _attr_translation_key = "modus"
    _attr_icon = "mdi:door"

    def __init__(self, reg, deur_id: str) -> None:
        d = reg.data["deuren"][deur_id]
        super().__init__(reg, deur_id, "Modus", f"Deur {d['naam']}")
        self.deur_id = deur_id
        self._attr_unique_id = f"{deur_id}_modus"

    @property
    def available(self) -> bool:
        return self.deur_id in self.reg.data["deuren"]

    @property
    def current_option(self):
        d = self.reg.data["deuren"].get(self.deur_id)
        return d["modus"] if d else None

    @property
    def extra_state_attributes(self):
        d = self.reg.data["deuren"].get(self.deur_id) or {}
        return {"deur_id": self.deur_id, "sinds": d.get("modus_sinds"), "door": d.get("modus_door"),
                "open_entiteit": d.get("open_entiteit")}

    async def async_select_option(self, option: str) -> None:
        self.reg.modus_zetten(self.deur_id, option, door="ha")
