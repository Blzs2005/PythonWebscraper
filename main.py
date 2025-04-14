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

logger = logging.getLogger(__name__)
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')

DEFAULT_USER_AGENT: str = 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/58.0.3029.110 Safari/537.3'
DEFAULT_DELAY: float = 1.0
DEFAULT_MAX_PAGES: int = 100
REQUEST_TIMEOUT: int = 15
IGNORED_EXTENSIONS: Set[str] = {
    '.pdf', '.doc', '.docx', '.xls', '.xlsx', '.ppt', '.pptx',
    '.zip', '.rar', '.tar', '.gz', '.7z',
    '.jpg', '.jpeg', '.png', '.gif', '.bmp', '.svg', '.webp',
    '.mp3', '.mp4', '.avi', '.mov', '.wmv',
    '.js', '.css', '.xml', '.json', '.csv', '.txt'
}

robots_parsers: Dict[str, Optional[robotparser.RobotFileParser]] = {}

def normalize_url(url: str) -> str:
    parsed = urlparse(url)
    path: str = parsed.path
    if len(path) > 1 and path.endswith('/'):
        path = path.rstrip('/')
    path = path if path else '/'
    normalized_parsed = parsed._replace(path=path, fragment="")
    return urlunparse(normalized_parsed)

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

def can_fetch(url: str, user_agent: str) -> bool:
    parsed_url = urlparse(url)
    base_url: str = f"{parsed_url.scheme}://{parsed_url.netloc}"
    robots_url: str = urljoin(base_url, "/robots.txt")

    parser: Optional[robotparser.RobotFileParser] = robots_parsers.get(base_url)

    if base_url in robots_parsers:
        parser = robots_parsers[base_url]
        if parser is None:
            return True
    else:
        parser = robotparser.RobotFileParser()
        parser.set_url(robots_url)
        try:
            logger.info(f"  Fetching robots.txt from: {robots_url}")
            parser.read()
            robots_parsers[base_url] = parser
        except Exception as e:
            logger.warning(f"  Could not fetch or parse robots.txt at {robots_url}: {e}")
            robots_parsers[base_url] = None
            return True

    if parser:
        try:
            return parser.can_fetch(user_agent, url)
        except Exception as e:
            logger.warning(f"  Error checking can_fetch for {url} with robots.txt parser: {e}")
            return True
    else:
        return True

def fetch_url_content(session: Session, url: str) -> Optional[str]:
    logger.info(f"  Fetching: {url}")
    try:
        response = session.get(url, timeout=REQUEST_TIMEOUT)
        response.raise_for_status()

        content_type: str = response.headers.get('Content-Type', '').lower()
        if 'text/html' not in content_type:
            logger.info(f"  Skipping non-HTML content ({content_type}) at: {url}")
            return None
        return response.text
    except requests.exceptions.Timeout:
        logger.warning(f"  Timeout error fetching URL {url}")
        return None
    except requests.exceptions.HTTPError as e:
        logger.warning(f"  HTTP error fetching URL {url}: {e}")
        return None
    except requests.exceptions.ConnectionError as e:
        logger.warning(f"  Connection error fetching URL {url}: {e}")
        return None
    except requests.exceptions.RequestException as e:
        logger.error(f"  Error fetching URL {url}: {e}")
        return None
    except Exception as e:
        logger.error(f"  An unexpected error occurred during fetch for {url}: {e}")
        return None

def extract_text_from_html(html_content: str) -> Tuple[str, Optional[BeautifulSoup]]:
    if not html_content:
        return "", None
    try:
        soup = BeautifulSoup(html_content, 'html.parser')
        text: str = soup.get_text(separator=' ', strip=True)
        return text, soup
    except Exception as e:
        logger.error(f"  Error parsing HTML: {e}")
        return "", None

def get_safe_filename(url: str) -> str:
    safe_name: str = re.sub(r'^https?://|^ftps?://', '', url)
    safe_name = re.sub(r'[^\w.-]+', '_', safe_name)
    safe_name = safe_name.strip('_.-')
    safe_name = safe_name[:100]
    if not safe_name.lower().endswith('.txt'):
        safe_name += ".txt"
    return safe_name if safe_name != ".txt" else "extracted_text.txt"

def save_text_to_file(text: str, filename: str) -> bool:
    try:
        safe_filename: str = os.path.basename(filename)
        full_path: str = os.path.abspath(safe_filename)
        with open(full_path, 'w', encoding='utf-8') as f:
            f.write(text)
        logger.info(f"Successfully saved combined extracted text to: {full_path}")
        return True
    except IOError as e:
        logger.error(f"Error saving file '{filename}': {e}")
        return False
    except Exception as e:
        logger.error(f"An unexpected error occurred during saving to '{filename}': {e}")
        return False

