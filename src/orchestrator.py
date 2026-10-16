"""
Product Scraper Orchestrator.

Coordinates the full workflow: discovery → extraction loop → validation → aggregation.
"""
import logging
import asyncio
import httpx
from datetime import datetime
from typing import List, Set, Optional

from src.schemas import (
    ProductScrapeRequest,
    ProductScrapeResult,
    ResultSummary,
    ProductRecord,
    ProductError,
    ErrorStageEnum,
    StatusEnum,
)
from src.discovery import LinkDiscovery, _HEADERS
from src.extraction import ProductExtractor
from src.browser_fetch import BrowserFetcher

logger = logging.getLogger(__name__)


class ProductScraperOrchestrator:
    """Orchestrates product scraping workflow."""
    
    def __init__(self,
                 request: ProductScrapeRequest,
                 aws_region: str = "us-east-1",
                 bedrock_model: str = "us.amazon.nova-2-lite-v1:0",
                 max_concurrent: int = 5):
        self.request = request
        self.aws_region = aws_region
        self.bedrock_model = bedrock_model
        self.max_concurrent = max_concurrent
        
        self.extractor = ProductExtractor(bedrock_model, aws_region)
        self.discovery = LinkDiscovery(
            request.source_url,
            request.allowed_domains,
            request.max_depth,
            use_browser=request.use_browser_fallback,
        )
        
        self.products: List[ProductRecord] = []
        self.errors: List[ProductError] = []
        self.summary = ResultSummary(
            discovered_links=0,
            processed_links=0,
            successful_products=0,
            failed_products=0,
            duplicates_removed=0,
            cache_hits=0,
        )
    
    async def run(self) -> ProductScrapeResult:
        """Execute full scraping workflow."""
        started_at = datetime.utcnow().isoformat() + "Z"
        use_browser = self.request.use_browser_fallback
        browser = BrowserFetcher() if use_browser else None

        try:
            async with httpx.AsyncClient(
                timeout=30.0, headers=_HEADERS, follow_redirects=True
            ) as session:
                try:
                    logger.info(f"[{self.request.request_id}] Phase 1: Link Discovery"
                                + (" [browser mode]" if use_browser else ""))
                    product_urls = await self.discovery.discover(
                        self.request.max_products, session=session, browser=browser
                    )
                    self.summary.discovered_links = len(product_urls)

                    if not product_urls:
                        logger.warning(f"[{self.request.request_id}] No product links discovered")
                        return self._build_result(started_at, StatusEnum.PARTIAL)

                    logger.info(
                        f"[{self.request.request_id}] Phase 2: Extracting details from "
                        f"{len(product_urls)} products"
                    )
                    await self._extract_products(product_urls, session=session, browser=browser)

                    logger.info(f"[{self.request.request_id}] Phase 3: Deduplication")
                    self.summary.duplicates_removed = self._deduplicate_products()

                    logger.info(f"[{self.request.request_id}] Phase 4: Quality Validation")
                    self.products = self._validate_products()

                    status = StatusEnum.COMPLETED if not self.errors else StatusEnum.PARTIAL
                    logger.info(
                        f"[{self.request.request_id}] Workflow complete: "
                        f"{len(self.products)} valid products, {len(self.errors)} errors"
                    )
                    return self._build_result(started_at, status)

                except Exception as e:
                    logger.exception(f"[{self.request.request_id}] Orchestration failed")
                    return self._build_result(started_at, StatusEnum.FAILED, error=str(e))
        finally:
            if browser:
                await browser.close()
    
    async def _extract_products(self, product_urls: List[str],
                                  session: Optional[httpx.AsyncClient] = None,
                                  browser: Optional[BrowserFetcher] = None) -> None:
        """Extract product data from URLs with concurrency control."""
        self.summary.processed_links = len(product_urls)
        semaphore = asyncio.Semaphore(self.max_concurrent)

        async def fetch_and_extract(url: str) -> None:
            async with semaphore:
                try:
                    # Browser path for JS-heavy/bot-protected sites
                    if browser is not None:
                        html = await browser.fetch(url)
                        if not html:
                            raise RuntimeError("Browser fetch returned empty content")
                    elif session is not None:
                        resp = await session.get(url)
                        resp.raise_for_status()
                        html = resp.text
                    else:
                        async with httpx.AsyncClient(
                            timeout=30.0, headers=_HEADERS, follow_redirects=True
                        ) as client:
                            resp = await client.get(url, follow_redirects=True)
                            resp.raise_for_status()
                            html = resp.text
                    
                    # Extract product data
                    record, metadata = await self.extractor.extract(
                        url,
                        html,
                        self.request.source_meta.currency_hint
                    )
                    
                    if record:
                        self.products.append(record)
                        self.summary.successful_products += 1
                    else:
                        self.errors.append(ProductError(
                            url=url,
                            stage=ErrorStageEnum.EXTRACT,
                            reason="Extraction returned None",
                            retry_count=0,
                            recoverable=True
                        ))
                        self.summary.failed_products += 1
                
                except Exception as e:
                    logger.error(f"Failed to extract {url}: {e}")
                    self.errors.append(ProductError(
                        url=url,
                        stage=ErrorStageEnum.FETCH,
                        reason=str(e),
                        retry_count=0,
                        recoverable=True
                    ))
                    self.summary.failed_products += 1
        
        # Execute all extractions concurrently
        await asyncio.gather(*[fetch_and_extract(url) for url in product_urls])
    
    def _deduplicate_products(self) -> int:
        """Remove duplicate products; falls back to URL as fingerprint when title is generic."""
        seen: Set[str] = set()
        unique_products = []
        duplicates = 0

        for product in self.products:
            title_lower = product.title.lower().strip()
            brand_lower = (product.brand or "").lower().strip()

            # Use URL as fingerprint when title carries no meaningful information
            if title_lower in ("", "unknown"):
                fingerprint = product.source_url
            else:
                fingerprint = f"{title_lower}:{brand_lower}"

            if fingerprint not in seen:
                seen.add(fingerprint)
                unique_products.append(product)
            else:
                duplicates += 1
                logger.debug(f"Removed duplicate: {product.title}")

        self.products = unique_products
        return duplicates
    
    def _validate_products(self) -> List[ProductRecord]:
        """Validate products against schema constraints."""
        valid = []
        
        for product in self.products:
            try:
                # Pydantic validation happens automatically
                # Additional business rules:
                
                # Require title
                if not product.title or len(product.title.strip()) == 0:
                    self.errors.append(ProductError(
                        url=product.source_url,
                        stage=ErrorStageEnum.VALIDATE,
                        reason="Missing required field: title",
                        retry_count=0
                    ))
                    self.summary.failed_products += 1
                    continue
                
                # Require price amount
                if product.price.amount <= 0:
                    self.errors.append(ProductError(
                        url=product.source_url,
                        stage=ErrorStageEnum.VALIDATE,
                        reason="Invalid price: amount must be > 0",
                        retry_count=0
                    ))
                    self.summary.failed_products += 1
                    continue
                
                valid.append(product)
            
            except Exception as e:
                logger.error(f"Validation error for {product.source_url}: {e}")
                self.errors.append(ProductError(
                    url=product.source_url,
                    stage=ErrorStageEnum.VALIDATE,
                    reason=str(e),
                    retry_count=0
                ))
                self.summary.failed_products += 1
        
        return valid
    
    def _build_result(self,
                      started_at: str,
                      status: StatusEnum,
                      error: Optional[str] = None) -> ProductScrapeResult:
        """Build final result object."""
        return ProductScrapeResult(
            request_id=self.request.request_id,
            source_url=self.request.source_url,
            status=status,
            started_at=started_at,
            finished_at=datetime.utcnow().isoformat() + "Z",
            summary=self.summary,
            products=self.products,
            errors=self.errors,
        )
