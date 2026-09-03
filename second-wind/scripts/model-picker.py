#!/usr/bin/env python3
"""Put the account readings into the primary profile's model picker.

Claude Code can show a description under each model in the picker. When
`refresh.model_picker` is on, second-wind writes one row per alias with the
current usage in the description, so the figures are visible at the moment a
model is chosen. When it is off, the key is removed again, and only ever the
key second-wind marked as its own.

Nothing else in settings.json is touched: the file is read, one key is changed,
and it is written back through a temp file in the same directory. The first
edit takes a backup.

  model-picker.py [--off] [--print]
"""
import argparse
import json
import os
import re
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import swlib  # noqa: E402

ALIASES = ("fable", "opus", "sonnet", "haiku")
LIMIT = 90
# The brief's own wording, cut to something that fits a picker row.
PHRASES = (("login expired", "login expired"),
           ("trust prompt", "trust prompt"),
           ("labels moved", "parser mismatch"),
           ("no usage reading", "no reading"),
           ("no current reading", "no reading"))


def short_phrase(rest):
    """A picker row has no space for a sentence."""
    head = rest.split(",")[0].strip()
    for needle, word in PHRASES:
        if needle in rest:
            return word
    return head[:20]


def compact(lines):
    """Squeeze the session brief into one line, at most LIMIT characters.

    A brief line reads `Work Claude: 5h 2%, 7d 16% (3m ago)`. The picker
    has room for the account and its figures, nothing else, so this keeps the
    first word of the label and the percentages.
    """
    parts = []
    for line in lines:
        label, _, rest = line.partition(": ")
        words = label.split()
        name = words[0] if words else label
        name = name[:1].upper() + name[1:]
        windows = re.findall(r"\b(5h|7d|included|auto|api)\s+(\d+)%", rest)
        body = " · ".join("%s %s%%" % pair for pair in windows) \
            if windows else short_phrase(rest)
        parts.append(("%s %s" % (name, body)).strip())
    text = " | ".join(parts)
    if len(text) <= LIMIT:
        return text
    kept = []
    for part in parts:
        if len(" | ".join(kept + [part])) > LIMIT:
            break
        kept.append(part)
    return " | ".join(kept) if kept else text[:LIMIT - 1] + "…"


def describe(cfg=None):
    return compact(swlib.brief_lines(cfg))


def picker_block(description):
    return {
        "_second_wind": True,
        "replaceBuiltInOptions": True,
        "options": [{"value": alias, "label": alias.capitalize(),
                     "description": description} for alias in ALIASES],
    }


def read_settings(path):
    """The settings file as a dict, or None when it is there but not JSON. A
    file we cannot parse is never overwritten."""
    if not os.path.exists(path):
        return {}
    try:
        with open(path) as handle:
            data = json.load(handle)
    except Exception:
        return None
    return data if isinstance(data, dict) else None


def apply(cfg=None, on=None):
    """Write or remove our picker key. Returns a line saying what happened."""
    cfg = swlib.load_config() if cfg is None else cfg
    if not cfg:
        return "second-wind is not set up, so nothing was written."
    config_dir = (cfg.get("primary") or {}).get("config_dir")
    if not config_dir:
        return "no primary profile is configured, so nothing was written."
    path = os.path.join(swlib.expand(config_dir), "settings.json")
    data = read_settings(path)
    if data is None:
        return "%s is not valid JSON, so it was left alone." % swlib.tilde(path)
    if on is None:
        on = (cfg.get("refresh") or {}).get("model_picker") is True

    current = data.get("modelPicker")
    ours = isinstance(current, dict) and current.get("_second_wind") is True
    if not on:
        if not ours:
            return "the model picker is off and nothing of ours was there."
        swlib.backup_once(path)
        data.pop("modelPicker")
        swlib.write_json_atomic(path, data, mode=0o600)
        return "removed the model picker from %s." % swlib.tilde(path)

    block = picker_block(describe(cfg))
    if current == block:
        return "the model picker already says this."
    swlib.backup_once(path)
    data["modelPicker"] = block
    swlib.write_json_atomic(path, data, mode=0o600)
    return "model picker: %s" % block["options"][0]["description"]


def main():
    os.umask(0o077)
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--off", action="store_true",
                    help="remove our key whatever the config says")
    ap.add_argument("--print", dest="show", action="store_true",
                    help="print the description and write nothing")
    a = ap.parse_args()
    if a.show:
        print(describe())
        return 0
    print(apply(on=False if a.off else None))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