def main() -> None:
    parser = argparse.ArgumentParser(description="Scrape text content from a website, staying within the same domain.")
    parser.add_argument("start_url", help="The starting URL for the crawl.")
    parser.add_argument("-m", "--max-pages", type=int, default=DEFAULT_MAX_PAGES,
                        help=f"Maximum number of pages to crawl (default: {DEFAULT_MAX_PAGES}).")
    parser.add_argument("-o", "--output-file", type=str, default=None,
                        help="Filename to save the combined extracted text (default: generated from start URL).")
    parser.add_argument("-d", "--delay", type=float, default=DEFAULT_DELAY,
                        help=f"Delay in seconds between requests (default: {DEFAULT_DELAY}).")
    parser.add_argument("-ua", "--user-agent", type=str, default=DEFAULT_USER_AGENT,
                        help="User-Agent string to use for requests.")
    parser.add_argument("--skip-save-prompt", action="store_true",
                        help="Skip the prompt and save automatically if text is extracted.")
    parser.add_argument("-v", "--verbose", action="store_true",
                        help="Enable debug logging for more detailed output.")

    args = parser.parse_args()

    log_level = logging.DEBUG if args.verbose else logging.INFO
    logging.getLogger().setLevel(log_level)
    for handler in logging.getLogger().handlers:
        handler.setLevel(log_level)

    logger.debug("Debug logging enabled.")

    start_url: str = args.start_url
    max_pages: int = args.max_pages
    output_filename_arg: Optional[str] = args.output_file
    delay: float = args.delay
    user_agent: str = args.user_agent

    if not start_url.startswith(('http://', 'https://')):
        start_url = 'https://' + start_url

    start_url = normalize_url(start_url)

    parsed_start_url = urlparse(start_url)
    if not parsed_start_url.netloc:
        logger.error(f"Invalid start URL provided: {start_url}")
        return

    base_domain: str = parsed_start_url.netloc

    urls_to_visit: Deque[str] = deque([start_url])
    visited_urls: Set[str] = set()
    all_pages_text: List[str] = []

    logger.info(f"Starting crawl from: {start_url}")
    logger.info(f"Will stay within domain: {base_domain}")
    logger.info(f"Max pages: {max_pages}, Delay: {delay}s, User-Agent: {user_agent}")

    pages_crawled: int = 0

    with requests.Session() as session:
        session.headers.update({'User-Agent': user_agent})

        while urls_to_visit and pages_crawled < max_pages:
            current_url: str = urls_to_visit.popleft()

            if current_url in visited_urls:
                continue

            if not is_likely_html(current_url):
                logger.debug(f"  Skipping non-HTML or non-HTTP(S) URL: {current_url}")
                visited_urls.add(current_url)
                continue

            parsed_current = urlparse(current_url)
            if parsed_current.netloc != base_domain:
                logger.debug(f"  Skipping external link: {current_url}")
                continue

            if not can_fetch(current_url, user_agent):
                logger.warning(f"  Skipping disallowed URL (robots.txt): {current_url}")
                visited_urls.add(current_url)
                continue

            visited_urls.add(current_url)
            pages_crawled += 1
            logger.info(f"[{pages_crawled}/{max_pages}] Visiting: {current_url}")

            html_content: Optional[str] = fetch_url_content(session, current_url)

            if html_content:
                extracted_text: str
                soup: Optional[BeautifulSoup]
                extracted_text, soup = extract_text_from_html(html_content)
                if extracted_text:
                    all_pages_text.append(extracted_text)
                    logger.info(f"  Extracted ~{len(extracted_text)} characters.")

                if soup:
                    links_found: int = 0
                    for link in soup.find_all('a', href=True):
                        href: str = link.get('href', '')
                        if not href:
                            continue

                        if href.startswith(('mailto:', 'tel:', 'javascript:')):
                            logger.debug(f"  Skipping non-web link: {href}")
                            continue

                        try:
                            absolute_url: str = urljoin(current_url, href)
                            normalized_absolute_url: str = normalize_url(absolute_url)
                        except ValueError as e:
                            logger.debug(f"  Could not parse or join URL '{href}' from {current_url}: {e}")
                            continue

                        parsed_absolute = urlparse(normalized_absolute_url)

                        if parsed_absolute.scheme in ['http', 'https'] and \
                           parsed_absolute.netloc == base_domain and \
                           normalized_absolute_url not in visited_urls and \
                           normalized_absolute_url not in urls_to_visit and \
                           is_likely_html(normalized_absolute_url):
                            urls_to_visit.append(normalized_absolute_url)
                            links_found += 1
                    logger.info(f"  Found {links_found} new potential HTML links.")

                if delay > 0:
                    logger.debug(f"  Waiting {delay} second(s)...")
                    time.sleep(delay)
            else:
                logger.warning(f"  Failed to fetch or process content for {current_url}")

    logger.info(f"Crawl finished. Visited {len(visited_urls)} pages (max limit: {max_pages}).")

    if all_pages_text:
        full_extracted_text: str = "\n\n--- Page Break ---\n\n".join(all_pages_text)
        logger.info(f"Total extracted text length: {len(full_extracted_text)} characters.")

        output_filename: str
        if output_filename_arg:
            output_filename = output_filename_arg
        else:
            output_filename = get_safe_filename(start_url)

        do_save: bool = False
        if args.skip_save_prompt:
            do_save = True
        else:
            save_output: str = input(f"Save the combined text to '{output_filename}'? (y/N): ").strip().lower()
            if save_output.startswith('y'):
                do_save = True

        if do_save:
            save_text_to_file(full_extracted_text, output_filename)
        else:
            logger.info("Skipping file save.")
    else:
        logger.info("No text was extracted during the crawl.")

if __name__ == "__main__":
    main()
