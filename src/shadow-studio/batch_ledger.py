"""Offline SQLite reservation ledger. Never calls a publisher or external service.

Reservations are only planning claims, not proof of rights or authorization to publish.
Quota units are caller-provided estimates, not verified YouTube quota measurements.
"""
import sqlite3
from contextlib import contextmanager
from datetime import date, datetime, timezone
from pathlib import Path

SCHEMA = """
CREATE TABLE IF NOT EXISTS control (id INTEGER PRIMARY KEY CHECK(id=1), stopped INTEGER NOT NULL);
INSERT OR IGNORE INTO control(id,stopped) VALUES(1,0);
CREATE TABLE IF NOT EXISTS reservations (
 id INTEGER PRIMARY KEY, day TEXT NOT NULL, fingerprint TEXT NOT NULL UNIQUE,
 kind TEXT NOT NULL, quota_units INTEGER NOT NULL CHECK(quota_units>0),
 state TEXT NOT NULL DEFAULT 'reserved', created_at TEXT NOT NULL);
CREATE INDEX IF NOT EXISTS idx_reservations_day ON reservations(day);
CREATE TABLE IF NOT EXISTS rights_evidence (
 fingerprint TEXT PRIMARY KEY, evidence_reference TEXT NOT NULL,
 authorized INTEGER NOT NULL CHECK(authorized IN (0,1)));
CREATE TABLE IF NOT EXISTS audit (
 id INTEGER PRIMARY KEY, occurred_at TEXT NOT NULL, event TEXT NOT NULL,
 day TEXT, fingerprint TEXT, detail TEXT NOT NULL);
"""

@contextmanager
def transaction(path):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    db = sqlite3.connect(path, timeout=10, isolation_level=None)
    try:
        db.execute("PRAGMA busy_timeout=10000")
        db.execute("BEGIN IMMEDIATE")
        for statement in SCHEMA.split(";"):
            if statement.strip():
                db.execute(statement)
        try:
            yield db
            db.execute("COMMIT")
        except BaseException:
            db.execute("ROLLBACK")
            raise
    finally:
        db.close()


def audit(db, event, day=None, fingerprint=None, detail=""):
    db.execute("INSERT INTO audit(occurred_at,event,day,fingerprint,detail) VALUES(?,?,?,?,?)",
               (datetime.now(timezone.utc).isoformat(), event, day, fingerprint, detail))


def set_stop(path, stopped):
    if type(stopped) is not bool:
        raise ValueError("Stop flag must be boolean")
    with transaction(path) as db:
        db.execute("UPDATE control SET stopped=? WHERE id=1", (int(stopped),))
        audit(db, "emergency_stop" if stopped else "manual_resume_required")


def register_rights(path, fingerprint, evidence_reference, *, authorized=False):
    """Record a trusted reviewer decision; arbitrary caller claims are not sufficient."""
    if (not isinstance(fingerprint, str) or len(fingerprint) != 64
            or any(c not in "0123456789abcdef" for c in fingerprint)
            or not isinstance(evidence_reference, str) or not evidence_reference.strip()
            or type(authorized) is not bool):
        raise ValueError("Invalid rights evidence")
    with transaction(path) as db:
        db.execute("""INSERT INTO rights_evidence(fingerprint,evidence_reference,authorized)
                      VALUES(?,?,?) ON CONFLICT(fingerprint) DO UPDATE SET
                      evidence_reference=excluded.evidence_reference,
                      authorized=excluded.authorized""",
                   (fingerprint, evidence_reference.strip(), int(authorized)))
        audit(db, "rights_review", fingerprint=fingerprint,
              detail="approved" if authorized else "denied")


def reserve(path, *, day, fingerprint, kind, permission_verified=False,
            quality_passed=False, daily_capacity=20, quota_limit=None,
            quota_units=1600):
    """Atomic fail-closed reservation. Returns (accepted, reason).

    No network, uploads, rendering, or public publishing are performed.
    The rights boolean must originate from a trusted rights-ledger verification.
    """
    try:
        valid_day = date.fromisoformat(day).isoformat() == day if type(day) is str else False
    except ValueError:
        valid_day = False
    if (not valid_day
            or not isinstance(fingerprint,str) or len(fingerprint)!=64
            or any(c not in "0123456789abcdef" for c in fingerprint)
            or kind not in ("clip","story")
            or type(daily_capacity) is not int or not 1 <= daily_capacity <= 20
            or type(quota_units) is not int or quota_units <= 0
            or type(quota_limit) is not int or quota_limit < 0):
        return False, "invalid_or_unverified_configuration"
    with transaction(path) as db:
        if db.execute("SELECT stopped FROM control WHERE id=1").fetchone()[0]:
            audit(db, "rejected", day, fingerprint, "emergency_stop")
            return False, "emergency_stop"
        rights_row = db.execute("SELECT authorized FROM rights_evidence WHERE fingerprint=?",
                                (fingerprint,)).fetchone() if kind == "clip" else None
        if not permission_verified or (kind == "clip" and rights_row != (1,)):
            audit(db, "rejected", day, fingerprint, "rights_unverified")
            return False, "rights_unverified"
        if not quality_passed:
            audit(db, "rejected", day, fingerprint, "quality_unverified")
            return False, "quality_unverified"
        if db.execute("SELECT 1 FROM reservations WHERE fingerprint=?", (fingerprint,)).fetchone():
            audit(db, "rejected", day, fingerprint, "duplicate")
            return False, "duplicate"
        count, used = db.execute(
            "SELECT COUNT(*),COALESCE(SUM(quota_units),0) FROM reservations WHERE day=?",
            (day,)).fetchone()
        if count >= daily_capacity:
            audit(db, "rejected", day, fingerprint, "daily_capacity")
            return False, "daily_capacity"
        if used + quota_units > quota_limit:
            audit(db, "rejected", day, fingerprint, "quota_budget")
            return False, "quota_budget"
        db.execute("""INSERT INTO reservations(day,fingerprint,kind,quota_units,created_at)
                      VALUES(?,?,?,?,?)""",
                   (day,fingerprint,kind,quota_units,datetime.now(timezone.utc).isoformat()))
        audit(db, "reserved", day, fingerprint, kind)
        return True, "reserved"


def snapshot(path):
    with transaction(path) as db:
        return {
            "stopped": bool(db.execute("SELECT stopped FROM control WHERE id=1").fetchone()[0]),
            "reservations": db.execute("SELECT day,fingerprint,kind,quota_units FROM reservations ORDER BY id").fetchall(),
            "audit": db.execute("SELECT event,detail FROM audit ORDER BY id").fetchall(),
            "public_enabled": False,
        }
