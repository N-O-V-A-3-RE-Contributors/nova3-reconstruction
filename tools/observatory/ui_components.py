"""Small, bounded curses layout primitives. No I/O, dependencies or ANSI escapes."""
from __future__ import annotations

import curses
from dataclasses import dataclass
import textwrap
import unicodedata


@dataclass(frozen=True)
class Rect:
    y: int
    x: int
    h: int
    w: int

    def inset(self, padding=1):
        return Rect(self.y + padding, self.x + padding,
                    max(0, self.h - 2 * padding), max(0, self.w - 2 * padding))


@dataclass(frozen=True)
class Layout:
    """One live breakpoint policy; both dimensions constrain dashboard density."""
    width: int
    height: int

    @property
    def density(self):
        for name, width, height in (('panoramic', 192, 54), ('spacious', 160, 45),
                                    ('medium', 120, 40), ('compact', 72, 18)):
            if self.width >= width and self.height >= height:
                return name
        return 'tiny'

    @property
    def compact(self):
        return self.density in ('tiny', 'compact')

    @property
    def content(self):
        return Rect(6, 1, max(0, self.height - 9), max(0, self.width - 2))

    @property
    def activity_columns(self):
        # The privacy panel needs less width than the full dashboard cards.
        return 2 if self.width >= 100 else 1

    @property
    def worker_columns(self):
        return 1 if self.compact else 2

    @property
    def owner_capacity(self):
        return 4 if not self.compact else 2 if self.content.h >= 20 else 1

    def cards(self, area, count):
        """Preserve the four-card summary, with space shared evenly."""
        columns = 2 if count > 1 else 1
        return grid(area, columns, (count + columns - 1) // columns)[:count]


def scroll_window(offset, count, capacity):
    """Clamp persisted scroll after resize, filtering or refreshed data."""
    capacity = max(1, capacity)
    start = max(0, min(offset, max(0, count - capacity)))
    return start, min(count, start + capacity)


def navigation(painter, y, entries, selected, width, accent='cyan'):
    """An unbroken current label, with a contiguous window of its neighbours.

    Reserve both edge markers before adding neighbours so labels never overlap.
    Shortcuts remain available even when their label is outside the window.
    """
    active = next(i for i, (key, _) in enumerate(entries) if key == selected)
    labels = [('[' + label + ']' if key == selected else ' ' + label + ' ')
              for key, label in entries]
    start, stop = active, active + 1
    used = cell_width(labels[active])
    budget = max(0, width - 4)
    while True:
        if start and used + cell_width(labels[start-1]) + 1 <= budget:
            start -= 1
            used += cell_width(labels[start]) + 1
        elif stop < len(labels) and used + cell_width(labels[stop]) + 1 <= budget:
            used += cell_width(labels[stop]) + 1
            stop += 1
        else:
            break
    painter.put(y, 1, '<' if start else '', 'muted', 1)
    x = 3
    for i in range(start, stop):
        painter.put(y, x, labels[i], accent if entries[i][0] == selected else 'muted',
                    max(0, min(cell_width(labels[i]), width - x - 2)))
        x += cell_width(labels[i]) + 1
    painter.put(y, width-2, '>' if stop < len(labels) else '', 'muted', 1)


def grid(area: Rect, columns=2, rows=2, gap=1) -> list[Rect]:
    """Row-major tiles, distributing all remaining cells without overlap."""
    columns, rows = max(1, columns), max(1, rows)
    gx = min(max(0, gap), max(0, area.w) // max(1, columns - 1))
    gy = min(max(0, gap), max(0, area.h) // max(1, rows - 1))
    width = max(0, area.w - gx * (columns - 1))
    height = max(0, area.h - gy * (rows - 1))
    widths = [width // columns + (i < width % columns) for i in range(columns)]
    heights = [height // rows + (i < height % rows) for i in range(rows)]
    tiles = []
    y = area.y
    for h in heights:
        x = area.x
        for w in widths:
            tiles.append(Rect(y, x, h, w))
            x += w + gx
        y += h + gy
    return tiles


ASCII = str.maketrans({'╭': '+', '╮': '+', '╰': '+', '╯': '+', '─': '-',
                      '━': '=', '│': '|', '█': '#', '░': '.', '◈': '*',
                      '•': '.', '·': '.', '↑': '^', '↓': 'v', '▶': '>',
                      '–': '-', '—': '-', '…': '~'})


def safe_text(value, unicode=True):
    text = ''.join(c if unicodedata.category(c)[0] != 'C' else ' ' for c in str(value))
    if not unicode:
        return text.translate(ASCII).encode('ascii', 'replace').decode('ascii')
    return text


def cell_width(text):
    return sum(0 if unicodedata.combining(c) else 2 if unicodedata.east_asian_width(c) in 'WF' else 1
               for c in text)


def truncate(text, width, unicode=True):
    """Fit display cells, not codepoints; keep combining marks attached."""
    text = safe_text(text, unicode)
    if cell_width(text) <= width:
        return text
    marker = '…' if unicode else '~'
    available = max(0, width - 1)
    result, used = '', 0
    for c in text:
        n = cell_width(c)
        if used + n > available:
            break
        result += c
        used += n
    return result + marker if width > 0 else ''


def palette():
    """Use a charcoal surface on 256-colour terminals; degrade to default/mono.

    Never address pairs/colors outside the terminal's reported capacity. No
    init_color: the user's terminal palette is never modified.
    """
    names = ('normal', 'muted', 'cyan', 'green', 'yellow', 'red', 'magenta', 'blue')
    result = dict.fromkeys(names, 0)
    result['bold'] = curses.A_BOLD
    result['cyan'] = curses.A_BOLD
    result['yellow'] = curses.A_BOLD
    try:
        if not curses.has_colors():
            return result
        curses.start_color()
        colors = getattr(curses, 'COLORS', 0)
        pairs = getattr(curses, 'COLOR_PAIRS', 0)
        bg = 234 if colors >= 256 else curses.COLOR_BLACK
        if colors < 256:
            try:
                curses.use_default_colors()
                bg = -1
            except curses.error:
                pass
        foregrounds = ((252, 245, 80, 78, 221, 203, 140, 110) if colors >= 256 else
                       (7, 7, 6, 2, 3, 1, 5, 4))
        for index, (name, fg) in enumerate(zip(names, foregrounds), 1):
            if index >= pairs or fg >= colors:
                continue
            try:
                curses.init_pair(index, fg, bg)
                result[name] = curses.color_pair(index)
            except curses.error:
                pass
    except curses.error:
        pass
    # Typography still conveys hierarchy without color.
    result['cyan'] |= curses.A_BOLD
    result['yellow'] |= curses.A_BOLD
    result['red'] |= curses.A_BOLD
    return result


class Card:
    """Border plus clipped inner lines. The painter owns curses and its palette."""
    def __init__(self, painter, area: Rect, title, badge='', severity='cyan'):
        self.painter, self.area = painter, area
        painter.frame(area.y, area.x, area.h, area.w, title, 'muted')
        badge_width = cell_width(safe_text(badge))
        if badge and area.w >= cell_width(safe_text(title)) + badge_width + 9:
            painter.put(area.y, area.x + area.w - badge_width - 4,
                        '[' + badge + ']', severity, badge_width + 2)
        self.inner = area.inset()

    def line(self, index, text, style='normal'):
        r = self.inner
        if 0 <= index < r.h:
            self.painter.put(r.y + index, r.x + 1, text, style, max(0, r.w - 2))

    def lines(self, rows, start=0):
        for index, row in enumerate(rows):
            text, style = row if isinstance(row, tuple) else (row, 'normal')
            self.line(start + index, text, style)

    def scrolled(self, rows, offset=0):
        """Wrap card prose and return the clamped offset for resize-safe scroll."""
        width = max(1, self.inner.w - 2)
        wrapped = []
        for row in rows:
            text, style = row if isinstance(row, tuple) else (row, 'normal')
            # Wrapping is only for prose; aligned tables use line() directly.
            for part in textwrap.wrap(safe_text(text), width) or ['']:
                # CJK/full-width characters may occupy two terminal cells.
                line, used = '', 0
                for c in part:
                    size = cell_width(c)
                    if used + size > width:
                        wrapped.append((line, style))
                        line, used = '', 0
                    line += c
                    used += size
                wrapped.append((line, style))
        start, stop = scroll_window(offset, len(wrapped), self.inner.h)
        self.lines(wrapped[start:stop])
        return start

    def meter(self, index, label, value, total, style='cyan'):
        """A task share, never a source-completion percentage."""
        self.line(index, self.meter_text(label, value, total), style)

    def meter_text(self, label, value, total):
        """Build a proportional meter that also works in a scrolled card."""
        width = max(0, self.inner.w - 4)
        if width < 30:
            return f'{label} {value}/{total}'
        bar_width = max(3, width - 17)
        filled = round(bar_width * min(1, max(0, value / total))) if total else 0
        return f'{label:<11} {value:>4} ' + '█' * filled + '░' * (bar_width - filled)
