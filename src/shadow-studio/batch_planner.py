"""Offline clips-first batch planning. No upload, network, or publisher integration."""
from dataclasses import dataclass
import hashlib
from urllib.parse import urlparse


@dataclass(frozen=True)
class Candidate:
    source_url: str
    start_ms: int
    end_ms: int
    permission_reference: str
    license_scope: str
    source_sha256: str
    highlight_score: float
    kind: str = "clip"


def fingerprint(item):
    canonical = f"{item.source_url}|{item.start_ms}|{item.end_ms}|{item.source_sha256}"
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def plan_batch(candidates, *, daily_capacity=20, already_reserved=0,
               stopped=False, quota_remaining=None, quota_cost_per_upload=1600,
               existing_fingerprints=()):
    """Return approved candidates and rejection reasons; no side effects.

    Quota is a conservative *budget input* supplied by a future trusted ledger;
    this function does not claim to know the platform's actual API costs.
    """
    if type(daily_capacity) is not int or not 1 <= daily_capacity <= 20:
        raise ValueError("Invalid daily capacity")
    if type(already_reserved) is not int or already_reserved < 0:
        raise ValueError("Invalid reservation count")
    if type(quota_cost_per_upload) is not int or quota_cost_per_upload <= 0:
        raise ValueError("Invalid quota cost")
    if quota_remaining is None or type(quota_remaining) is not int or quota_remaining < 0:
        return [], [("batch", "quota_unverified")]
    if stopped:
        return [], [("batch", "emergency_stop")]
    selected, rejected = [], []
    seen = set(existing_fingerprints)
    remaining = max(0, daily_capacity - already_reserved)
    budget = quota_remaining // quota_cost_per_upload
    def rank(item):
        try:
            score = float(item.highlight_score)
            if score != score or score in (float("inf"), float("-inf")):
                return -1.0
            return score
        except (TypeError, ValueError, OverflowError):
            return -1.0
    for item in sorted(candidates, key=lambda x: (x.kind != "clip", -rank(x))):
        try:
            url = urlparse(item.source_url)
            authorized = (url.scheme == "https" and bool(url.hostname)
                          and bool(item.permission_reference.strip())
                          and "repost" in item.license_scope.lower()
                          and "edit" in item.license_scope.lower())
            valid = (type(item.start_ms) is int and type(item.end_ms) is int
                     and 0 <= item.start_ms < item.end_ms
                     and len(item.source_sha256) == 64
                     and all(c in "0123456789abcdef" for c in item.source_sha256.lower())
                     and 0 <= rank(item) <= 1)
        except (AttributeError, TypeError, ValueError):
            authorized, valid = False, False
        key = fingerprint(item)
        if not authorized or not valid:
            rejected.append((key, "rights_or_metadata_unverified"))
        elif key in seen:
            rejected.append((key, "duplicate"))
        elif len(selected) >= remaining:
            rejected.append((key, "daily_capacity"))
        elif len(selected) >= budget:
            rejected.append((key, "quota_budget"))
        else:
            selected.append(item)
            seen.add(key)
    return selected, rejected
