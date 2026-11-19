"""Offline deployment checks. Never authorizes, uploads or uses paid services."""
from pathlib import Path
import re
import tomllib

ROOT = Path(__file__).resolve().parents[1]


def check():
    config = tomllib.loads((ROOT / "netlify.toml").read_text(encoding="utf-8"))
    assert config["build"]["publish"] == "netlify-site", "Unexpected public directory"
    assert "command" not in config["build"], "Static landing should have no build command"
    redirects = config["redirects"]
    assert any(
        rule["from"] == "/oauth/*"
        and rule["to"] == "/oauth-unavailable.html"
        and rule["status"] in (302, 303, 307, 308)
        and rule.get("force") is True
        for rule in redirects
    ), "OAuth routes must not silently pretend to be functional"
    for page in ("index.html", "oauth-unavailable.html"):
        html = (ROOT / "netlify-site" / page).read_text(encoding="utf-8")
        assert "<html" in html and "</html>" in html, f"Malformed {page}"
        assert not re.search(r"(?:refresh_token|client_secret|access_token)\s*[:=]\s*['\"][^'\"]+", html, re.I), "Potential embedded credential"
    workflow = (ROOT / ".github/workflows" / "cloud-automation-preflight.yml").read_text(encoding="utf-8")
    assert "workflow_dispatch:" in workflow
    assert "schedule:" not in workflow, "Do not activate unattended cloud jobs yet"
    assert "private_upload.py" not in workflow, "Preflight must never upload"
    assert "contents: read" in workflow, "Keep GitHub token read-only"
    print("PASS static Netlify deployment, fail-closed OAuth routes and read-only manual cloud preflight")


if __name__ == "__main__":
    check()
