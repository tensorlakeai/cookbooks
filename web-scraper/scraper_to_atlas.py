"""
Web Scraper to MongoDB Atlas Vector Search - Tensorlake Application

This application scrapes web pages, converts HTML and PDFs to clean markdown,
removes boilerplate content (headers, footers, navigation, marketing),
generates embeddings using MongoDB's Voyage AI, and stores them in
MongoDB Atlas Vector Search.

Features:
- Parallel BFS crawling using Tensorlake map() for concurrent page fetching
- Parallel PDF processing using Tensorlake map()
- Tensorlake application with progress streaming
- PyDoll headless browser for JavaScript-rendered content
- HTML to Markdown conversion with boilerplate removal
- PDF link detection and conversion to markdown
- Chunking for optimal embedding
- Voyage AI embeddings (voyage-4-large model)
- MongoDB Atlas Vector Search storage
"""

import asyncio
import json
import os
import platform
import re
import tempfile
import urllib.request
from urllib.parse import urlparse

from pydantic import BaseModel, Field
from tensorlake.applications import Image, RequestContext, Retries, application, function

# Image with Chromium, pydoll, and dependencies for web scraping
scraper_image = (
    Image(name="scraper-to-atlas-image", base_image="python:3.11.0")
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
    .run("pip install pydoll-python tensorlake beautifulsoup4 markdownify pymupdf4llm")
    .run("apt-get clean && rm -rf /var/lib/apt/lists/*")
    .run("pip cache purge")
)

# Image for embedding and MongoDB operations (no browser needed)
# Note: VOYAGE_API_KEY and MONGO_URI can be set as Tensorlake secrets
embedding_image = (
    Image(name="embedding-image", base_image="python:3.11.0")
    .run("pip install --upgrade pip wheel")
    .run("pip install tensorlake voyageai pymongo")
    .run("pip cache purge")
)


class ScrapeAndEmbedInput(BaseModel):
    """Input model for the scrape_and_embed application."""

    url: str = Field(description="The starting URL to scrape")
    max_depth: int = Field(
        default=1,
        ge=0,
        description="Maximum depth to crawl (0 means only the starting URL)",
    )
    max_links: int = Field(
        default=10,
        ge=1,
        description="Maximum number of links to process",
    )
    include_pdfs: bool = Field(
        default=True,
        description="Whether to download and convert PDF links to markdown",
    )
    max_pdfs: int = Field(
        default=5,
        ge=0,
        description="Maximum number of PDFs to process (0 means unlimited)",
    )
    mongo_uri: str = Field(
        default_factory=lambda: os.environ.get("MONGO_URI", ""),
        description="MongoDB Atlas connection string",
    )
    voyage_api_key: str = Field(
        default_factory=lambda: os.environ.get("VOYAGE_API_KEY", ""),
        description="Voyage AI API key",
    )
    database_name: str = Field(
        default="web_scraper",
        description="MongoDB database name",
    )
    collection_name: str = Field(
        default="documents",
        description="MongoDB collection name",
    )
    vector_index_name: str = Field(
        default="vector_index",
        description="Vector search index name",
    )


# HTML elements to remove (boilerplate, navigation, marketing)
BOILERPLATE_TAGS = [
    "header", "footer", "nav", "aside", "script", "style",
    "noscript", "iframe", "form", "button", "input", "select", "textarea",
]

BOILERPLATE_CLASSES = [
    "header", "footer", "nav", "navbar", "navigation", "sidebar", "menu",
    "advertisement", "ad", "ads", "banner", "cookie", "cookies", "popup",
    "modal", "social", "share", "newsletter", "subscribe", "signup", "login",
    "signin", "promo", "promotion", "cta", "call-to-action", "related",
    "recommended", "comment", "comments", "breadcrumb", "breadcrumbs",
    "pagination", "widget",
]

BOILERPLATE_IDS = [
    "header", "footer", "nav", "navbar", "sidebar", "menu",
    "ad", "ads", "banner", "cookie-banner", "newsletter", "comments", "social",
]


