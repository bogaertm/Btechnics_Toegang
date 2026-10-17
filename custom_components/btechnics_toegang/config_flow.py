"""Installatie zonder velden. Opties: de Akuvox S532 (IP, gebruiker en wachtwoord van de HTTP API, welke deur)."""
from __future__ import annotations

import voluptuous as vol

from homeassistant.config_entries import ConfigEntry, ConfigFlow, OptionsFlow
from homeassistant.core import callback
from homeassistant.helpers.selector import (
    SelectSelector, SelectSelectorConfig, SelectSelectorMode, TextSelector, TextSelectorConfig, TextSelectorType,
)

from .const import DOMAIN

NIEUWE_DEUR = "__nieuw__"


class ToegangConfigFlow(ConfigFlow, domain=DOMAIN):
    VERSION = 1

    async def async_step_user(self, user_input=None):
        if self._async_current_entries():
            return self.async_abort(reason="single_instance_allowed")
        if user_input is not None:
            return self.async_create_entry(title="Btechnics Toegang", data={})
        return self.async_show_form(step_id="user")

    @staticmethod
    @callback
    def async_get_options_flow(entry: ConfigEntry) -> OptionsFlow:
        return ToegangOpties()


class ToegangOpties(OptionsFlow):
    async def async_step_init(self, user_input=None):
        reg = self.hass.data[DOMAIN]["register"]
        o = dict(self.config_entry.options)
        fouten, extra = {}, {}
        if user_input is not None:
            nieuw = {**o, **{k: v for k, v in user_input.items() if k != "akuvox_wachtwoord"}}
            if user_input.get("akuvox_wachtwoord"):
                nieuw["akuvox_wachtwoord"] = user_input["akuvox_wachtwoord"]
            if nieuw.get("akuvox_deur") == NIEUWE_DEUR and not nieuw.get("akuvox_aan"):
                nieuw["akuvox_deur"] = ""   # geen deur aanmaken zolang de S532 niet aan staat
            if nieuw.get("akuvox_deur") == NIEUWE_DEUR:
                naam = "Voordeur Akuvox"
                bestaand = next((d for d in reg.data["deuren"].values() if d["naam"].lower() == naam.lower()), None)
                nieuw["akuvox_deur"] = (bestaand or reg.deur_toevoegen(naam, wie="instellingen"))["id"]
            if nieuw.get("akuvox_aan") and (not nieuw.get("akuvox_ip") or not nieuw.get("akuvox_gebruiker") or not nieuw.get("akuvox_wachtwoord")):
                fouten["base"] = "akuvox"
                extra = {"fout": "vul IP, gebruiker en wachtwoord in"}
            elif nieuw.get("akuvox_aan"):
                from .akuvox import Akuvox, AkuvoxFout
                try:
                    t = await Akuvox(self.hass, reg, nieuw).async_test()
                    extra = {"aantal": str(t["gebruikers"])}
                except AkuvoxFout as e:
                    fouten["base"] = "akuvox"
                    extra = {"fout": str(e)}
            if not fouten:
                return self.async_create_entry(title="", data=nieuw)
            o = nieuw
        deuren = [{"value": d["id"], "label": d["naam"]} for d in sorted(reg.data["deuren"].values(), key=lambda x: x["naam"].lower())]
        deuren.append({"value": NIEUWE_DEUR, "label": "Nieuwe deur Voordeur Akuvox"})
        schema = vol.Schema({
            vol.Optional("akuvox_aan", default=bool(o.get("akuvox_aan"))): bool,
            vol.Optional("akuvox_ip", default=o.get("akuvox_ip", "192.168.1.153")): str,
            vol.Optional("akuvox_gebruiker", default=o.get("akuvox_gebruiker", "")): str,
            vol.Optional("akuvox_wachtwoord", default=""): TextSelector(TextSelectorConfig(type=TextSelectorType.PASSWORD)),
            vol.Optional("akuvox_deur", default=o.get("akuvox_deur") or NIEUWE_DEUR): SelectSelector(
                SelectSelectorConfig(options=deuren, mode=SelectSelectorMode.DROPDOWN)),
            vol.Optional("https_poort", default=int(o.get("https_poort") or 8443)): vol.All(vol.Coerce(int), vol.Range(min=1024, max=65535)),
            vol.Optional("enkel_https", default=bool(o.get("enkel_https"))): bool,
        })
        return self.async_show_form(step_id="init", data_schema=schema, errors=fouten,
                                    description_placeholders={"fout": extra.get("fout", ""),
                                                              "wachtwoord": "ingesteld" if o.get("akuvox_wachtwoord") else "nog niet ingesteld"})
