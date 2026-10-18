"""Pack images, titles and texts on a page and get it back as HTML.

Usage from another file:

    from page_organizer import build_page

    html = build_page(["a.jpg", "b.png"], ["A title"], ["some text", "more text"],
                      page_width=600, page_height=850, seed=1, out_path="out/page.html")
"""

from __future__ import annotations  # lets packer() name Grid before the class is defined

import math
import os
import random
from html import escape
from pathlib import Path
from urllib.parse import quote

from PIL import Image, ImageDraw
from PIL import ImageFont
from rectpack import newPacker, GuillotineBssfSas, SORT_NONE

FONT_DIR = Path(__file__).parent / "fonts"  # works whatever folder you run the script from


# packer function, takes boxes (anything with .width and .height, like PageImage and Title)
# and puts them neatly in grid, in the order given (SORT_NONE: rectpack would otherwise
# re-sort them biggest first). Sets .x / .y on the boxes that fit, returns how many fit.
def packer(grid: Grid, boxes: list, page_width: int, page_height: int, gutter=0) -> int:
    packer = newPacker(pack_algo=GuillotineBssfSas, sort_algo=SORT_NONE, rotation=False)
    packer.add_bin(page_width, page_height)
    for i, box in enumerate(boxes):
        box.x = box.y = None  # forget positions from an earlier packing
        packer.add_rect(box.width + gutter, box.height + gutter, rid=i)
    packer.pack()
    placed = 0
    for _, x, y, width, height, i in packer.rect_list():
        grid.occupy(x, y, width, height)
        boxes[i].x, boxes[i].y = x, y
        placed += 1
    return placed


# Simple Grid class, for now it works in pixels
class Grid:
    def __init__(self, columns, rows):
        self.columns = columns
        self.rows = rows
        self.taken = [[False] * columns for _ in range(rows)]

    def occupy(self, x, y, width, height):
        for row in range(y, y + height):
            for column in range(x, x + width):
                self.taken[row][column] = True

    def is_free(self, x, y, width, height):
        # outside the grid counts as not free (negative indexes would wrap around silently)
        if x < 0 or y < 0 or x + width > self.columns or y + height > self.rows:
            return False
        return not any(
            self.taken[row][column]
            for row in range(y, y + height)
            for column in range(x, x + width)
        )


# Title class, same shit as you can imagine, but with split function
# font_path / font_size: pass them to force a font or size, leave None for random ones
# rng: pass one shared random.Random(seed) to make the layout reproducible
class Title:
    def __init__(self, text, max_width, font_path=None, font_size=None, rng=random):
        self.text = text
        self.rng = rng
        self.font_size = font_size or self._balan_dim()  # size first, then the font at that size
        self.font = ImageFont.truetype(font_path or self._random_font(), self.font_size)
        self.lines = self._split(max_width)
        # whole pixels, rounded up: the Grid can't occupy 30.7 cells
        self.width = math.ceil(max(self.font.getlength(line) for line in self.lines))
        self.height = math.ceil(self.font_size * 1.2 * len(self.lines))
        self.x = None   # filled in after packing
        self.y = None
        self.link = None  # where clicking it goes, set by build_page

    def _split(self, max_width):
        lines, current_line = [], ""
        for word in self.text.split():
            test_line = (current_line + " " + word).strip()
            if self.font.getlength(test_line) <= max_width:
                current_line = test_line
            else:
                if current_line:
                    lines.append(current_line)
                current_line = word
        lines.append(current_line)
        return lines

    def _random_font(self):
        font_paths = sorted(FONT_DIR.glob("*.ttf"))   # sorted keeps the seed reproducible
        if not font_paths:
            raise FileNotFoundError(f"no .ttf fonts in {FONT_DIR}")
        return self.rng.choice(font_paths)

    def _balan_dim(self, min_size=8, max_size=110):
        text = self.text
        length = len(text)
        short_limit, long_limit = 15, 30

        # t = 0 for short titles, 1 for long ones, linear in between
        t = (length - short_limit) / (long_limit - short_limit)
        t = max(0, min(1, t))

        mu = 50 + t * (20 - 50)       # 50 -> 20
        sigma = 20 + t * (5 - 20)     # 20 -> 5

        size = self.rng.gauss(mu, sigma)
        # return max(min_size, min(max_size, round(size)))
        return round(size)


class PageImage:
    def __init__(self, path, target_width):
        self.path = path
        with Image.open(path) as image:
            original_width, original_height = image.size
        self.aspect_ratio = original_height / original_width
        self.resize(target_width)
        self.x = None   # filled in after packing
        self.y = None
        self.link = None  # where clicking it goes, set by build_page

    def resize(self, new_width):
        self.width = round(new_width)  # whole pixels, like Title
        self.height = max(1, round(self.width * self.aspect_ratio))  # very wide banners would round to 0


# how the HTML points at a local file (image or font): relative to the page
# if we know where it's saved, otherwise an absolute file:// address
def _src(path, out_dir) -> str:
    path = Path(path).resolve()
    if out_dir is not None:
        try:
            return quote(Path(os.path.relpath(path, out_dir)).as_posix())
        except ValueError:  # Windows: file on a different drive than the page
            pass
    return path.as_uri()


# an item is either plain content or a (content, link) pair -> always (content, link)
def _split_link(item):
    return item if isinstance(item, tuple) else (item, None)