def is_pdf_url(url: str) -> bool:
    """Check if URL points to a PDF file."""
    parsed = urlparse(url)
    return parsed.path.lower().endswith(".pdf")


def is_same_domain(base_url: str, target_url: str) -> bool:
    """Check if target URL is on the same domain as base URL."""
    base_domain = urlparse(base_url).netloc
    target_domain = urlparse(target_url).netloc
    return base_domain == target_domain


def normalize_url(url: str) -> str:
    """Normalize URL by removing fragments and trailing slashes."""
    parsed = urlparse(url)
    normalized = parsed._replace(fragment="")
    result = normalized.geturl()
    if result.endswith("/") and parsed.path != "/":
        result = result[:-1]
    return result


@application()
@function(secrets=["VOYAGE_API_KEY", "MONGO_URI"])
def scrape_and_embed(input: ScrapeAndEmbedInput) -> dict:
    """
    Main application function that scrapes web pages and stores embeddings in Atlas.

    Uses BFS (breadth-first search) with parallel fetching at each depth level
    via Tensorlake's map() to process multiple pages concurrently.

    Args:
        input: Configuration for scraping and embedding

    Returns:
        Dictionary with scraping results and storage statistics
    """
    ctx = RequestContext.get()

    url = str(input.url)
    max_depth = input.max_depth
    max_links = input.max_links

    visited = set()
    all_documents = []
    pdf_urls = set()

    # BFS: start with the seed URL
    current_level_urls = [normalize_url(url)]

    ctx.progress.update(
        0, max_links,
        f"Starting parallel scrape of {url}",
        {"status": "starting", "url": url}
    )

    # Phase 1: Parallel BFS - fetch all URLs at each depth level concurrently
    for depth in range(max_depth + 1):
        if not current_level_urls or len(visited) >= max_links:
            break

        # Deduplicate and filter URLs for this level
        urls_to_fetch = []
        for u in current_level_urls:
            if u in visited or is_pdf_url(u):
                if is_pdf_url(u) and input.include_pdfs:
                    pdf_urls.add(u)
                continue
            urls_to_fetch.append(u)
            visited.add(u)
            if len(visited) >= max_links:
                break

        if not urls_to_fetch:
            break

        ctx.progress.update(
            len(visited), max_links,
            f"Fetching {len(urls_to_fetch)} pages at depth {depth} in parallel",
            {"status": "fetching", "depth": str(depth), "batch_size": str(len(urls_to_fetch))}
        )

        # Parallel fetch all URLs at this depth level using map()
        results = fetch_and_convert.map(urls_to_fetch)

        next_level_urls = []
        for result in results:
            if result["success"]:
                # Add chunks to documents list
                for i, chunk in enumerate(result["chunks"]):
                    all_documents.append({
                        "text": chunk,
                        "metadata": {
                            "source_url": result["url"],
                            "title": result["title"],
                            "chunk_index": i,
                            "total_chunks": len(result["chunks"]),
                            "content_type": "html",
                        }
                    })

                # Collect links for next level and PDF URLs
                for link in result["links"]:
                    normalized = normalize_url(link)
                    if not is_same_domain(url, normalized):
                        continue
                    if is_pdf_url(normalized):
                        if input.include_pdfs:
                            pdf_urls.add(normalized)
                    elif normalized not in visited:
                        next_level_urls.append(normalized)

        ctx.progress.update(
            len(visited), max_links,
            f"Completed depth {depth}: {len(urls_to_fetch)} pages fetched in parallel",
            {"status": "level_complete", "depth": str(depth), "pages": str(len(urls_to_fetch))}
        )

        # Deduplicate next level URLs
        current_level_urls = list(set(next_level_urls))

    # Phase 2: Process all PDFs in parallel using map()
    pdfs_processed = 0
    if pdf_urls and input.include_pdfs:
        pdfs_to_process = list(pdf_urls)
        if input.max_pdfs > 0:
            pdfs_to_process = pdfs_to_process[:input.max_pdfs]

        if pdfs_to_process:
            ctx.progress.update(
                len(visited), max_links,
                f"Processing {len(pdfs_to_process)} PDFs in parallel",
                {"status": "processing_pdfs", "count": str(len(pdfs_to_process))}
            )

            # Parallel PDF processing using map()
            pdf_results = fetch_and_convert_pdf.map(pdfs_to_process)

            for result in pdf_results:
                if result["success"]:
                    pdfs_processed += 1
                    for i, chunk in enumerate(result["chunks"]):
                        all_documents.append({
                            "text": chunk,
                            "metadata": {
                                "source_url": result["url"],
                                "title": result["url"].split("/")[-1],
                                "chunk_index": i,
                                "total_chunks": len(result["chunks"]),
                                "content_type": "pdf",
                            }
                        })

    # Phase 3: Generate embeddings and store in MongoDB
    if all_documents:
        ctx.progress.update(
            len(visited), max_links,
            f"Generating embeddings for {len(all_documents)} chunks",
            {"status": "embedding", "total_chunks": str(len(all_documents))}
        )

        storage_result = embed_and_store(
            all_documents,
            input.mongo_uri,
            input.voyage_api_key,
            input.database_name,
            input.collection_name,
            input.vector_index_name,
        )

        ctx.progress.update(
            len(visited), max_links,
            "Completed",
            {
                "status": "completed",
                "pages_processed": str(len(visited)),
                "pdfs_processed": str(pdfs_processed),
                "total_chunks": str(len(all_documents)),
                "documents_stored": str(storage_result.get("documents_stored", 0)),
            }
        )
    else:
        storage_result = {"documents_stored": 0}
        ctx.progress.update(
            len(visited), max_links,
            "Completed (no content found)",
            {"status": "completed", "pages_processed": str(len(visited))}
        )

    return {
        "base_url": url,
        "max_depth": max_depth,
        "max_links": max_links,
        "pages_processed": len(visited),
        "pdfs_found": len(pdf_urls),
        "pdfs_processed": pdfs_processed,
        "total_chunks": len(all_documents),
        "documents_stored": storage_result.get("documents_stored", 0),
    }


