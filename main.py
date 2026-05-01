import requests
from requests import Session
from bs4 import BeautifulSoup
import os
import re
import time
from collections import deque
from urllib.parse import urljoin, urlparse, urlunparse
import logging
import argparse
from urllib import robotparser
from typing import Deque, Set, List, Tuple, Optional, Dict
from dataclasses import dataclass, field

logger = logging.getLogger(__name__)
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')

DEFAULT_USER_AGENT: str = 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/58.0.3029.110 Safari/537.3'
DEFAULT_DELAY: float = 1.0
DEFAULT_MAX_PAGES: int = 100
REQUEST_TIMEOUT: int = 15
MAX_RETRIES: int = 2
RETRY_BACKOFF: float = 1.5
IGNORED_EXTENSIONS: Set[str] = {
    '.pdf', '.doc', '.docx', '.xls', '.xlsx', '.ppt', '.pptx',
    '.zip', '.rar', '.tar', '.gz', '.7z',
    '.jpg', '.jpeg', '.png', '.gif', '.bmp', '.svg', '.webp',
    '.mp3', '.mp4', '.avi', '.mov', '.wmv',
    '.js', '.css', '.xml', '.json', '.csv', '.txt'
}

@dataclass
class CrawlConfig:
    start_url: str
    max_pages: int
    delay: float
    user_agent: str
    output_file: Optional[str]
    skip_save_prompt: bool

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

def extract_text_from_html(html_content: str) -> Tuple[str, Optional[BeautifulSoup]]:
    if not html_content:
        return "", None
    try:
        soup = BeautifulSoup(html_content, 'html.parser')
        for tag in soup(['script', 'style', 'noscript']):
            tag.decompose()
        text = soup.get_text(separator=' ', strip=True)
        return text, soup
    except Exception as e:
        logger.error(f"Error parsing HTML: {e}")
        return "", None

def get_safe_filename(url: str) -> str:
    safe_name = re.sub(r'^https?://|^ftps?://', '', url)
    safe_name = re.sub(r'[^\w.-]+', '_', safe_name).strip('_.-')[:100]
    if not safe_name.lower().endswith('.txt'):
        safe_name += ".txt"
    return safe_name if safe_name != ".txt" else "extracted_text.txt"

def save_text_to_file(text: str, filename: str) -> bool:
    try:
        directory, name = os.path.split(filename)
        if not name:
            logger.error("Filename is empty or invalid.")
            return False
        # sanitize name only; preserve user-given dir
        safe_name = re.sub(r'[<>:"|?*\x00-\x1f]', '_', name)
        full_path = os.path.abspath(os.path.join(directory, safe_name) if directory else safe_name)
        os.makedirs(os.path.dirname(full_path), exist_ok=True) if os.path.dirname(full_path) else None
        with open(full_path, 'w', encoding='utf-8') as f:
            f.write(text)
        logger.info(f"Saved extracted text to: {full_path}")
        return True
    except OSError as e:
        logger.error(f"Error saving file '{filename}': {e}")
        return False

