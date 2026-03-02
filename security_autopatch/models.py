from pydantic import BaseModel, Field


class FileSnippet(BaseModel):
    path: str
    content: str
    line_count: int = 0


class SecuritySweepRequest(BaseModel):
    repo_path: str = "."
    repo_url: str = ""
    repo_branch: str = ""
    include_globs: list[str] = Field(default_factory=lambda: ["**/*.py", "**/*.js", "**/*.ts", "**/*.rs", "**/*.go"])
    exclude_globs: list[str] = Field(
        default_factory=lambda: [
            "**/.venv/**",
            "**/venv/**",
            "**/node_modules/**",
            "**/dist/**",
            "**/build/**",
            "**/migrations/**",
            "**/__pycache__/**",
        ]
    )
    file_extensions: list[str] = Field(default_factory=lambda: [".py", ".js", ".ts", ".rs", ".go"])
    vulnerability_classes: list[str] = Field(
        default_factory=lambda: ["idor", "sql_injection", "ssrf", "command_injection"]
    )
    max_files_per_detector: int = Field(default=20, ge=1, le=200)
    max_chars_per_file: int = Field(default=8000, ge=200, le=200000)
    max_findings_per_detector: int = Field(default=5, ge=1, le=20)
    model: str = ""  # Model selection handled by Claude Agent SDK
    test_command: str = ""
    run_validation: bool = True
    generate_fixes: bool = True


class SecuritySweepReport(BaseModel):
    repo_path: str
    repo_branch: str = ""
    files_scanned: int
    vulnerability_classes: list[str] = Field(default_factory=list)
    summary_markdown: str
    stage_outputs: dict[str, dict[str, str]] = Field(default_factory=dict)
