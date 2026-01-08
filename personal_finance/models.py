"""
Pydantic models for the Personal Finance Tensorlake Application.

This module contains all the data models used for input/output
validation and serialization.
"""

from typing import Optional
from pydantic import BaseModel, Field


# =============================================================================
# Application Response Models
# =============================================================================

class FinanceResponse(BaseModel):
    """Output from the finance_analyzer application."""
    success: bool
    message: str
    account_type: Optional[str] = None
    account_name: Optional[str] = None
    account_holder: Optional[str] = None
    statement_date: Optional[str] = None
    total_transactions: int = 0
    total_spending: float = 0.0
    total_income: float = 0.0
    categories_summary: dict = Field(default_factory=dict)


class QueryResponse(BaseModel):
    """Output from the finance_query application."""
    success: bool
    question: str
    answer: str
    sql_query: Optional[str] = None
    raw_results: Optional[list] = None
    files_created: Optional[list] = None


# =============================================================================
# Finance Agent Function Models
# =============================================================================

class DocumentContentInput(BaseModel):
    """Input for getting document content."""
    file_id: str
    content: str


class DocumentContentResult(BaseModel):
    """Result of getting document content."""
    file_id: str
    content: str


class SaveTransactionsInput(BaseModel):
    """Input for saving transactions to the database."""
    statement_date: str
    account_type: str
    account_name: str
    account_holder: Optional[str] = None
    transactions: list
    database_url: str


class SaveTransactionsResult(BaseModel):
    """Result of saving transactions."""
    success: bool
    saved_count: int = 0
    skipped_duplicates: int = 0
    total_spending: float = 0.0
    total_income: float = 0.0
    categories: dict = Field(default_factory=dict)
    error: Optional[str] = None


class ExtractionSchemaResult(BaseModel):
    """Schema for transaction extraction."""
    schema_text: str


# =============================================================================
# Query Agent Function Models
# =============================================================================

class SQLQueryInput(BaseModel):
    """Input for SQL query execution."""
    sql: str
    explanation: str = ""


class SQLQueryResult(BaseModel):
    """Result of SQL query execution."""
    success: bool
    rows: list = Field(default_factory=list)
    row_count: int = 0
    error: Optional[str] = None
    formatted_output: str = ""


class SchemaResult(BaseModel):
    """Database schema result."""
    schema_text: str


class CodeExecutionInput(BaseModel):
    """Input for code execution."""
    code: str
    language: str = "python"
    timeout: int = 30


class CodeExecutionResult(BaseModel):
    """Result of code execution."""
    success: bool
    output: str = ""
    error: Optional[str] = None
    files_created: list = Field(default_factory=list)
