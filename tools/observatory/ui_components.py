"""Small, bounded curses layout primitives. No I/O, dependencies or ANSI escapes."""
from __future__ import annotations

import curses
from dataclasses import dataclass
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


def grid(area: Rect, columns=2, rows=2, gap=1) -> list[Rect]:
    """Row-major tiles, distributing all remaining cells without overlap."""
    width = max(0, area.w - gap * (columns - 1))
    height = max(0, area.h - gap * (rows - 1))
    widths = [width // columns + (i < width % columns) for i in range(columns)]
    heights = [height // rows + (i < height % rows) for i in range(rows)]
    tiles = []
    y = area.y
    for h in heights:
        x = area.x
        for w in widths:
            tiles.append(Rect(y, x, h, w))
            x += w + gap
        y += h + gap
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
        if badge and area.w >= len(title) + len(badge) + 9:
            painter.put(area.y, area.x + area.w - len(badge) - 4,
                        '[' + badge + ']', severity, len(badge) + 2)
        self.inner = area.inset()

    def line(self, index, text, style='normal'):
        r = self.inner
        if 0 <= index < r.h:
            self.painter.put(r.y + index, r.x + 1, text, style, max(0, r.w - 2))

    def lines(self, rows, start=0):
        for index, row in enumerate(rows):
            text, style = row if isinstance(row, tuple) else (row, 'normal')
            self.line(start + index, text, style)

    def meter(self, index, label, value, total, style='cyan'):
        """A task share, never a source-completion percentage."""
        width = max(0, self.inner.w - 4)
        if width < 30:
            self.line(index, f'{label} {value}/{total}', style)
            return
        bar_width = max(3, width - 17)
        filled = round(bar_width * min(1, max(0, value / total))) if total else 0
        self.line(index, f'{label:<11} {value:>4} ' + '█' * filled + '░' * (bar_width - filled), style)
