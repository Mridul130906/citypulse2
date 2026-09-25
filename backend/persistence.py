import json
import sqlite3
from .normalization import normalize


class Store:
    def __init__(self, path=":memory:"):
        self.db = sqlite3.connect(path, check_same_thread=False)
        self.db.execute("PRAGMA journal_mode=WAL")
        self.db.execute("PRAGMA synchronous=NORMAL")
        self.db.executescript('''
        CREATE TABLE IF NOT EXISTS events(id TEXT PRIMARY KEY, source TEXT, source_id TEXT, zone TEXT, observed TEXT, body TEXT, raw TEXT, UNIQUE(source,source_id));
        CREATE INDEX IF NOT EXISTS zone_time ON events(zone, observed);
        CREATE INDEX IF NOT EXISTS source_time ON events(source, observed);
        CREATE TABLE IF NOT EXISTS quarantine(id INTEGER PRIMARY KEY, source TEXT, received TEXT, reason TEXT, raw TEXT);
        CREATE TABLE IF NOT EXISTS session(id INTEGER PRIMARY KEY CHECK(id=1), body TEXT);
        ''')

    def ingest(self, source, raw, received):
        try:
            event = normalize(source, raw, received)
        except (ValueError, KeyError, TypeError, StopIteration, OverflowError) as error:
            self.db.execute("INSERT INTO quarantine(source,received,reason,raw) VALUES(?,?,?,?)", (source, received.isoformat(), str(error), json.dumps(raw)))
            self.db.commit()
            return None
        cursor = self.db.execute("INSERT OR IGNORE INTO events VALUES(?,?,?,?,?,?,?)", (event["event_id"], source, event["source_event_id"], event["zone_id"], event["observed_at"], json.dumps(event), json.dumps(raw)))
        self.db.commit()
        return event if cursor.rowcount else None

    def events(self, zone=None, source=None, since=None, until=None, limit=10000, offset=0):
        conditions, args = [], []
        for column, op, value in [("zone", "=", zone), ("source", "=", source), ("observed", ">=", since), ("observed", "<=", until)]:
            if value is not None:
                conditions.append(f"{column}{op}?")
                args.append(value)
        query = "SELECT body FROM events" + (" WHERE " + " AND ".join(conditions) if conditions else "")
        return [json.loads(r[0]) for r in self.db.execute(query + " ORDER BY observed DESC,id DESC LIMIT ? OFFSET ?", args + [limit, offset])]

    def evidence(self, ident):
        row = self.db.execute("SELECT body,raw FROM events WHERE id=?", (ident,)).fetchone()
        return {"event": json.loads(row[0]), "original": json.loads(row[1])} if row else None

    def save(self, state):
        self.db.execute("INSERT OR REPLACE INTO session VALUES(1,?)", (json.dumps(state),))
        self.db.commit()

    def reset(self):
        self.db.executescript("DELETE FROM events; DELETE FROM quarantine; DELETE FROM session;")
