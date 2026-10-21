"""extract.py: keep only the interesting images, titles and sentences of an archived page.

Usage:
    from extract import extract_content
    content = extract_content(html, site_url="http://www.geocities.com/Area51/1234/")
    content["images"]     # list of dicts: url, alt, width, height
    content["titles"]     # list of strings
    content["sentences"]  # list of strings
    content["links"]      # list of web addresses to crawl next (from the content: no ads, no menus)

    links = find_links(soup, site_url)  # just the links, from an already parsed page

Fetch pages with the "id_" form of the Wayback URL, which returns the original HTML
without archive.org's toolbar:
    https://web.archive.org/web/<timestamp>id_/<original url>
The function also strips the toolbar if it is still there.

What gets thrown away:
    - archive.org toolbar, scripts, styles, forms, iframes
    - page headers, footers, navigation, sidebars, banners, ads (by tag, class and id)
    - blocks that are mostly links (menus)
    - ad images (banner sizes, ad words in addresses, links to other sites)
    - spacers, bullets, counters, and images repeated on the page
    - boilerplate sentences (copyright, "best viewed with", "click here", ...)
    - ad links: ad networks and counters in the address, click-tracking redirects,
      rel="sponsored", banner-sized or ad-labelled picture links, links inside ad boxes
    - menu links: extract_content takes links only from what is left after removing
      headers, footers, navigation, sidebars and link-heavy blocks (find_links alone
      keeps them, if you call it on a page that wasn't cleaned)
"""

import re
from collections import Counter
from urllib.parse import urldefrag, urljoin, urlparse

from bs4 import BeautifulSoup, Comment

NOISE_TAGS = ["script", "style", "noscript", "nav", "header", "footer", "aside",
              "form", "iframe", "object", "embed", "applet", "map", "select", "button"]

# matched as whole parts of a class or id, so "shadow" or "loader" don't count as "ad"
BORING_NAMES = re.compile(
    r"(^|[\s_\-])(nav|navbar|navigation|menu|footer|header|masthead|sidebar|toolbar|"
    r"banner|cookie|share|social|ad|ads|advert|advertisement|sponsor|promo|copyright|"
    r"breadcrumb|wm-ipp[\w-]*)([\s_\-]|$)",
    re.I,
)

# \b = start of a word, so "ad." matches ad.doubleclick.net or my-ad.gif but not download.html,
# and "counter" matches counter.cgi but not encounter.html
AD_ADDRESS = re.compile(
    r"doubleclick|googlesyndication|adserver|adclick|adsrv|/ads?/|/banners?/|banner_?ad|"
    r"sponsor|geoguide|\bads?\.|clickserve|linkexchange|valueclick|fastclick|"
    r"hitbox|\bcounter|hitcount|sitemeter|statcounter|webring",
    re.I,
)

# class / id of a block that holds ads (narrower than BORING_NAMES: menus stay, they lead deeper into a site)
AD_BLOCK_NAMES = re.compile(
    r"(^|[\s_\-])(ad|ads|adbox|adsense|advert|advertisement|advertising|sponsor|sponsored|"
    r"promo|banner)([\s_\-]|$)",
    re.I,
)

# click-tracking redirects: a script that forwards you to another address, e.g. click.php?url=http://...
REDIRECT = re.compile(r"[?&](url|u|dest|target|redirect|goto|link)=(https?|www)", re.I)

# standard ad sizes of the banner era (width, height)
BANNER_SIZES = [(468, 60), (728, 90), (120, 600), (160, 600), (300, 250), (125, 125),
                (234, 60), (120, 240), (120, 90), (120, 60), (392, 72), (336, 280)]

BOILERPLATE = re.compile(
    r"copyright|all rights reserved|©|best viewed|netscape|internet explorer|"
    r"click here|last updated|last modified|you are visitor|visitors? since|"
    r"this page (was|has been)|powered by|hosted by|free home ?page|webmaster|"
    r"sign my guestbook|view my guestbook|back to (top|home)|e-?mail me",
    re.I,
)

TITLE_TAGS = ["title", "h1", "h2", "h3", "h4", "marquee"]
MIN_IMAGE_SIDE = 40
MIN_SENTENCE_WORDS = 5
MAX_TITLE_LENGTH = 120


def extract_content(html, site_url):
    soup = BeautifulSoup(html, "html.parser")
    remove_wayback_toolbar(soup)
    remove_noise(soup)
    remove_menus(soup)
    links = find_links(soup, site_url)   # after cleaning: menu, header, footer and sidebar links are gone
    return {
        "images": find_images(soup, site_url),
        "titles": find_titles(soup),
        "sentences": find_sentences(soup),
        "links": links,
    }


def remove_wayback_toolbar(soup):
    # the toolbar sits between two HTML comments; drop everything between them
    for comment in soup.find_all(string=lambda text: isinstance(text, Comment)):
        if "BEGIN WAYBACK TOOLBAR INSERT" in comment:
            node = comment.next_sibling
            while node is not None:
                following = node.next_sibling
                is_end = isinstance(node, Comment) and "END WAYBACK TOOLBAR INSERT" in node
                node.extract()
                if is_end:
                    break
                node = following
    for comment in soup.find_all(string=lambda text: isinstance(text, Comment)):
        comment.extract()
    for tag in soup.select('[id^="wm-"], [id^="donato"]'):
        tag.decompose()


def remove_noise(soup):
    for tag in soup(NOISE_TAGS):
        tag.decompose()
    for tag in soup.find_all(True):
        if tag.decomposed:
            continue
        names = " ".join(tag.get("class", [])) + " " + (tag.get("id") or "")
        if BORING_NAMES.search(names):
            tag.decompose()


