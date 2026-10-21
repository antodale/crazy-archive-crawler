# crazy-archive-crawler
## Simple fun experiment for old web exploration and old-school deterministic generative algorithms

--The project is still in its very early stages--

To launch the program:
```
python fanzi-crawler.py "your query" [MADNESS] [DEPTH] [YEAR] [DIMENSIONS]
```
MADNESS is a parameter for how randomized the search is going to be with respect to the user's query. The process calls datamuse to obtain a list (more or less long, depending on the parameter) of semantically related words that will become queries as well.

DEPTH indicates a parameter of how many layers of links the crawler is going to explore (despite a decay value, higher values will still provoke an exponential growth)

YEAR the desired year, or at least the closest one if nothing returns. This propagates also for the other links. If none: 2015.

DIMENSIONS in pixels. If None: 1748 x 2480.

Every result is both printed in html format (with linked sources!!) and .png.

The page_organizer.py organizes text and images in the page wiht bins packing solving algorithm.

There is still a work in progress algorithm that should filter ads, banners and unrelated stuff.
