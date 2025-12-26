# Web Scraper

A serverless web crawler that scrapes websites up to N levels deep using Chrome in headless mode, producing a dictionary of links and their content. If the links are PDFs or other binaries, they are converted into base64 bytes. 

Once deployed, the scraper will be available as an HTTP API that you can integrate into any application.

## Local Development

```bash
# Create virtual environment
python3 -m venv venv
source venv/bin/activate

# Install dependencies
pip install -r requirements.txt
```

## Test Locally

```bash
# Run with Tensorlake's local runner
python main.py
```

Or test the crawler directly:

```bash
python -c "
from main import crawl
print(crawl({'url': 'https://example.com', 'max_depth': 2, 'max_links': 5}))
"
```

## Deploy to Tensorlake

```bash
# Login to Tensorlake
tensorlake login
# or set an API Key of your Tensorlake Project
export TENSORLAKE_API_KEY=tl_xxx

# Deploy
tensorlake deploy main.py
```

## Test with curl

Once deployed, test via the Tensorlake API:

```bash
curl -X POST https://api.tensorlake.ai/applications/crawl \
  -H "Content-Type: application/json" \
  -H "Authorization: Bearer $TENSORLAKE_API_KEY" \
  -d '{"url": "https://example.com", "max_depth": 2, "max_links": 10}'
```

## How It Works

The crawler uses PyDoll headless browser to render JavaScript content, then recursively follows links using depth-first search. Binary files (images, PDFs) are automatically base64 encoded. Each page fetch includes automatic retries and timeout handling.
