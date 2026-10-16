"""
Product Scraper Agent Source Package.

Exports main schemas and orchestration classes.
"""
from src.schemas import (
    ProductScrapeRequest,
    ProductScrapeResult,
    ProductRecord,
    ResultSummary,
    ProductError,
)

__all__ = [
    "ProductScrapeRequest",
    "ProductScrapeResult",
    "ProductRecord",
    "ResultSummary",
    "ProductError",
]
