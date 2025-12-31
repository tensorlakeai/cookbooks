"""
Web Crawler with Depth-First Search - Tensorlake + PyDoll

This crawler takes a URL and recursively crawls the website up to N levels deep,
producing a dictionary of links and their content. Files are base64 encoded with
metadata about their type.

Features:
- Depth-first search with configurable depth limit
- PyDoll headless browser for JavaScript-rendered content
- Base64 encoding for binary files (images, PDFs, etc.)
- Progress streaming via Tensorlake application context
- Parallel content fetching offloaded to separate Tensorlake functions
"""

import asyncio
import base64
import json
import mimetypes
import platform
from urllib.parse import urlparse

from pydantic import BaseModel, Field
from tensorlake.applications import Image, Retries, application, function

# Image with Chromium and pydoll for web scraping function
scraper_image = (
    Image(name="web-scraper-image", base_image="python:3.11.0")
    .env("DEBIAN_FRONTEND", "noninteractive")
    .run(
        """apt-get update && \
apt-get install -y \
gnupg \
wget \
iproute2 \
wkhtmltopdf \
libx11-xcb1 \
libdbus-glib-1-2 \
git \
tini \
chromium"""
    )
    .run("pip install --upgrade pip wheel")
    .run("pip install pydoll-python tensorlake")
    .run("apt-get clean && rm -rf /var/lib/apt/lists/*")
    .run("pip cache purge")
)


# File extensions that should be base64 encoded
BINARY_EXTENSIONS = {
    ".pdf",
    ".png",
    ".jpg",
    ".jpeg",
    ".gif",
    ".webp",
    ".ico",
    ".svg",
    ".zip",
    ".tar",
    ".gz",
    ".rar",
    ".7z",
    ".doc",
    ".docx",
    ".xls",
    ".xlsx",
    ".ppt",
    ".pptx",
    ".mp3",
    ".mp4",
    ".wav",
    ".avi",
    ".mov",
    ".webm",
    ".woff",
    ".woff2",
    ".ttf",
    ".eot",
    ".otf",
}


class CrawlInput(BaseModel):
    """Input model for the crawl function."""

    url: str = Field(description="The starting URL to crawl")
    max_depth: int = Field(
        default=3,
        ge=0,
        description="Maximum depth to crawl (0 means only the starting URL)",
    )
    max_links: int = Field(
        default=5,
        ge=1,
        description="Maximum number of links to crawl (None means unlimited)",
    )


def is_binary_url(url: str) -> bool:
    """Check if URL points to a binary file based on extension."""
    parsed = urlparse(url)
    path = parsed.path.lower()
    return any(path.endswith(ext) for ext in BINARY_EXTENSIONS)


def get_file_type(url: str) -> str:
    """Get the MIME type for a URL based on its extension."""
    parsed = urlparse(url)
    mime_type, _ = mimetypes.guess_type(parsed.path)
    return mime_type or "application/octet-stream"


def is_same_domain(base_url: str, target_url: str) -> bool:
    """Check if target URL is on the same domain as base URL."""
    base_domain = urlparse(base_url).netloc
    target_domain = urlparse(target_url).netloc
    return base_domain == target_domain


def normalize_url(url: str) -> str:
    """Normalize URL by removing fragments and trailing slashes."""
    parsed = urlparse(url)
    # Remove fragment and normalize path
    normalized = parsed._replace(fragment="")
    result = normalized.geturl()
    # Remove trailing slash for consistency (except for root)
    if result.endswith("/") and parsed.path != "/":
        result = result[:-1]
    return result


@application()
@function()
def crawl(input: CrawlInput) -> dict:
    """
    Main crawler function that performs depth-first search on a website.

    Args:
        input: CrawlInput model with 'url', 'max_depth', and optional 'max_links' fields

    Returns:
        Dictionary with crawled links and their content
    """
    url = str(input.url)
    max_depth = input.max_depth
    max_links = input.max_links
    ctx = None  # RequestContext not passed in local mode

    visited = set()
    results = {}
    base_url = url

    # Stack for DFS: (url, depth)
    stack = [(normalize_url(url), 0)]

    while stack:
        # Stop if we've reached the max_links limit
        if max_links is not None and len(results) >= max_links:
            break
        current_url, depth = stack.pop()

        # Skip if already visited or exceeds max depth
        if current_url in visited:
            continue
        if depth > max_depth:
            continue

        visited.add(current_url)

        if ctx:
            ctx.stream_output(
                {
                    "status": "crawling",
                    "url": current_url,
                    "depth": depth,
                    "visited_count": len(visited),
                }
            )

        # Offload content fetching to separate tensorlake function
        content_result = fetch_content(current_url)

        if content_result["success"]:
            results[current_url] = content_result

            # If we haven't reached max depth, add discovered links to stack
            if depth < max_depth and "links" in content_result:
                for link in content_result["links"]:
                    normalized_link = normalize_url(link)
                    # Only crawl same-domain links that haven't been visited
                    if (
                        is_same_domain(base_url, normalized_link)
                        and normalized_link not in visited
                    ):
                        # DFS: add to stack (will be processed depth-first)
                        stack.append((normalized_link, depth + 1))

            if ctx:
                ctx.stream_output(
                    {
                        "status": "fetched",
                        "url": current_url,
                        "content_type": content_result.get("content_type", "unknown"),
                        "links_found": len(content_result.get("links", [])),
                    }
                )
        else:
            results[current_url] = content_result
            if ctx:
                ctx.stream_output(
                    {
                        "status": "failed",
                        "url": current_url,
                        "error": content_result.get("error", "Unknown error"),
                    }
                )

    if ctx:
        ctx.stream_output(
            {
                "status": "completed",
                "total_urls": len(results),
                "successful": sum(1 for r in results.values() if r.get("success")),
                "failed": sum(1 for r in results.values() if not r.get("success")),
            }
        )

    return {
        "base_url": url,
        "max_depth": max_depth,
        "max_links": max_links,
        "total_crawled": len(results),
        "pages": results,
    }


