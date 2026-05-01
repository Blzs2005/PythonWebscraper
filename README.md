# Simple Documentation Scraper

## Description

This Python script is a basic web scraper designed to extract text content primarily from API documentation websites or similar structured sites. It starts from a given URL, crawls internal links within the same domain (optionally including subdomains), extracts text from each page, deduplicates repeated content, and writes the result to a single output file — either buffered in memory or streamed page-by-page to disk.

## Features

*   **Targeted Crawling:** Starts at a specified URL and follows links within the same domain. Optionally widens scope to subdomains via `--include-subdomains`.
*   **Depth Control:** Limits how far from the start URL the crawl walks via `--max-depth` (in addition to the page count cap).
*   **URL Filtering:** `--include` / `--exclude` regex flags pre-filter URLs before fetching, e.g. to skip pagination, tag pages, or archive sections.
*   **Text Extraction:** Uses BeautifulSoup with `lxml` (auto-detected) and falls back to the stdlib `html.parser`. Strips `<script>`, `<style>`, and `<noscript>` before extraction. Captures the `<title>` of each page.
*   **Content Deduplication:** SHA-1 hashes the extracted text per page; duplicate bodies (common for nav-only pages) are skipped.
*   **Streaming Output Mode:** With `--stream`, each page is written to the output file as it is crawled and `flush()`ed immediately, keeping memory flat for large crawls.
*   **Encoding Handling:** Forces `apparent_encoding` when the server omits a charset or sends the misleading default `iso-8859-1`, avoiding mangled UTF-8 text.
*   **Polite Defaults:** Configurable inter-request delay (`--delay`); honors `robots.txt` `Disallow`, `Crawl-delay`, **and** `Request-rate` directives, automatically raising the effective delay if the site requests slower crawling.
*   **Realistic HTTP Session:** `requests.Session` reuses connections and ships sensible default headers (`User-Agent`, `Accept`, `Accept-Language`, `Accept-Encoding`).
*   **Retries with Backoff:** Transient failures (timeouts, connection errors, HTTP 429/5xx) are retried up to `MAX_RETRIES` (default 2) with exponential backoff.
*   **Auto-Bootstrap:** On first run the script reads `requirements.txt` and `pip install`s any missing dependencies into the active Python environment — no manual install step required.
*   **Robust Output Path:** Sanitizes the output filename, preserves any user-given directory, and creates parent directories as needed.
*   **Logging:** Uses Python's `logging` module with a `-v` debug flag; logs which HTML parser was selected, depth/page progress, dedup events, and robots policy decisions.

## Requirements

*   Python 3.8+
*   Required libraries: `requests`, `beautifulsoup4` (declared in `requirements.txt`)
*   Optional: `lxml` — installed automatically only if you add it to `requirements.txt`; otherwise the stdlib parser is used.

The script auto-installs anything in `requirements.txt` that is missing, so a fresh checkout typically needs no setup beyond:

```bash
python main.py <start_url>
```

To install manually anyway:

```bash
pip install -r requirements.txt
```

## Usage

```bash
python main.py <start_url> [options]
```

**Examples:**

```bash
# Crawl docs.example.com, cap at 50 pages, save to my_docs.txt
python main.py https://docs.example.com -m 50 -o my_docs.txt

# Crawl with a 2-second delay, no save prompt, verbose logging
python main.py https://api.another-site.org/v1/ --delay 2 --skip-save-prompt -v

# Stream a large crawl directly to disk (low memory), include subdomains, depth-limited
python main.py https://example.com --stream --include-subdomains --max-depth 3 -o site.txt

# Only crawl URLs containing "/docs/" and never crawl URLs matching "/blog/"
python main.py https://example.com --include "/docs/" --exclude "/blog/"
```

## Configuration (Command-Line Arguments)

| Flag | Description |
|------|-------------|
| `start_url` | (Required) The starting URL for the crawl. Scheme is auto-prefixed to `https://` if omitted. |
| `-m`, `--max-pages` | Maximum number of pages to crawl (default: `100`). |
| `--max-depth` | Maximum link depth from the start URL (`0` = unlimited, default). |
| `-o`, `--output-file` | Output filename (default: derived from start URL). May include a directory path. |
| `-d`, `--delay` | Delay in seconds between requests (default: `1.0`). May be raised automatically by `robots.txt`. |
| `-ua`, `--user-agent` | User-Agent string (default: a common browser UA). |
| `--skip-save-prompt` | Save automatically without the interactive confirmation prompt. |
| `--stream` | Stream pages to disk as they are crawled. Implies `--skip-save-prompt`. Recommended for large crawls. |
| `--include-subdomains` | Also crawl subdomains of the start URL host (e.g. `*.example.com`). |
| `--include <regex>` | Only crawl URLs matching this Python regex. |
| `--exclude <regex>` | Skip URLs matching this Python regex. |
| `-v`, `--verbose` | Enable debug logging. |

## Output Format

Each page is written as a block prefixed by a header so downstream tools can split on the URL boundary:

```
============================================================
URL: https://example.com/page
TITLE: Page Title
============================================================
<extracted text...>
```

In buffered mode the blocks are joined with blank lines; in `--stream` mode each block is written and flushed individually as it is crawled.

## Important Note: Refining Text Extraction

The script extracts *all* visible text after stripping `<script>`, `<style>`, and `<noscript>`. This still includes navigation menus, footers, sidebars, etc. For best results inspect the target site and modify `extract_page` in `main.py` to target the primary content container (e.g. `<main>`, `<article>`, `<div class="content">`) before calling `get_text()`.

## Limitations

*   **No JavaScript Rendering:** Only the initial HTML source is fetched. Content injected by client-side JS will be missed.
*   **Basic `robots.txt`:** Uses Python's stdlib `urllib.robotparser`. It handles `Disallow`, `Crawl-delay`, and `Request-rate`, but very complex or non-standard directives may be misread.
*   **HTML Structure Dependent:** Output quality depends heavily on the target site's HTML and on whether you customize `extract_page`.
*   **Single-Threaded:** Pages are fetched sequentially. This is intentional for politeness — there is no concurrency option.
*   **Auto-Bootstrap Side Effect:** The first run may invoke `pip install` against your active environment. Use a virtual environment if you want to keep dependencies isolated.

## Ethical Considerations

*   **Be Respectful:** Use a reasonable `--delay` and respect `Crawl-delay`/`Request-rate` directives the script reads from `robots.txt`.
*   **Check `robots.txt`:** The script honors basic rules automatically but you should still review the site's policy yourself.
*   **Terms of Service:** Verify scraping is permitted by the target site's ToS.
*   **Identify Yourself:** Override `--user-agent` with a descriptive string (and optional contact info) when running against sites that ask for it.
*   **Disclaimer:** The author is not responsible for any misuse of this script or for any violations of website terms of service resulting from its use. Use this script responsibly and ethically.

## License

Please refer to the `LICENSE` file for information on usage rights and limitations.