@function(image=scraper_image, timeout=120, retries=Retries(max_retries=2), memory=4)
def fetch_and_convert(url: str) -> dict:
    """
    Fetch a web page and convert to clean markdown chunks.

    Args:
        url: The URL to fetch

    Returns:
        Dictionary with success status, chunks, title, and links
    """
    return asyncio.run(_fetch_and_convert_async(url))


async def _fetch_and_convert_async(url: str) -> dict:
    """Async implementation of page fetching and conversion."""
    from bs4 import BeautifulSoup, Comment
    from markdownify import markdownify as md
    from pydoll.browser.chromium import Chrome
    from pydoll.browser.options import ChromiumOptions

    try:
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
            await tab.go_to(url)
            await asyncio.sleep(2)

            # Get page HTML
            html_result = await tab.execute_script(
                "return document.documentElement.outerHTML"
            )
            html = _extract_value(html_result)

            # Get page title
            title_result = await tab.execute_script('return document.title || ""')
            title = _extract_value(title_result)

            # Extract all links
            links_result = await tab.execute_script(
                """
                const links = Array.from(document.querySelectorAll('a[href]'));
                const filtered = links.map(a => a.href).filter(href =>
                    href.startsWith('http://') || href.startsWith('https://')
                );
                return JSON.stringify(filtered);
            """
            )
            links_json = _extract_value(links_result)
            links = json.loads(links_json) if links_json else []

        # Clean HTML and convert to markdown
        markdown = _html_to_markdown(html)

        # Chunk the markdown
        chunks = _chunk_text(markdown)

        return {
            "url": url,
            "success": True,
            "title": title,
            "chunks": chunks,
            "links": list(set(normalize_url(link) for link in links)),
        }
    except Exception as e:
        return {"url": url, "success": False, "error": str(e), "chunks": [], "links": []}


def _extract_value(result: dict):
    """Extract the actual value from PyDoll's CDP response."""
    try:
        return result["result"]["result"]["value"]
    except (KeyError, TypeError):
        return result


