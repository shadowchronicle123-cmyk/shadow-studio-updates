"""Proposed unattended-publication policy. Planning only: NO upload or network actions.

This module is intentionally not wired to any publisher. Public publishing remains
disabled until separate approval and verified end-to-end rollout.
"""
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone


@dataclass(frozen=True)
class PublicationPolicy:
    daily_target: int = 20
    daily_production_capacity: int = 20
    clips_first: bool = True
    public_enabled: bool = False
    minimum_interval_minutes: int = 60
    require_permission: bool = True
    require_quality_pass: bool = True
    require_private_upload_verified: bool = True
    require_audit_log: bool = True
    require_channel_verified: bool = True
    require_explicit_activation: bool = True


def eligible(policy, *, activated=False, stopped=False, published_today=0,
             last_published_at=None, now=None, permission_verified=False,
             quality_passed=False, duplicate=False, private_upload_verified=False,
             channel_verified=False, quota_available=False, audit_ready=False):
    """Fail closed; return (allowed, reason). No side effects or publishing."""
    if not policy.public_enabled or not activated:
        return False, "public_publishing_disabled"
    if (type(policy.daily_target) is not int or type(policy.daily_production_capacity) is not int
            or not 1 <= policy.daily_target <= 20
            or not 1 <= policy.daily_production_capacity <= 20
            or policy.daily_target > policy.daily_production_capacity
            or type(policy.minimum_interval_minutes) is not int
            or policy.minimum_interval_minutes < 0
            or type(published_today) is not int or published_today < 0):
        return False, "invalid_policy_or_count"
    if stopped:
        return False, "emergency_stop"
    if published_today >= policy.daily_target or published_today >= policy.daily_production_capacity:
        return False, "daily_limit"
    if not quota_available:
        return False, "quota_unavailable"
    if duplicate:
        return False, "duplicate"
    if policy.require_permission and not permission_verified:
        return False, "permission_unverified"
    if policy.require_quality_pass and not quality_passed:
        return False, "quality_unverified"
    if policy.require_private_upload_verified and not private_upload_verified:
        return False, "private_upload_unverified"
    if policy.require_channel_verified and not channel_verified:
        return False, "channel_unverified"
    if policy.require_audit_log and not audit_ready:
        return False, "audit_unavailable"
    if last_published_at is not None:
        if now is None or last_published_at.tzinfo is None or now.tzinfo is None:
            return False, "time_unverified"
        if now - last_published_at < timedelta(minutes=policy.minimum_interval_minutes):
            return False, "minimum_interval"
    return True, "eligible"
