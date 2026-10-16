"""
Tests for schemas module.
"""
import pytest
from src.schemas import (
    ProductScrapeRequest,
    ProductRecord,
    Money,
    AvailabilityEnum,
    ExtractionMeta,
)


def test_request_schema_valid():
    """Test valid ProductScrapeRequest."""
    req = ProductScrapeRequest(
        request_id="123e4567-e89b-12d3-a456-426614174000",
        tenant_id="test-tenant",
        source_url="https://example.com/products",
        crawl_ts="2026-08-22T10:00:00Z",
        max_products=100,
        max_depth=2,
        allowed_domains=["example.com"],
        use_browser_fallback=False,
        include_raw_html=False,
        source_meta={"country": "US", "currency_hint": "USD"},
    )
    assert req.request_id == "123e4567-e89b-12d3-a456-426614174000"
    assert req.tenant_id == "test-tenant"


def test_request_schema_invalid_url():
    """Test ProductScrapeRequest with invalid URL."""
    with pytest.raises(ValueError):
        ProductScrapeRequest(
            request_id="123e4567-e89b-12d3-a456-426614174000",
            tenant_id="test-tenant",
            source_url="not-a-url",
            crawl_ts="2026-08-22T10:00:00Z",
            max_products=100,
            max_depth=2,
            allowed_domains=["example.com"],
            use_browser_fallback=False,
            include_raw_html=False,
            source_meta={"country": "US", "currency_hint": "USD"},
        )


def test_product_record_valid():
    """Test valid ProductRecord."""
    record = ProductRecord(
        product_id="p_123456",
        source_url="https://example.com/product/abc",
        title="Test Product",
        brand="Test Brand",
        description="Test Description",
        category="Electronics",
        price=Money(amount=99.99, currency="USD"),
        availability=AvailabilityEnum.IN_STOCK,
        images=["https://example.com/image1.jpg"],
        confidence=0.95,
        extraction_meta=ExtractionMeta(
            model="nova-2-lite",
            token_in=1000,
            token_out=500,
            cache_hit=False,
            schema_version="v1",
        ),
    )
    assert record.product_id == "p_123456"
    assert record.confidence == 0.95
    assert record.price.amount == 99.99


def test_product_record_invalid_confidence():
    """Test ProductRecord with invalid confidence."""
    with pytest.raises(ValueError):
        ProductRecord(
            product_id="p_123456",
            source_url="https://example.com/product/abc",
            title="Test Product",
            brand="Test Brand",
            description="Test Description",
            category="Electronics",
            price=Money(amount=99.99, currency="USD"),
            availability=AvailabilityEnum.IN_STOCK,
            confidence=1.5,  # Invalid: > 1
            extraction_meta=ExtractionMeta(
                model="nova-2-lite",
                token_in=1000,
                token_out=500,
                cache_hit=False,
                schema_version="v1",
            ),
        )
