"""Clean, step-based console output.

The point of this module is to keep the terminal readable: every line printed
through here is something we deliberately chose to show (a step in the
WhatsApp authentication/listening flow, or a captured message) rather than
framework/library noise. See `logging_config.py` for how that noise is
silenced.
"""

from datetime import datetime

from Model.WhatsAppDataFetchingModel.whatsapp_message import WhatsAppChatMessage

_step_count = 0


def _timestamp() -> str:
    return datetime.now().strftime("%H:%M:%S")


def step(message: str) -> None:
    """Log a numbered step in a multi-step flow (e.g. connecting, pairing)."""
    global _step_count
    _step_count += 1
    print(f"[{_timestamp()}] STEP {_step_count}: {message}", flush=True)


def info(message: str) -> None:
    print(f"[{_timestamp()}] INFO  {message}", flush=True)


def success(message: str) -> None:
    print(f"[{_timestamp()}] DONE  {message}", flush=True)


def warn(message: str) -> None:
    print(f"[{_timestamp()}] WARN  {message}", flush=True)


def error(message: str) -> None:
    print(f"[{_timestamp()}] ERROR {message}", flush=True)


def prompt(message: str) -> None:
    """Log a line that is immediately followed by a terminal input() prompt."""
    print(f"[{_timestamp()}] INPUT NEEDED: {message}", flush=True)


def print_incoming_message(message: WhatsAppChatMessage) -> None:
    """Print a captured chat message (group or personal) in a readable,
    boxed format."""
    label = "GROUP" if message.chat_type == "group" else "PERSONAL"
    print("=" * 60, flush=True)
    print(f"Type      : {label}", flush=True)
    if message.chat_type == "group":
        print(f"Group     : {message.chat_name}", flush=True)
    print(f"Phone     : {message.sender_phone}", flush=True)
    print(f"Username  : {message.sender_name}", flush=True)
    print(f"Saved As  : {message.sender_saved_name}", flush=True)
    print(f"Time      : {message.received_at.strftime('%Y-%m-%d %H:%M:%S')} UTC", flush=True)
    print("-" * 60, flush=True)
    print(message.text, flush=True)
    print("=" * 60 + "\n", flush=True)
