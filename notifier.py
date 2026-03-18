"""Multi-channel user notification when a cart is secured."""

import logging
import sys
import time

log = logging.getLogger(__name__)


def notify_user(message: str, hold_minutes: int = 10) -> None:
    """
    Alert the user that a reservation cart has been held via:
      1. A loud console banner (always)
      2. A desktop OS notification (if plyer is installed)
      3. An audible terminal bell
    """
    banner = "=" * 62
    log.info(
        "\n%s\n  RESERVATION HELD — COMPLETE CHECKOUT NOW!\n\n%s\n\n"
        "  Cart held for ~%d minutes. Finish booking in the browser!\n%s",
        banner, message, hold_minutes, banner,
    )

    # Desktop notification (optional — install plyer for this)
    try:
        from plyer import notification  # type: ignore
        notification.notify(
            title="T0ckSn1per — Reservation Secured!",
            message=message,
            timeout=30,
        )
    except Exception:
        pass

    # Audible alert
    try:
        import winsound  # type: ignore  # Windows only
        for _ in range(5):
            winsound.Beep(1000, 400)
            time.sleep(0.1)
    except Exception:
        try:
            for _ in range(5):
                sys.stderr.write("\a")
                sys.stderr.flush()
                time.sleep(0.3)
        except Exception:
            pass
