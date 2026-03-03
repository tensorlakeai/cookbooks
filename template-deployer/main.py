"""
Template Deployer - Deploy Tensorlake applications from GitHub.

This application demonstrates:
- GitHub API integration for dynamic file discovery
- Parallel file fetching using map() - N files = N containers
- CLI-based deployment (same code path as manual deployment)
- Multi-stage orchestration with proper error handling

No hardcoded file lists. The deployer discovers what files exist
in a template directory and fetches them all in parallel.
"""
import ast
import os
import re
import sys
import tempfile
from dataclasses import dataclass, field
from typing import Optional, List, Dict, Tuple

import httpx
from tensorlake.applications import application, function, Retries, Image

# Custom image with dependencies needed to parse template source code
deployer_image = Image().run("pip install httpx beautifulsoup4")


def find_application_function_name(source_code: str) -> Optional[str]:
    """
    Find the name of the function decorated with @application in Python source code.

    Uses AST parsing for reliable detection.
    Returns the function name or None if not found.
    """
    try:
        tree = ast.parse(source_code)

        for node in ast.walk(tree):
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                # Check if this function has an @application decorator
                for decorator in node.decorator_list:
                    decorator_name = None
                    if isinstance(decorator, ast.Name):
                        decorator_name = decorator.id
                    elif isinstance(decorator, ast.Call):
                        if isinstance(decorator.func, ast.Name):
                            decorator_name = decorator.func.id

                    if decorator_name == "application":
                        return node.name

        return None
    except SyntaxError:
        return None


def find_entrypoint_file(files: Dict[str, str]) -> Optional[Tuple[str, str]]:
    """Scan all .py files for the @application decorator. Returns (filename, func_name) or None."""
    for name, content in files.items():
        if not name.endswith(".py"):
            continue
        func_name = find_application_function_name(content)
        if func_name:
            return (name, func_name)
    return None


def rename_application_function(source_code: str, old_name: str, new_name: str) -> str:
    """
    Rename a function throughout Python source code.

    This replaces the function definition and all references to it.
    Uses word-boundary matching to avoid partial replacements.
    """
    # Pattern matches the function name as a whole word
    # This handles: def old_name, old_name(, old_name), old_name,
    pattern = r'\b' + re.escape(old_name) + r'\b'
    return re.sub(pattern, new_name, source_code)


# Files to exclude from deployment package
EXCLUDED_FILES = {"README.md", "README", ".gitignore", "LICENSE", "__pycache__"}


@dataclass
class DeployRequest:
    """Request to deploy a template from GitHub."""
    template_id: str
    target_namespace: str
    app_name: str
    deployment_api_key: str
    github_repo: str = "tensorlakeai/examples"
    github_branch: str = "main"
    api_url: Optional[str] = None  # e.g. https://api.tensorlake.dev


@dataclass
class DeployResult:
    """Result of a deployment operation."""
    success: bool
    app_name: Optional[str] = None
    error: Optional[str] = None
    invoke_url: Optional[str] = None
    files_deployed: List[str] = field(default_factory=list)


@dataclass
class FileToFetch:
    """A file discovered via GitHub API."""
    name: str
    download_url: str


