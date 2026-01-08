"""
Utility functions for the Personal Finance Tensorlake Application.
"""

from datetime import datetime


def parse_date(date_value: str | None):
    """
    Parse a date string into a date object.

    Supports formats:
    - YYYY-MM-DD (ISO format)
    - MM/DD/YYYY (US format)
    - DD/MM/YYYY (European format)
    - MM-DD-YYYY (US with dashes)

    Args:
        date_value: Date string or None

    Returns:
        datetime.date object or None

    Raises:
        ValueError: If date string cannot be parsed
    """
    if date_value is None:
        return None

    if isinstance(date_value, str):
        formats = [
            "%Y-%m-%d",   # ISO: 2024-12-15
            "%m/%d/%Y",   # US: 12/15/2024
            "%d/%m/%Y",   # EU: 15/12/2024
            "%m-%d-%Y",   # US dashes: 12-15-2024
        ]
        for fmt in formats:
            try:
                return datetime.strptime(date_value, fmt).date()
            except ValueError:
                continue
        raise ValueError(f"Could not parse date: {date_value}")

    return date_value
