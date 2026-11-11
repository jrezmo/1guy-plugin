"""Not part of the plugin build (see scripts/vendor_canvas.py STUB): an always-empty source."""
TTL = 3600.0
CFG_KEY = "unused"


def fetch(*_a, **_k):
    return {"blocks": [], "count": 0}


def media_mentions(*_a, **_k):
    return []


def inbox(*_a, **_k):
    return {"widget": "email", "wired": False, "unread": 0, "total": None, "items": []}
