"""Silences third-party log noise so the terminal only shows our own
step-based output (Middleware/step_logger.py) plus Uvicorn's own
startup/shutdown lines.

`neonize` configures the Python root logger (level=INFO, with its own
handler) as an import side effect, and forwards every internal WhatsApp
protocol event (whatsmeow.*) through it — including at WARNING/ERROR
severity for things that are purely internal protocol chatter (app-state
key sync, duplicate contact dedup, etc.) and not actionable from this
codebase. WARNING-level suppression only hides INFO/DEBUG, so those still
leaked through; CRITICAL is what actually silences them. We import neonize
once here (so its logging.basicConfig side effect happens) and then
override the levels it set.
"""

import logging


def configure_logging() -> None:
    import neonize.utils  # noqa: F401  (triggers neonize's logging.basicConfig)

    # whatsmeow logs many component-specific loggers dynamically
    # (whatsmeow.Client, whatsmeow.Store, ...) that never set their own
    # level, so they inherit whatever the root logger is set to. Raising
    # root itself to CRITICAL silences all of them in one place, not just
    # the two names below (kept explicit too, for clarity/defense-in-depth).
    logging.getLogger().setLevel(logging.CRITICAL)
    logging.getLogger("whatsmeow.Client").setLevel(logging.CRITICAL)
    logging.getLogger("Whatsmeow.Database").setLevel(logging.CRITICAL)
    logging.getLogger("neonize.utils.log").setLevel(logging.CRITICAL)

    # Per-request access logs would otherwise print for every poll from a
    # future frontend; Uvicorn's own startup/shutdown lines (uvicorn.error)
    # are left untouched since those double as useful step confirmations.
    logging.getLogger("uvicorn.access").setLevel(logging.WARNING)
