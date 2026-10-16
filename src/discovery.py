"""
Product Link Discovery Module.

Discovers product URLs from a seed catalog/search page using 
link extraction and optional browser rendering.
"""
import logging
import asyncio
from typing import List, Set, Optional, Any
from urllib.parse import urljoin, urlparse
import httpx
from bs4 import BeautifulSoup
from src.browser_fetch import BrowserFetcher

logger = logging.getLogger(__name__)

# Minimal browser headers to avoid bot-detection 403s on sites like eBay
_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/125.0.0.0 Safari/537.36"
    ),
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,*/*;q=0.8",
    "Accept-Language": "en-US,en;q=0.9",
    "Accept-Encoding": "gzip, deflate, br",
    "Connection": "keep-alive",
    "Upgrade-Insecure-Requests": "1",
    "Sec-Fetch-Dest": "document",
    "Sec-Fetch-Mode": "navigate",
    "Sec-Fetch-Site": "none",
    "Sec-Fetch-User": "?1",
}


class LinkDiscovery:
    """Discovers product links from a seed URL."""

    def __init__(self, seed_url: str, allowed_domains: List[str], max_depth: int = 2,
                 use_browser: bool = False):
        self.seed_url = seed_url
        self.allowed_domains = set(allowed_domains)
        self.max_depth = max_depth
        self.use_browser = use_browser
        self.discovered_urls: Set[str] = set()
        self.visited_urls: Set[str] = set()
    
    def _is_allowed_domain(self, url: str) -> bool:
        """Check if URL belongs to allowed domains."""
        try:
            parsed = urlparse(url)
            domain = parsed.netloc.lower()
            # Strip www prefix for comparison
            domain_clean = domain.replace("www.", "")
            for allowed in self.allowed_domains:
                allowed_clean = allowed.replace("www.", "")
                if domain_clean == allowed_clean or domain == allowed:
                    return True
        except Exception:
            pass
        return False
    
    def _is_product_url(self, url: str) -> bool:
        """Heuristic to identify if URL is a product page."""
        product_keywords = [
            "/product/", "/item/", "/p/", "/products/",
            "/sku/", "/details/", "/listing/",
            "product_id=", "item_id=", "id=",
            "/itm/",        # eBay individual listing
            "-in-bulk",     # WeSellCellular model pages
            "/dp/",         # Amazon product pages (ASIN)
        ]
        url_lower = url.lower()
        return any(kw in url_lower for kw in product_keywords)
    
    @staticmethod
    def _shopify_json_url(seed_url: str) -> Optional[str]:
        """Return the products.json API URL if seed looks like a Shopify collection."""
        parsed = urlparse(seed_url)
        path = parsed.path.rstrip("/")
        if "/collections/" in path and not path.endswith("/products.json"):
            base = f"{parsed.scheme}://{parsed.netloc}"
            return f"{base}{path}/products.json?limit=250"
        return None

    async def _discover_shopify(self, json_url: str, max_links: int,
                                 session: Optional[Any] = None) -> List[str]:
        """Fetch Shopify products.json and return individual product URLs."""
        import json as _json
        base = "{u.scheme}://{u.netloc}".format(u=urlparse(self.seed_url))

        async def _get(client):
            resp = await client.get(json_url, follow_redirects=True)
            resp.raise_for_status()
            return resp.json()

        if session is not None:
            data = await _get(session)
        else:
            async with httpx.AsyncClient(
                timeout=30.0, headers=_HEADERS, follow_redirects=True
            ) as client:
                data = await _get(client)

        links = []
        for product in data.get("products", [])[:max_links]:
            handle = product.get("handle")
            if handle:
                # Derive collection path from seed URL for the product URL
                parsed = urlparse(self.seed_url)
                collection_path = parsed.path.rstrip("/")
                links.append(f"{base}{collection_path}/products/{handle}")
        logger.info(f"Shopify JSON API: found {len(links)} product links")
        return links

    async def discover(self, max_links: int = 200,
                        session: Optional[Any] = None,
                        browser: Optional[BrowserFetcher] = None) -> List[str]:
        """
        Discover product links from seed URL.
        Uses Playwright when use_browser=True and a BrowserFetcher is provided.
        """
        import httpx
        try:
            logger.info(f"Starting link discovery from {self.seed_url}")

            # Shopify stores: use products.json API — bypasses Cloudflare JS challenges
            shopify_url = self._shopify_json_url(self.seed_url)
            if shopify_url:
                try:
                    links = await self._discover_shopify(shopify_url, max_links, session)
                    if links:
                        return links[:max_links]
                    logger.debug("Shopify JSON returned no products; falling back to HTML")
                except Exception as e:
                    logger.debug(f"Shopify JSON fallback failed ({e}); using HTML discovery")

            # Fetch seed page — browser path or httpx path
            if self.use_browser and browser is not None:
                html = await browser.fetch(self.seed_url)
                if not html:
                    logger.warning("Browser fetch returned no content; falling back to httpx")
                    html = None
            else:
                html = None

            if html is None:
                async def _fetch(client):
                    resp = await client.get(self.seed_url, follow_redirects=True)
                    resp.raise_for_status()
                    return resp.text

                if session is not None:
                    html = await _fetch(session)
                else:
                    async with httpx.AsyncClient(
                        timeout=30.0, headers=_HEADERS, follow_redirects=True
                    ) as client:
                        html = await _fetch(client)
            
            # Extract all links
            soup = BeautifulSoup(html, "html.parser")
            product_links = []
            
            for link in soup.find_all("a", href=True):
                href = link.get("href", "").strip()
                if not href:
                    continue
                
                # Resolve relative URLs
                full_url = urljoin(self.seed_url, href)
                
                # Filter by domain
                if not self._is_allowed_domain(full_url):
                    continue
                
                # Remove fragments
                full_url = full_url.split("#")[0]
                
                # Heuristic product URL detection
                if self._is_product_url(full_url) and full_url not in self.discovered_urls:
                    product_links.append(full_url)
                    self.discovered_urls.add(full_url)
                    
                    if len(product_links) >= max_links:
                        break
            
            logger.info(f"Discovered {len(product_links)} product links")
            return product_links[:max_links]
        
        except Exception as e:
            logger.error(f"Error during link discovery: {e}")
            return []
