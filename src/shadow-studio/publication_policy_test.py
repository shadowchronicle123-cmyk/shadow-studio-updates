"""Offline tests for proposed, disabled public-publishing policy."""
from dataclasses import replace
from datetime import datetime, timedelta, timezone

from publication_policy import PublicationPolicy, eligible


def main():
    policy = PublicationPolicy()
    assert policy.daily_target == 20 and policy.daily_production_capacity == 20
    assert policy.clips_first and not policy.public_enabled
    now = datetime(2026, 11, 19, 12, tzinfo=timezone.utc)
    good = dict(activated=True, stopped=False, published_today=0,
                now=now, last_published_at=now - timedelta(hours=2),
                permission_verified=True, quality_passed=True, duplicate=False,
                private_upload_verified=True, channel_verified=True,
                quota_available=True, audit_ready=True)
    assert eligible(policy, **good) == (False, "public_publishing_disabled")
    enabled = replace(policy, public_enabled=True)
    assert eligible(enabled, **good) == (True, "eligible")
    for override, reason in [
        ({"activated": False}, "public_publishing_disabled"),
        ({"stopped": True}, "emergency_stop"),
        ({"published_today": 20}, "daily_limit"),
        ({"quota_available": False}, "quota_unavailable"),
        ({"duplicate": True}, "duplicate"),
        ({"permission_verified": False}, "permission_unverified"),
        ({"quality_passed": False}, "quality_unverified"),
        ({"private_upload_verified": False}, "private_upload_unverified"),
        ({"channel_verified": False}, "channel_unverified"),
        ({"audit_ready": False}, "audit_unavailable"),
        ({"last_published_at": now - timedelta(minutes=59)}, "minimum_interval"),
        ({"last_published_at": now + timedelta(minutes=1)}, "minimum_interval"),
    ]:
        assert eligible(enabled, **(good | override)) == (False, reason), reason
    assert eligible(enabled, **(good | {"published_today": 19}))[0]
    for bad_policy in [
        replace(enabled, daily_target=0),
        replace(enabled, daily_target=21),
        replace(enabled, daily_production_capacity=0),
        replace(enabled, daily_target=20, daily_production_capacity=10),
        replace(enabled, minimum_interval_minutes=-1),
    ]:
        assert eligible(bad_policy, **good) == (False, "invalid_policy_or_count")
    for bad_count in (-1, 1.5, True):
        assert eligible(enabled, **(good | {"published_today": bad_count})) == (
            False, "invalid_policy_or_count")
    print("PASS disabled-by-default 20/day clips-first publication policy and safety gates")


if __name__ == "__main__":
    main()
