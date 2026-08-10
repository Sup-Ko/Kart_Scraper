"""Rate-limited HTTP for public government endpoints.

These are taxpayer-funded services with published usage expectations. The
per-host throttle is deliberately more conservative than the documented caps —
being a well-behaved client is the price of continued access, and the SEC in
particular will block an IP that ignores it.

Set a real contact address in ``CONTACT`` (or the ``GOVDATA_CONTACT``
environment variable): the SEC requires a descriptive User-Agent identifying
who is making the request.
"""

from __future__ import annotations

import gzip
import json
import os
import time
import urllib.error
import urllib.parse
import urllib.request
import zlib

CONTACT = os.environ.get("GOVDATA_CONTACT", "govdata research (set GOVDATA_CONTACT)")

# Minimum seconds between requests per host.
RATE_LIMITS = {
    "www.sec.gov": 0.15,
    "disclosures-clerk.house.gov": 1.0,
    "api.usaspending.gov": 0.5,
    "www.defense.gov": 1.0,
}
DEFAULT_DELAY = 0.5

_last_hit: dict[str, float] = {}


class NotFound(Exception):
    """The endpoint has nothing for this request (404, or SEC's 403-for-absent)."""


def get(url: str, *, json_body: dict | None = None, timeout: int = 45,
        retries: int = 4) -> bytes:
    """Fetch a URL, honouring the per-host rate limit. POSTs if json_body given."""
    host = urllib.parse.urlparse(url).netloc
    delay = RATE_LIMITS.get(host, DEFAULT_DELAY)
    since = time.time() - _last_hit.get(host, 0.0)
    if since < delay:
        time.sleep(delay - since)

    headers = {"User-Agent": CONTACT, "Accept-Encoding": "gzip, deflate"}
    data = None
    if json_body is not None:
        data = json.dumps(json_body).encode()
        headers["Content-Type"] = "application/json"

    req = urllib.request.Request(url, data=data, headers=headers)

    last_exc: Exception | None = None
    for attempt in range(retries):
        try:
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                _last_hit[host] = time.time()
                raw = resp.read()
                return _decode(raw, resp.headers.get("Content-Encoding", ""))
        except urllib.error.HTTPError as exc:
            _last_hit[host] = time.time()
            if exc.code in (403, 404):
                # SEC serves 403 for Archives paths that don't exist, which is
                # what weekends and federal holidays look like.
                raise NotFound(f"{exc.code} for {url}") from exc
            if exc.code in (429, 503) and attempt < retries - 1:
                time.sleep(2 ** attempt * 2)
                last_exc = exc
                continue
            raise
        except (urllib.error.URLError, TimeoutError) as exc:
            last_exc = exc
            if attempt < retries - 1:
                time.sleep(2 ** attempt)
                continue
            raise
    raise RuntimeError(f"unreachable: {url}") from last_exc


def _decode(raw: bytes, encoding: str) -> bytes:
    """Handle both encodings we advertise in Accept-Encoding."""
    enc = (encoding or "").lower()
    if "gzip" in enc:
        return gzip.decompress(raw)
    if "deflate" in enc:
        try:
            return zlib.decompress(raw)
        except zlib.error:
            return zlib.decompress(raw, -zlib.MAX_WBITS)  # raw deflate
    return raw
