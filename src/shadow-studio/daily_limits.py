"""Offline per-day ceilings. Limits can be tightened but not silently raised."""
from datetime import date
from batch_ledger import transaction, audit

def configure_day(path, day, *, capacity, quota_ceiling):
    try:
        valid_day = type(day) is str and date.fromisoformat(day).isoformat() == day
    except ValueError:
        valid_day = False
    if (not valid_day or type(capacity) is not int or not 1 <= capacity <= 20
            or type(quota_ceiling) is not int or quota_ceiling < 0):
        return False, "invalid_daily_limits"
    with transaction(path) as db:
        old = db.execute("SELECT capacity,quota_ceiling FROM daily_limits WHERE day=?",
                         (day,)).fetchone()
        if old and (capacity > old[0] or quota_ceiling > old[1]):
            audit(db, "daily_limit_rejected", day, detail="cannot_raise_limits")
            return False, "cannot_raise_limits"
        db.execute("""INSERT INTO daily_limits(day,capacity,quota_ceiling)
                      VALUES(?,?,?) ON CONFLICT(day) DO UPDATE SET
                      capacity=excluded.capacity,quota_ceiling=excluded.quota_ceiling""",
                   (day, capacity, quota_ceiling))
        audit(db, "daily_limit_set", day, detail=f"{capacity}:{quota_ceiling}")
        return True, "configured"
