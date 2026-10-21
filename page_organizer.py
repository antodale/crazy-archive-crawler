"""Pack images, titles and texts on as many pages as needed and get them back as HTML.

Usage from another file:

    from page_organizer import build_page

    html = build_page(["a.jpg", "b.png"], ["A title"], ["some text", "more text"],
                      page_width=2480, page_height=3508, seed=1,
                      out_path="out/zine.html", png_path="out/zine.png")
    # -> out/zine.html with every page, out/zine_p1.png, out/zine_p2.png, ...

All sizes are in pixels. Everything given is placed: new pages are added until it all fits.
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
from rectpack import newPacker, GuillotineBssfSas, SORT_NONE, PackingBin, PackingMode

FONT_DIR = Path(__file__).parent / "fonts"  # works whatever folder you run the script from
A4 = (2480, 3508)        # A4 at 300 dpi, in pixels: the default page
REFERENCE_WIDTH = 794    # A4 at 96 dpi: font sizes are designed for this width and scaled for others


# packs boxes on as many pages as needed, in the order given: each box goes on the open page
# where it fits best, and a new page is opened when it fits nowhere. Sets .x, .y and .page
# (0, 1, 2...) on every box and returns the number of pages. A box bigger than an empty
# page can't be placed and keeps .page = None (build_page shrinks boxes so that can't happen).
def pack_pages(boxes: list, page_width: int, page_height: int, gutter=0) -> int:
    packer = newPacker(mode=PackingMode.Online, bin_algo=PackingBin.BBF,
                       pack_algo=GuillotineBssfSas, rotation=False)
    packer.add_bin(page_width, page_height, count=float("inf"))
    for i, box in enumerate(boxes):
        box.x = box.y = box.page = None
        packer.add_rect(box.width + gutter, box.height + gutter, rid=i)
    for page, x, y, width, height, i in packer.rect_list():
        boxes[i].x, boxes[i].y, boxes[i].page = x, y, page
    return len(packer)


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
# scale: multiplies the random size, so titles keep their look on bigger pages
class Title:
    def __init__(self, text, max_width, font_path=None, font_size=None, rng=random, scale=1):
        self.text = text
        self.rng = rng
        # size first, then the font at that size; at least 1px, Pillow can't make a 0px font
        self.font_size = font_size or max(1, round(self._balan_dim() * scale))
        self.font = ImageFont.truetype(font_path or self._random_font(), self.font_size)
        self.lines = self._split(max_width)
        # whole pixels, rounded up: the Grid can't occupy 30.7 cells
        self.width = math.ceil(max(self.font.getlength(line) for line in self.lines))
        self.height = math.ceil(self.font_size * 1.2 * len(self.lines))
        self.x = None   # filled in after packing
        self.y = None
        self.page = None  # which page it landed on, filled in by pack_pages
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
        self.page = None  # which page it landed on, filled in by pack_pages
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


# draws the packed boxes into a PNG with Pillow: same positions, fonts and line breaks as the HTML.
# page: draw only the boxes on that page (None = every placed box)
def _render_png(boxes: list, page_width: int, page_height: int, path, page=None) -> None:
    image = Image.new("RGB", (page_width, page_height), "white")
    draw = ImageDraw.Draw(image)
    for box in boxes:
        if box.x is None or (page is not None and box.page != page):
            continue  # didn't fit, or is on another page
        if isinstance(box, PageImage):
            with Image.open(box.path) as picture:
                picture = picture.convert("RGBA").resize((box.width, box.height))
                image.paste(picture, (box.x, box.y), picture)  # 3rd arg: keep transparency
        else:
            line_height = box.font_size * 1.2
            visible = max(1, int(box.height // line_height))  # lines that fit the box (it may have been cut)
            for line_number, line in enumerate(box.lines[:visible]):
                draw.text((box.x, box.y + line_number * line_height), line, font=box.font, fill="black")
    image.save(path)


# "out/zine.png", page 2 -> "out/zine_p2.png"
def page_png_path(png_path, page_number: int) -> str:
    base, extension = os.path.splitext(str(png_path))
    return f"{base}_p{page_number}{extension}"


# shrinks a box until it fits on an empty page (page minus gutter), or pack_pages could never place it.
# Images keep their proportions; text boxes are cut (the HTML hides the overflow, the PNG skips lines).
def _fit_on_page(box, room_width: int, room_height: int) -> None:
    if isinstance(box, PageImage):
        while box.width > room_width or box.height > room_height:
            box.resize(min(room_width, box.width * room_height / box.height, box.width - 1))
    else:
        box.width, box.height = min(box.width, room_width), min(box.height, room_height)


# main function: images are file paths, titles and texts are strings, sizes in pixels.
# Any item can also be a (content, link) pair: that element becomes clickable and opens link.
# Everything is placed: pages are added until it all fits. Same seed -> same pages.
# gutter: space between boxes, None = scaled to the page (about 12px on A4 at 300 dpi).
# Returns the HTML with every page; if out_path is given it's also saved there, if png_path
# is given each page is also drawn as an image: zine.png -> zine_p1.png, zine_p2.png, ...
def build_page(images: list, titles: list, texts: list, page_width: int = A4[0], page_height: int = A4[1],
               gutter: int | None = None, seed=None, out_path=None, png_path=None) -> str:
    rng = random.Random(seed)
    out_dir = Path(out_path).resolve().parent if out_path else None
    scale = page_width / REFERENCE_WIDTH  # fonts grow with the page, so any resolution looks the same
    if gutter is None:
        gutter = max(1, round(4 * scale))
    max_width = page_width - gutter  # nothing wider than the page

    page_images, page_titles, page_texts = [], [], []
    for item in images:
        path, link = _split_link(item)
        page_images.append(PageImage(path, rng.randint(page_width // 5, page_width // 2)))
        page_images[-1].link = link
    for item in titles:
        text, link = _split_link(item)
        page_titles.append(Title(text, max_width, rng=rng, scale=scale))
        page_titles[-1].link = link
    for item in texts:  # texts are boxes like titles, just smaller and narrower
        text, link = _split_link(item)
        page_texts.append(Title(text, rng.randint(page_width // 4, page_width // 2),
                                font_size=round(rng.randint(10, 16) * scale), rng=rng))
        page_texts[-1].link = link

    # mix the three kinds evenly through the packing order, so every page gets images, titles
    # and text: each box gets a position between 0 and 1 within its own kind, then all are sorted
    groups = [page_images, page_titles, page_texts]
    keyed = [((i + rng.random()) / len(group), box) for group in groups for i, box in enumerate(group)]
    boxes = [box for _, box in sorted(keyed, key=lambda pair: pair[0])]
    for box in boxes:
        _fit_on_page(box, page_width - gutter, page_height - gutter)
    n_pages = pack_pages(boxes, page_width, page_height, gutter)

    fonts = {}  # font file -> css family name

    def element_html(e) -> str:
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
            return (f'<a href="{escape(e.link)}" target="_blank" rel="noopener" '
                    f'style="{style} display:block; color:inherit; text-decoration:none;">{content}</a>')
        return f'<div style="{style}">{content}</div>'

    pages_html = []
    for p in range(n_pages):
        parts = [element_html(e) for e in boxes if e.page == p]
        pages_html.append('<div class="page">\n' + "\n".join(parts) + "\n</div>")

    font_css = "\n".join(f'@font-face {{ font-family: {family}; src: url("{escape(_src(path, out_dir))}"); }}'
                         for path, family in fonts.items())
    # on screen the pages sit one under the other; printed, each page is its own sheet
    page = f"""<!doctype html>
<html><head><meta charset="utf-8"><title>fanzi</title>
<style>
{font_css}
@page {{ size: {page_width}px {page_height}px; margin: 0; }}
body {{ margin: 0; background: #ddd; }}
.page {{ position: relative; width: {page_width}px; height: {page_height}px; margin: 20px auto;
         background: white; overflow: hidden; }}
@media print {{ body {{ background: none; }} .page {{ margin: 0; break-after: page; }} }}
</style></head>
<body>
{chr(10).join(pages_html)}
</body></html>
"""
    if out_path:
        os.makedirs(out_dir, exist_ok=True)
        Path(out_path).write_text(page, encoding="utf-8")
    if png_path:
        os.makedirs(Path(png_path).resolve().parent, exist_ok=True)
        for p in range(n_pages):
            _render_png(boxes, page_width, page_height, page_png_path(png_path, p + 1), page=p)
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