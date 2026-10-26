"""Persistent application delivery observations; independent of dashboard state."""
import csv
import io
import os
import sqlite3
import time
import threading
from contextlib import contextmanager

PATH = os.path.join(os.path.dirname(__file__), "telemetry.db")
RUN_ID = os.environ.get("IOT_RUN_ID", "manual")
_SCHEMA_LOCK = threading.Lock()


@contextmanager
def connect():
    db = sqlite3.connect(PATH, timeout=10)
    try:
        with db:
            with _SCHEMA_LOCK:
                db.execute("""CREATE TABLE IF NOT EXISTS events (
                    timestamp REAL, run_id TEXT, source TEXT, device TEXT, protocol TEXT,
                    bytes INTEGER, boot_id TEXT, seq INTEGER, received INTEGER, peer_ip TEXT)""")
                if "peer_ip" not in {r[1] for r in db.execute("PRAGMA table_info(events)")}:
                    db.execute("ALTER TABLE events ADD COLUMN peer_ip TEXT")
            yield db
    finally:
        db.close()


def record(payload, source, protocol, size, received=True, peer_ip=None):
    seq = payload.get("seq")
    if not isinstance(seq, int) or isinstance(seq, bool) or seq < 0:
        seq = None
    with connect() as db:
        db.execute("INSERT INTO events (timestamp,run_id,source,device,protocol,bytes,"
                   "boot_id,seq,received,peer_ip) VALUES (?,?,?,?,?,?,?,?,?,?)", (
            time.time(), RUN_ID, source, str(payload.get("device", "")),
            protocol, size, str(payload.get("boot_id", "legacy")), seq,
            int(received), peer_ip or source))


def export_csv(run_id=None, since=0):
    with connect() as db:
        cursor = db.execute("SELECT * FROM events WHERE timestamp >= ? "
                            "AND (? IS NULL OR run_id=?) ORDER BY timestamp",
                            (since, run_id, run_id))
        output = io.StringIO()
        writer = csv.writer(output)
        writer.writerow([c[0] for c in cursor.description])
        writer.writerows(cursor)
        return output.getvalue()


def delivery_summary(run_id=None):
    # Separate boots and transports; late packets repair previously observed gaps.
    # This measures gaps BETWEEN observed sequence numbers, not unobserved tails.
    with connect() as db:
        rows = db.execute("""SELECT run_id,source,device,protocol,boot_id,
            MIN(seq),MAX(seq),COUNT(DISTINCT seq),COUNT(*)
            FROM events WHERE received=1 AND seq IS NOT NULL
            AND (? IS NULL OR run_id=?)
            GROUP BY run_id,source,device,protocol,boot_id""", (run_id, run_id)).fetchall()
    return [dict(zip(("run_id", "source", "device", "protocol", "boot_id",
                      "first_seq", "last_seq", "unique_received", "received"), row),
                 observed_missing=row[6] - row[5] + 1 - row[7],
                 observed_loss_pct=100 * (1 - row[7] / (row[6] - row[5] + 1)))
            for row in rows]
