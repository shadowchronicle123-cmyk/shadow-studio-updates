"""Offline persistent daily ceilings and reviewer provenance regression tests."""
from pathlib import Path
from tempfile import TemporaryDirectory
from concurrent.futures import ThreadPoolExecutor
from batch_ledger import register_rights, reserve, snapshot, set_stop
from daily_limits import configure_day

DAY = "2026-11-20"

def claim(path, n, *, quota_limit=32000, daily_capacity=20):
    return reserve(path, day=DAY, fingerprint=f"{n:064x}", kind="clip",
                   permission_verified=True, quality_passed=True,
                   quota_limit=quota_limit, daily_capacity=daily_capacity,
                   quota_units=1600)

def run():
    with TemporaryDirectory() as tmp:
        path = Path(tmp) / "limits.sqlite3"
        assert configure_day(path, DAY, capacity=2, quota_ceiling=3200) == (True, "configured")
        assert configure_day(path, DAY, capacity=20, quota_ceiling=32000) == (False, "cannot_raise_limits")
        assert configure_day(path, "2026-02-30", capacity=2, quota_ceiling=3200) == (False, "invalid_daily_limits")
        for n in range(8):
            register_rights(path, f"{n:064x}", f"license-{n}",
                            authorized=True, reviewer_reference="qa-reviewer")
        evidence = snapshot(path)["rights_evidence"]
        assert evidence[0][1:] == ("license-0", 1, "qa-reviewer")
        with ThreadPoolExecutor(max_workers=8) as pool:
            results = list(pool.map(lambda n: claim(path, n), range(8)))
        assert sum(ok for ok, _ in results) == 2, results
        assert len(snapshot(path)["reservations"]) == 2
        assert snapshot(path)["daily_limits"] == [(DAY, 2, 3200)]
        assert configure_day(path, DAY, capacity=1, quota_ceiling=1600) == (True, "configured")
        assert claim(path, 7) == (False, "daily_capacity")
        assert configure_day(path, DAY, capacity=2, quota_ceiling=1600) == (False, "cannot_raise_limits")
        set_stop(path, True)
        assert claim(path, 6) == (False, "emergency_stop")
        assert snapshot(path)["stopped"] is True
        assert snapshot(path)["public_enabled"] is False
    with TemporaryDirectory() as tmp:
        path = Path(tmp) / "quota.sqlite3"
        assert configure_day(path, DAY, capacity=20, quota_ceiling=1600) == (True, "configured")
        for n in range(2):
            register_rights(path, f"{n:064x}", f"license-{n}",
                            authorized=True, reviewer_reference="qa-reviewer")
        assert claim(path, 0) == (True, "reserved")
        assert claim(path, 1) == (False, "quota_budget")
    print("PASS persisted daily ceilings, concurrent claims, rights provenance, stop and quota")

if __name__ == "__main__":
    run()
