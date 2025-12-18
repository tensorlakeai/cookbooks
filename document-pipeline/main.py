"""
Document Processing Pipeline - Demonstrates Multi-Stage Workflows in Tensorlake

This example shows a multi-stage pipeline where documents flow through
different processing stages, each with its own resource requirements.
The pipeline: Download -> Parse -> Enrich -> Summarize

What this replaces if built traditionally:
- Workflow orchestration (Airflow, Step Functions)
- Multiple Kubernetes deployments with different resource specs
- Inter-service communication (gRPC, message queues)
- State management between pipeline stages
- Checkpointing and resume-from-failure logic
"""

from typing import List
import urllib.request
import re
import json

from tensorlake.applications import application, function, Retries


@application()
@function()
def process_documents(urls: List[str]) -> List[dict]:
    """
    Process multiple documents through a multi-stage pipeline.

    Each stage runs in containers with different resource allocations.
    If the pipeline fails mid-way, Tensorlake can resume from the last
    successful stage (durability).
    """
    # Stage 1: Download all documents in parallel
    # Each download runs in a lightweight container
    documents = download.map(urls)

    # Stage 2: Parse content (heavier processing)
    # Runs in containers with more CPU/memory
    parsed = parse_content.map(documents)

    # Stage 3: Enrich with metadata
    enriched = enrich.map(parsed)

    # Stage 4: Generate summaries
    summaries = summarize.map(enriched)

    return summaries


@function(timeout=60, retries=Retries(max_retries=2), cpu=1, memory=1)
def download(url: str) -> dict:
    """
    Download document from URL.

    Lightweight container (1 CPU, 1GB RAM) optimized for I/O.
    Automatic retries handle transient network failures.
    """
    try:
        req = urllib.request.Request(
            url,
            headers={'User-Agent': 'TensorlakeBot/1.0'}
        )
        with urllib.request.urlopen(req, timeout=30) as response:
            content = response.read()
            content_type = response.headers.get('Content-Type', 'unknown')

            return {
                "url": url,
                "content": content.decode('utf-8', errors='ignore'),
                "content_type": content_type,
                "size_bytes": len(content),
                "stage": "downloaded",
                "success": True
            }
    except Exception as e:
        return {
            "url": url,
            "success": False,
            "error": str(e),
            "stage": "download_failed"
        }


@function(cpu=2, memory=2, timeout=120)
def parse_content(doc: dict) -> dict:
    """
    Parse and structure document content.

    Heavier container (2 CPU, 2GB RAM) for text processing.
    This stage might use libraries like BeautifulSoup or custom parsers.
    """
    if not doc.get("success"):
        return doc

    content = doc["content"]

    # Extract structural elements
    # Find title (first major heading or line)
    lines = content.split('\n')
    title = None
    for line in lines[:20]:
        line = line.strip()
        if line and len(line) < 200:
            # Check if it looks like a title
            clean_line = re.sub(r'<[^>]+>', '', line).strip()
            if clean_line and not clean_line.endswith((',', ';')):
                title = clean_line[:100]
                break

    # Extract text content (strip HTML if present)
    text = re.sub(r'<script[^>]*>.*?</script>', '', content, flags=re.DOTALL | re.IGNORECASE)
    text = re.sub(r'<style[^>]*>.*?</style>', '', text, flags=re.DOTALL | re.IGNORECASE)
    text = re.sub(r'<[^>]+>', ' ', text)
    text = re.sub(r'\s+', ' ', text).strip()

    # Find all links
    links = re.findall(r'https?://[^\s<>"\']+', content)
    links = list(set(links))[:20]  # Dedupe and limit

    # Identify sections (lines that look like headers)
    sections = []
    for line in lines:
        clean = re.sub(r'<[^>]+>', '', line).strip()
        if clean and 3 < len(clean) < 80 and not clean.endswith(('.', ',', ';', ':')):
            if clean[0].isupper():
                sections.append(clean)
    sections = sections[:10]

    return {
        **doc,
        "stage": "parsed",
        "parsed": {
            "title": title,
            "text_length": len(text),
            "text_preview": text[:500] + "..." if len(text) > 500 else text,
            "links_found": len(links),
            "links": links[:10],
            "sections": sections,
        }
    }


