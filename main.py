import os
import sys
import subprocess
import importlib
import importlib.util

def _ensure_requirements() -> None:
    req_file = os.path.join(os.path.dirname(os.path.abspath(__file__)), "requirements.txt")
    if not os.path.isfile(req_file):
        return
    import_aliases = {"beautifulsoup4": "bs4"}
    missing: list[str] = []
    with open(req_file, encoding="utf-8") as f:
        for line in f:
            pkg = line.strip().split("#", 1)[0].strip()
            if not pkg:
                continue
            base = pkg.split("==")[0].split(">=")[0].split("<")[0].split("~=")[0].strip()
            mod = import_aliases.get(base.lower(), base.replace("-", "_"))
            if importlib.util.find_spec(mod) is None:
                missing.append(pkg)
    if not missing:
        return
    print(f"[bootstrap] Installing missing packages: {', '.join(missing)}", file=sys.stderr)
    try:
        subprocess.check_call([sys.executable, "-m", "pip", "install", "-r", req_file])
    except subprocess.CalledProcessError as e:
        print(f"[bootstrap] pip install failed: {e}", file=sys.stderr)
        sys.exit(1)
    importlib.invalidate_caches()

_ensure_requirements()

import requests
from requests import Session
from bs4 import BeautifulSoup
import re
import time
import hashlib
from collections import deque
from urllib.parse import urljoin, urlparse, urlunparse
import logging
import argparse
from urllib import robotparser
from typing import Deque, Set, List, Tuple, Optional, Dict, TextIO, Pattern
from dataclasses import dataclass

logger = logging.getLogger(__name__)
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')

DEFAULT_USER_AGENT: str = 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/58.0.3029.110 Safari/537.3'
DEFAULT_DELAY: float = 1.0
DEFAULT_MAX_PAGES: int = 100
DEFAULT_MAX_DEPTH: int = 0  # 0 = unlimited
REQUEST_TIMEOUT: int = 15
MAX_RETRIES: int = 2
RETRY_BACKOFF: float = 1.5
PAGE_SEPARATOR: str = "=" * 60
IGNORED_EXTENSIONS: Set[str] = {
    '.pdf', '.doc', '.docx', '.xls', '.xlsx', '.ppt', '.pptx',
    '.zip', '.rar', '.tar', '.gz', '.7z',
    '.jpg', '.jpeg', '.png', '.gif', '.bmp', '.svg', '.webp',
    '.mp3', '.mp4', '.avi', '.mov', '.wmv',
    '.js', '.css', '.xml', '.json', '.csv', '.txt'
}

def _pick_parser() -> str:
    if importlib.util.find_spec("lxml") is not None:
        return "lxml"
    return "html.parser"

HTML_PARSER: str = _pick_parser()

@dataclass
class CrawlConfig:
    start_url: str
    max_pages: int
    max_depth: int
    delay: float
    user_agent: str
    output_file: Optional[str]
    skip_save_prompt: bool
    stream: bool
    include_subdomains: bool
    include_pattern: Optional[Pattern[str]]
    exclude_pattern: Optional[Pattern[str]]

def normalize_url(url: str) -> str:
    parsed = urlparse(url)
    path = parsed.path
    if len(path) > 1 and path.endswith('/'):
        path = path.rstrip('/')
    path = path or '/'
    return urlunparse(parsed._replace(path=path, fragment=""))

def is_likely_html(url: str) -> bool:
    try:
        parsed = urlparse(url)
        if parsed.scheme not in ('http', 'https'):
            return False
        path = parsed.path.lower()
        return not any(path.endswith(ext) for ext in IGNORED_EXTENSIONS)
    except ValueError:
        logger.debug(f"Could not parse URL for HTML check: {url}")
        return False

def extract_page(html_content: str) -> Tuple[str, str, Optional[BeautifulSoup]]:
    """Returns (text, title, soup)."""
    if not html_content:
        return "", "", None
    try:
        soup = BeautifulSoup(html_content, HTML_PARSER)
        for tag in soup(['script', 'style', 'noscript']):
            tag.decompose()
        text = soup.get_text(separator=' ', strip=True)
        title = ""
        if soup.title and soup.title.string:
            title = soup.title.string.strip()
        return text, title, soup
    except Exception as e:
        logger.error(f"Error parsing HTML: {e}")
        return "", "", None

def get_safe_filename(url: str) -> str:
    safe_name = re.sub(r'^https?://|^ftps?://', '', url)
    safe_name = re.sub(r'[^\w.-]+', '_', safe_name).strip('_.-')[:100]
    if not safe_name.lower().endswith('.txt'):
        safe_name += ".txt"
    return safe_name if safe_name != ".txt" else "extracted_text.txt"

