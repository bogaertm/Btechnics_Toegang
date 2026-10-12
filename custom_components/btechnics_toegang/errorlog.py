"""Foutenlog van 60 dagen: waarschuwingen en fouten van deze integratie, ook na een herstart.

Het logboek van Home Assistant (system_log) begint bij elke herstart opnieuw. Hier worden de meldingen
van de eigen loggers (custom_components.<domein>) bewaard in een kleine SQLite-databank in de configmap.
Dezelfde melding op dezelfde dag telt op in plaats van een nieuwe rij. Schrijven gebeurt in blokken
buiten de event loop."""
from __future__ import annotations

import logging
import re
import sqlite3
import threading
import time
import traceback
from contextlib import closing

KEEP_DAYS = 60


class ErrorLog(logging.Handler):
    def __init__(self, path: str, logger_name: str, keep_days: int = KEEP_DAYS):
        super().__init__(level=logging.WARNING)
        self.path, self.logger_name, self.keep_days = path, logger_name, keep_days
        self._buf: list = []
        self._lock = threading.Lock()
        with closing(sqlite3.connect(self.path)) as c:
            c.execute("CREATE TABLE IF NOT EXISTS fout (id INTEGER PRIMARY KEY, dag TEXT, eerste REAL, laatste REAL, "
                      "niveau TEXT, bron TEXT, bericht TEXT, details TEXT, aantal INTEGER, UNIQUE(dag, niveau, bron, bericht))")
            c.commit()

    # ---- logging ----
    def emit(self, record: logging.LogRecord):
        try:
            # geheugenadressen en dergelijke weglaten, zodat dezelfde melding samen geteld wordt
            msg = re.sub(r"0x[0-9a-fA-F]+", "0x…", record.getMessage())
            details = ""
            if record.exc_info:
                details = "".join(traceback.format_exception(*record.exc_info))[-4000:]
            with self._lock:
                self._buf.append((record.created, record.levelname, record.name, msg[:2000], details))
                if len(self._buf) > 5000:      # nooit onbeperkt groeien als het wegschrijven hapert
                    del self._buf[:1000]
        except Exception:  # noqa: BLE001  loggen mag nooit zelf iets stukmaken
            self.handleError(record)

    def attach(self):
        lg = logging.getLogger(self.logger_name)
        if self not in lg.handlers:
            lg.addHandler(self)

    def detach(self):
        logging.getLogger(self.logger_name).removeHandler(self)

    # ---- wegschrijven (in de executor) ----
    def flush_to_db(self):
        with self._lock:
            rows, self._buf = self._buf, []
        with closing(sqlite3.connect(self.path)) as c:
            for ts, lvl, src, msg, det in rows:
                day = time.strftime("%Y-%m-%d", time.localtime(ts))
                c.execute("INSERT INTO fout (dag, eerste, laatste, niveau, bron, bericht, details, aantal) VALUES (?,?,?,?,?,?,?,1) "
                          "ON CONFLICT(dag, niveau, bron, bericht) DO UPDATE SET laatste = excluded.laatste, aantal = aantal + 1, "
                          "details = CASE WHEN excluded.details <> '' THEN excluded.details ELSE details END",
                          (day, ts, ts, lvl, src, msg, det))
            c.execute("DELETE FROM fout WHERE laatste < ?", (time.time() - self.keep_days * 86400,))
            c.commit()
        return len(rows)

    def query(self, days: int = KEEP_DAYS, limit: int = 500) -> dict:
        since = time.time() - days * 86400
        with closing(sqlite3.connect(self.path)) as c:
            c.row_factory = sqlite3.Row
            rows = c.execute("SELECT dag, eerste, laatste, niveau, bron, bericht, details, aantal FROM fout WHERE laatste >= ? "
                             "ORDER BY laatste DESC LIMIT ?", (since, int(limit))).fetchall()
            tot = c.execute("SELECT niveau, SUM(aantal) n FROM fout WHERE laatste >= ? GROUP BY niveau", (since,)).fetchall()
        return {"keep_days": self.keep_days, "days": days, "totals": {r["niveau"]: r["n"] for r in tot},
                "rows": [{"day": r["dag"], "first": r["eerste"], "last": r["laatste"], "level": r["niveau"],
                          "source": r["bron"].split(".")[-1], "message": r["bericht"], "details": r["details"], "count": r["aantal"]} for r in rows]}


async def async_setup_errorlog(hass, domain: str):
    """Eenmalig: logger koppelen, elke minuut wegschrijven, ook bij het afsluiten. Geeft de ErrorLog terug."""
    from datetime import timedelta

    from homeassistant.const import EVENT_HOMEASSISTANT_STOP
    from homeassistant.helpers.event import async_track_time_interval

    key = f"{domain}_errorlog"
    if key in hass.data:
        return hass.data[key]
    log = await hass.async_add_executor_job(ErrorLog, hass.config.path(f"{domain}_foutenlog.db"), f"custom_components.{domain}")
    log.attach()
    hass.data[key] = log

    async def _flush(_now=None):
        try:
            await hass.async_add_executor_job(log.flush_to_db)
        except Exception:  # noqa: BLE001  volgende minuut opnieuw (niet via de eigen logger: geen lus)
            logging.getLogger("homeassistant").debug("foutenlog %s wegschrijven mislukt", domain, exc_info=True)

    async_track_time_interval(hass, _flush, timedelta(minutes=1))
    hass.bus.async_listen_once(EVENT_HOMEASSISTANT_STOP, _flush)
    return log