class Crawler:
    def __init__(self, config: CrawlConfig):
        self.config = config
        self.base_domain = urlparse(config.start_url).netloc
        self.urls_to_visit: Deque[str] = deque([config.start_url])
        self.queued: Set[str] = {config.start_url}
        self.visited_urls: Set[str] = set()
        self.robots_cache: Dict[str, Optional[robotparser.RobotFileParser]] = {}
        self.session = self._init_session()
        self.effective_delay = config.delay

    def _init_session(self) -> Session:
        session = Session()
        session.headers.update({'User-Agent': self.config.user_agent})
        return session

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

    def _apply_crawl_delay(self, url: str) -> None:
        parser = self._get_robots_parser(url)
        if parser is None:
            return
        try:
            cd = parser.crawl_delay(self.config.user_agent)
            if cd and cd > self.effective_delay:
                logger.info(f"Honoring robots Crawl-delay {cd}s for {urlparse(url).netloc}")
                self.effective_delay = float(cd)
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

        logger.info(f"Start: {self.config.start_url} | domain: {self.base_domain}")
        logger.info(f"Max: {self.config.max_pages} | delay: {self.config.delay}s | UA: {self.config.user_agent}")

        while self.urls_to_visit and crawled < self.config.max_pages:
            url = self.urls_to_visit.popleft()
            self.queued.discard(url)

            if not self._should_visit(url):
                continue

            self.visited_urls.add(url)
            crawled += 1
            logger.info(f"[{crawled}/{self.config.max_pages}] {url}")

            self._apply_crawl_delay(url)
            html = self._fetch(url)

            if html:
                text, soup = extract_text_from_html(html)
                if text:
                    all_text.append(text)
                    logger.info(f"Extracted ~{len(text)} chars.")
                if soup:
                    self._find_new_links(soup, url)
                if self.effective_delay > 0:
                    time.sleep(self.effective_delay)
            else:
                logger.warning(f"No content for {url}")

        logger.info(f"Done. Visited {len(self.visited_urls)} pages (limit {self.config.max_pages}).")
        return all_text

    def _should_visit(self, url: str) -> bool:
        if url in self.visited_urls:
            return False
        if not is_likely_html(url):
            self.visited_urls.add(url)
            return False
        if urlparse(url).netloc != self.base_domain:
            self.visited_urls.add(url)
            return False
        if not self._can_fetch(url):
            logger.warning(f"Disallowed by robots.txt: {url}")
            self.visited_urls.add(url)
            return False
        return True

    def _find_new_links(self, soup: BeautifulSoup, current_url: str) -> None:
        added = 0
        for link in soup.find_all('a', href=True):
            href = link.get('href', '').strip()
            if not href or href.startswith(('mailto:', 'tel:', 'javascript:', '#')):
                continue
            try:
                absolute = urljoin(current_url, href)
                norm = normalize_url(absolute)
            except ValueError:
                continue

            parsed = urlparse(norm)
            if (parsed.scheme in ('http', 'https')
                    and parsed.netloc == self.base_domain
                    and norm not in self.visited_urls
                    and norm not in self.queued
                    and is_likely_html(norm)):
                self.urls_to_visit.append(norm)
                self.queued.add(norm)
                added += 1
        logger.info(f"Found {added} new links.")

def main() -> None:
    parser = argparse.ArgumentParser(description="Scrape text content from a website, staying within the same domain.")
    parser.add_argument("start_url", help="The starting URL for the crawl.")
    parser.add_argument("-m", "--max-pages", type=int, default=DEFAULT_MAX_PAGES,
                        help=f"Maximum pages to crawl (default: {DEFAULT_MAX_PAGES}).")
    parser.add_argument("-o", "--output-file", type=str, default=None,
                        help="Output filename (default: derived from start URL).")
    parser.add_argument("-d", "--delay", type=float, default=DEFAULT_DELAY,
                        help=f"Delay between requests (default: {DEFAULT_DELAY}).")
    parser.add_argument("-ua", "--user-agent", type=str, default=DEFAULT_USER_AGENT,
                        help="User-Agent string.")
    parser.add_argument("--skip-save-prompt", action="store_true",
                        help="Save automatically without prompting.")
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

    if not urlparse(start_url).netloc:
        logger.error(f"Invalid start URL: {args.start_url}")
        return

    if args.max_pages <= 0 or args.delay < 0:
        logger.error("max-pages must be > 0 and delay must be >= 0")
        return

    config = CrawlConfig(
        start_url=start_url,
        max_pages=args.max_pages,
        delay=args.delay,
        user_agent=args.user_agent,
        output_file=args.output_file,
        skip_save_prompt=args.skip_save_prompt,
    )

    crawler = Crawler(config)
    try:
        pages = crawler.crawl()
    except KeyboardInterrupt:
        logger.info("Interrupted by user; saving what was gathered.")
        pages = []

    if not pages:
        logger.info("No text extracted.")
        return

    full_text = "\n\n--- Page Break ---\n\n".join(pages)
    logger.info(f"Total text: {len(full_text)} chars.")

    out = config.output_file or get_safe_filename(config.start_url)

    do_save = config.skip_save_prompt
    if not do_save:
        try:
            ans = input(f"Save text to '{out}'? (y/N): ").strip().lower()
            do_save = ans.startswith('y')
        except (EOFError, KeyboardInterrupt):
            logger.info("\nSkipping save.")
            do_save = False

    if do_save:
        save_text_to_file(full_text, out)
    else:
        logger.info("Skipping save.")

if __name__ == "__main__":
    main()
