#!/usr/bin/env python3
"""normalize.py: turn recorded terminal bytes into the text a person saw.

Usage:
  normalize.py [FILE]            read FILE (or stdin), write the normalized text to stdout

Every terminal driver runs this before redaction, gating and rendering. It:
  - removes ANSI escape sequences: CSI (colours, cursor moves, clears), OSC (titles,
    hyperlinks; ended by BEL or ESC \\), DCS/APC/PM/SOS strings, and two-character escapes
  - folds carriage-return overwrites and backspaces into the final visible content of the
    line, as a terminal would draw it (a progress line written ten times is one line)
  - applies the line-local CSI controls that change visible text: erase in line (K), cursor
    forward/back (C, D) and horizontal absolute (G)
  - expands tabs to the next 8-column stop and drops other control characters
  - strips trailing spaces from every line and ends the text with one newline (empty input
    gives empty output)
Screen clears and cursor addressing across lines are not interpreted: the text that was
written stays, in order, so nothing is lost.
Exit: 0 written, 2 unreadable input.
"""

import sys

sys.dont_write_bytecode = True

ESC = "\x1b"


def _skip_string(text, i):
    """Index after a string sequence that ends with BEL or ST (ESC \\), or the end of text."""
    n = len(text)
    while i < n:
        if text[i] == "\x07":
            return i + 1
        if text[i] == ESC and i + 1 < n and text[i + 1] == "\\":
            return i + 2
        i += 1
    return n


def normalize(data):
    text = data.decode("utf-8", errors="replace") if isinstance(data, (bytes, bytearray)) else data
    lines = [[]]
    col = 0
    i, n = 0, len(text)

    def put(ch):
        nonlocal col
        line = lines[-1]
        while len(line) < col:
            line.append(" ")
        if col < len(line):
            line[col] = ch
        else:
            line.append(ch)
        col += 1

    while i < n:
        ch = text[i]
        if ch == ESC:
            if i + 1 >= n:
                break
            nxt = text[i + 1]
            if nxt == "[":
                j = i + 2
                while j < n and not ("@" <= text[j] <= "~"):
                    j += 1
                if j >= n:
                    break
                params, final = text[i + 2:j], text[j]
                nums = [p for p in params.lstrip("?>=!").split(";")]
                first = int(nums[0]) if nums and nums[0].isdigit() else None
                line = lines[-1]
                if final == "K":
                    mode = first or 0
                    if mode == 0:
                        del line[col:]
                    elif mode == 1:
                        for k in range(min(col + 1, len(line))):
                            line[k] = " "
                    else:
                        line.clear()
                elif final == "C":
                    col += first or 1
                elif final == "D":
                    col = max(0, col - (first or 1))
                elif final == "G":
                    col = max(0, (first or 1) - 1)
                i = j + 1
            elif nxt in "]PX^_":
                i = _skip_string(text, i + 2)
            else:
                i += 2 + (1 if nxt in "()*+#%" and i + 2 < n else 0)
            continue
        if ch == "\n":
            lines.append([])
            col = 0
        elif ch == "\r":
            col = 0
        elif ch == "\b":
            col = max(0, col - 1)
        elif ch == "\t":
            target = (col // 8 + 1) * 8
            while col < target:
                put(" ")
        elif ch >= " " and ch != "\x7f":
            put(ch)
        i += 1
    out = "\n".join("".join(line).rstrip(" ") for line in lines)
    out = out.rstrip("\n")
    return out + "\n" if out else ""


def main(argv=None):
    argv = sys.argv[1:] if argv is None else argv
    if len(argv) > 1 or (argv and argv[0] in ("-h", "--help")):
        print(__doc__)
        return 2 if len(argv) > 1 else 0
    try:
        data = open(argv[0], "rb").read() if argv else sys.stdin.buffer.read()
    except OSError as exc:
        print(f"normalize.py: cannot read input: {exc.strerror}", file=sys.stderr)
        return 2
    sys.stdout.write(normalize(data))
    return 0


if __name__ == "__main__":
    sys.exit(main())
