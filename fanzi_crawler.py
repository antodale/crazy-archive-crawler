from __future__ import annotations
import extract
import page_organizer
import math
import os
from dataclasses import dataclass, field
from urllib.parse import urljoin, urlparse
import random
import re
import zlib
from html import escape
import requests
from bs4 import BeautifulSoup
from PIL import Image
from rectpack import PackingBin, PackingMode, newPacker
from waybackpy import WaybackMachineCDXServerAPI
import shutil
import sys
from urllib.parse import urldefrag
from extract import extract_content

USER_AGENT = "fanzi-crawler/1.0"
SESSION = requests.Session()
SESSION.headers["User-Agent"] = USER_AGENT
MADNESS = 3
DEPTH = 3
YEAR = 2015
PAGE_SIZE = page_organizer.A4  # (width, height) in pixels: A4 at 300 dpi

def nn_word(word, temperature=int(MADNESS/2), n=MADNESS):
    r = requests.get("https://api.datamuse.com/words", params={"ml": word, "max": int(1 + n * temperature)})  # Datamuse wants a whole number

    return list(set([w["word"] for w in r.json()]))

# print(nn_word("twin towers"))


def search_wayback(query: str, n: int = MADNESS) -> list[str]:
    """First n site URLs for a keyword search, as on the Wayback Machine homepage.

    Uses the undocumented endpoint behind the search box: results are whole
    sites (not single pages) and it returns at most 50 of them.
    """
    r = SESSION.get("https://web.archive.org/__wb/search/anchor",
                    params={"q": query}, timeout=30)
    r.raise_for_status()
    return [site["link"] for site in r.json()[:n]]


@dataclass
class ScrapedPage:
    url: str
    timestamp: str
    text: str                                        # clean sentences, one per line
    images: list[str] = field(default_factory=list)  # raw Wayback URLs
    links: list[str] = field(default_factory=list)   # original (live-web) URLs, no ads
    titles: list[str] = field(default_factory=list)  # <title>, h1-h4 and marquee text


def raw_url(timestamp: str, url: str) -> str:
    """Wayback URL that serves the original bytes (no toolbar, no rewritten links)."""
    return f"https://web.archive.org/web/{timestamp}id_/{url}"


def wayback_url(page: ScrapedPage) -> str:
    """The archived copy of a scraped page, as a person browses it on the Wayback Machine."""
    return f"https://web.archive.org/web/{page.timestamp}/{page.url}"


def scrape_page(url: str, year: int | None = None) -> ScrapedPage:
    cdx = WaybackMachineCDXServerAPI(url, USER_AGENT, filters=["statuscode:200", "mimetype:text/html"])
    # closest capture to the middle of that year (without month/day, waybackpy would use today's date)
    snap = cdx.near(year=year, month=7, day=1) if year else cdx.newest()

    r = SESSION.get(raw_url(snap.timestamp, snap.original), timeout=60)
    r.raise_for_status()

    # extract.py keeps only the interesting parts: no ads, menus, counters, spacers or boilerplate
    content = extract_content(r.text, snap.original)
    images = [extract.archived_image_url(snap.timestamp, image["url"]) for image in content["images"]]
    text = "\n".join(content["sentences"])

    return ScrapedPage(snap.original, snap.timestamp, text, images, content["links"], content["titles"])


def download_image(url: str, out_dir: str) -> str:
    """Save an image to out_dir and return its local path."""
    os.makedirs(out_dir, exist_ok=True)
    name = os.path.basename(urlparse(url).path) or "image"
    path = os.path.join(out_dir, f"{zlib.crc32(url.encode()):08x}_{name}")  # prefix avoids name clashes

    r = SESSION.get(url, timeout=60)
    r.raise_for_status()
    with open(path, "wb") as f:
        f.write(r.content)
    return path

def safe_name(text: str) -> str:
    """Text usable as a file name: anything but letters, digits and - becomes _ ("9/11 attack" -> "9_11_attack")."""
    return re.sub(r"[^\w-]+", "_", text).strip("_") or "search"


