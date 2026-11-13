"""
The video's own YouTube settings (the YouTube settings window: privacy, tags, category, made for kids, latency, DVR,
starting and stopping by itself) and its title and description, as kept and as checked on the way in. Part of the
stream's settings (choices.py).
"""
from __future__ import annotations

from .constants import CATEGORIES, LATENCIES, PRIVACIES, TAGS_MAX, YOUTUBE_DEFAULTS


def kept(raw) -> dict:
    """The video's YouTube settings as kept (anything missing or not one of the choices: its default)."""
    raw = raw if isinstance(raw, dict) else {}
    tags = [t.strip() for t in raw.get("tags") or [] if isinstance(t, str) and t.strip()] if isinstance(raw.get("tags"), list) else []
    return {
        "privacy": raw.get("privacy") if raw.get("privacy") in PRIVACIES else YOUTUBE_DEFAULTS["privacy"],
        "tags": list(dict.fromkeys(tags)),
        "category": raw.get("category") if raw.get("category") in CATEGORIES else YOUTUBE_DEFAULTS["category"],
        "made_for_kids": raw.get("made_for_kids") is True,
        "latency": raw.get("latency") if raw.get("latency") in LATENCIES else YOUTUBE_DEFAULTS["latency"],
        "dvr": raw.get("dvr") is not False,
        "auto_start": raw.get("auto_start") is not False,
        "auto_stop": raw.get("auto_stop") is not False,
    }


def checked(sent: dict, now: dict) -> dict:
    """The video's YouTube settings as sent (only what's sent changes), checked: ValueError says what's wrong."""
    if not isinstance(sent, dict):
        raise ValueError("The YouTube settings didn't come through.")
    out = dict(now)
    if sent.get("privacy") is not None:
        if sent["privacy"] not in PRIVACIES:
            raise ValueError("Choose public, unlisted or private.")
        out["privacy"] = sent["privacy"]
    if sent.get("latency") is not None:
        if sent["latency"] not in LATENCIES:
            raise ValueError("Choose normal, low or ultra-low latency.")
        out["latency"] = sent["latency"]
    if sent.get("category") is not None:
        if sent["category"] not in CATEGORIES:
            raise ValueError("Choose one of the categories listed.")
        out["category"] = sent["category"]
    if sent.get("tags") is not None:
        if not isinstance(sent["tags"], list) or not all(isinstance(t, str) for t in sent["tags"]):
            raise ValueError("The tags didn't come through.")
        tags = list(dict.fromkeys(t.strip() for t in sent["tags"] if t.strip()))
        if any(c in t for t in tags for c in "<>"):
            raise ValueError("YouTube doesn't allow < or > in a tag.")
        if len(",".join(tags)) > TAGS_MAX:
            raise ValueError(f"YouTube allows {TAGS_MAX} characters of tags; these come to {len(','.join(tags))}.")
        out["tags"] = tags
    for name in ("made_for_kids", "dvr", "auto_start", "auto_stop"):
        if sent.get(name) is not None:
            out[name] = bool(sent[name])
    return out


def text(text: str, most: int, what: str) -> str:
    text = text.strip()
    if len(text) > most:
        raise ValueError(f"YouTube allows a {what} of up to {most} characters; this one has {len(text)}.")
    if "<" in text or ">" in text:
        raise ValueError(f"YouTube doesn't allow < or > in a {what}.")
    return text