@application()
@function(timeout=180)
def deploy_template(request: dict) -> dict:
    """
    Deploy a template from a GitHub repository.

    Pipeline:
    1. Discover files via GitHub API (no hardcoded file list)
    2. Fetch all files in parallel using map()
    3. Deploy using tensorlake CLI

    This is Tensorlake deploying Tensorlake - the same code path
    as the CLI, orchestrated as a distributed application.
    """
    # Parse request dict into dataclass
    req = DeployRequest(
        template_id=request["template_id"],
        target_namespace=request["target_namespace"],
        app_name=request["app_name"],
        deployment_api_key=request["deployment_api_key"],
        github_repo=request.get("github_repo", "tensorlakeai/examples"),
        github_branch=request.get("github_branch", "main"),
        api_url=request.get("api_url"),
    )

    print(f"[STAGE] Starting deployment of template: {req.template_id}")
    print(f"[INFO] Target namespace: {req.target_namespace}")
    print(f"[INFO] App name: {req.app_name}")
    sys.stdout.flush()

    # Stage 1: Discover files in the template directory
    print(f"[STAGE] Discovering template files from {req.github_repo}@{req.github_branch}...")
    sys.stdout.flush()

    discovery = discover_template_files(
        repo=req.github_repo,
        branch=req.github_branch,
        template_id=req.template_id,
    )

    if discovery.get("error"):
        print(f"[ERROR] Discovery failed: {discovery['error']}")
        sys.stdout.flush()
        return {"success": False, "error": discovery["error"]}

    files_to_fetch: List[FileToFetch] = discovery["files"]

    if not files_to_fetch:
        print(f"[ERROR] Template '{req.template_id}' is empty or not found")
        sys.stdout.flush()
        return {
            "success": False,
            "error": f"Template '{req.template_id}' is empty or not found"
        }

    print(f"[SUCCESS] Found {len(files_to_fetch)} files to deploy")
    for f in files_to_fetch:
        print(f"[INFO]   - {f.name}")
    sys.stdout.flush()

    # Stage 2: Fetch all files in parallel using map()
    # Each file download runs in its own container
    print(f"[STAGE] Fetching {len(files_to_fetch)} files in parallel...")
    sys.stdout.flush()

    download_urls = [f.download_url for f in files_to_fetch]
    file_contents = fetch_file.map(download_urls)

    # Build files dictionary
    files: Dict[str, str] = {}
    for file_meta, content in zip(files_to_fetch, file_contents):
        if content is None:
            print(f"[ERROR] Failed to download {file_meta.name}")
            sys.stdout.flush()
            return {
                "success": False,
                "error": f"Failed to download {file_meta.name}"
            }
        files[file_meta.name] = content

    print(f"[SUCCESS] All {len(files)} files fetched successfully")
    sys.stdout.flush()

    # Stage 3: Auto-detect entrypoint and rename application function
    print(f"[STAGE] Detecting entrypoint and renaming application to: {req.app_name}")
    sys.stdout.flush()

    entrypoint = find_entrypoint_file(files)
    if not entrypoint:
        print(f"[ERROR] No @application decorated function found in any .py file")
        sys.stdout.flush()
        return {
            "success": False,
            "error": "No @application decorated function found in any .py file"
        }

    entrypoint_file, original_func_name = entrypoint
    print(f"[INFO] Found application function '{original_func_name}' in {entrypoint_file}")

    if original_func_name != req.app_name:
        print(f"[INFO] Renaming: {original_func_name} -> {req.app_name}")
        updated_content = rename_application_function(files[entrypoint_file], original_func_name, req.app_name)
        files[entrypoint_file] = updated_content
        print(f"[SUCCESS] Application renamed successfully")
    else:
        print(f"[INFO] Application name already matches, no rename needed")

    sys.stdout.flush()

    # Stage 4: Deploy using tensorlake CLI
    print(f"[STAGE] Deploying application via tensorlake CLI...")
    sys.stdout.flush()

    deploy_result = deploy_via_cli(
        files=files,
        namespace=req.target_namespace,
        api_key=req.deployment_api_key,
        api_url=req.api_url,
        entrypoint_file=entrypoint_file,
    )

    if not deploy_result["success"]:
        print(f"[ERROR] Deployment failed: {deploy_result.get('error')}")
        sys.stdout.flush()
        return {
            "success": False,
            "error": deploy_result.get("error"),
            "stderr": deploy_result.get("stderr"),
            "stdout": deploy_result.get("stdout"),
        }

    # Construct invoke URL using provided api_url or default
    api_base = req.api_url.rstrip('/') if req.api_url else "https://api.tensorlake.ai"
    invoke_url = f"{api_base}/v1/namespaces/{req.target_namespace}/applications/{req.app_name}"

    print(f"[SUCCESS] Application deployed successfully!")
    print(f"[INFO] Invoke URL: {invoke_url}")
    sys.stdout.flush()

    return {
        "success": True,
        "app_name": req.app_name,
        "invoke_url": invoke_url,
        "files_deployed": list(files.keys()),
        "deploy_output": deploy_result.get("stdout"),
    }