def resolve_output_path(filename: str) -> Optional[str]:
    """Sanitize filename, ensure parent dir exists. Returns absolute path or None on error."""
    directory, name = os.path.split(filename)
    if not name:
        logger.error("Filename is empty or invalid.")
        return None
    safe_name = re.sub(r'[<>:"|?*\x00-\x1f]', '_', name)
    full_path = os.path.abspath(os.path.join(directory, safe_name) if directory else safe_name)
    parent = os.path.dirname(full_path)
    if parent:
        try:
            os.makedirs(parent, exist_ok=True)
        except OSError as e:
            logger.error(f"Could not create directory '{parent}': {e}")
            return None
    return full_path

def format_page_block(url: str, title: str, text: str) -> str:
    return f"{PAGE_SEPARATOR}\nURL: {url}\nTITLE: {title}\n{PAGE_SEPARATOR}\n{text}\n"

class Crawler:
    def __init__(self, config: CrawlConfig, output_handle: Optional[TextIO] = None):
        self.config = config
        parsed = urlparse(config.start_url)
        self.base_host = (parsed.hostname or '').lower()
        self.urls_to_visit: Deque[Tuple[str, int]] = deque([(config.start_url, 0)])
        self.queued: Set[str] = {config.start_url}
        self.visited_urls: Set[str] = set()
        self.content_hashes: Set[str] = set()
        self.robots_cache: Dict[str, Optional[robotparser.RobotFileParser]] = {}
        self.session = self._init_session()
        self.effective_delay = config.delay
        self.output_handle = output_handle
        self.pages_extracted = 0

    def _init_session(self) -> Session:
        session = Session()
        session.headers.update({
            'User-Agent': self.config.user_agent,
            'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8',
            'Accept-Language': 'en-US,en;q=0.5',
            'Accept-Encoding': 'gzip, deflate',
        })
        return session

    def _host_matches(self, host: str) -> bool:
        host = (host or '').lower()
        if not host:
            return False
        if host == self.base_host:
            return True
        if self.config.include_subdomains and host.endswith('.' + self.base_host):
            return True
        return False

    def _get_robots_parser(self, url: str) -> Optional[robotparser.RobotFileParser]:
        parsed = urlparse(url)
        base = f"{parsed.scheme}://{parsed.netloc}"
        if base in self.robots_cache:
            return self.robots_cache[base]

        robots_url = urljoin(base, "/robots.txt")
        parser = robotparser.RobotFileParser()
        parser.set_url(robots_url)
        try:
            logger.info(f"Fetching robots.txt: {robots_url}")
            resp = self.session.get(robots_url, timeout=REQUEST_TIMEOUT)
            if resp.status_code >= 400:
                logger.info(f"robots.txt unavailable ({resp.status_code}); allowing all.")
                self.robots_cache[base] = None
                return None
            parser.parse(resp.text.splitlines())
            self.robots_cache[base] = parser
            return parser
        except requests.RequestException as e:
            logger.warning(f"Could not fetch robots.txt at {robots_url}: {e}")
            self.robots_cache[base] = None
            return None

    def _can_fetch(self, url: str) -> bool:
        parser = self._get_robots_parser(url)
        if parser is None:
            return True
        try:
            return parser.can_fetch(self.config.user_agent, url)
        except Exception as e:
            logger.warning(f"can_fetch error for {url}: {e}")
            return True

    def _apply_robots_rate(self, url: str) -> None:
        parser = self._get_robots_parser(url)
        if parser is None:
            return
        try:
            cd = parser.crawl_delay(self.config.user_agent)
            if cd and float(cd) > self.effective_delay:
                logger.info(f"Honoring robots Crawl-delay {cd}s for {urlparse(url).netloc}")
                self.effective_delay = float(cd)
        except Exception:
            pass
        try:
            rr = parser.request_rate(self.config.user_agent)
            if rr and rr.requests > 0:
                per_req = rr.seconds / rr.requests
                if per_req > self.effective_delay:
                    logger.info(f"Honoring robots Request-rate {rr.requests}/{rr.seconds}s "
                                f"= {per_req:.2f}s/req for {urlparse(url).netloc}")
                    self.effective_delay = per_req
        except Exception:
            pass

    def _fetch(self, url: str) -> Optional[str]:
        logger.info(f"Fetching: {url}")
        last_err: Optional[Exception] = None
        for attempt in range(MAX_RETRIES + 1):
            try:
                resp = self.session.get(url, timeout=REQUEST_TIMEOUT)
                if resp.status_code in (429, 500, 502, 503, 504):
                    raise requests.HTTPError(f"retryable status {resp.status_code}")
                resp.raise_for_status()
                ctype = resp.headers.get('Content-Type', '').lower()
                if 'text/html' not in ctype and 'application/xhtml' not in ctype:
                    logger.info(f"Skipping non-HTML ({ctype}): {url}")
                    return None
                # Force apparent encoding when server sent no/wrong charset
                if not resp.encoding or resp.encoding.lower() == 'iso-8859-1':
                    resp.encoding = resp.apparent_encoding or resp.encoding
                return resp.text
            except (requests.Timeout, requests.ConnectionError, requests.HTTPError) as e:
                last_err = e
                if attempt < MAX_RETRIES:
                    wait = RETRY_BACKOFF ** attempt
                    logger.warning(f"Fetch failed ({e}); retry {attempt+1}/{MAX_RETRIES} in {wait:.1f}s")
                    time.sleep(wait)
                    continue
            except requests.RequestException as e:
                last_err = e
                break
        logger.error(f"Giving up on {url}: {last_err}")
        return None

    def crawl(self) -> List[str]:
        all_text: List[str] = []
        crawled = 0

        domain_label = f"*.{self.base_host}" if self.config.include_subdomains else self.base_host
        logger.info(f"Start: {self.config.start_url} | domain: {domain_label} | parser: {HTML_PARSER}")
        logger.info(f"Max pages: {self.config.max_pages} | max depth: "
                    f"{self.config.max_depth or 'unlimited'} | delay: {self.config.delay}s")

        while self.urls_to_visit and crawled < self.config.max_pages:
            url, depth = self.urls_to_visit.popleft()
            self.queued.discard(url)

            if not self._should_visit(url):
                continue

            self.visited_urls.add(url)
            crawled += 1
            logger.info(f"[{crawled}/{self.config.max_pages}] (d={depth}) {url}")

            self._apply_robots_rate(url)
            html = self._fetch(url)

            if html:
                text, title, soup = extract_page(html)
                if text:
                    text_hash = hashlib.sha1(text.encode('utf-8', errors='ignore')).hexdigest()
                    if text_hash in self.content_hashes:
                        logger.info(f"Duplicate content; skipping page body for {url}")
                    else:
                        self.content_hashes.add(text_hash)
                        block = format_page_block(url, title, text)
                        if self.output_handle is not None:
                            self.output_handle.write(block + "\n")
                            self.output_handle.flush()
                        else:
                            all_text.append(block)
                        self.pages_extracted += 1
                        logger.info(f"Extracted ~{len(text)} chars (title: {title!r}).")
                if soup:
                    self._enqueue_links(soup, url, depth)
                if self.effective_delay > 0:
                    time.sleep(self.effective_delay)
            else:
                logger.warning(f"No content for {url}")

        logger.info(f"Done. Visited {len(self.visited_urls)} URLs, "
                    f"extracted {self.pages_extracted} pages (limit {self.config.max_pages}).")
        return all_text

    def _passes_filters(self, url: str) -> bool:
        if self.config.include_pattern and not self.config.include_pattern.search(url):
            return False
        if self.config.exclude_pattern and self.config.exclude_pattern.search(url):
            return False
        return True

    def _should_visit(self, url: str) -> bool:
        if url in self.visited_urls:
            return False
        if not is_likely_html(url):
            self.visited_urls.add(url)
            return False
        parsed = urlparse(url)
        if not self._host_matches(parsed.hostname or ''):
            self.visited_urls.add(url)
            return False
        if not self._passes_filters(url):
            self.visited_urls.add(url)
            return False
        if not self._can_fetch(url):
            logger.warning(f"Disallowed by robots.txt: {url}")
            self.visited_urls.add(url)
            return False
        return True

    def _enqueue_links(self, soup: BeautifulSoup, current_url: str, current_depth: int) -> None:
        if self.config.max_depth and current_depth >= self.config.max_depth:
            return
        added = 0
        next_depth = current_depth + 1
        for link in soup.find_all('a', href=True):
            href_attr = link.get('href')
            if not isinstance(href_attr, str):
                continue
            href = href_attr.strip()
            if not href or href.startswith(('mailto:', 'tel:', 'javascript:', '#')):
                continue
            try:
                norm = normalize_url(urljoin(current_url, href))
            except ValueError:
                continue

            parsed = urlparse(norm)
            if (parsed.scheme in ('http', 'https')
                    and self._host_matches(parsed.hostname or '')
                    and norm not in self.visited_urls
                    and norm not in self.queued
                    and is_likely_html(norm)
                    and self._passes_filters(norm)):
                self.urls_to_visit.append((norm, next_depth))
                self.queued.add(norm)
                added += 1
        logger.info(f"Found {added} new links.")

