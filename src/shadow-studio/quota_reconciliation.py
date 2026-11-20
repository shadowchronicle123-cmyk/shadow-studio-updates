"""Offline conservative quota reconciliation; no YouTube calls or uploads."""
from batch_ledger import transaction, audit

SCHEMA = """CREATE TABLE IF NOT EXISTS quota_reconciliation (
 fingerprint TEXT PRIMARY KEY,
 extra_units INTEGER NOT NULL CHECK(extra_units >= 0),
 actual_units INTEGER,
 outcome TEXT NOT NULL CHECK(outcome IN ('confirmed','uncertain'))
)"""

def reconcile(path, fingerprint, *, outcome, actual_units=None):
    if (type(fingerprint) is not str or len(fingerprint) != 64
            or any(c not in "0123456789abcdef" for c in fingerprint)
            or outcome not in ("confirmed", "uncertain")
            or (outcome == "confirmed" and
                (type(actual_units) is not int or actual_units < 0))
            or (outcome == "uncertain" and actual_units is not None)):
        return False, "invalid_reconciliation"
    with transaction(path) as db:
        db.execute(SCHEMA)
        row = db.execute("SELECT day,quota_units FROM reservations WHERE fingerprint=?",
                         (fingerprint,)).fetchone()
        if row is None:
            audit(db, "reconcile_rejected", fingerprint=fingerprint, detail="unknown_reservation")
            return False, "unknown_reservation"
        day, estimate = row
        old = db.execute("SELECT extra_units,actual_units,outcome FROM quota_reconciliation WHERE fingerprint=?",
                         (fingerprint,)).fetchone()
        if old and old[2] == "confirmed" and outcome == "uncertain":
            return False, "cannot_downgrade_confirmed"
        if old and old[2] == "confirmed" and actual_units is not None and actual_units < old[1]:
            return False, "cannot_reduce_observed_quota"
        extra = max(old[0] if old else 0, max(0, actual_units - estimate) if actual_units is not None else 0)
        db.execute("""INSERT INTO quota_reconciliation(fingerprint,extra_units,actual_units,outcome)
                      VALUES(?,?,?,?) ON CONFLICT(fingerprint) DO UPDATE SET
                      extra_units=excluded.extra_units,actual_units=excluded.actual_units,
                      outcome=excluded.outcome""", (fingerprint, extra, actual_units, outcome))
        audit(db, "quota_reconciled", day, fingerprint, f"{outcome}:{extra}")
        return True, "reconciled"
