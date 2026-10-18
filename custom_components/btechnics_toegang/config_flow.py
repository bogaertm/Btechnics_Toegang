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


NIEUWE_NAAM = {1: "Voordeur Akuvox", 2: "Akuvox 2"}


class ToegangOpties(OptionsFlow):
    """Instellingen: tot 2 Akuvox toestellen (toestel 1 met de oude sleutels akuvox_*, toestel 2 met akuvox2_*) en https."""

    async def async_step_init(self, user_input=None):
        from .akuvox import Akuvox, AkuvoxFout, optie_prefix
        reg = self.hass.data[DOMAIN]["register"]
        o = dict(self.config_entry.options)
        fouten, extra = {}, {}
        if user_input is not None:
            wachtwoorden = {f"{optie_prefix(n)}wachtwoord" for n in (1, 2)}
            nieuw = {**o, **{k: v for k, v in user_input.items() if k not in wachtwoorden}}
            for k in wachtwoorden:
                if user_input.get(k):
                    nieuw[k] = user_input[k]
            for nr in (1, 2):
                v = optie_prefix(nr)
                if nieuw.get(f"{v}deur") == NIEUWE_DEUR and not nieuw.get(f"{v}aan"):
                    nieuw[f"{v}deur"] = ""   # geen deur aanmaken zolang het toestel niet aan staat
                if nieuw.get(f"{v}deur") == NIEUWE_DEUR:
                    naam = NIEUWE_NAAM[nr]
                    bestaand = next((d for d in reg.data["deuren"].values() if d["naam"].lower() == naam.lower()), None)
                    nieuw[f"{v}deur"] = (bestaand or reg.deur_toevoegen(naam, wie="instellingen"))["id"]
            if nieuw.get("akuvox2_aan") and nieuw.get("akuvox_aan") and nieuw.get("akuvox2_deur") == nieuw.get("akuvox_deur"):
                fouten["base"] = "akuvox"
                extra = {"fout": "toestel 1 en 2 moeten elk een eigen deur hebben"}
            for nr in (1, 2):
                v = optie_prefix(nr)
                if fouten or not nieuw.get(f"{v}aan"):
                    continue
                if not nieuw.get(f"{v}ip") or not nieuw.get(f"{v}gebruiker") or not nieuw.get(f"{v}wachtwoord"):
                    fouten["base"] = "akuvox"
                    extra = {"fout": f"toestel {nr}: vul IP, gebruiker en wachtwoord in"}
                    continue
                try:
                    await Akuvox(self.hass, reg, nieuw, nr).async_test()
                except AkuvoxFout as e:
                    fouten["base"] = "akuvox"
                    extra = {"fout": f"toestel {nr}: {e}"}
            if not fouten:
                return self.async_create_entry(title="", data=nieuw)
            o = nieuw
        deuren = [{"value": d["id"], "label": d["naam"]} for d in sorted(reg.data["deuren"].values(), key=lambda x: x["naam"].lower())]
        velden = {}
        for nr in (1, 2):
            v = optie_prefix(nr)
            keuzes = deuren + [{"value": NIEUWE_DEUR, "label": f"Nieuwe deur {NIEUWE_NAAM[nr]}"}]
            velden.update({
                vol.Optional(f"{v}aan", default=bool(o.get(f"{v}aan"))): bool,
                vol.Optional(f"{v}ip", default=o.get(f"{v}ip", "192.168.1.153" if nr == 1 else "")): str,
                vol.Optional(f"{v}gebruiker", default=o.get(f"{v}gebruiker", "")): str,
                vol.Optional(f"{v}wachtwoord", default=""): TextSelector(TextSelectorConfig(type=TextSelectorType.PASSWORD)),
                vol.Optional(f"{v}deur", default=o.get(f"{v}deur") or NIEUWE_DEUR): SelectSelector(
                    SelectSelectorConfig(options=keuzes, mode=SelectSelectorMode.DROPDOWN)),
            })
        velden.update({
            vol.Optional("https_poort", default=int(o.get("https_poort") or 8443)): vol.All(vol.Coerce(int), vol.Range(min=1024, max=65535)),
            vol.Optional("enkel_https", default=bool(o.get("enkel_https"))): bool,
        })
        ww = lambda k: "ingesteld" if o.get(k) else "nog niet ingesteld"  # noqa: E731
        return self.async_show_form(step_id="init", data_schema=vol.Schema(velden), errors=fouten,
                                    description_placeholders={"fout": extra.get("fout", ""), "wachtwoord": ww("akuvox_wachtwoord"),
                                                              "wachtwoord2": ww("akuvox2_wachtwoord")})
