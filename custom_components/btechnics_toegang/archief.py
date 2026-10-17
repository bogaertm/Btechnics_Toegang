"""Toegangsarchief (SQLite in de configmap), 400 dagen. Zelfde vragen en antwoorden als Btechnics VTO,
zodat de historiekkaart dezelfde werking heeft. Alle methodes zijn blokkerend: in de executor uitvoeren."""
from __future__ import annotations

import re
import sqlite3
import threading
import time
from contextlib import closing
from datetime import datetime, tzinfo

SCHEMA = """
CREATE TABLE IF NOT EXISTS access (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    uid        TEXT    NOT NULL UNIQUE,
    door_id    TEXT    NOT NULL,
    door       TEXT    NOT NULL,
    ts         INTEGER NOT NULL,
    name       TEXT    NOT NULL,
    card       TEXT    NOT NULL DEFAULT '',
    method     TEXT    NOT NULL,
    status     TEXT    NOT NULL,
    persoon_id TEXT    NOT NULL DEFAULT '',
    tekst      TEXT    NOT NULL DEFAULT ''
);
CREATE INDEX IF NOT EXISTS ix_access_door ON access (door_id, id);
CREATE INDEX IF NOT EXISTS ix_access_ts ON access (ts);
CREATE INDEX IF NOT EXISTS ix_access_name ON access (name COLLATE NOCASE);
"""
MAX_ROWS = 50000
_LABEL_RE = re.compile(r"^(Op afstand|Exitknop|Onbekende badge|Onbekend gezicht|Foute code|Onbekende code)$")


def is_label(name: str) -> bool:
    return bool(_LABEL_RE.match(name or ""))


def _like(term: str) -> str:
    return "%" + term.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_") + "%"


def _merge_people(raw) -> list:
    merged = {}
    for p in raw:
        m = merged.setdefault(p["name"].casefold(), {"spellings": {}, "count": 0, "opened": 0, "last": 0, "doors": set()})
        m["spellings"][p["name"]] = m["spellings"].get(p["name"], 0) + p["n"]
        m["count"] += p["n"]
        m["opened"] += p["o"] or 0
        m["last"] = max(m["last"], p["last"] or 0)
        m["doors"] |= set((p["doors"] or "").split(",")) - {""}
    out = [{"name": max(m["spellings"].items(), key=lambda x: (x[1], x[0]))[0], "count": m["count"], "opened": m["opened"],
            "refused": m["count"] - m["opened"], "last": m["last"], "doors": sorted(m["doors"])} for m in merged.values()]
    out.sort(key=lambda p: (-p["count"], p["name"].casefold()))
    return out[:1000]


