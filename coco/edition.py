"""Channel policy hooks.

coco asks this module before meeting content or audio goes to a channel, and the settings page
lists only the channels it allows. This build allows every channel. A build prepared for an
organisation can replace the module to keep meeting content on the channels that organisation
has cleared; nothing else needs to change.
"""
from __future__ import annotations


def name() -> str:
    """Identifier of this build, reported to the settings page."""
    return "public"


def restricted() -> bool:
    """True when some channels are kept away from meeting content."""
    return False


def visible_presets(presets: dict) -> dict:
    """AI presets offered in Settings, the first-run guide and on the command line."""
    return presets


def blocked_reason(profile: dict) -> str:
    """Why meeting content may not go to this AI profile ("" = allowed)."""
    return ""


def asr_blocked_reason(channel: str, base_url: str = "") -> str:
    """Why meeting audio may not go to this transcription channel ("" = allowed)."""
    return ""


def subscription_allowed(preset: str) -> bool:
    """Whether the first-run guide offers this subscription (claude-cli / codex-cli)."""
    return True


def note(section: str) -> str:
    """One line shown at the top of a settings section ("ai" or "asr"); "" = none."""
    return ""
