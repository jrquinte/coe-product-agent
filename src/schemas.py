"""
Schemas for Product Scraper Agent.
Pydantic models matching OpenAPI contract.
"""
from typing import Optional, List, Dict, Any
from pydantic import BaseModel, Field, field_validator
from datetime import datetime
from enum import Enum


class AvailabilityEnum(str, Enum):
    IN_STOCK = "in_stock"
    OUT_OF_STOCK = "out_of_stock"
    PREORDER = "preorder"
    UNKNOWN = "unknown"


class ErrorStageEnum(str, Enum):
    DISCOVER = "discover"
    FETCH = "fetch"
    EXTRACT = "extract"
    VALIDATE = "validate"
    DEDUP = "dedup"


class StatusEnum(str, Enum):
    COMPLETED = "completed"
    PARTIAL = "partial"
    FAILED = "failed"


class SourceMeta(BaseModel):
    """Source metadata."""
    country: str = Field(..., min_length=2, max_length=2, pattern="^[A-Z]{2}$")
    currency_hint: str = Field(..., min_length=3, max_length=3, pattern="^[A-Z]{3}$")


class ProductScrapeRequest(BaseModel):
    """Request contract for product scraping."""
    request_id: str = Field(..., description="UUID for trace/idempotency")
    tenant_id: str = Field(..., min_length=1, max_length=128)
    source_url: str = Field(..., description="Seed URL for discovery")
    crawl_ts: str = Field(..., description="ISO8601 timestamp")
    max_products: int = Field(default=200, ge=1, le=1000)
    max_depth: int = Field(default=2, ge=0, le=5)
    allowed_domains: List[str] = Field(..., min_items=1, max_items=50)
    use_browser_fallback: bool = Field(default=False)
    include_raw_html: bool = Field(default=False)
    source_meta: SourceMeta

    @field_validator("source_url")
    @classmethod
    def validate_url(cls, v: str):
        if not v.startswith(("http://", "https://")):
            raise ValueError("Must be valid HTTP(S) URL")
        return v


class Money(BaseModel):
    """Price information."""
    amount: float = Field(..., ge=0)
    currency: str = Field(..., min_length=3, max_length=3, pattern="^[A-Z]{3}$")


class ProductAttributes(BaseModel):
    """Flexible product attributes."""
    color: Optional[str] = None
    size: Optional[str] = None

    class Config:
        extra = "allow"


class ProductVariant(BaseModel):
    """Product variant with SKU and price."""
    sku: Optional[str] = Field(None, max_length=256)
    price: Money


class ExtractionMeta(BaseModel):
    """Metadata about extraction process."""
    model: str = Field(..., min_length=1, max_length=128)
    token_in: int = Field(..., ge=0)
    token_out: int = Field(..., ge=0)
    cache_hit: bool
    schema_version: str = Field(..., min_length=1, max_length=32)
    extraction_duration_ms: Optional[int] = Field(None, ge=0)
    raw_html_ref: Optional[str] = None


class ProductRecord(BaseModel):
    """Single extracted product."""
    product_id: str = Field(..., min_length=1, max_length=256)
    source_url: str = Field(...)
    canonical_url: Optional[str] = None
    title: str = Field(..., min_length=1, max_length=1000)
    brand: Optional[str] = Field(None, max_length=256)
    description: Optional[str] = Field(None, max_length=20000)
    category: Optional[str] = Field(None, max_length=512)
    price: Money
    availability: AvailabilityEnum = AvailabilityEnum.UNKNOWN
    images: List[str] = Field(default_factory=list, max_items=50)
    attributes: ProductAttributes = Field(default_factory=ProductAttributes)
    variants: List[ProductVariant] = Field(default_factory=list, max_items=200)
    confidence: float = Field(..., ge=0, le=1)
    extraction_meta: ExtractionMeta


class ProductError(BaseModel):
    """Error information for a failed product."""
    url: str
    stage: ErrorStageEnum
    reason: str = Field(..., min_length=1, max_length=4000)
    retry_count: int = Field(..., ge=0)
    recoverable: Optional[bool] = None


class ResultSummary(BaseModel):
    """Summary statistics of scraping run."""
    discovered_links: int = Field(..., ge=0)
    processed_links: int = Field(..., ge=0)
    successful_products: int = Field(..., ge=0)
    failed_products: int = Field(..., ge=0)
    duplicates_removed: int = Field(..., ge=0)
    cache_hits: int = Field(..., ge=0)


class ProductScrapeResult(BaseModel):
    """Result contract for product scraping."""
    request_id: str
    source_url: str
    status: StatusEnum
    started_at: str
    finished_at: str
    summary: ResultSummary
    products: List[ProductRecord] = Field(default_factory=list)
    errors: List[ProductError] = Field(default_factory=list)
