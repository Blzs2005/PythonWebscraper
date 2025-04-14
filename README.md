# Simple Documentation Scraper

## Description

This Python script is a basic web scraper designed to extract text content primarily from API documentation websites or similar structured sites. It starts from a given URL, crawls internal links within the same domain, extracts text from each page, and combines the text into a single output file.

## Features

*   **Targeted Crawling:** Starts at a specified URL and only follows links within the same domain.
*   **Text Extraction:** Uses BeautifulSoup to parse HTML and extract textual content.
*   **Configurable:** Allows setting the maximum number of pages to crawl, the delay between requests, the output filename, and the User-Agent string via command-line arguments.
*   **Politeness:** Includes a configurable delay between requests to avoid overwhelming the target server.
*   **Basic `robots.txt` Respect:** Includes a basic check for `robots.txt` rules (requires the `urllib.robotparser` module).
*   **Link Filtering:** Attempts to ignore links pointing to common non-HTML file extensions (like PDFs, images, archives) and non-web schemes (like `mailto:`, `tel:`).
*   **Output:** Saves the combined extracted text from all crawled pages into a single `.txt` file.
*   **Logging:** Uses Python's `logging` module for informative output during the crawl, with an optional verbose mode (`-v`).
*   **Session Management:** Uses `requests.Session` for potentially improved performance with persistent connections.

## Requirements

*   Python 3.x
*   Required libraries: `requests`, `beautifulsoup4`

You can install the required libraries using pip and the provided `requirements.txt` file:

```bash
pip install -r requirements.txt
```

## Usage

Run the script from your terminal using Python, providing the starting URL as the main argument.

```bash
python main.py <start_url> [options]
```

**Example:**

```bash
# Crawl docs.example.com starting from the main page, limit to 50 pages, save to my_docs.txt
python main.py https://docs.example.com -m 50 -o my_docs.txt

# Crawl with a 2-second delay, skip the save confirmation prompt, and enable verbose logging
python main.py https://api.another-site.org/v1/ --delay 2 --skip-save-prompt -v
```

## Configuration (Command-Line Arguments)

*   `start_url`: (Required) The starting URL for the crawl.
*   `-m` or `--max-pages`: Maximum number of pages to crawl (default: 100).
*   `-o` or `--output-file`: Filename to save the combined text (default: generated from the start URL).
*   `-d` or `--delay`: Delay in seconds between requests (default: 1).
*   `-ua` or `--user-agent`: User-Agent string for requests (default: a common browser UA).
*   `--skip-save-prompt`: Automatically save the output file without asking for confirmation.
*   `-v` or `--verbose`: Enable debug logging for more detailed output.

## Important Note: Refining Text Extraction

The script currently extracts *all* text from the `<body>` of each page using `soup.get_text()`. This often includes navigation menus, footers, sidebars, etc., which might not be relevant documentation content.

**For best results, you MUST inspect the HTML structure of your target website and modify the `extract_text_from_html` function in `main.py`**. Target specific HTML elements (like `<main>`, `<article>`, `<div class="content">`, etc.) that contain the primary documentation content.

*Performance Tip:* Consider installing the `lxml` library (`pip install lxml`) and changing the parser in `extract_text_from_html` from `'html.parser'` to `'lxml'` for potentially faster HTML parsing, especially on complex pages.

## Limitations

*   **No JavaScript Rendering:** The scraper only fetches and parses the initial HTML source. It does not execute JavaScript, so content loaded dynamically might be missed.
*   **Basic `robots.txt`:** The `robots.txt` handling is basic and relies on Python's standard library parser. Complex rules or directives might not be fully interpreted.
*   **HTML Structure Dependent:** The quality of the extracted text heavily depends on targeting the correct HTML elements (see "Important Note" above).
*   **Basic Link Filtering:** Filtering of non-HTML links is based on common file extensions and schemes; edge cases might be missed.

## Ethical Considerations

*   **Be Respectful:** Use a reasonable delay (`--delay`) to avoid overloading the website's server. Default is 1 second.
*   **Check `robots.txt`:** While the script has basic checks, manually review the target site's `robots.txt` file (`http://<domain>/robots.txt`) to understand their crawling policies.
*   **Terms of Service:** Review the website's Terms of Service to ensure scraping is permitted.
*   **Identify Yourself:** Use a descriptive User-Agent string (`--user-agent`) if desired, potentially including contact information.
*   **Disclaimer:** The author is not responsible for any misuse of this script or for any violations of website terms of service resulting from its use. Use this script responsibly and ethically.

## License

Please refer to the `LICENSE` file for information on usage rights and limitations.
