"""
Configuration for the Personal Finance Tensorlake Application.

This module contains Tensorlake image definitions and other configuration.
"""

from tensorlake.applications import Image


# =============================================================================
# Tensorlake Image Definitions
# =============================================================================

def create_agent_image() -> Image:
    """
    Create the image for running Claude agents.

    Includes:
    - Node.js 24 for Claude CLI
    - Claude Agent SDK
    - Database connectivity (asyncpg)
    """
    return (
        Image(name="agent-image")
        .run("apt-get update && apt-get install -y curl")
        .run("curl -fsSL https://deb.nodesource.com/setup_24.x | bash -")
        .run("apt-get install -y nodejs")
        .run(
            'npm install --global --ignore-scripts pnpm@11.28.5 && '
            'mkdir -p /usr/local/lib/tensorlake-tools && '
            'pnpm --dir /usr/local/lib/tensorlake-tools --config.minimumReleaseAge=1440 '
            '--config.minimumReleaseAgeStrict=true '
            '--config.minimumReleaseAgeIgnoreMissingTime=false '
            '--config.trustLockfile=false --config.blockExoticSubdeps=true '
            '--config.strictDepBuilds=true --config.optimisticRepeatInstall=false add '
            '--ignore-scripts --save-exact @anthropic-ai/claude-code@2.1.294 && '
            'node /usr/local/lib/tensorlake-tools/node_modules/@anthropic-ai/claude-code/install.cjs && '
            '/usr/local/lib/tensorlake-tools/node_modules/.bin/claude --version && '
            'for executable in /usr/local/lib/tensorlake-tools/node_modules/.bin/*; do [ ! '
            '-e "$executable" ] || ln -sf "$executable" /usr/local/bin/; done'
        )
        .run("pip install claude-agent-sdk>=0.1.0 asyncpg>=0.29.0 pydantic>=2.0.0")
    )


def create_parser_image() -> Image:
    """
    Create the image for document parsing.

    Includes:
    - Tensorlake SDK for DocumentAI
    """
    return (
        Image(name="parser-image")
        .run("pip install tensorlake>=0.1.0")
    )


def create_code_execution_image() -> Image:
    """
    Create the image for code execution (charts, reports).

    Includes:
    - Python data science libraries (matplotlib, pandas, numpy, etc.)
    - Report generation tools (jinja2, weasyprint, reportlab)
    - Node.js charting libraries
    """
    return (
        Image(name="code-exec-image")
        .run("apt-get update && apt-get install -y curl fonts-liberation")
        .run("curl -fsSL https://deb.nodesource.com/setup_24.x | bash -")
        .run("apt-get install -y nodejs")
        # Python data science and plotting libraries
        .run("pip install matplotlib pandas numpy plotly seaborn scipy scikit-learn")
        # Report generation
        .run("pip install jinja2 weasyprint reportlab fpdf2")
        # Additional utilities
        .run("pip install pillow openpyxl xlsxwriter tabulate")
        # Node.js charting and report libraries
        .run(
            'npm install --global --ignore-scripts pnpm@11.28.5 && '
            'mkdir -p /usr/local/lib/tensorlake-tools && '
            'pnpm --dir /usr/local/lib/tensorlake-tools --config.minimumReleaseAge=1440 '
            '--config.minimumReleaseAgeStrict=true '
            '--config.minimumReleaseAgeIgnoreMissingTime=false '
            '--config.trustLockfile=false --config.blockExoticSubdeps=true '
            '--config.strictDepBuilds=true --config.optimisticRepeatInstall=false add '
            '--ignore-scripts --save-exact chart.js@4.5.1 puppeteer@25.12.0 pdfkit@0.20.2 '
            'xlsx@0.18.5 && '
            'for executable in /usr/local/lib/tensorlake-tools/node_modules/.bin/*; do [ ! '
            '-e "$executable" ] || ln -sf "$executable" /usr/local/bin/; done'
        )
        .run("pnpm --dir /usr/local/lib/tensorlake-tools exec puppeteer browsers install chrome")
        .env("NODE_PATH", "/usr/local/lib/tensorlake-tools/node_modules")
    )


# Create image instances
agent_image = create_agent_image()
parser_image = create_parser_image()
code_exec_image = create_code_execution_image()