def free_name(folder: str, base: str, extensions: list) -> str:
    """base, or base(2), base(3)...: the first name with no existing file for any of the extensions."""
    name, index = base, 1
    while any(os.path.exists(os.path.join(folder, name + ext)) for ext in extensions):
        index += 1
        name = f"{base}({index})"
    return name


def swarm_scrape(og_word: str, n=MADNESS, year: int | None = YEAR): 
    word_nn_list = nn_word(og_word, n=n)
    scraped_list = []
    url_list = []
    for word in word_nn_list:
        word_top_urls = search_wayback(word, n+1-word_nn_list.index(word))
        url_list = url_list + word_top_urls
    for url in url_list:
        try:
            scraped_list.append(scrape_page(url, year))
        except Exception as e:  # dead site, no captures, timeout...: skip it
            print(f"skipped {url}: {e}")
    return scraped_list

def bar(done: int, total: int, width: int = 20) -> str:
    """Text progress bar, like [########------------] 8/20."""
    filled = width * done // total if total else width
    return f"[{'#' * filled}{'-' * (width - filled)}] {done}/{total}"


def status(line: str) -> None:
    """Rewrite the current terminal line in place: \\r jumps back to its start."""
    width = shutil.get_terminal_size().columns - 1
    sys.stdout.write("\r" + line[:width].ljust(width))
    sys.stdout.flush()


def rabbit_hole_gen(urls: list, depth: int, decay: int | None = 10, year: int | None = YEAR) -> list:
    """Follow links depth levels deep. Returns one list of ScrapedPage per level:
    layers[0] = the start pages, layers[1] = pages they link to, ...
    Each page passes at most `decay` links on; dead links are skipped, no URL is visited twice.
    Shows a live status line while it crawls."""
    visited = set()
    layers = []
    to_visit = list(urls)
    explored, dead = 0, 0
    images, titles = set(), set()  # sets: count each image / title once

    def show(done: int) -> None:
        # short labels and bar so the whole line fits an 80-column terminal
        status(f"layer {len(layers) + 1}/{depth} {bar(done, len(to_visit), 12)} | explored {explored} "
               f"| images {len(images)} | titles {len(titles)} | dead {dead}")

    while to_visit and len(layers) < depth:
        layer, next_urls = [], []
        for i, url in enumerate(to_visit):
            show(i)
            url = urldefrag(url).url              # page.html#top is the same page as page.html
            if url in visited:
                continue
            visited.add(url)
            explored += 1
            try:
                page = scrape_page(url, year)
            except Exception:                     # no captures, dead site, timeout...
                dead += 1
                continue
            visited.add(page.url)                 # same page, as Wayback spells it (e.g. with :80)
            layer.append(page)
            images.update(page.images)
            titles.update(page.titles)
            next_urls += [l for l in page.links if l.startswith("http")][:decay]
        show(len(to_visit))
        print()                                   # keep this layer's final line, start a new one
        layers.append(layer)
        to_visit = next_urls
    return layers
    

    
def random_gen(range: int, temp) -> tuple:
    n_titles = random.randrange(range)
    n_imgs = random.randrange(range)*5
    n_words = random.randrange(500*range)
    return n_titles, n_imgs, n_words

