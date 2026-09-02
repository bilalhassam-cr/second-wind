#!/usr/bin/env python3
"""Cap the body of an exchange log without losing either end of the reply.

    truncate.py <infile> <outfile> [max_kb]

Writes the capped body to <outfile> and prints one JSON line describing the
original:

    {"reply_bytes": 7340032, "kept_bytes": 204800, "truncated": true}

`reply_bytes` is always the full size, so the ledger records what the worker
actually said rather than what survived the cap.

Why both ends. A long reply is worth keeping at the top, where the worker
states what it did, and at the bottom, where it says what it could not do or
where it failed. Cutting only the tail loses the second, which is the half that
matters when a delegation goes wrong. So the cap keeps the first 75% and the
last 25% of its budget and names the gap in between.

Byte counts, not characters: the cap is a disk-space limit. Slicing bytes can
land inside a UTF-8 sequence, so each cut is nudged to the nearest character
boundary before writing.
"""
import json
import os
import sys

DEFAULT_MAX_KB = 200
# Verbatim from the spec. The %d is the number of bytes dropped.
MARKER_TEXT = "[second-wind] %d bytes omitted here]"
# Blank lines around it so the marker reads as its own paragraph in the
# markdown exchange rather than running into the reply text.
MARKER = "\n\n" + MARKER_TEXT + "\n\n"
HEAD_SHARE = 0.75


def limit_bytes(max_kb):
    """Byte budget for the body. Anything that is not a positive number falls
    back to the default, because a cap of zero would throw the reply away."""
    try:
        value = int(max_kb)
    except (TypeError, ValueError):
        return DEFAULT_MAX_KB * 1024
    if value <= 0:
        return DEFAULT_MAX_KB * 1024
    return value * 1024


def _marker(omitted):
    return (MARKER % omitted).encode("utf-8")


def _trim_partial_tail(data):
    """Drop a UTF-8 sequence left incomplete by the cut at the end."""
    for back in range(0, min(4, len(data))):
        end = len(data) - back
        try:
            data[:end].decode("utf-8")
        except UnicodeDecodeError:
            continue
        return data[:end]
    return data


def _trim_partial_head(data):
    """Drop continuation bytes left stranded by the cut at the start."""
    for start in range(0, min(4, len(data))):
        try:
            data[start:].decode("utf-8")
        except UnicodeDecodeError:
            continue
        return data[start:]
    return data


def truncate(data, limit):
    """Return (body, truncated). `data` and the result are bytes."""
    if len(data) <= limit:
        return data, False
    # The marker has to fit inside the budget, and its own length depends on
    # the number it prints, so settle it by iteration. Three passes is more
    # than enough: only the digit count can change.
    keep = limit
    for _ in range(3):
        settled = max(0, limit - len(_marker(len(data) - keep)))
        if settled == keep:
            break
        keep = settled
    marker = _marker(len(data) - keep)
    head_len = int(keep * HEAD_SHARE)
    tail_len = keep - head_len
    head = _trim_partial_tail(data[:head_len])
    tail = _trim_partial_head(data[len(data) - tail_len:]) if tail_len else b""
    return head + marker + tail, True


def main(argv):
    if len(argv) < 3:
        sys.stderr.write("usage: truncate.py <infile> <outfile> [max_kb]\n")
        return 2
    infile, outfile = argv[1], argv[2]
    limit = limit_bytes(argv[3] if len(argv) > 3 else DEFAULT_MAX_KB)
    try:
        with open(infile, "rb") as handle:
            data = handle.read()
    except OSError as exc:
        sys.stderr.write("truncate.py: cannot read %s (%s)\n" % (infile, exc))
        return 1
    body, truncated = truncate(data, limit)
    try:
        # Same directory then rename, so a reader never sees half a body.
        temp = outfile + ".part"
        with open(temp, "wb") as handle:
            handle.write(body)
        os.chmod(temp, 0o600)
        os.replace(temp, outfile)
    except OSError as exc:
        sys.stderr.write("truncate.py: cannot write %s (%s)\n" % (outfile, exc))
        return 1
    print(json.dumps({"reply_bytes": len(data), "kept_bytes": len(body),
                      "truncated": truncated}))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