def _html_to_markdown(html: str) -> str:
    """Convert HTML to clean markdown, removing boilerplate content."""
    from bs4 import BeautifulSoup, Comment
    from markdownify import markdownify as md

    soup = BeautifulSoup(html, "html.parser")

    # Remove comments
    for comment in soup.find_all(string=lambda text: isinstance(text, Comment)):
        comment.extract()

    # Remove boilerplate tags
    for tag in BOILERPLATE_TAGS:
        for element in soup.find_all(tag):
            element.decompose()

    # Remove elements with boilerplate classes
    for class_name in BOILERPLATE_CLASSES:
        for element in soup.find_all(class_=re.compile(class_name, re.IGNORECASE)):
            element.decompose()

    # Remove elements with boilerplate IDs
    for id_name in BOILERPLATE_IDS:
        for element in soup.find_all(id=re.compile(id_name, re.IGNORECASE)):
            element.decompose()

    # Remove hidden elements
    for element in soup.find_all(style=re.compile(r"display:\s*none", re.IGNORECASE)):
        element.decompose()

    # Remove elements with aria-hidden="true"
    for element in soup.find_all(attrs={"aria-hidden": "true"}):
        element.decompose()

    # Find the main content area
    main_content = (
        soup.find("main")
        or soup.find("article")
        or soup.find(id=re.compile(r"content|main", re.IGNORECASE))
        or soup.find(class_=re.compile(r"content|main|article", re.IGNORECASE))
        or soup.find("body")
        or soup
    )

    # Convert to markdown
    markdown = md(
        str(main_content),
        heading_style="ATX",
        bullets="-",
        strip=["img"],
    )

    # Clean up the markdown
    markdown = re.sub(r"\n{3,}", "\n\n", markdown)
    markdown = "\n".join(line.strip() for line in markdown.split("\n"))
    markdown = markdown.strip()

    return markdown


def _chunk_text(text: str, chunk_size: int = 1000, overlap: int = 200) -> list:
    """Split text into overlapping chunks for embedding."""
    if not text:
        return []

    chunks = []
    start = 0

    while start < len(text):
        end = start + chunk_size

        if end < len(text):
            # Try to break at a paragraph or sentence boundary
            para_break = text.rfind("\n\n", start, end)
            if para_break > start + chunk_size // 2:
                end = para_break
            else:
                sentence_break = max(
                    text.rfind(". ", start, end),
                    text.rfind("! ", start, end),
                    text.rfind("? ", start, end),
                )
                if sentence_break > start + chunk_size // 2:
                    end = sentence_break + 1

        chunks.append(text[start:end].strip())
        start = end - overlap if end < len(text) else len(text)

    return [c for c in chunks if c]


@function(image=scraper_image, timeout=120, retries=Retries(max_retries=2), memory=4)
def fetch_and_convert_pdf(url: str) -> dict:
    """
    Fetch a PDF and convert to markdown chunks.

    Args:
        url: The PDF URL to fetch

    Returns:
        Dictionary with success status and chunks
    """
    import pymupdf4llm

    try:
        # Fetch PDF content
        req = urllib.request.Request(
            url, headers={"User-Agent": "TensorlakeScraper/1.0"}
        )
        with urllib.request.urlopen(req, timeout=60) as response:
            pdf_content = response.read()

        # Write to temp file and convert
        with tempfile.NamedTemporaryFile(suffix=".pdf", delete=False) as f:
            f.write(pdf_content)
            temp_path = f.name

        try:
            markdown = pymupdf4llm.to_markdown(temp_path)
        finally:
            os.unlink(temp_path)

        # Chunk the markdown
        chunks = _chunk_text(markdown)

        return {
            "url": url,
            "success": True,
            "chunks": chunks,
        }
    except Exception as e:
        return {"url": url, "success": False, "error": str(e), "chunks": []}