def link_density(element):
    text_length = len(element.get_text(" ", strip=True))
    link_length = sum(len(link.get_text(" ", strip=True)) for link in element.find_all("a"))
    return link_length / max(1, text_length)


def remove_menus(soup):
    # small blocks that are mostly links are menus, link lists or webrings
    for tag in soup.find_all(["ul", "ol", "td", "div", "p", "center", "table", "font"]):
        if tag.decomposed:
            continue
        words = len(tag.get_text(" ", strip=True).split())
        if 0 < words < 80 and len(tag.find_all("a")) >= 2 and link_density(tag) > 0.5:
            tag.decompose()


def domain(url):
    host = urlparse(url).netloc.lower()
    return host[4:] if host.startswith("www.") else host


def number_attribute(tag, name):
    match = re.match(r"\s*(\d+)", str(tag.get(name, "")))
    return int(match.group(1)) if match else None


def is_banner_size(width, height, tolerance=2):
    if width is None or height is None:
        return False
    return any(abs(width - banner_width) <= tolerance and abs(height - banner_height) <= tolerance
               for banner_width, banner_height in BANNER_SIZES)


def find_images(soup, site_url):
    site_domain = domain(site_url)
    images = soup.find_all("img")
    source_counts = Counter(image.get("src", "") for image in images)
    found, seen = [], set()

    for image in images:
        source = (image.get("src") or "").strip()
        if not source or source.startswith("data:"):
            continue
        url = urljoin(site_url, source)
        width = number_attribute(image, "width")
        height = number_attribute(image, "height")
        alt = (image.get("alt") or "").strip()
        link = image.find_parent("a")
        link_target = urljoin(site_url, link.get("href", "")) if link else ""

        if url in seen or source_counts[source] > 2:          # bullets, dividers, repeated icons
            continue
        if (width is not None and width < MIN_IMAGE_SIDE) or (height is not None and height < MIN_IMAGE_SIDE):
            continue                                           # spacers, bullets, lines
        if is_banner_size(width, height):
            continue
        if AD_ADDRESS.search(url) or AD_ADDRESS.search(link_target) or re.search(r"advert|sponsor", alt, re.I):
            continue
        image_is_foreign = domain(url) != site_domain
        link_is_foreign = bool(link_target) and domain(link_target) != site_domain
        if image_is_foreign and link_is_foreign:
            continue                                           # hosted elsewhere, links elsewhere: ad or badge
        small = width is not None and width < 150 and height is not None and height < 150
        if link and not link_is_foreign and small:
            continue                                           # small linked image on the same site: a nav button

        seen.add(url)
        found.append({"url": url, "alt": alt, "width": width, "height": height})
    return found


def is_ad_link(link, url):
    """True if an <a> tag is an advertisement, a tracker or a counter rather than a real link."""
    if AD_ADDRESS.search(url) or REDIRECT.search(url):
        return True
    if "sponsored" in (link.get("rel") or []):           # rel="sponsored": marked as an ad by the page itself
        return True
    image = link.find("img")
    if image is not None:                                  # a picture link: the classic banner ad
        if is_banner_size(number_attribute(image, "width"), number_attribute(image, "height")):
            return True
        if AD_ADDRESS.search(image.get("src") or "") or re.search(r"advert|sponsor", image.get("alt") or "", re.I):
            return True
    for block in link.parents:                             # sitting inside an "ad" box
        names = " ".join(block.get("class", [])) + " " + (block.get("id") or "")
        if AD_BLOCK_NAMES.search(names):
            return True
    return False


def find_links(soup, site_url):
    """Web addresses the page links to, without ads, trackers, mail links or repeats.

    Only reads the soup, so it can run before or after other cleaning.
    """
    links, seen = [], set()
    for link in soup.find_all("a", href=True):
        url = urldefrag(urljoin(site_url, link["href"].strip())).url  # page.html#top -> page.html
        if not url.startswith(("http://", "https://")):      # mailto:, javascript:, ftp:...
            continue
        if url in seen or is_ad_link(link, url):
            continue
        seen.add(url)
        links.append(url)
    return links


def clean_text(text):
    return re.sub(r"\s+", " ", text).strip()


def find_titles(soup):
    titles, seen = [], set()
    for tag in soup.find_all(TITLE_TAGS):
        text = clean_text(tag.get_text(" "))
        key = text.lower()
        if not text or len(text) > MAX_TITLE_LENGTH or key in seen or BOILERPLATE.search(text):
            continue
        seen.add(key)
        titles.append(text)
        if tag.name != "title":
            tag.decompose()      # so titles don't come back as sentences
    return titles


def find_sentences(soup):
    body = soup.body or soup
    text = clean_text(body.get_text(" "))
    pieces = re.split(r"(?<=[.!?])\s+", text)
    sentences, seen = [], set()
    for piece in pieces:
        sentence = piece.strip()
        key = sentence.lower()
        if len(sentence.split()) < MIN_SENTENCE_WORDS or key in seen or BOILERPLATE.search(sentence):
            continue
        seen.add(key)
        sentences.append(sentence)
    return sentences


def archived_image_url(timestamp, image_url):
    """Wayback address that serves the original image file (im_ = raw image, no toolbar)."""
    return f"https://web.archive.org/web/{timestamp}im_/{image_url}"


def is_interesting_image_file(path):
    """Second check after downloading, for images whose size wasn't in the HTML."""
    from PIL import Image
    with Image.open(path) as image:
        width, height = image.size
    return min(width, height) >= MIN_IMAGE_SIDE and not is_banner_size(width, height)
