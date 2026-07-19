"""One-command burner-cookie refresh: reads fresh Instagram session cookies
directly from your local Chrome profile (browser_cookie3), exports them in
Netscape format (the same format yt-dlp already expects), pushes them to
Render as the cookies.txt Secret File via Render's real API, and polls /health
afterward to confirm the restart picked them up.

LOCAL-ONLY. Never deployed to Render (render.yaml's buildCommand only installs
requirements.txt, not requirements-local.txt -- see that file), never called
by the live app. This replaces the manual "export via extension, paste in
dashboard" steps in COOKIES.md with a single command.

WHY THIS DOESN'T INCREASE INSTAGRAM-SIDE RISK: it never logs into Instagram,
authenticates, or does anything IG's bot-detection would notice. It only
READS cookies that already exist in your browser's cookie store because you
logged into the burner account normally, through a real browser, before
running this. The only thing automated here is the copy/paste step -- reading
a local file (the browser's cookie DB) and uploading it over HTTPS -- nothing
IG-facing at all. Same trust boundary as the manual process it replaces.

CANNOT BE FULLY TESTED: this fundamentally needs your real local Chrome
profile (with the burner account actually logged in) and real Render
credentials. The unit-testable pieces (Netscape export shape, the Render
request shape, the /health polling logic) have mocked tests in
tests/test_refresh_cookies.py; the actual end-to-end run against your browser
and your Render service has NOT been and cannot be verified from here.

Setup (once):
    pip install -r requirements-local.txt
    # .env: set RENDER_API_KEY (Render dashboard -> Account Settings -> API Keys)
    #       and RENDER_SERVICE_ID (Render dashboard -> your service -> Settings,
    #       or the "srv-..." in the service's URL) -- see .env.example.

Usage:
    python scripts/refresh_cookies.py
    python scripts/refresh_cookies.py --browser edge   # override the default (chrome)
    python scripts/refresh_cookies.py --dry-run        # write cookies.txt locally only,
                                                        # skip the Render push + health poll
"""
from __future__ import annotations

import argparse
import http.cookiejar
import os
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from dotenv import load_dotenv

load_dotenv()

RENDER_API_KEY = os.environ.get("RENDER_API_KEY", "").strip()
RENDER_SERVICE_ID = os.environ.get("RENDER_SERVICE_ID", "").strip()
REELBRAIN_URL = os.environ.get("REELBRAIN_URL", "").strip()

COOKIE_DOMAIN = "instagram.com"
DEFAULT_BROWSER = "chrome"
OUTPUT_PATH = Path(__file__).resolve().parent.parent / "cookies.txt"

RENDER_SECRET_FILE_NAME = "cookies.txt"

# Render's free-tier restart after a Secret File change isn't instant; poll a
# few times with real gaps rather than one immediate check that's guaranteed
# to see the old (pre-restart) state.
HEALTH_POLL_ATTEMPTS = 6
HEALTH_POLL_DELAY_SECONDS = 15.0


class RefreshError(RuntimeError):
    """Raised for any failure in this script's own logic (missing config, no
    cookies found, etc.) -- deliberately distinct from httpx's own exceptions
    so callers/tests can tell "we detected a problem" from "the network broke"."""


def _browser_reader(browser: str):
    import browser_cookie3

    readers = {"chrome": browser_cookie3.chrome, "edge": browser_cookie3.edge}
    try:
        return readers[browser]
    except KeyError:
        raise RefreshError(f"unsupported browser: {browser!r} (choices: {sorted(readers)})") from None


def export_netscape_cookies(browser: str, output_path: Path) -> int:
    """Reads instagram.com cookies from the given local browser's cookie store
    and writes them to output_path in Netscape format (the same format yt-dlp
    already expects via --cookies). Returns the number of cookies written.
    Raises RefreshError if no matching cookies are found -- almost always
    means you're not logged into the burner account in this browser/profile."""
    reader = _browser_reader(browser)
    source_jar = reader(domain_name=COOKIE_DOMAIN)

    jar = http.cookiejar.MozillaCookieJar(str(output_path))
    count = 0
    for cookie in source_jar:
        jar.set_cookie(cookie)
        count += 1

    if count == 0:
        raise RefreshError(
            f"no {COOKIE_DOMAIN} cookies found in {browser} -- are you logged into "
            "the burner account in this browser/profile?"
        )

    jar.save(ignore_discard=True, ignore_expires=True)
    return count


