"""Offline SQLite ledger tests: concurrency, quota, duplicate, stop, audit."""
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from tempfile import TemporaryDirectory
from batch_ledger import reserve, set_stop, snapshot, register_rights

DAY = "2026-11-20"

def claim(path, n, **kw):
    return reserve(path, day=DAY, fingerprint=f"{n:064x}", kind="clip",
                   permission_verified=True, quality_passed=True,
                   quota_limit=32000, **kw)

def run():
    with TemporaryDirectory() as tmp:
        path = Path(tmp) / "state" / "ledger.sqlite3"
        assert claim(path, 1) == (False, "rights_unverified")
        for n in (1,2,3):
            register_rights(path, f"{n:064x}", f"review-{n}", authorized=True)
        assert claim(path, 1) == (True, "reserved")
        assert claim(path, 1) == (False, "duplicate")
        assert claim(path, 2, quota_units=32000) == (False, "quota_budget")
        assert reserve(path, day=DAY, fingerprint=f"{2:064x}", kind="clip",
                       permission_verified=False, quality_passed=True,
                       quota_limit=32000) == (False, "rights_unverified")
        assert reserve(path, day=DAY, fingerprint=f"{2:064x}", kind="clip",
                       permission_verified=True, quality_passed=False,
                       quota_limit=32000) == (False, "quality_unverified")
        set_stop(path, True)
        assert claim(path, 3) == (False, "emergency_stop")
        assert snapshot(path)["stopped"]
        set_stop(path, False)
        assert claim(path, 3) == (True, "reserved")
        assert snapshot(path)["public_enabled"] is False
        assert snapshot(path)["audit"][0] == ("rejected", "rights_unverified")
        assert ("emergency_stop", "") in snapshot(path)["audit"]
    with TemporaryDirectory() as tmp:
        path = Path(tmp) / "ledger.sqlite3"
        for n in range(30):
            register_rights(path, f"{n:064x}", f"review-{n}", authorized=True)
        with ThreadPoolExecutor(max_workers=8) as pool:
            results = list(pool.map(lambda n: claim(path, n), range(30)))
        assert sum(ok for ok, _ in results) == 20, results
        assert len(snapshot(path)["reservations"]) == 20
        assert sum(q for _,_,_,q in snapshot(path)["reservations"]) == 32000
        assert all(reason in ("reserved","daily_capacity") for _,reason in results)
    with TemporaryDirectory() as tmp:
        path = Path(tmp) / "ledger.sqlite3"
        register_rights(path, f"{42:064x}", "review-42", authorized=True)
        with ThreadPoolExecutor(max_workers=8) as pool:
            results = list(pool.map(lambda _: claim(path, 42), range(12)))
        assert sum(ok for ok, _ in results) == 1
        assert len(snapshot(path)["reservations"]) == 1
    with TemporaryDirectory() as tmp:
        path = Path(tmp) / "ledger.sqlite3"
        register_rights(path, f"{5:064x}", "review-5", authorized=False)
        assert claim(path, 5) == (False, "rights_unverified")
        for bad_day in ("2026-02-30", "2026-11-20x", "bad"):
            assert reserve(path, day=bad_day, fingerprint=f"{6:064x}", kind="clip",
                           permission_verified=True, quality_passed=True,
                           quota_limit=32000)[1] == "invalid_or_unverified_configuration"
    print("PASS offline atomic 20/day ledger, quota, rights, stop, duplicates, concurrency and audit")

if __name__ == "__main__":
    run()
