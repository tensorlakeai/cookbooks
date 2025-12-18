# Template Deployer

**Deploy Tensorlake applications programmatically from GitHub.**

This example demonstrates advanced Tensorlake patterns by implementing a meta-application: a Tensorlake Application that deploys other Tensorlake Applications. This is the same code that powers one-click template deployment in the Tensorlake dashboard.

## What It Demonstrates

| Pattern | How It's Used |
|---------|---------------|
| **GitHub API Integration** | Discovers files dynamically - no hardcoded file lists |
| **Parallel Execution (map)** | Fetches N files using N parallel containers |
| **SDK Internals** | Uses `load_code()` and `create_application_manifest()` - same as CLI |
| **Secret Management** | Securely stores API credentials for deployment |
| **Multi-Stage Pipeline** | Discover → Fetch → Parse → Deploy |

## Architecture

```
                         deploy_template()
                               │
         ┌─────────────────────┼─────────────────────┐
         │                     │                     │
         ▼                     │                     │
┌─────────────────┐            │                     │
│ discover_files  │ ◄──────────┘                     │
│  (GitHub API)   │                                  │
└────────┬────────┘                                  │
         │ returns: [file1, file2, file3]            │
         ▼                                           │
┌─────────────────┐                                  │
│  fetch_file     │ .map([url1, url2, url3])         │
│  (3 parallel    │ ─────────────────────────────────┤
│   containers)   │                                  │
└────────┬────────┘                                  │
         │ returns: [content1, content2, content3]   │
         ▼                                           │
┌─────────────────┐                                  │
│generate_manifest│ ◄────────────────────────────────┤
│  (SDK internals)│                                  │
└────────┬────────┘                                  │
         │                                           │
         ▼                                           │
┌─────────────────┐                                  │
│deploy_to_namespace│ ◄──────────────────────────────┘
│  (APIClient)    │
└─────────────────┘
```

## Key Features

### Dynamic File Discovery

No hardcoded file lists. The deployer uses the GitHub Contents API to discover what files exist:

```python
@function(timeout=30, retries=Retries(max_retries=2))
def discover_template_files(repo: str, branch: str, template_id: str) -> dict:
    url = f"https://api.github.com/repos/{repo}/contents/{template_id}"
    response = httpx.get(url, params={"ref": branch}, ...)
    # Returns all files in the directory
```

### Parallel File Fetching

Files are fetched in parallel using `map()` - each file in its own container:

```python
# 5 files = 5 parallel containers
download_urls = [f.download_url for f in files_to_fetch]
file_contents = fetch_file.map(download_urls)
```

### SDK Internals

Uses the exact same code path as `tensorlake deploy` CLI:

```python
from tensorlake.cli.deploy import load_code, get_functions
from tensorlake.applications.remote.manifests.application import create_application_manifest

load_code(main_path)  # Executes decorators, registers functions
functions = get_functions()
manifest = create_application_manifest(app_func, functions)
```

## Usage

### Request Schema

```json
{
  "template_id": "web-scraper",
  "target_namespace": "my-project",
  "app_name": "my-web-scraper",
  "github_repo": "tensorlakeai/examples",
  "github_branch": "main"
}
```

### Response Schema

```json
{
  "success": true,
  "app_name": "my-web-scraper",
  "invoke_url": "https://api.tensorlake.ai/v1/namespaces/my-project/applications/my-web-scraper/invoke",
  "files_deployed": ["main.py", "requirements.txt"]
}
```

### Deploy from a Fork

```json
{
  "template_id": "my-custom-template",
  "target_namespace": "my-project",
  "app_name": "custom-app",
  "github_repo": "myorg/my-examples-fork",
  "github_branch": "feature-branch"
}
```

## Configuration

### Required Secret

```bash
tensorlake secrets set TENSORLAKE_DEPLOYER_API_KEY=<your-api-key>
```

The API key needs permissions to create applications in target namespaces.

## Deploy

```bash
cd template-deployer
tensorlake deploy
```

## The Meta Beauty

```
User clicks "Deploy web-scraper" in dashboard
                    │
                    ▼
         Platform invokes template-deployer
         (a Tensorlake Application)
                    │
                    ▼
         template-deployer discovers files via GitHub API
                    │
                    ▼
         template-deployer fetches files in parallel (map)
                    │
                    ▼
         template-deployer parses code using SDK internals
                    │
                    ▼
         template-deployer deploys web-scraper to user's namespace
                    │
                    ▼
         User's web-scraper is now running!
```

**Tensorlake deploying Tensorlake.** The first thing users see the platform do is deploy their application using the same distributed patterns they'll use in their own code.
