"""
Product Extraction Module.

Uses AWS Bedrock with configurable models to extract structured 
product data from HTML content.
"""
import logging
import json
import time
import hashlib
from typing import Optional, Dict, Any, Tuple
from urllib.parse import urlparse, parse_qs
import httpx
import boto3
from bs4 import BeautifulSoup

from src.schemas import ProductRecord, ExtractionMeta, Money, AvailabilityEnum

logger = logging.getLogger(__name__)


class ProductExtractor:
    """Extracts product data using Bedrock."""
    
    # System prompt for extraction
    EXTRACTION_SYSTEM_PROMPT = """You are an expert product data extraction agent.
Your task is to extract structured product information from HTML content and return valid JSON.

CRITICAL RULES:
1. Return ONLY valid JSON - no markdown, no explanations, no code blocks.
2. For missing fields, use null instead of empty strings or fabrications.
3. Preserve all field names exactly as specified.
4. Validate all data against constraints:
   - amount >= 0
   - confidence between 0 and 1
   - availability must be one of: in_stock, out_of_stock, preorder, unknown
5. Use provided currency from context (default USD).
6. Extract images as full URLs if possible, otherwise null.
7. Do not hallucinate fields - only extract what is clearly present.
"""
    
    EXTRACTION_USER_TEMPLATE = """Extract product information from this HTML snippet.
Return a JSON object matching this structure (use null for missing fields):

{{
  "product_id": "unique_identifier",
  "title": "product_title",
  "brand": "brand_name or null",
  "description": "product_description or null",
  "category": "category or null",
  "price": {{
    "amount": number,
    "currency": "{currency}"
  }},
  "availability": "in_stock|out_of_stock|preorder|unknown",
  "images": ["url1", "url2"],
  "attributes": {{
    "color": "value or null",
    "size": "value or null"
  }},
  "variants": [
    {{"sku": "sku_or_null", "price": {{"amount": number, "currency": "{currency}"}}}}
  ],
  "confidence": 0.95
}}

HTML CONTENT:
{html}"""
    
    def __init__(self, 
                 bedrock_model: str = "us.amazon.nova-2-lite-v1:0",
                 aws_region: str = "us-east-1"):
        self.bedrock_model = bedrock_model
        self.bedrock_client = boto3.client("bedrock-runtime", region_name=aws_region)
    
    async def extract(self, 
                      url: str,
                      html: str,
                      currency: str = "USD") -> Tuple[Optional[ProductRecord], Dict[str, Any]]:
        """
        Extract product data from HTML.
        
        Args:
            url: Source product URL
            html: HTML content
            currency: Currency code (USD, EUR, etc)
        
        Returns:
            Tuple of (ProductRecord or None, metadata dict with tokens/duration)
        """
        start_time = time.time()
        metadata: Dict[str, Any] = {
            "model": self.bedrock_model,
            "token_in": 0,
            "token_out": 0,
            "cache_hit": False,
            "schema_version": "v1",
            "extraction_duration_ms": 0,
        }
        
        try:
            # Layer 1: URL params  (zero cost)
            url_data = self._extract_from_url(url, currency)

            # Layer 2: HTML structured fields  (zero cost)
            html_data = self._parse_html_fields(html, url, currency)

            # Layer 3: Bedrock enrichment (fills gaps, improves confidence)
            html_truncated = self._truncate_html(html, max_tokens=6000)
            
            # Prepare prompt
            user_prompt = self.EXTRACTION_USER_TEMPLATE.format(
                html=html_truncated,
                currency=currency
            )
            
            # Call Bedrock
            response = await self._call_bedrock(user_prompt)
            
            # Parse response
            extracted_data = response.get("extracted_data", {})
            metadata["token_in"] = response.get("token_in", 0)
            metadata["token_out"] = response.get("token_out", 0)

            # Merge priority: Bedrock > HTML parser > URL params
            base = {**url_data, **html_data}
            merged = {**base, **{k: v for k, v in extracted_data.items() if v is not None}}

            record = ProductRecord(
                product_id=self._generate_product_id(merged.get("title", "Unknown"), url),
                source_url=url,
                title=merged.get("title") or "Unknown",
                brand=merged.get("brand"),
                description=merged.get("description"),
                category=merged.get("category"),
                price=Money(
                    amount=float(merged.get("price", {}).get("amount") or 0),
                    currency=currency
                ),
                availability=self._parse_availability(merged.get("availability")),
                images=merged.get("images", []),
                attributes=merged.get("attributes", {}),
                variants=merged.get("variants", []),
                confidence=float(merged.get("confidence") or 0.5),
                extraction_meta=ExtractionMeta(**metadata)
            )
            
            # Record timing
            duration_ms = int((time.time() - start_time) * 1000)
            record.extraction_meta.extraction_duration_ms = duration_ms
            
            logger.info(f"Extracted product from {url}: {record.product_id}")
            return record, metadata
        
        except Exception as e:
            logger.error(f"Extraction failed for {url}: {e}")
            metadata["extraction_duration_ms"] = int((time.time() - start_time) * 1000)
            return None, metadata
    
    async def _call_bedrock(self, user_prompt: str) -> Dict[str, Any]:
        """Call Bedrock API to extract product data."""
        try:
            # Bedrock Converse API: content must be a list; use the key name directly (tagged union)
            messages = [
                {"role": "user", "content": [{"text": user_prompt}]}
            ]
            
            response = self.bedrock_client.converse(
                modelId=self.bedrock_model,
                messages=messages,
                system=[{"text": self.EXTRACTION_SYSTEM_PROMPT}],
                inferenceConfig={
                    "maxTokens": 2000,
                    "temperature": 0.1,  # Low temperature for deterministic extraction
                }
            )
            
            # Extract response content
            content = response["output"]["message"]["content"][0]["text"]

            # Strip markdown code fences Bedrock sometimes wraps JSON in
            content = content.strip()
            if content.startswith("```"):
                lines = content.splitlines()
                content = "\n".join(
                    l for l in lines if not l.strip().startswith("```")
                ).strip()

            # Parse JSON from response
            extracted_data = json.loads(content)
            
            # Get token counts
            usage = response.get("usage", {})
            token_in = usage.get("inputTokens", 0)
            token_out = usage.get("outputTokens", 0)
            
            return {
                "extracted_data": extracted_data,
                "token_in": token_in,
                "token_out": token_out,
            }
        
        except json.JSONDecodeError as e:
            logger.error(f"Failed to parse Bedrock response as JSON: {e}")
            return {"extracted_data": {}, "token_in": 0, "token_out": 0}
        except Exception as e:
            logger.error(f"Bedrock API error: {e}")
            return {"extracted_data": {}, "token_in": 0, "token_out": 0}
    
    def _parse_html_fields(self, html: str, url: str, currency: str) -> dict:
        """Extract structured fields directly from page HTML without LLM."""
        import re
        result: dict = {}
        try:
            soup = BeautifulSoup(html, "html.parser")
            text = soup.get_text(separator=" ", strip=True)

            # Key:Value pairs present on digicircle-style pages
            patterns = {
                "category":    r"Category\s*:\s*([A-Z][^\$\n]{1,40}?)(?=\s*Cosmetic|\s*Wholesale|\s*List|\s*Item|$)",
                "condition":   r"Cosmetic Condition\s*:\s*([^\$\n]{1,40}?)(?=\s*Wholesale|\s*List|\s*Item|$)",
                "price_whole": r"Wholesale Price\s*:\s*\$([\d,\.]+)",
                "price_list":  r"List Price\s*:\s*\$([\d,\.]+)",
                "item_note":   r"Item Note\s*:\s*(.+?)(?=\s*Please Sign In|\s*Browse More|$)",
            }
            for key, pat in patterns.items():
                m = re.search(pat, text, re.IGNORECASE)
                if m:
                    result[key] = m.group(1).strip()

            # Price: prefer wholesale price
            for price_key in ("price_whole", "price_list"):
                raw = result.pop(price_key, None)
                if raw and "price" not in result:
                    try:
                        result["price"] = {"amount": float(raw.replace(",", "")), "currency": currency}
                    except ValueError:
                        pass
            result.pop("price_list", None)

            # Availability: page is in-stock if a price is shown
            if result.get("price"):
                result["availability"] = "in_stock"

            # Description: combine Item Note + condition
            parts = [p for p in [result.pop("item_note", None), result.pop("condition", None)] if p]
            if parts:
                result["description"] = " | ".join(parts)

            # Attributes: condition
            cond = result.pop("condition", None)
            if cond:
                result.setdefault("attributes", {})["condition"] = cond

            # Images: first <img> whose src path contains 'product_image'
            base = "{u.scheme}://{u.netloc}".format(u=urlparse(url))
            images = []
            for img in soup.find_all("img", src=True):
                src = img["src"]
                if "product_image" in src.lower():
                    images.append(src if src.startswith("http") else base + "/" + src.lstrip("/"))
            if images:
                result["images"] = images

        except Exception as e:
            logger.debug(f"HTML field parse error for {url}: {e}")
        return result

    def _parse_html_fields(self, html: str, url: str, currency: str) -> dict:
        """Extract structured fields directly from page HTML without LLM."""
        import re
        result: dict = {}
        try:
            soup = BeautifulSoup(html, "html.parser")
            text = soup.get_text(separator=" ", strip=True)

            patterns = {
                "category":    r"Category\s*:\s*([A-Z][^\$\n]{1,40}?)(?=\s*Cosmetic|\s*Wholesale|\s*List|\s*Item|$)",
                "condition":   r"Cosmetic Condition\s*:\s*([^\$\n]{1,40}?)(?=\s*Wholesale|\s*List|\s*Item|$)",
                "price_whole": r"Wholesale Price\s*:\s*\$([\d,\.]+)",
                "price_list":  r"List Price\s*:\s*\$([\d,\.]+)",
                "item_note":   r"Item Note\s*:\s*(.+?)(?=\s*Please Sign In|\s*Browse More|$)",
            }
            for key, pat in patterns.items():
                m = re.search(pat, text, re.IGNORECASE)
                if m:
                    result[key] = m.group(1).strip()

            # Prefer wholesale price over list price
            for price_key in ("price_whole", "price_list"):
                raw = result.pop(price_key, None)
                if raw and "price" not in result:
                    try:
                        result["price"] = {"amount": float(raw.replace(",", "")), "currency": currency}
                    except ValueError:
                        pass
            result.pop("price_list", None)

            if result.get("price"):
                result["availability"] = "in_stock"

            # Build description from Item Note + cosmetic condition
            cond = result.pop("condition", None)
            note = result.pop("item_note", None)
            parts = [p for p in [note, cond] if p]
            if parts:
                result["description"] = " | ".join(parts)
            if cond:
                result.setdefault("attributes", {})["condition"] = cond

            # First product image (skip logos/icons)
            base_url = "{u.scheme}://{u.netloc}".format(u=urlparse(url))
            images = []
            for img in soup.find_all("img", src=True):
                src = img["src"]
                if "product_image" in src.lower():
                    images.append(src if src.startswith("http") else base_url + "/" + src.lstrip("/"))
            if images:
                result["images"] = images

        except Exception as e:
            logger.debug(f"HTML field parse error for {url}: {e}")
        return result

    def _extract_from_url(self, url: str, currency: str) -> dict:
        """Extract brand, title, and price from URL query params (zero tokens)."""
        try:
            params = parse_qs(urlparse(url).query)
            brand = params.get("make", [None])[0]
            model = params.get("model", [None])[0]
            cost_raw = params.get("cost", [None])[0]
            cost = round(float(cost_raw), 2) if cost_raw else 0.0

            result: dict = {}
            if brand:
                result["brand"] = brand
            if model:
                title = f"{brand} {model}" if brand else model
                result["title"] = title
            if cost:
                result["price"] = {"amount": cost, "currency": currency}
            return result
        except Exception:
            return {}

    def _truncate_html(self, html: str, max_tokens: int = 6000) -> str:
        """
        Truncate HTML to fit within token budget.
        Keeps most important tags and truncates text.
        """
        try:
            soup = BeautifulSoup(html, "html.parser")
            
            # Remove script and style elements
            for element in soup(["script", "style", "nav", "footer"]):
                element.decompose()
            
            # Get text and keep first N tokens worth
            text = soup.get_text()
            # Rough estimate: ~1 token per word
            words = text.split()[:max_tokens * 2]
            return " ".join(words)
        
        except Exception as e:
            logger.warning(f"HTML truncation failed: {e}, using raw HTML")
            return html[:10000]  # Fallback to character limit
    
    def _generate_product_id(self, title: str, url: str) -> str:
        """Generate stable product ID from title and URL."""
        combined = f"{title}:{url}"
        hash_obj = hashlib.sha256(combined.encode())
        return f"p_{hash_obj.hexdigest()[:16]}"
    
    def _parse_availability(self, avail_str: Optional[str]) -> AvailabilityEnum:
        """Parse availability string to enum."""
        if not avail_str:
            return AvailabilityEnum.UNKNOWN
        
        avail_lower = str(avail_str).lower()
        if "out" in avail_lower or "unavailable" in avail_lower:
            return AvailabilityEnum.OUT_OF_STOCK
        elif "pre" in avail_lower:
            return AvailabilityEnum.PREORDER
        elif "stock" in avail_lower or "available" in avail_lower:
            return AvailabilityEnum.IN_STOCK
        
        return AvailabilityEnum.UNKNOWN