def _compile_pattern(pat: Optional[str], label: str) -> Optional[Pattern[str]]:
    if not pat:
        return None
    try:
        return re.compile(pat)
    except re.error as e:
        logger.error(f"Invalid {label} regex {pat!r}: {e}")
        sys.exit(2)

def main() -> None:
    parser = argparse.ArgumentParser(description="Scrape text content from a website, staying within the same domain.")
    parser.add_argument("start_url", help="The starting URL for the crawl.")
    parser.add_argument("-m", "--max-pages", type=int, default=DEFAULT_MAX_PAGES,
                        help=f"Maximum pages to crawl (default: {DEFAULT_MAX_PAGES}).")
    parser.add_argument("--max-depth", type=int, default=DEFAULT_MAX_DEPTH,
                        help="Maximum link depth from start URL (0 = unlimited).")
    parser.add_argument("-o", "--output-file", type=str, default=None,
                        help="Output filename (default: derived from start URL).")
    parser.add_argument("-d", "--delay", type=float, default=DEFAULT_DELAY,
                        help=f"Delay between requests (default: {DEFAULT_DELAY}).")
    parser.add_argument("-ua", "--user-agent", type=str, default=DEFAULT_USER_AGENT,
                        help="User-Agent string.")
    parser.add_argument("--skip-save-prompt", action="store_true",
                        help="Save automatically without prompting.")
    parser.add_argument("--stream", action="store_true",
                        help="Stream pages to disk as crawled (low memory; implies --skip-save-prompt).")
    parser.add_argument("--include-subdomains", action="store_true",
                        help="Crawl subdomains of the start URL host.")
    parser.add_argument("--include", type=str, default=None,
                        help="Only crawl URLs matching this regex.")
    parser.add_argument("--exclude", type=str, default=None,
                        help="Skip URLs matching this regex.")
    parser.add_argument("-v", "--verbose", action="store_true",
                        help="Enable debug logging.")

    args = parser.parse_args()

    log_level = logging.DEBUG if args.verbose else logging.INFO
    logging.getLogger().setLevel(log_level)
    for handler in logging.getLogger().handlers:
        handler.setLevel(log_level)

    start_url = args.start_url
    if not start_url.startswith(('http://', 'https://')):
        start_url = 'https://' + start_url
    start_url = normalize_url(start_url)

    if not urlparse(start_url).hostname:
        logger.error(f"Invalid start URL: {args.start_url}")
        return

    if args.max_pages <= 0 or args.delay < 0 or args.max_depth < 0:
        logger.error("max-pages must be > 0; delay and max-depth must be >= 0.")
        return

    config = CrawlConfig(
        start_url=start_url,
        max_pages=args.max_pages,
        max_depth=args.max_depth,
        delay=args.delay,
        user_agent=args.user_agent,
        output_file=args.output_file,
        skip_save_prompt=args.skip_save_prompt or args.stream,
        stream=args.stream,
        include_subdomains=args.include_subdomains,
        include_pattern=_compile_pattern(args.include, "--include"),
        exclude_pattern=_compile_pattern(args.exclude, "--exclude"),
    )

    out_name = config.output_file or get_safe_filename(config.start_url)

    output_handle: Optional[TextIO] = None
    output_path: Optional[str] = None
    if config.stream:
        output_path = resolve_output_path(out_name)
        if output_path is None:
            return
        try:
            output_handle = open(output_path, 'w', encoding='utf-8')
        except OSError as e:
            logger.error(f"Cannot open '{output_path}' for writing: {e}")
            return
        logger.info(f"Streaming output to: {output_path}")

    crawler = Crawler(config, output_handle=output_handle)
    try:
        try:
            buffered = crawler.crawl()
        except KeyboardInterrupt:
            logger.info("Interrupted by user.")
            buffered = []
    finally:
        if output_handle is not None:
            output_handle.close()

    if config.stream:
        if crawler.pages_extracted == 0:
            logger.info("No text extracted; output file is empty.")
        else:
            logger.info(f"Wrote {crawler.pages_extracted} pages to {output_path}.")
        return

    if not buffered:
        logger.info("No text extracted.")
        return

    full_text = "\n".join(buffered)
    logger.info(f"Total text: {len(full_text)} chars across {crawler.pages_extracted} pages.")

    do_save = config.skip_save_prompt
    if not do_save:
        try:
            ans = input(f"Save text to '{out_name}'? (y/N): ").strip().lower()
            do_save = ans.startswith('y')
        except (EOFError, KeyboardInterrupt):
            logger.info("\nSkipping save.")
            do_save = False

    if not do_save:
        logger.info("Skipping save.")
        return

    final_path = resolve_output_path(out_name)
    if final_path is None:
        return
    try:
        with open(final_path, 'w', encoding='utf-8') as f:
            f.write(full_text)
        logger.info(f"Saved to: {final_path}")
    except OSError as e:
        logger.error(f"Error saving '{final_path}': {e}")

if __name__ == "__main__":
    main()
