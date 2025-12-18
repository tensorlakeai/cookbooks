"""
Parallel Web Scraper - Demonstrates Map-Reduce in Tensorlake

This example shows how to process multiple URLs in parallel using Tensorlake's
map-reduce capabilities. Each URL is fetched in its own container, with automatic
retries on failure. Results are aggregated incrementally as they complete.

What this replaces if built traditionally:
- A task queue (Celery, SQS, etc.)
- Worker pool management
- Retry logic and dead letter queues
- Result aggregation service
- Failure handling and recovery
"""

from typing import List
import urllib.request
import re

from tensorlake.applications import application, function, Retries


@application()
@function()
def scrape_sites(urls: List[str]) -> dict:
    """
    Scrape multiple websites in parallel and aggregate word counts.

    This single function call fans out to N parallel containers (one per URL),
    then reduces results as they arrive. If any individual scrape fails,
    others continue - no single failure blocks the pipeline.
    """
    # Map: Process each URL in parallel
    # Under the hood, Tensorlake spins up a container for each URL
    word_counts = count_words.map(urls)

    # Reduce: Aggregate results incrementally as they arrive
    # The reducer is called with each result as soon as it's ready
    return merge_counts.reduce(word_counts, {
        "total_words": 0,
        "sites_processed": 0,
        "successful": 0,
        "failed": 0,
        "results": []
    })


@function(timeout=30, retries=Retries(max_retries=2))
def count_words(url: str) -> dict:
    """
    Fetch a URL and count words.

    Each invocation runs in its own container with:
    - 30 second timeout
    - Up to 2 automatic retries on failure
    - Independent scaling from other functions
    """
    try:
        req = urllib.request.Request(
            url,
            headers={'User-Agent': 'TensorlakeBot/1.0'}
        )
        with urllib.request.urlopen(req, timeout=15) as response:
            html = response.read().decode('utf-8', errors='ignore')

            # Strip HTML tags
            text = re.sub(r'<[^>]+>', ' ', html)
            # Extract words
            words = re.findall(r'\b[a-zA-Z]{2,}\b', text.lower())

            # Get top 10 most frequent words (excluding common stop words)
            stopwords = {'the', 'and', 'for', 'are', 'but', 'not', 'you', 'all',
                        'can', 'had', 'her', 'was', 'one', 'our', 'out', 'has',
                        'have', 'been', 'were', 'will', 'with', 'that', 'this',
                        'from', 'they', 'would', 'there', 'their', 'what', 'about'}

            word_freq = {}
            for word in words:
                if word not in stopwords and len(word) > 3:
                    word_freq[word] = word_freq.get(word, 0) + 1

            top_words = sorted(word_freq.items(), key=lambda x: x[1], reverse=True)[:10]

            return {
                "url": url,
                "word_count": len(words),
                "unique_words": len(set(words)),
                "top_words": [{"word": w, "count": c} for w, c in top_words],
                "success": True
            }
    except Exception as e:
        return {
            "url": url,
            "word_count": 0,
            "success": False,
            "error": str(e)
        }


@function()
def merge_counts(accumulated: dict, result: dict) -> dict:
    """
    Merge word counts from each site.

    This function is called incrementally as each map result arrives.
    Results maintain order but processing happens in parallel.
    """
    new_results = accumulated["results"] + [result]

    return {
        "total_words": accumulated["total_words"] + result.get("word_count", 0),
        "sites_processed": accumulated["sites_processed"] + 1,
        "successful": accumulated["successful"] + (1 if result.get("success") else 0),
        "failed": accumulated["failed"] + (0 if result.get("success") else 1),
        "results": new_results
    }


# Local testing
if __name__ == "__main__":
    from tensorlake.applications import run_local_application

    test_urls = [
        "https://example.com",
        "https://httpbin.org/html",
    ]

    request = run_local_application(scrape_sites, test_urls)
    print(request.output())