@function(timeout=30, retries=Retries(max_retries=2), image=deployer_image)
def discover_template_files(repo: str, branch: str, template_id: str) -> dict:
    """
    Discover files in a template directory using GitHub Contents API.

    Returns list of files with their download URLs. No hardcoding -
    discovers whatever files exist in the template directory.

    GET /repos/{owner}/{repo}/contents/{path}?ref={branch}
    """
    url = f"https://api.github.com/repos/{repo}/contents/{template_id}"
    params = {"ref": branch}
    headers = {
        "Accept": "application/vnd.github.v3+json",
        "User-Agent": "tensorlake-template-deployer",
    }

    _github_timeout = 15
    try:
        response = httpx.get(url, params=params, headers=headers, timeout=_github_timeout)

        if response.status_code == 404:
            return {"error": f"Template '{template_id}' not found in {repo}@{branch}"}

        if response.status_code == 403:
            # Check if rate limited
            if "rate limit" in response.text.lower():
                return {"error": "GitHub API rate limit exceeded. Try again later."}
            return {"error": f"GitHub API access denied: {response.text}"}

        response.raise_for_status()

        items = response.json()

        # Handle case where path is a file, not a directory
        if isinstance(items, dict):
            return {"error": f"'{template_id}' is a file, not a template directory"}

        # Filter to deployable files
        files = [
            FileToFetch(
                name=item["name"],
                download_url=item["download_url"],
            )
            for item in items
            if item["type"] == "file"
            and item["name"] not in EXCLUDED_FILES
            and not item["name"].startswith(".")
        ]

        return {"files": files}

    except httpx.TimeoutException:
        return {"error": f"GitHub API request timed out after {_github_timeout}"}
    except Exception as e:
        return {"error": f"GitHub API error: {str(e)}"}


@function(timeout=30, retries=Retries(max_retries=3), image=deployer_image)
def fetch_file(download_url: str) -> Optional[str]:
    """
    Fetch a single file from GitHub raw URL.

    Each invocation runs in its own container. When called via map(),
    N files are fetched by N parallel containers simultaneously.

    Uses raw.githubusercontent.com which has generous rate limits
    (separate from the GitHub API limits).
    """
    try:
        response = httpx.get(
            download_url,
            timeout=15,
            follow_redirects=True,
            headers={"User-Agent": "tensorlake-template-deployer"},
        )
        response.raise_for_status()
        return response.text
    except Exception:
        return None


@function(timeout=120, image=deployer_image)
def deploy_via_cli(files: Dict[str, str], namespace: str, api_key: str, entrypoint_file: str, api_url: Optional[str] = None) -> dict:
    """
    Deploy application using tensorlake CLI.

    Writes files to temp directory and runs `tensorlake deploy`.
    This uses the exact same code path as manual CLI deployment.

    The API key is passed from the request (customer's temporary scoped key).
    Organization is inferred from the API key's project scope.

    Output is streamed line-by-line via print() for real-time log visibility.
    """
    import subprocess

    with tempfile.TemporaryDirectory() as tmpdir:
        # Write all files to temp directory
        for filename, content in files.items():
            filepath = os.path.join(tmpdir, filename)
            with open(filepath, "w") as f:
                f.write(content)
            print(f"[INFO] Wrote {filename}")
        sys.stdout.flush()

        main_path = os.path.join(tmpdir, entrypoint_file)

        try:
            # Run tensorlake deploy with API key via environment variable
            # Organization is inferred from the API key's project scope
            env = os.environ.copy()
            env["TENSORLAKE_API_KEY"] = api_key
            if api_url:
                env["TENSORLAKE_API_URL"] = api_url

            print(f"[CLI] Running: tensorlake --namespace {namespace} deploy {entrypoint_file}")
            sys.stdout.flush()

            # Use Popen for streaming output line-by-line
            process = subprocess.Popen(
                [
                    "tensorlake",
                    "--namespace", namespace,
                    "deploy", main_path,
                ],
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                text=True,
                cwd=tmpdir,
                env=env,
                bufsize=1,  # Line buffered
            )

            output_lines = []
            # Stream output line-by-line
            for line in process.stdout:
                line = line.rstrip('\n')
                if line:  # Skip empty lines
                    print(f"[CLI] {line}")
                    sys.stdout.flush()
                    output_lines.append(line)

            process.wait(timeout=90)
            output = '\n'.join(output_lines)

            if process.returncode != 0:
                print(f"[ERROR] tensorlake deploy exited with code {process.returncode}")
                sys.stdout.flush()
                return {
                    "success": False,
                    "error": f"tensorlake deploy failed with exit code {process.returncode}",
                    "stdout": output,
                }

            print(f"[SUCCESS] tensorlake deploy completed successfully")
            sys.stdout.flush()
            return {
                "success": True,
                "stdout": output,
            }

        except subprocess.TimeoutExpired:
            process.kill()
            print(f"[ERROR] Deployment timed out after 90 seconds")
            sys.stdout.flush()
            return {"success": False, "error": "Deployment timed out"}
        except Exception as e:
            import traceback
            print(f"[ERROR] Deployment exception: {str(e)}")
            sys.stdout.flush()
            return {
                "success": False,
                "error": f"Deployment failed: {str(e)}",
                "traceback": traceback.format_exc()
            }


