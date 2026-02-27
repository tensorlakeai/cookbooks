"""Fund Analysis — Pydantic Models

Data models for extracting and analyzing multi-fund financial reports.
Mirrors the FundData schema from the LlamaExtract Fidelity fund analysis example.
"""

from pydantic import BaseModel, Field
from typing import Optional


# ---------------------------------------------------------------------------
# Document splitting models
# ---------------------------------------------------------------------------

class SplitCategories(BaseModel):
    """Fund names discovered in the document's table of contents."""
    split_categories: list[str]


class PageSplit(BaseModel):
    """A detected split boundary on a page."""
    split_name: str = Field(
        description="Name in the format {split_key}_X where X is a short tag"
    )
    split_description: str = Field(description="Short description of the split")
    page_number: int = Field(description="Page number where the split starts")


class PageSplits(BaseModel):
    """All splits detected on a single page."""
    splits: list[PageSplit]


# ---------------------------------------------------------------------------
# Fund extraction models
# ---------------------------------------------------------------------------

class FundData(BaseModel):
    """Structured fund data extraction schema for financial reports."""

    # Identifiers
    fund_name: str = Field(
        description="Full fund name exactly as it appears, e.g. 'Fidelity Asset Manager 20%'"
    )
    target_equity_pct: Optional[int] = Field(
        default=None,
        description="Target equity percentage from fund name (20, 30, 40, 50, 60, 70, or 85)",
    )
    report_date: Optional[str] = Field(
        default=None, description="Report date in YYYY-MM-DD format"
    )

    # Asset Allocation (as percentages, e.g. 27.4 for 27.4%)
    equity_pct: Optional[float] = Field(
        default=None,
        description="Actual equity allocation percentage from 'Equity Central Funds' section",
    )
    fixed_income_pct: Optional[float] = Field(
        default=None,
        description="Fixed income allocation percentage from 'Fixed-Income Central Funds' section",
    )
    money_market_pct: Optional[float] = Field(
        default=None,
        description="Money market allocation percentage from 'Money Market Central Funds' section",
    )
    other_pct: Optional[float] = Field(
        default=None,
        description="Other investments percentage (Treasury + Investment Companies + other)",
    )

    # Primary Share Class Metrics
    nav: Optional[float] = Field(
        default=None,
        description="Net Asset Value per share for the main retail class",
    )
    net_assets_usd: Optional[float] = Field(
        default=None,
        description="Total net assets in USD for the main retail class",
    )
    expense_ratio: Optional[float] = Field(
        default=None,
        description="Expense ratio as percentage (e.g. 0.48 for 0.48%)",
    )
    management_fee: Optional[float] = Field(
        default=None,
        description="Management fee rate as percentage",
    )

    # Performance (as percentages)
    one_year_return: Optional[float] = Field(
        default=None,
        description="One-year total return percentage (e.g. 13.74 for 13.74%)",
    )
    portfolio_turnover: Optional[float] = Field(
        default=None, description="Portfolio turnover rate percentage"
    )

    # Risk Metrics (in USD)
    equity_futures_notional: Optional[float] = Field(
        default=None,
        description="Net notional amount of equity futures contracts",
    )
    bond_futures_notional: Optional[float] = Field(
        default=None,
        description="Net notional amount of bond/treasury futures contracts",
    )

    # Fund Flows (in USD)
    net_investment_income: Optional[float] = Field(
        default=None,
        description="Net investment income for the period from Statement of Operations",
    )
    total_distributions: Optional[float] = Field(
        default=None,
        description="Total distributions to shareholders",
    )
    net_asset_change: Optional[float] = Field(
        default=None,
        description="Net change in assets (end minus beginning net assets)",
    )


class FundComparisonData(BaseModel):
    """Collection of fund data optimized for analysis."""
    funds: list[FundData]

    def to_csv_rows(self) -> list[dict]:
        return [fund.model_dump() for fund in self.funds]


# ---------------------------------------------------------------------------
# Application I/O models
# ---------------------------------------------------------------------------

class FundAnalysisRequest(BaseModel):
    """Input for the fund analysis pipeline."""
    split_description: str = Field(
        default="Find and split by the main funds in this document",
        description="How to identify fund sections in the document.",
    )
    split_rules: str = Field(
        default=(
            "- You must split by the name of the fund\n"
            "- Each fund will have tables underneath it (schedule of investments, financial statements)\n"
            "- Each fund usually has schedule of investments right underneath it\n"
            "- Do not tag the cover page or table of contents"
        ),
        description="Rules for detecting fund section boundaries.",
    )
    split_key: str = Field(
        default="fund",
        description="Prefix for split names (e.g. fund_20pct).",
    )
    analysis_query: str = Field(
        default="",
        description="Optional natural language query to run on the extracted data.",
    )


class FundAnalysisReport(BaseModel):
    """Output from the fund analysis pipeline."""
    fund_data: FundComparisonData
    split_map: dict[str, list[int]] = Field(
        default_factory=dict,
        description="Mapping of split names to page numbers.",
    )
    total_pages: int = 0
    analysis_result: str = ""
    summary: str = ""
