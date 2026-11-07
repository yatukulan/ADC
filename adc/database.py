"""Local Database (DB).

Secret: how and where data is stored (an SQLite file) and the layout of records and log entries. Other modules
see inventory records, log entries, the user list, the transmission queue and open-transaction markers.
There is deliberately no operation that changes or deletes a log entry.
"""
import json
import sqlite3
from typing import Dict, List, Optional

from .common import Result


class Database:
    def __init__(self, path: str, hw) -> None:
        self._hw = hw                                    # the source of time for log entries
        self._c = sqlite3.connect(path, check_same_thread=False, isolation_level=None)
        self._c.row_factory = sqlite3.Row
        self._c.execute("PRAGMA synchronous=FULL")
        self._c.executescript("""
            CREATE TABLE IF NOT EXISTS inventory(medication TEXT PRIMARY KEY, drawer INTEGER, quantity INTEGER,
                expiry TEXT, threshold INTEGER, discrepant INTEGER DEFAULT 0);
            CREATE TABLE IF NOT EXISTS log(seq INTEGER PRIMARY KEY AUTOINCREMENT, time REAL, iso TEXT, type TEXT,
                user_id TEXT, patient_id TEXT, medication TEXT, quantity INTEGER, outcome TEXT, detail TEXT);
            CREATE TRIGGER IF NOT EXISTS log_no_update BEFORE UPDATE ON log
                BEGIN SELECT RAISE(ABORT, 'log entries cannot be modified'); END;
            CREATE TRIGGER IF NOT EXISTS log_no_delete BEFORE DELETE ON log
                BEGIN SELECT RAISE(ABORT, 'log entries cannot be deleted'); END;
            CREATE TABLE IF NOT EXISTS users(credential_hash TEXT PRIMARY KEY, user_id TEXT, role TEXT);
            CREATE TABLE IF NOT EXISTS queue(seq INTEGER PRIMARY KEY AUTOINCREMENT, message TEXT);
            CREATE TABLE IF NOT EXISTS txn(txn_id INTEGER PRIMARY KEY, data TEXT);
            CREATE TABLE IF NOT EXISTS counters(name TEXT PRIMARY KEY, value INTEGER);
        """)

    def close(self) -> None:
        self._c.close()

    # ---- inventory records ----
    def save_inventory(self, rec: Dict) -> None:
        """Implements: FR-INV-02 (a record holds medication, quantity on hand, drawer, expiration date and low-stock
        threshold), NFR-REL-01 (the change is committed to disk before the call returns)."""
        self._c.execute("INSERT OR REPLACE INTO inventory VALUES(?,?,?,?,?,?)",
                        (rec["medication"], rec["drawer"], rec["quantity"], rec["expiry"], rec["threshold"],
                         int(rec["discrepant"])))

    def inventory(self) -> List[Dict]:
        rows = [dict(r) for r in self._c.execute("SELECT * FROM inventory ORDER BY drawer")]
        return [dict(r, discrepant=bool(r["discrepant"])) for r in rows]

    # ---- log ----
    def log(self, type: str, user_id: str = "", patient_id: str = "", medication: str = "", quantity=None,
            outcome: str = "", detail: str = "") -> None:
        """Implements: FR-LOG-08 (an entry can be added and never changed), NFR-SEC-01 (an entry has a field for
        the patient identifier and none for a name)."""
        t = self._hw.now()
        self._c.execute("INSERT INTO log(time,iso,type,user_id,patient_id,medication,quantity,outcome,detail) "
                        "VALUES(?,?,?,?,?,?,?,?,?)",
                        (t, self._hw.iso(t), type, user_id, patient_id, medication, quantity, outcome, detail))

    def log_entries(self) -> List[Dict]:
        return [dict(r) for r in self._c.execute("SELECT * FROM log ORDER BY seq")]

    def log_since(self, seq: int) -> List[Dict]:
        """The entries added after entry number seq, oldest first."""
        return [dict(r) for r in self._c.execute("SELECT * FROM log WHERE seq > ? ORDER BY seq", (seq,))]

    def last_seq(self) -> int:
        """The number of the newest log entry, or 0 for an empty log."""
        return self._c.execute("SELECT COALESCE(MAX(seq), 0) FROM log").fetchone()[0]

    def review(self, session, params) -> Result:
        """Implements: FR-LOG-09 (the Pharmacist and the Technician can view log entries; Access Control has
        already checked the role)."""
        return Result(True, data=self.log_entries())

    # ---- authorized users ----
    def users(self) -> List[Dict]:
        return [dict(r) for r in self._c.execute("SELECT * FROM users")]

    def replace_users(self, users: List[Dict]) -> None:
        self._c.execute("BEGIN")
        self._c.execute("DELETE FROM users")
        self._c.executemany("INSERT INTO users VALUES(?,?,?)", [(u["credential_hash"], u["user_id"], u["role"]) for u in users])
        self._c.execute("COMMIT")

    # ---- transmission queue ----
    def queue_push(self, message: Dict) -> None:
        self._c.execute("INSERT INTO queue(message) VALUES(?)", (json.dumps(message),))

    def queue_first(self) -> Optional[Dict]:
        r = self._c.execute("SELECT message FROM queue ORDER BY seq LIMIT 1").fetchone()
        return json.loads(r[0]) if r else None

    def queue_pop(self) -> None:
        self._c.execute("DELETE FROM queue WHERE seq=(SELECT MIN(seq) FROM queue)")

    def queue_length(self) -> int:
        return self._c.execute("SELECT COUNT(*) FROM queue").fetchone()[0]

    # ---- transactions in progress (so that a power loss can be recorded) ----
    def save_txn(self, txn_id: int, data: Dict) -> None:
        self._c.execute("INSERT OR REPLACE INTO txn VALUES(?,?)", (txn_id, json.dumps(data)))

    def close_txn(self, txn_id: int) -> None:
        self._c.execute("DELETE FROM txn WHERE txn_id=?", (txn_id,))

    def open_txns(self) -> List[Dict]:
        return [json.loads(r[0]) for r in self._c.execute("SELECT data FROM txn ORDER BY txn_id")]

    def next_id(self, name: str) -> int:
        """A number that is never used twice, also across a power loss."""
        self._c.execute("INSERT INTO counters VALUES(?,1) ON CONFLICT(name) DO UPDATE SET value=value+1", (name,))
        return self._c.execute("SELECT value FROM counters WHERE name=?", (name,)).fetchone()[0]