def push_to_render(cookies_text: str) -> None:
    """PUT the cookies.txt content to Render's Secret Files API
    (api-docs.render.com/reference/add-or-update-secret-file). Render restarts
    the service automatically on any Secret File change -- same effect as
    pasting the new contents into the dashboard by hand."""
    import httpx

    if not RENDER_API_KEY or not RENDER_SERVICE_ID:
        raise RefreshError(
            "RENDER_API_KEY and RENDER_SERVICE_ID must both be set in .env -- "
            "see .env.example and PROGRESS.md for where to get these."
        )

    response = httpx.put(
        f"https://api.render.com/v1/services/{RENDER_SERVICE_ID}/secret-files/{RENDER_SECRET_FILE_NAME}",
        headers={
            "Authorization": f"Bearer {RENDER_API_KEY}",
            "Content-Type": "application/json",
        },
        json={"content": cookies_text},
        timeout=30.0,
    )
    response.raise_for_status()


def poll_health_until_cookies_ok(
    attempts: int = HEALTH_POLL_ATTEMPTS, delay_seconds: float = HEALTH_POLL_DELAY_SECONDS
) -> bool:
    """Polls {REELBRAIN_URL}/health, waiting delay_seconds before each attempt
    (Render's restart takes real time), until cookies_file is true or the
    attempt budget runs out. Returns whether it ever saw cookies_file: true.
    Prints each attempt's result so a human watching stdout sees progress."""
    import httpx

    if not REELBRAIN_URL:
        print("REELBRAIN_URL not set in .env -- skipping post-push /health verification.")
        return False

    for attempt in range(1, attempts + 1):
        time.sleep(delay_seconds)
        try:
            response = httpx.get(f"{REELBRAIN_URL.rstrip('/')}/health", timeout=10.0)
            response.raise_for_status()
            body = response.json()
        except Exception as exc:  # noqa: BLE001 - polling a service that may still be restarting
            print(f"  [{attempt}/{attempts}] /health check failed: {exc}")
            continue
        print(
            f"  [{attempt}/{attempts}] cookies_file={body.get('cookies_file')} "
            f"cookie_health={body.get('cookie_health')}"
        )
        if body.get("cookies_file"):
            return True
    return False


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Refresh Render's burner cookies.txt from a local browser session."
    )
    parser.add_argument(
        "--browser", choices=["chrome", "edge"], default=DEFAULT_BROWSER,
        help=f"which local browser holds the burner IG session (default: {DEFAULT_BROWSER})",
    )
    parser.add_argument(
        "--dry-run", action="store_true",
        help="write cookies.txt locally and stop -- skip the Render push and health poll",
    )
    args = parser.parse_args()

    print(f"Reading {COOKIE_DOMAIN} cookies from local {args.browser}...")
    count = export_netscape_cookies(args.browser, OUTPUT_PATH)
    print(f"Wrote {count} cookies to {OUTPUT_PATH}")

    if args.dry_run:
        print("--dry-run: stopping before the Render push.")
        return

    print("Pushing to Render Secret File cookies.txt...")
    push_to_render(OUTPUT_PATH.read_text(encoding="utf-8"))
    print("Pushed. Render will auto-restart the service now.")

    print(f"Polling /health (up to {HEALTH_POLL_ATTEMPTS} tries, {HEALTH_POLL_DELAY_SECONDS:.0f}s apart)...")
    ok = poll_health_until_cookies_ok()
    if ok:
        print("SUCCESS: cookies.txt is live on Render and the service sees it.")
    else:
        print(
            "COULD NOT CONFIRM within the poll window. The push itself may still have "
            "worked -- check the Render dashboard and /health manually; a free-tier "
            "restart can occasionally take longer than this script waits."
        )


if __name__ == "__main__":
    try:
        main()
    except RefreshError as exc:
        sys.exit(f"refresh_cookies.py: {exc}")
