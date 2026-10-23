"""Work out the real visitor IP address for rate limiting.

On Render the API sits behind proxies, so ``request.client.host`` is the
proxy's address, not the visitor's. Treating that as the visitor made every
user share ONE rate-limit bucket (for example only a handful of sign-ups per
hour for the whole site).

``X-Forwarded-For`` is a comma-separated list. Each proxy APPENDS the address
it received the request from, but a client can also pre-fill the header with
fake values at the front. So only entries added by OUR proxies are trusted:
we count ``TRUSTED_PROXY_COUNT`` entries from the right-hand end.
"""

import ipaddress

from fastapi import Request

from app.config import settings


def _valid_ip(value: str) -> str | None:
    try:
        return str(ipaddress.ip_address(value.strip()))
    except ValueError:
        return None


def client_ip(request: Request) -> str:
    peer = request.client.host if request.client else "unknown"
    count = settings.TRUSTED_PROXY_COUNT
    if count <= 0:
        return peer

    header = request.headers.get("x-forwarded-for", "")
    parts = [part.strip() for part in header.split(",") if part.strip()]
    if not parts:
        return peer

    # With N trusted proxies the visitor is the Nth entry from the right. If
    # the list is shorter than N, fewer proxies were involved and the first
    # entry is the visitor.
    candidate = parts[-count] if len(parts) >= count else parts[0]
    return _valid_ip(candidate) or peer
