"""
AgentCore-compatible Product Scraper Agent.

Main entrypoint for AWS Bedrock AgentCore deployment.
Orchestrates product discovery and extraction workflow.
"""
import asyncio
import json
import logging
import os
import sys
from pathlib import Path
from typing import Dict, Any, Optional
from datetime import datetime
from uuid import uuid4

# Add src to path for imports
sys.path.insert(0, str(Path(__file__).parent.parent))

from bedrock_agentcore import BedrockAgentCoreApp

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s [%(levelname)s] [%(name)s] %(message)s',
    datefmt='%Y-%m-%d %H:%M:%S'
)
logger = logging.getLogger(__name__)

# Create AgentCore application
app = BedrockAgentCoreApp(debug=False)


@app.ping
def health_check():
    """Lightweight health check - returns immediately without heavy imports."""
    return "HEALTHY"


@app.entrypoint
async def scrape_products(request_data: Dict[str, Any], _context=None) -> Dict[str, Any]:
    """
    Main entrypoint for product scraper agent.
    
    Accepts a product scrape request contract and runs the full workflow:
    1. Discover product links from seed URL
    2. Extract product details per URL
    3. Aggregate and validate results
    4. Return final product data
    
    Args:
        request_data: request_contract.json contents (equivalent to work_order):
            - request_id: uuid for trace/idempotency (optional, auto-generated)
            - tenant_id: tenant identifier
            - source_url: seed URL from the contract
            - crawl_ts: ISO8601 timestamp (optional, defaults to now)
            - max_products: max products to extract (1-1000)
            - max_depth: max link crawl depth (0-5)
            - allowed_domains: list of allowed domains
            - use_browser_fallback: boolean
            - include_raw_html: boolean
            - source_meta: dict with country, currency_hint
            - output_destination: local path or s3:// URI for result JSON
    
    Returns:
        Dictionary matching ProductScrapeResult schema with:
            - request_id, source_url, status
            - started_at, finished_at
            - summary (discovered_links, processed_links, etc.)
            - products: list of ProductRecord objects
            - errors: list of ProductError objects
    """
    started_at = datetime.utcnow().isoformat() + "Z"
    request_id = request_data.get("request_id", str(uuid4()))
    source_url = request_data.get("source_url")

    try:
        logger.info(f"[{request_id}] Starting product scrape from {source_url}")
        
        # Lazy import - only load when actually invoked
        from src.orchestrator import ProductScraperOrchestrator
        from src.schemas import ProductScrapeRequest, ProductScrapeResult
        
        # Parse and validate request
        try:
            req = ProductScrapeRequest(**request_data)
        except Exception as e:
            logger.error(f"[{request_id}] Invalid request: {e}")
            return {
                "request_id": request_id,
                "source_url": source_url,
                "status": "failed",
                "error_code": "INVALID_REQUEST",
                "message": str(e)
            }
        
        # Initialize orchestrator
        orchestrator = ProductScraperOrchestrator(
            request=req,
            aws_region=os.getenv("AWS_REGION", "us-east-1"),
            bedrock_model=os.getenv("BEDROCK_MODEL_ID", "us.amazon.nova-2-lite-v1:0")
        )
        
        # Execute scraping workflow
        result = await orchestrator.run()
        result.finished_at = datetime.utcnow().isoformat() + "Z"

        logger.info(
            f"[{request_id}] Scrape completed: "
            f"{result.summary.successful_products} products, "
            f"{result.summary.failed_products} failures, "
            f"{result.summary.cache_hits} cache hits"
        )

        result_dict = result.model_dump(exclude_none=False)

        # Support both payload styles: output_destination and output.destination.
        output_block = request_data.get("output")
        nested_output_destination = (
            output_block.get("destination")
            if isinstance(output_block, dict)
            else ""
        )
        output_destination = (
            request_data.get("output_destination")
            or nested_output_destination
            or os.getenv("OUTPUT_DESTINATION", "")
        )
        if output_destination:
            from src.output_writer import OutputWriter
            site_name = request_data.get("site_name", source_url)
            out_path = OutputWriter(output_destination).write(result_dict, site_name=site_name)
            result_dict["output_path"] = out_path
        else:
            logger.warning(
                f"[{request_id}] No output destination configured; "
                "skipping result persistence. Set output_destination, output.destination, "
                "or OUTPUT_DESTINATION."
            )

        return result_dict
        
    except Exception as e:
        logger.exception(f"[{request_id}] Unexpected error during scraping")
        return {
            "request_id": request_id,
            "source_url": source_url,
            "status": "failed",
            "error_code": "INTERNAL_ERROR",
            "message": str(e),
            "started_at": started_at,
            "finished_at": datetime.utcnow().isoformat() + "Z"
        }


if __name__ == "__main__":
    app.run()
