"""Offline quota reconciliation regression tests."""
from pathlib import Path
from tempfile import TemporaryDirectory
from batch_ledger import register_rights, reserve, snapshot
from quota_reconciliation import reconcile

DAY = "2026-11-20"

def claim(path, n, limit=4000):
    return reserve(path, day=DAY, fingerprint=f"{n:064x}", kind="clip",
                   permission_verified=True, quality_passed=True,
                   quota_limit=limit, quota_units=1600)

def run():
    with TemporaryDirectory() as tmp:
        path = Path(tmp) / "quota.sqlite3"
        for n in range(4):
            register_rights(path, f"{n:064x}", f"permission-{n}",
                            authorized=True, reviewer_reference="offline-qa")
        assert claim(path, 0) == (True, "reserved")
        assert reconcile(path, f"{0:064x}", outcome="uncertain") == (True, "reconciled")
        assert claim(path, 0) == (False, "duplicate")
        assert reconcile(path, f"{0:064x}", outcome="confirmed", actual_units=3000) == (True, "reconciled")
        assert claim(path, 1) == (False, "quota_budget")
        assert reconcile(path, f"{0:064x}", outcome="confirmed", actual_units=100) == (False, "cannot_reduce_observed_quota")
        assert reconcile(path, f"{0:064x}", outcome="uncertain") == (False, "cannot_downgrade_confirmed")
        assert snapshot(path)["quota_reconciliation"][0][1:] == (1400,3000,"confirmed")
        assert reconcile(path, f"{2:064x}", outcome="confirmed", actual_units=100) == (False, "unknown_reservation")
        assert reconcile(path, f"{0:064x}", outcome="confirmed", actual_units=-1) == (False, "invalid_reconciliation")
        assert claim(path, 2, limit=4600) == (True, "reserved")
        assert claim(path, 3, limit=4600) == (False, "quota_budget")
        assert snapshot(path)["public_enabled"] is False
    print("PASS conservative offline quota reconciliation, uncertain state, overage and audit")

if __name__ == "__main__":
    run()
