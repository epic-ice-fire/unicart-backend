from __future__ import annotations

import asyncio
import logging
from collections.abc import Callable
from typing import Any

logger = logging.getLogger("unicart.notifications")


def notification_emails_for_user(user: Any) -> list[str]:
    """Return deduplicated transactional addresses for a user.

    The primary UniCart account email always receives operational notifications.
    The PAU address is added only after PAU verification has succeeded. This
    deliberately does not change where the PAU verification code itself goes.
    """
    candidates = [getattr(user, "email", None)]
    if bool(getattr(user, "is_student_verified", False)):
        candidates.append(getattr(user, "student_pau_email", None))

    result: list[str] = []
    seen: set[str] = set()
    for candidate in candidates:
        address = str(candidate or "").strip()
        if not address:
            continue
        key = address.casefold()
        if key in seen:
            continue
        seen.add(key)
        result.append(address)
    return result


async def send_to_user_addresses(
    user: Any,
    sender: Callable[..., bool],
    **kwargs: Any,
) -> dict[str, bool]:
    """Best-effort delivery to every eligible address for one verified user."""
    results: dict[str, bool] = {}
    for address in notification_emails_for_user(user):
        try:
            results[address] = bool(
                await asyncio.to_thread(sender, user_email=address, **kwargs)
            )
        except Exception:
            logger.exception("Transactional notification sender raised unexpectedly.")
            results[address] = False
    return results
