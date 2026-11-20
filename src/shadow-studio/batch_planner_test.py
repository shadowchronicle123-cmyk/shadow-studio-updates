"""No-network batch planner safety tests."""
from dataclasses import replace
from batch_planner import Candidate, plan_batch, fingerprint

base = Candidate("https://example.org/licensed/stream", 1000, 11000,
                 "signed-permission-123", "edit and repost", "a"*64, .95)
def run():
    clip2 = replace(base, start_ms=12000, end_ms=22000, highlight_score=.80)
    story = replace(base, source_url="https://example.org/story", kind="story", highlight_score=1)
    selected, rejected = plan_batch([story, clip2, base], quota_remaining=4800)
    assert selected == [base, clip2, story] and not rejected
    assert plan_batch([base, base], quota_remaining=3200)[1][0][1] == "duplicate"
    assert plan_batch([base], existing_fingerprints={fingerprint(base)}, quota_remaining=3200)[1][0][1] == "duplicate"
    assert plan_batch([base], stopped=True, quota_remaining=3200)[1] == [("batch", "emergency_stop")]
    assert plan_batch([base])[1] == [("batch", "quota_unverified")]
    assert plan_batch([base], quota_remaining=1599)[1][0][1] == "quota_budget"
    assert plan_batch([base], already_reserved=20, quota_remaining=3200)[1][0][1] == "daily_capacity"
    for bad in [replace(base, permission_reference=""),
                replace(base, license_scope="view only"),
                replace(base, source_url="http://example.org/video"),
                replace(base, end_ms=1000),
                replace(base, highlight_score=float("nan")),
                replace(base, source_sha256="wrong")]:
        assert plan_batch([bad], quota_remaining=3200)[1][0][1] == "rights_or_metadata_unverified"
    for kwargs in [{"daily_capacity":21},{"already_reserved":-1},{"quota_cost_per_upload":0}]:
        try:
            plan_batch([base], quota_remaining=3200, **kwargs)
        except ValueError:
            pass
        else:
            raise AssertionError(f"Unsafe configuration accepted: {kwargs}")
    print("PASS offline clips-first batch planning, rights, duplicates, quota, stop, capacity")

if __name__ == "__main__":
    run()