@function(cpu=1, memory=1)
def enrich(doc: dict) -> dict:
    """
    Enrich document with computed metadata.

    Standard container - lightweight enrichment logic.
    """
    if not doc.get("success"):
        return doc

    content = doc.get("content", "")
    parsed = doc.get("parsed", {})
    text_preview = parsed.get("text_preview", "")

    # Compute text statistics
    words = re.findall(r'\b\w+\b', content)
    sentences = re.split(r'[.!?]+', content)
    sentences = [s.strip() for s in sentences if s.strip()]

    # Simple language detection based on common words
    english_indicators = {'the', 'and', 'is', 'are', 'was', 'were', 'have', 'has', 'been'}
    spanish_indicators = {'el', 'la', 'los', 'las', 'es', 'son', 'que', 'de', 'en'}
    french_indicators = {'le', 'la', 'les', 'est', 'sont', 'que', 'de', 'en', 'et'}

    word_set = set(w.lower() for w in words[:200])
    en_score = len(word_set & english_indicators)
    es_score = len(word_set & spanish_indicators)
    fr_score = len(word_set & french_indicators)

    if en_score >= es_score and en_score >= fr_score:
        detected_lang = "en"
    elif es_score > fr_score:
        detected_lang = "es"
    else:
        detected_lang = "fr" if fr_score > 0 else "unknown"

    # Compute reading time (average 200 words per minute)
    reading_time_mins = len(words) / 200

    return {
        **doc,
        "stage": "enriched",
        "metadata": {
            "word_count": len(words),
            "sentence_count": len(sentences),
            "unique_words": len(set(w.lower() for w in words)),
            "avg_words_per_sentence": round(len(words) / max(len(sentences), 1), 1),
            "estimated_reading_time_mins": round(reading_time_mins, 1),
            "detected_language": detected_lang,
            "content_type": doc.get("content_type"),
            "size_bytes": doc.get("size_bytes"),
        }
    }


@function(cpu=2, memory=2)
def summarize(doc: dict) -> dict:
    """
    Generate document summary.

    This stage could integrate with an LLM API for better summaries.
    For demo purposes, we use extractive summarization.
    """
    if not doc.get("success"):
        return {
            "url": doc.get("url"),
            "success": False,
            "error": doc.get("error", "Previous stage failed"),
        }

    content = doc.get("content", "")
    parsed = doc.get("parsed", {})
    metadata = doc.get("metadata", {})

    # Simple extractive summarization: take first N sentences
    # Strip HTML first
    text = re.sub(r'<[^>]+>', ' ', content)
    text = re.sub(r'\s+', ' ', text).strip()

    sentences = re.split(r'(?<=[.!?])\s+', text)
    sentences = [s.strip() for s in sentences if len(s.strip()) > 20]

    # Take first 3 meaningful sentences as summary
    summary_sentences = sentences[:3]
    summary = ' '.join(summary_sentences)

    # Truncate if too long
    if len(summary) > 500:
        summary = summary[:497] + "..."

    # Final output - clean structure without intermediate data
    return {
        "url": doc.get("url"),
        "success": True,
        "title": parsed.get("title"),
        "summary": summary if summary else "No summary available",
        "metadata": metadata,
        "structure": {
            "sections": parsed.get("sections", []),
            "links_count": parsed.get("links_found", 0),
        }
    }


# Local testing
if __name__ == "__main__":
    from tensorlake.applications import run_local_application

    test_urls = [
        "https://example.com",
        "https://httpbin.org/html",
    ]

    request = run_local_application(process_documents, test_urls)
    print(json.dumps(request.output(), indent=2))
