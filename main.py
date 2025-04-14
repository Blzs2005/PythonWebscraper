import requests
from bs4 import BeautifulSoup
import os
import re
import time
from collections import deque
from urllib.parse import urljoin, urlparse
import logging
import argparse
from urllib import robotparser

logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')

DEFAULT_USER_AGENT = 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/58.0.3029.110 Safari/537.3'
DEFAULT_DELAY = 1
DEFAULT_MAX_PAGES = 100

robots_parsers = {}

def can_fetch(url, user_agent):
    parsed_url = urlparse(url)
    base_url = f"{parsed_url.scheme}://{parsed_url.netloc}"
    robots_url = urljoin(base_url, "/robots.txt")

    parser = robots_parsers.get(base_url)
    if not parser:
        parser = robotparser.RobotFileParser()
        parser.set_url(robots_url)
        try:
            logging.info(f"  Fetching robots.txt from: {robots_url}")
            parser.read()
            robots_parsers[base_url] = parser
        except Exception as e:
            logging.warning(f"  Could not fetch or parse robots.txt at {robots_url}: {e}")
            robots_parsers[base_url] = None
            return True

    if parser:
        try:
            return parser.can_fetch(user_agent, url)
        except Exception as e:
            logging.warning(f"  Error checking can_fetch for {url} with robots.txt: {e}")
            return True
    else:
        return True

def fetch_url_content(url, user_agent):
    logging.info(f"  Fetching: {url}")
    try:
        headers = {'User-Agent': user_agent}
        response = requests.get(url, headers=headers, timeout=15)
        response.raise_for_status()
        content_type = response.headers.get('Content-Type', '')
        if 'text/html' not in content_type:
            logging.info(f"  Skipping non-HTML content ({content_type}) at: {url}")
            return None
        return response.text
    except requests.exceptions.Timeout:
        logging.warning(f"  Timeout error fetching URL {url}")
        return None
    except requests.exceptions.RequestException as e:
        logging.error(f"  Error fetching URL {url}: {e}")
        return None
    except Exception as e:
        logging.error(f"  An unexpected error occurred during fetch for {url}: {e}")
        return None

def extract_text_from_html(html_content):
    if not html_content:
        return "", None
    try:
        soup = BeautifulSoup(html_content, 'html.parser')
        text = soup.get_text(separator=' ', strip=True)
        return text, soup
    except Exception as e:
        logging.error(f"  Error parsing HTML: {e}")
        return "", None

def get_safe_filename(url):
    safe_name = re.sub(r'^https?://|^ftps?://', '', url)
    safe_name = re.sub(r'[^\w.-]+', '_', safe_name)
    safe_name = safe_name.strip('_.-')
    safe_name = safe_name[:100]
    if not safe_name.lower().endswith('.txt'):
        safe_name += ".txt"
    return safe_name if safe_name != ".txt" else "extracted_text.txt"

def save_text_to_file(text, filename):
    try:
        safe_filename = os.path.basename(filename)
        full_path = os.path.abspath(safe_filename)
        with open(full_path, 'w', encoding='utf-8') as f:
            f.write(text)
        logging.info(f"Successfully saved combined extracted text to: {full_path}")
        return True
    except IOError as e:
        logging.error(f"Error saving file '{filename}': {e}")
        return False
    except Exception as e:
        logging.error(f"An unexpected error occurred during saving to '{filename}': {e}")
        return False

def main():
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

    args = parser.parse_args()

    start_url = args.start_url
    max_pages = args.max_pages
    output_filename_arg = args.output_file
    delay = args.delay
    user_agent = args.user_agent

    if not start_url.startswith(('http://', 'https://')):
        start_url = 'https://' + start_url

    parsed_start_url = urlparse(start_url)
    if not parsed_start_url.netloc:
        logging.error(f"Invalid start URL provided: {start_url}")
        return

    base_domain = parsed_start_url.netloc

    urls_to_visit = deque([start_url])
    visited_urls = set()
    all_pages_text = []

    logging.info(f"Starting crawl from: {start_url}")
    logging.info(f"Will stay within domain: {base_domain}")
    logging.info(f"Max pages: {max_pages}, Delay: {delay}s, User-Agent: {user_agent}")

    pages_crawled = 0

    while urls_to_visit and pages_crawled < max_pages:
        current_url = urls_to_visit.popleft()

        if current_url in visited_urls:
            continue

        parsed_current = urlparse(current_url)
        if parsed_current.netloc != base_domain:
            logging.debug(f"  Skipping external link: {current_url}")
            continue

        if not can_fetch(current_url, user_agent):
            logging.warning(f"  Skipping disallowed URL (robots.txt): {current_url}")
            visited_urls.add(current_url)
            continue

        visited_urls.add(current_url)
        pages_crawled += 1
        logging.info(f"[{pages_crawled}/{max_pages}] Visiting: {current_url}")

        html_content = fetch_url_content(current_url, user_agent)

        if html_content:
            extracted_text, soup = extract_text_from_html(html_content)
            if extracted_text:
                all_pages_text.append(extracted_text)
                logging.info(f"  Extracted ~{len(extracted_text)} characters.")

            if soup:
                links_found = 0
                for link in soup.find_all('a', href=True):
                    href = link['href']
                    absolute_url = urljoin(current_url, href)
                    absolute_url = urlparse(absolute_url)._replace(fragment="").geturl()
                    parsed_absolute = urlparse(absolute_url)

                    if parsed_absolute.scheme in ['http', 'https'] and \
                       parsed_absolute.netloc == base_domain and \
                       absolute_url not in visited_urls and \
                       absolute_url not in urls_to_visit:
                        urls_to_visit.append(absolute_url)
                        links_found += 1
                logging.info(f"  Found {links_found} new potential links.")

            if delay > 0:
                logging.debug(f"  Waiting {delay} second(s)...")
                time.sleep(delay)
        else:
            logging.warning(f"  Failed to fetch or process content for {current_url}")

    logging.info(f"Crawl finished. Visited {len(visited_urls)} pages (max limit: {max_pages}).")

    if all_pages_text:
        full_extracted_text = "\n\n--- Page Break ---\n\n".join(all_pages_text)
        logging.info(f"Total extracted text length: {len(full_extracted_text)} characters.")

        if output_filename_arg:
            output_filename = output_filename_arg
        else:
            output_filename = get_safe_filename(start_url)

        do_save = False
        if args.skip_save_prompt:
            do_save = True
        else:
            save_output = input(f"Save the combined text to '{output_filename}'? (y/N): ").strip().lower()
            if save_output.startswith('y'):
                do_save = True

        if do_save:
            save_text_to_file(full_extracted_text, output_filename)
        else:
            logging.info("Skipping file save.")
    else:
        logging.info("No text was extracted during the crawl.")

if __name__ == "__main__":
    main()