if __name__ == "__main__":
    # usage: python fanzi_crawler.py "twin towers" [madness] [depth] [year] [size]
    # year: pages are taken from the capture closest to that year
    # size: page size in pixels, WIDTHxHEIGHT, e.g. 1748x2480 (default: A4 at 300 dpi, 2480x3508)
    query = sys.argv[1]
    MADNESS = float(sys.argv[2]) if len(sys.argv) > 2 else MADNESS
    DEPTH = int(sys.argv[3]) if len(sys.argv) > 3 else DEPTH
    YEAR = int(sys.argv[4]) if len(sys.argv) > 4 else YEAR  # default: the YEAR constant at the top
    if len(sys.argv) > 5:
        size = re.fullmatch(r"(\d+)x(\d+)", sys.argv[5].lower())
        if not size:
            sys.exit(f"page size must be WIDTHxHEIGHT in pixels, like 2480x3508, not {sys.argv[5]!r}")
        PAGE_SIZE = (int(size.group(1)), int(size.group(2)))
    decay = MADNESS
    sites_per_word = 3
    out_dir = "fanzi_out"
    rng = random.Random()

    print("=" * 40)
    print(f"  fanzi crawler: {query!r}")
    print(f"  MADNESS {MADNESS:g}   DEPTH {DEPTH}   YEAR {YEAR}")
    print(f"  PAGE {PAGE_SIZE[0]}x{PAGE_SIZE[1]} px")
    print("=" * 40)

    # First search with neighbour words: only collect site URLs,
    # rabbit_hole_gen scrapes them as its first layer
    start_urls = []
    words = [query] + nn_word(query, MADNESS)
    for i, qu in enumerate(words):
        status(f"searching {bar(i, len(words))} {qu!r} | sites {len(start_urls)}")
        try:
            for url in search_wayback(qu, sites_per_word):
                if url not in start_urls:
                    start_urls.append(url)
        except requests.RequestException:
            pass  # one failed search is not a reason to stop
    status(f"searching {bar(len(words), len(words))} | sites {len(start_urls)}")
    print()

    # Second pass with rabbit hole
    layers = rabbit_hole_gen(rng.sample(start_urls, min(10, len(start_urls))), DEPTH, int(decay), year=YEAR)
    pages = [page for layer in layers for page in layer]
    print(f"{len(pages)} pages scraped in {len(layers)} layers")

    # Third: build the fanzine with ALL the content, on as many pages as it needs.
    # Every element is a (content, link) pair, link = the archived page it was taken
    # from, so clicking it on the final page goes there
    image_sources = {i: p for p in pages for i in p.images}  # image url -> page it was on
    to_download = list(image_sources)
    images = []
    for i, url in enumerate(to_download):
        status(f"downloading images {bar(i, len(to_download))} | ok {len(images)}")
        try:
            path = download_image(url, os.path.join(out_dir, "images"))
            with Image.open(path) as im:
                im.verify()  # skip files that aren't really images
            if not extract.is_interesting_image_file(path):
                continue     # real size says spacer or banner (the HTML didn't give its size)
        except Exception:    # never archived, broken file...
            continue
        images.append((path, wayback_url(image_sources[url])))
    status(f"downloading images {bar(len(to_download), len(to_download))} | ok {len(images)}")
    print()

    titles, seen = [], set()  # every title once (a site often repeats its <title> on all its pages)
    for page in pages:
        for title in page.titles:
            if title not in seen:
                seen.add(title)
                titles.append((title, wayback_url(page)))

    texts = []  # all the text, cut in runs of 2-30 consecutive words: nothing left out
    for page in pages:
        words = page.text.split()
        start = 0
        while start < len(words):
            k = rng.randint(2, 30)
            texts.append((" ".join(words[start:start + k]), wayback_url(page)))
            start += k

    # file name: search + parameters, "x" for the ones not given, e.g. twin_towers_4_5_xxxx_x
    def name_part(position: int, value: str, placeholder: str) -> str:
        return value if len(sys.argv) > position else placeholder

    base = "_".join([safe_name(query), name_part(2, f"{MADNESS:g}", "x"), name_part(3, str(DEPTH), "x"),
                     name_part(4, str(YEAR), "xxxx"), name_part(5, f"{PAGE_SIZE[0]}x{PAGE_SIZE[1]}", "x")])
    name = free_name(out_dir, base, [".html", "_p1.png"])  # adds (2), (3)... if already taken
    html_path = os.path.join(out_dir, name + ".html")
    png_path = os.path.join(out_dir, name + ".png")       # becomes name_p1.png, name_p2.png, ...
    status(f"laying out {len(images)} images, {len(titles)} titles, {len(texts)} texts...")
    html = page_organizer.build_page(images, titles, texts, PAGE_SIZE[0], PAGE_SIZE[1],
                                     out_path=html_path, png_path=png_path)
    print()
    n_pages = html.count('<div class="page">')
    print(f"written {html_path} and {n_pages} PNG page{'s' * (n_pages != 1)} ({name}_p1.png ...)")