@function(image=scraper_image, timeout=60, retries=Retries(max_retries=2), memory=4)
def fetch_content(url: str) -> dict:
    """
    Fetch content from a URL using PyDoll headless browser.

    For HTML pages: extracts text content and discovers links.
    For binary files: base64 encodes the content with file type metadata.

    Args:
        url: The URL to fetch

    Returns:
        Dictionary containing the content and metadata
    """
    return asyncio.run(_fetch_content_async(url))


async def _fetch_content_async(url: str) -> dict:
    """Async implementation of content fetching using PyDoll."""
    try:
        # Check if this is a binary file
        if is_binary_url(url):
            return await _fetch_binary_content(url)
        else:
            return await _fetch_page_content(url)
    except Exception as e:
        return {"url": url, "success": False, "error": str(e)}


async def _fetch_binary_content(url: str) -> dict:
    """Fetch and base64 encode binary content."""
    import urllib.request

    try:
        req = urllib.request.Request(
            url, headers={"User-Agent": "TensorlakeCrawler/1.0"}
        )
        with urllib.request.urlopen(req, timeout=30) as response:
            content = response.read()
            content_type = response.headers.get("Content-Type", get_file_type(url))

            return {
                "url": url,
                "success": True,
                "content_type": content_type,
                "is_binary": True,
                "file_metadata": {
                    "mime_type": content_type,
                    "size_bytes": len(content),
                    "extension": (
                        urlparse(url).path.split(".")[-1]
                        if "." in urlparse(url).path
                        else None
                    ),
                },
                "content_base64": base64.b64encode(content).decode("utf-8"),
                "links": [],  # Binary files don't have links
            }
    except Exception as e:
        return {"url": url, "success": False, "is_binary": True, "error": str(e)}


def extract_value(result: dict):
    """Extract the actual value from PyDoll's CDP response."""
    try:
        return result["result"]["result"]["value"]
    except (KeyError, TypeError):
        return result


async def _fetch_page_content(url: str) -> dict:
    """Fetch HTML page content using PyDoll headless browser."""
    from pydoll.browser.chromium import Chrome
    from pydoll.browser.options import ChromiumOptions

    options = ChromiumOptions()
    if platform.system() == "Linux":
        options.binary_location = "/usr/bin/chromium"
    options.add_argument("--headless=new")
    options.add_argument("--disable-gpu")
    options.add_argument("--no-sandbox")
    options.add_argument("--disable-dev-shm-usage")
    options.add_argument("--disable-notifications")

    async with Chrome(options=options) as browser:
        tab = await browser.start()

        # Navigate to the page
        await tab.go_to(url)

        # Wait for page to load
        await asyncio.sleep(2)

        # Get page content
        html_result = await tab.execute_script(
            "return document.documentElement.outerHTML"
        )
        html = extract_value(html_result)

        # Extract text content
        text_result = await tab.execute_script(
            """
            return document.body ? document.body.innerText : '';
        """
        )
        text_content = extract_value(text_result)

        # Extract all links (use JSON.stringify since arrays are returned as object refs)
        links_result = await tab.execute_script(
            """
            const links = Array.from(document.querySelectorAll('a[href]'));
            const filtered = links.map(a => a.href).filter(href =>
                href.startsWith('http://') || href.startsWith('https://')
            );
            return JSON.stringify(filtered);
        """
        )
        links_json = extract_value(links_result)
        links_data = json.loads(links_json) if links_json else []

        # Get page title
        title_result = await tab.execute_script('return document.title || ""')
        title = extract_value(title_result)

        # Get meta description
        meta_result = await tab.execute_script(
            """
            const meta = document.querySelector('meta[name="description"]');
            return meta ? meta.getAttribute('content') : '';
        """
        )
        meta_description = extract_value(meta_result)

        # Deduplicate and normalize links
        unique_links = list(set(normalize_url(link) for link in (links_data or [])))

        return {
            "url": url,
            "success": True,
            "content_type": "text/html",
            "is_binary": False,
            "title": title,
            "meta_description": meta_description,
            "text_content": text_content,
            "html_length": len(html) if html else 0,
            "links": unique_links,
        }


# Local testing
if __name__ == "__main__":
    from tensorlake.applications import run_local_application

    # Test with a simple website
    test_url = "https://en.wikipedia.org/wiki/Sam_Altman"
    max_depth = 2

    print(f"Starting crawl of {test_url} with max depth {max_depth}")
    print("-" * 50)

    request = run_local_application(
        crawl, CrawlInput(url=test_url, max_depth=max_depth, max_links=2)
    )
    result = request.output()

    print("\nCrawl completed!")
    print(f"Total pages crawled: {result['total_crawled']}")
    print("\nPages:")
    for url, data in result["pages"].items():
        if data["success"]:
            content_type = data.get("content_type", "unknown")
            if data.get("is_binary"):
                print(f"  [BINARY] {url} - {content_type}")
            else:
                print(f"  [HTML] {url} - {data.get('title', 'No title')}")
        else:
            print(f"  [FAILED] {url} - {data.get('error', 'Unknown error')}")