class Archief:
    def __init__(self, path: str):
        self._path = path
        self._lock = threading.Lock()
        with self._lock, closing(self._conn()) as c, c:
            c.execute("PRAGMA journal_mode=WAL")
            c.executescript(SCHEMA)
            kolommen = {r["name"] for r in c.execute("PRAGMA table_info(access)")}
            if "foto" not in kolommen:
                c.execute("ALTER TABLE access ADD COLUMN foto TEXT NOT NULL DEFAULT ''")

    def _conn(self):
        c = sqlite3.connect(self._path, timeout=30)
        c.row_factory = sqlite3.Row
        c.create_function("cf", 1, lambda v: v.casefold() if isinstance(v, str) else v, deterministic=True)
        return c

    def toevoegen(self, rijen: list[dict]) -> int:
        """rijen: {uid, door_id, door, ts, name, card, method, opened, persoon_id, tekst}. Dubbele uid wordt genegeerd."""
        if not rijen:
            return 0
        with self._lock, closing(self._conn()) as c, c:
            voor = c.total_changes
            c.executemany(
                "INSERT OR IGNORE INTO access (uid, door_id, door, ts, name, card, method, status, persoon_id, tekst, foto) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                [(r["uid"], r["door_id"], r["door"], int(r["ts"]), r["name"], r.get("card") or "", r["method"],
                  "1" if r["opened"] else "0", r.get("persoon_id") or "", r.get("tekst") or "", r.get("foto") or "")
                 for r in rijen])
            return c.total_changes - voor

    def hernoem_persoon(self, persoon_id: str, naam: str) -> None:
        """Naam van een persoon bijwerken in de historiek (zodat zoeken op de nieuwe naam alles vindt)."""
        with self._lock, closing(self._conn()) as c, c:
            c.execute("UPDATE access SET name = ? WHERE persoon_id = ?", (naam, persoon_id))

    def recent(self, door_id: str, limit: int) -> list:
        with closing(self._conn()) as c:
            rows = c.execute("SELECT door_id, door, ts, name, method, status, foto FROM access WHERE door_id = ? "
                             "ORDER BY ts DESC, id DESC LIMIT ?", (door_id, int(limit))).fetchall()
        return [{"door_id": r["door_id"], "door": r["door"], "ts": r["ts"], "name": r["name"], "method": r["method"],
                 **({"photo": r["foto"]} if r["foto"] else {}),
                 "opened": r["status"] == "1"} for r in rows]

    def prune(self, dagen: int, now: float | None = None) -> int:
        cutoff = int((now or time.time()) - dagen * 86400)
        with self._lock, closing(self._conn()) as c, c:
            return c.execute("DELETE FROM access WHERE ts < ?", (cutoff,)).rowcount

    def fotos_in_gebruik(self) -> set:
        with closing(self._conn()) as c:
            return {r[0] for r in c.execute("SELECT foto FROM access WHERE foto <> ''")}

    def query(self, tz: tzinfo, door_ids=None, search=None, person=None, status="all", start=None, end=None,
              limit=200, offset=0, max_id=None) -> dict:
        where, args = ["1=1"], []
        if door_ids:
            where.append(f"door_id IN ({','.join('?' * len(door_ids))})")
            args += list(door_ids)
        if person is not None:
            where.append("cf(name) = ?")
            args.append(person.casefold())
        if search:
            where.append("(cf(name) LIKE ? ESCAPE '\\' OR cf(card) LIKE ? ESCAPE '\\')")
            term = _like(search.strip().casefold())
            args += [term, term]
        if status == "opened":
            where.append("status = '1'")
        elif status == "refused":
            where.append("status <> '1'")
        if start is not None:
            where.append("ts >= ?")
            args.append(int(start))
        if end is not None:
            where.append("ts < ?")
            args.append(int(end))
        limit = max(0, min(int(limit), MAX_ROWS))
        with closing(self._conn()) as c:
            if max_id is None:
                max_id = c.execute("SELECT COALESCE(MAX(id), 0) FROM access").fetchone()[0]
            where.append("id <= ?")
            args.append(int(max_id))
            w = "WHERE " + " AND ".join(where)
            tot = c.execute(f"SELECT COUNT(*) n, SUM(status = '1') o FROM access {w}", args).fetchone()
            rows = c.execute(f"SELECT id, door_id, door, ts, name, card, method, status, foto FROM access {w} "
                             "ORDER BY ts DESC, id DESC LIMIT ? OFFSET ?", args + [limit, max(0, int(offset))]).fetchall()
            raw_people = c.execute(f"SELECT name, COUNT(*) n, SUM(status = '1') o, MAX(ts) last, GROUP_CONCAT(DISTINCT door) doors "
                                   f"FROM access {w} GROUP BY name", args).fetchall()
            months, days = {}, {}
            for ts, st, name, door in c.execute(f"SELECT ts, status, name, door FROM access {w}", args):
                local = datetime.fromtimestamp(ts, tz)
                b = months.setdefault(local.strftime("%Y-%m"), [0, 0])
                b[0] += 1
                b[1] += st == "1"
                d = days.setdefault(local.strftime("%Y-%m-%d"), {"count": 0, "opened": 0, "first": ts, "last": ts,
                                                                  "people": set(), "doors": {}})
                d["count"] += 1
                d["opened"] += st == "1"
                d["first"], d["last"] = min(d["first"], ts), max(d["last"], ts)
                if name and not is_label(name):
                    d["people"].add(name.casefold())
                d["doors"][door] = d["doors"].get(door, 0) + 1
        total, opened = tot["n"] or 0, tot["o"] or 0
        return {
            "max_id": int(max_id), "total": total, "opened": opened, "refused": total - opened,
            "rows": [{"id": r["id"], "door_id": r["door_id"], "door": r["door"], "ts": r["ts"], "name": r["name"] or "?",
                      "card": r["card"], "method": r["method"], "opened": r["status"] == "1",
                      **({"photo": r["foto"]} if r["foto"] else {})} for r in rows],
            "people": _merge_people(raw_people),
            "months": [{"month": m, "count": n, "opened": o} for m, (n, o) in sorted(months.items())],
            "days": [{"day": k, "count": v["count"], "opened": v["opened"], "refused": v["count"] - v["opened"],
                      "people": len(v["people"]), "first": v["first"], "last": v["last"],
                      "doors": [{"door": dn, "count": n} for dn, n in sorted(v["doors"].items(), key=lambda x: (-x[1], x[0]))]}
                     for k, v in sorted(days.items(), reverse=True)],
        }

    def counts_since(self, start: int) -> dict:
        with closing(self._conn()) as c:
            rows = c.execute("SELECT door_id, COUNT(*) n, SUM(status = '1') o FROM access WHERE ts >= ? GROUP BY door_id",
                             (int(start),)).fetchall()
        return {r["door_id"]: {"opened": r["o"] or 0, "refused": r["n"] - (r["o"] or 0)} for r in rows}

    def stats(self) -> dict:
        with closing(self._conn()) as c:
            rows = c.execute("SELECT door_id, COUNT(*) n, MIN(ts) first, MAX(ts) last FROM access GROUP BY door_id").fetchall()
        return {r["door_id"]: {"count": r["n"], "first": r["first"], "last": r["last"]} for r in rows}

    def voor_persoon(self, persoon_id: str, limit: int = 10) -> list:
        with closing(self._conn()) as c:
            rows = c.execute("SELECT door, ts, method, status FROM access WHERE persoon_id = ? ORDER BY ts DESC LIMIT ?",
                             (persoon_id, int(limit))).fetchall()
        return [{"door": r["door"], "ts": r["ts"], "method": r["method"], "opened": r["status"] == "1"} for r in rows]
