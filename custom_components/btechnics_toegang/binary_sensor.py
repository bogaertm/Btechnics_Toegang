"""Online of offline per gekoppelde tablet (offline na 3 minuten zonder status)."""
import time
from datetime import timedelta

from homeassistant.components.binary_sensor import BinarySensorDeviceClass, BinarySensorEntity
from homeassistant.core import callback
from homeassistant.helpers.event import async_track_time_interval

from .const import DOMAIN, OFFLINE_NA
from .entiteit import ToegangEntiteit


async def async_setup_entry(hass, entry, async_add_entities):
    reg = hass.data[DOMAIN]["register"]
    ents = [TabletOnline(reg, t["id"]) for t in reg.data["tablets"].values()]
    async_add_entities(ents)
    if ents:
        @callback
        def _tik(_now) -> None:
            for e in ents:
                if e.hass:
                    e.async_write_ha_state()

        entry.async_on_unload(async_track_time_interval(hass, _tik, timedelta(seconds=30)))


class TabletOnline(ToegangEntiteit, BinarySensorEntity):
    _attr_device_class = BinarySensorDeviceClass.CONNECTIVITY
    _attr_translation_key = "online"

    def __init__(self, reg, tablet_id: str) -> None:
        t = reg.data["tablets"][tablet_id]
        super().__init__(reg, tablet_id, "Online", f"Tablet {t['naam']}")
        self.tablet_id = tablet_id
        self._attr_unique_id = f"{tablet_id}_online"

    @property
    def available(self) -> bool:
        return self.tablet_id in self.reg.data["tablets"]

    @property
    def is_on(self) -> bool:
        t = self.reg.data["tablets"].get(self.tablet_id) or {}
        mono = t.get("laatst_mono")
        return bool(mono and time.monotonic() - mono < OFFLINE_NA)

    @property
    def extra_state_attributes(self):
        t = self.reg.data["tablets"].get(self.tablet_id) or {}
        d = self.reg.data["deuren"].get(t.get("deur_id"), {})
        return {"tablet_id": self.tablet_id, "deur": d.get("naam"), "laatst_gezien": t.get("laatst"),
                "app_versie": t.get("app_versie"), **{k: v for k, v in (t.get("status") or {}).items()
                                                       if k in ("versie", "log_wachtrij", "batterij", "nfc")}}
