"""Bind approval to exact bytes, not just a mutable review-status field."""
import hashlib
from pathlib import Path


def file_digest(path: Path) -> str:
    digest = hashlib.sha256()
    with Path(path).open('rb') as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b''):
            digest.update(chunk)
    return digest.hexdigest()


def approved_unchanged(item: dict, movie: Path) -> bool:
    """Fail closed for legacy approvals that were not tied to a checksum."""
    expected = item.get('approved_video_sha256')
    return (item.get('review_status') == 'approved'
            and isinstance(expected, str) and len(expected) == 64
            and Path(movie).is_file() and file_digest(movie) == expected)
