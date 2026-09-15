"""Daily rosters: who works today and for how long.

The manager saves a roster for a business date. A date with no roster of its own carries the most
recent earlier one forward; with no roster at all, the institution pack's team size and shift length
apply. Saves are appended, never overwritten, so every change of plan stays visible.
"""

from __future__ import annotations

from pathlib import Path

from lastmile.config.schema import Collector, InstitutionPack, Roster
from lastmile.store import db


def save(roster_date: str, collectors: list[Collector], saved_by: str, note: str | None = None,
         data_dir: Path | None = None) -> Roster:
    r = Roster(roster_date=roster_date, collectors=collectors, source="saved", saved_by=saved_by, saved_at=db.now(),
               note=note)
    with db.connect(data_dir) as con:
        con.execute("INSERT INTO rosters (roster_date, collectors_json, saved_by, saved_at, note) VALUES (?,?,?,?,?)",
                    (roster_date, db.dumps([c.model_dump() for c in collectors]), saved_by, r.saved_at, note))
    return r


def for_date(roster_date: str, institution: InstitutionPack, data_dir: Path | None = None) -> Roster:
    with db.connect(data_dir) as con:
        row = con.execute("SELECT * FROM rosters WHERE roster_date <= ? ORDER BY roster_date DESC, id DESC LIMIT 1",
                          (roster_date,)).fetchone()
    if row is None:
        return Roster.default(institution.capacity, roster_date)
    source = "saved" if row["roster_date"] == roster_date else "carried_forward"
    note = row["note"] if source == "saved" else f"carried forward from {row['roster_date']}"
    return Roster(roster_date=roster_date, collectors=[Collector(**c) for c in db.loads(row["collectors_json"])],
                  source=source, saved_by=row["saved_by"], saved_at=row["saved_at"], note=note)


def history(roster_date: str, data_dir: Path | None = None) -> list[dict]:
    with db.connect(data_dir) as con:
        rows = db.rows(con, "SELECT * FROM rosters WHERE roster_date = ? ORDER BY id DESC", (roster_date,))
    for r in rows:
        r["collectors"] = db.loads(r.pop("collectors_json"))
    return rows
