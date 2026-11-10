"""What every OCR engine returns, and the row reconstruction they all share."""
import re
from dataclasses import dataclass, field


@dataclass
class Line:
    text: str
    box: tuple  # x0, y0, x1, y1 in page pixels
    kind: str = "text"  # layout label when the engine has one


@dataclass
class Reading:
    engine: str
    device: str
    lines: list = field(default_factory=list)          # Line objects, any order
    seconds_per_page: list = field(default_factory=list)

    def rows(self):
        return merge_rows(self.lines)


def split_block(text, box, kind="text"):
    """A multi-line block -> one Line per text line, height shared evenly."""
    parts = [p.strip() for p in text.splitlines() if p.strip()]
    if not parts:
        return []
    x0, y0, x1, y1 = box
    h = (y1 - y0) / len(parts)
    return [Line(p, (x0, y0 + k * h, x1, y0 + (k + 1) * h), kind) for k, p in enumerate(parts)]


def merge_rows(lines, max_shift=0.4):
    """Group lines that sit on the same horizontal band into one row, left to right.

    A label and its amount are often read as two separate boxes (or two layout
    regions); joined with a double space they become "Total HT  1 234,56 €" again.
    A line joins the row whose centre is closest, if within `max_shift` x its height:
    centre distance, not mere overlap, so that two columns whose lines are offset by
    half a line (an address beside a date block) are not zipped together.
    """
    rows = []  # each: [centre y, height, [lines]]
    for ln in sorted(lines, key=lambda l: (l.box[1] + l.box[3]) / 2):
        cy, h = (ln.box[1] + ln.box[3]) / 2, max(1.0, ln.box[3] - ln.box[1])
        best = None
        for row in rows:
            d = abs(cy - row[0])
            if d <= max_shift * min(h, row[1]) and (best is None or d < best[0]):
                best = (d, row)
        if best:
            best[1][2].append(ln)
        else:
            rows.append([cy, h, [ln]])
    rows.sort(key=lambda r: r[0])
    out = []
    for _, _, items in rows:
        items.sort(key=lambda l: l.box[0])
        out.append("  ".join(re.sub(r"\s+", " ", l.text).strip() for l in items))
    return out