# draws the packed boxes into a PNG with Pillow: same positions, fonts and line breaks as the HTML
def _render_png(boxes: list, page_width: int, page_height: int, path) -> None:
    page = Image.new("RGB", (page_width, page_height), "white")
    draw = ImageDraw.Draw(page)
    for box in boxes:
        if box.x is None:
            continue  # didn't fit
        if isinstance(box, PageImage):
            with Image.open(box.path) as picture:
                picture = picture.convert("RGBA").resize((box.width, box.height))
                page.paste(picture, (box.x, box.y), picture)  # 3rd arg: keep transparency
        else:
            line_height = box.font_size * 1.2
            for line_number, line in enumerate(box.lines):
                draw.text((box.x, box.y + line_number * line_height), line, font=box.font, fill="black")
    page.save(path)


# main function: images are file paths, titles and texts are strings, sizes in pixels.
# Any item can also be a (content, link) pair: that element becomes clickable and opens link.
# Same seed -> same page. Boxes that don't fit on the page are left out.
# Returns the HTML; if out_path is given it's also saved there, if png_path is given
# the same page is also drawn as an image there.
def build_page(images: list, titles: list, texts: list, page_width: int = 600, page_height: int = 850,
               gutter: int = 4, seed=None, out_path=None, png_path=None) -> str:
    rng = random.Random(seed)
    out_dir = Path(out_path).resolve().parent if out_path else None
    max_width = page_width - gutter  # nothing wider than the page

    page_images, page_titles, page_texts = [], [], []
    for item in images:
        path, link = _split_link(item)
        page_images.append(PageImage(path, rng.randint(page_width // 5, page_width // 2)))
        page_images[-1].link = link
    for item in titles:
        text, link = _split_link(item)
        page_titles.append(Title(text, max_width, rng=rng))
        page_titles[-1].link = link
    for item in texts:  # texts are boxes like titles, just smaller and narrower
        text, link = _split_link(item)
        page_texts.append(Title(text, rng.randint(page_width // 4, page_width // 2),
                                font_size=rng.randint(10, 16), rng=rng))
        page_texts[-1].link = link

    boxes = page_images + page_titles + page_texts  # packing order: images, titles, texts
    packer(Grid(page_width, page_height), boxes, page_width, page_height, gutter)

    fonts = {}  # font file -> css family name
    parts = []
    for e in boxes:
        if e.x is None:
            continue  # didn't fit
        box = f"position:absolute; left:{e.x}px; top:{e.y}px; width:{e.width}px; height:{e.height}px;"
        if isinstance(e, PageImage):
            content = f'<img src="{escape(_src(e.path, out_dir))}" style="width:100%; height:100%; object-fit:cover; display:block;">'
            style = box
        else:
            family = fonts.setdefault(e.font.path, f"font{len(fonts)}")
            content = escape(chr(10).join(e.lines))
            # white-space:pre keeps the exact line breaks Title measured
            style = (f"{box} font-family:{family}; font-size:{e.font_size}px; "
                     f"line-height:1.2; white-space:pre; overflow:hidden;")
        if e.link:  # clickable box: a link that looks like the plain element, opens in a new tab
            parts.append(f'<a href="{escape(e.link)}" target="_blank" rel="noopener" '
                         f'style="{style} display:block; color:inherit; text-decoration:none;">{content}</a>')
        else:
            parts.append(f'<div style="{style}">{content}</div>')

    font_css = "\n".join(f'@font-face {{ font-family: {family}; src: url("{escape(_src(path, out_dir))}"); }}'
                         for path, family in fonts.items())
    page = f"""<!doctype html>
<html><head><meta charset="utf-8"><title>fanzi</title>
<style>
{font_css}
body {{ margin: 0; background: #ddd; }}
.page {{ position: relative; width: {page_width}px; height: {page_height}px; margin: 0 auto; background: white; }}
</style></head>
<body><div class="page">
{chr(10).join(parts)}
</div></body></html>
"""
    if out_path:
        os.makedirs(out_dir, exist_ok=True)
        Path(out_path).write_text(page, encoding="utf-8")
    if png_path:
        os.makedirs(Path(png_path).resolve().parent, exist_ok=True)
        _render_png(boxes, page_width, page_height, png_path)
    return page

# demo: runs only with `python page_organizer.py`, not when another file imports this one
if __name__ == "__main__":
    seed = 42
    page_width, page_height = 1748, 2480    # A5 at 300 dpi, in pixels
    gutter = 20
    rng = random.Random(seed)

    # 1. build images
    image_paths = sorted(Path("images").glob("*.*"))
    images = [PageImage(path, rng.randint(300, 900)) for path in image_paths]

    # 2. build titles (Title picks its own random font from FONT_DIR)
    # utf-8-sig drops the invisible BOM that Windows editors often put at the start of the file
    title_texts = Path("titles.txt").read_text(encoding="utf-8-sig").splitlines()
    titles = [Title(text, rng.randint(400, 1200), rng=rng)
              for text in title_texts if text.strip()]

    # 3. pack them together
    boxes = images + titles
    rng.shuffle(boxes)          # avoids biggest-first, everything top-left
    grid = Grid(page_width, page_height)
    placed_count = packer(grid, boxes, page_width, page_height, gutter)
    print(f"placed {placed_count} of {len(boxes)}")

    # 4. draw the result
    page = Image.new("RGB", (page_width, page_height), "white")
    draw = ImageDraw.Draw(page)
    for box in boxes:
        if box.x is None:
            continue                    # didn't fit
        if isinstance(box, PageImage):
            with Image.open(box.path) as picture:
                picture = picture.convert("RGB").resize((box.width, box.height))
                page.paste(picture, (box.x, box.y))
        else:
            line_height = box.font_size * 1.2
            for line_number, line in enumerate(box.lines):
                draw.text((box.x, box.y + line_number * line_height),
                          line, font=box.font, fill="black")
    page.save("page.png")