@function(
    image=embedding_image,
    timeout=300,
    retries=Retries(max_retries=2),
    memory=2,
    secrets=["VOYAGE_API_KEY", "MONGO_URI"],
)
def embed_and_store(
    documents: list,
    mongo_uri: str,
    voyage_api_key: str,
    database_name: str,
    collection_name: str,
    vector_index_name: str,
) -> dict:
    """
    Generate embeddings and store documents in MongoDB Atlas.

    Args:
        documents: List of dicts with 'text' and 'metadata' keys
        mongo_uri: MongoDB Atlas connection string
        voyage_api_key: Voyage AI API key
        database_name: Database name
        collection_name: Collection name
        vector_index_name: Vector search index name

    Returns:
        Dictionary with storage statistics
    """
    import os

    import pymongo
    import voyageai

    if not documents:
        return {"documents_stored": 0}

    # Use passed key or fall back to environment variable
    api_key = voyage_api_key or os.environ.get("VOYAGE_API_KEY", "")
    mongo_connection = mongo_uri or os.environ.get("MONGO_URI", "")

    if not api_key:
        raise ValueError("VOYAGE_API_KEY not provided and not found in environment")
    if not mongo_connection:
        raise ValueError("MONGO_URI not provided and not found in environment")

    # Initialize Voyage AI client
    vo = voyageai.Client(api_key=api_key)

    # Extract texts for embedding
    texts = [doc["text"] for doc in documents]

    # Generate embeddings in batches
    batch_size = 128
    all_embeddings = []

    for i in range(0, len(texts), batch_size):
        batch = texts[i:i + batch_size]
        result = vo.embed(
            texts=batch,
            model="voyage-4-large",
            input_type="document",
        )
        all_embeddings.extend(result.embeddings)

    # Connect to MongoDB
    client = pymongo.MongoClient(mongo_connection)
    db = client[database_name]
    collection = db[collection_name]

    # Check if collection exists and create vector search index if needed
    try:
        existing_indexes = list(collection.list_search_indexes())
        index_names = [idx.get("name") for idx in existing_indexes]
        index_exists = vector_index_name in index_names
    except Exception:
        # Collection doesn't exist yet, index will be created after first insert
        index_exists = False

    # Insert documents first (this creates the collection if it doesn't exist)
    mongo_docs = []
    for doc, embedding in zip(documents, all_embeddings):
        mongo_doc = {
            "content": doc["text"],
            "embedding": embedding,
            **doc["metadata"],
        }
        mongo_docs.append(mongo_doc)

    result = collection.insert_many(mongo_docs)

    # Now create the vector search index if it doesn't exist
    if not index_exists:
        try:
            index_definition = {
                "definition": {
                    "mappings": {
                        "dynamic": True,
                        "fields": {
                            "embedding": {
                                "type": "knnVector",
                                "dimensions": 1024,  # voyage-4-large dimensions
                                "similarity": "cosine",
                            }
                        },
                    }
                },
                "name": vector_index_name,
            }
            collection.create_search_index(index_definition)
        except Exception as e:
            # Index might already exist or creation might fail - log but don't fail
            print(f"Note: Could not create search index: {e}")

    client.close()

    return {"documents_stored": len(result.inserted_ids)}


# Local testing
if __name__ == "__main__":
    from tensorlake.applications import run_local_application

    # Test configuration
    test_input = ScrapeAndEmbedInput(
        url="https://docs.anthropic.com/en/docs",
        max_depth=1,
        max_links=5,
        include_pdfs=True,
        mongo_uri=os.environ.get("MONGO_URI", ""),
        voyage_api_key=os.environ.get("VOYAGE_API_KEY", ""),
        database_name="web_scraper",
        collection_name="documents",
        vector_index_name="vector_index",
    )

    if not test_input.mongo_uri:
        print("Error: MONGO_URI environment variable not set")
        exit(1)

    if not test_input.voyage_api_key:
        print("Error: VOYAGE_API_KEY environment variable not set")
        exit(1)

    print(f"Starting scrape of {test_input.url}")
    print(f"Max depth: {test_input.max_depth}, Max links: {test_input.max_links}")
    print("-" * 50)

    request = run_local_application(scrape_and_embed, test_input)
    result = request.output()

    print("\nScraping complete!")
    print(f"Pages processed: {result['pages_processed']}")
    print(f"PDFs processed: {result['pdfs_processed']}")
    print(f"Total chunks: {result['total_chunks']}")
    print(f"Documents stored: {result['documents_stored']}")
