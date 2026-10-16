#!/usr/bin/env python3
"""
CLI runner for the Product Scraper Agent.

Usage:
    python scripts/run_agent.py --contract examples/request_contract.json
    python scripts/run_agent.py --url https://example.com/products --output ./out
    python scripts/run_agent.py --contract examples/request_contract.json --output s3://my-bucket/results/
"""
import argparse
import asyncio
import json
import logging
import sys
from datetime import datetime
from pathlib import Path
from uuid import uuid4

try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    pass  # .env support is optional

sys.path.insert(0, str(Path(__file__).parent.parent))

from src.orchestrator import ProductScraperOrchestrator
from src.output_writer import OutputWriter
from src.schemas import ProductScrapeRequest, SourceMeta


def _setup_logging(verbose: bool = False, log_dir: str = "./out/logs") -> None:
    level = logging.DEBUG if verbose else logging.INFO
    Path(log_dir).mkdir(parents=True, exist_ok=True)

    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    log_file = Path(log_dir) / f"product_scraper_{timestamp}.log"

    root = logging.getLogger()
    root.setLevel(level)
    root.handlers.clear()

    fmt = logging.Formatter("%(asctime)s [%(levelname)s] %(message)s", datefmt="%Y-%m-%d %H:%M:%S")
    detail_fmt = logging.Formatter(
        "%(asctime)s [%(levelname)s] [%(name)s] %(message)s", datefmt="%Y-%m-%d %H:%M:%S"
    )

    ch = logging.StreamHandler()
    ch.setLevel(level)
    ch.setFormatter(fmt)
    root.addHandler(ch)

    fh = logging.FileHandler(log_file, encoding="utf-8")
    fh.setLevel(level)
    fh.setFormatter(detail_fmt)
    root.addHandler(fh)

    for noisy in ("httpx", "httpcore", "botocore", "urllib3"):
        logging.getLogger(noisy).setLevel(logging.WARNING)

    logging.getLogger(__name__).info(f"Logging to: {log_file}")


def _load_contract(path: str) -> dict:
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


def _contract_from_args(args) -> dict:
    from urllib.parse import urlparse
    domain = urlparse(args.url).netloc
    return {
        "source_url": args.url,
        "site_name": args.site_name or domain,
        "max_products": args.max_products,
        "max_depth": args.max_depth,
        "allowed_domains": [domain],
        "use_browser_fallback": False,
        "include_raw_html": False,
        "source_meta": {"country": "US", "currency_hint": "USD"},
    }


async def _run(contract: dict, output_dest: str, aws_region: str, model_id: str) -> dict:
    import os

    request = ProductScrapeRequest(
        request_id=contract.get("request_id", str(uuid4())),
        tenant_id=contract.get("tenant_id", "default"),
        source_url=contract["source_url"],
        crawl_ts=contract.get("crawl_ts", datetime.utcnow().isoformat() + "Z"),
        max_products=contract.get("max_products", 200),
        max_depth=contract.get("max_depth", 2),
        allowed_domains=contract.get("allowed_domains", []),
        use_browser_fallback=contract.get("use_browser_fallback", False),
        include_raw_html=contract.get("include_raw_html", False),
        source_meta=SourceMeta(**contract.get("source_meta", {"country": "US", "currency_hint": "USD"})),
    )

    orchestrator = ProductScraperOrchestrator(
        request=request,
        aws_region=aws_region,
        bedrock_model=model_id or os.getenv("BEDROCK_MODEL_ID", "us.amazon.nova-2-lite-v1:0"),
    )

    started = datetime.now()
    result = await orchestrator.run()
    duration = (datetime.now() - started).total_seconds()

    result_dict = result.model_dump(exclude_none=False)

    # Write output
    writer = OutputWriter(output_dest)
    site_name = contract.get("site_name", "unknown")
    output_path = writer.write(result_dict, site_name=site_name)

    # Print summary
    s = result.summary
    print()
    print("=" * 60)
    print("PRODUCT SCRAPE SUMMARY")
    print("=" * 60)
    print(f"Site:                {site_name}")
    print(f"URL:                 {request.source_url}")
    print(f"Status:              {result.status.value}")
    print(f"Duration:            {duration:.1f}s")
    print()
    print(f"Discovered links:    {s.discovered_links}")
    print(f"Processed links:     {s.processed_links}")
    print(f"Successful products: {s.successful_products}")
    print(f"Failed products:     {s.failed_products}")
    print(f"Duplicates removed:  {s.duplicates_removed}")
    print(f"Cache hits:          {s.cache_hits}")
    print()
    print(f"Output written to:   {output_path}")
    print("=" * 60)

    return result_dict


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Product Scraper Agent — run from a contract JSON or direct URL",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  # Run from a contract JSON file (recommended):
  python scripts/run_agent.py --contract examples/request_contract.json

  # Write result to S3:
  python scripts/run_agent.py --contract examples/request_contract.json --output s3://my-bucket/products/

  # Quick run from a direct URL:
  python scripts/run_agent.py --url https://example.com/products --max-products 50

  # Verbose mode:
  python scripts/run_agent.py --contract examples/request_contract.json -v
        """,
    )

    parser.add_argument("--contract", type=str, help="Path to request_contract.json file")
    parser.add_argument("--url", type=str, help="Direct source URL (alternative to --contract)")
    parser.add_argument("--site-name", type=str, help="Site name (used with --url)")
    parser.add_argument("--max-products", type=int, default=200, help="Max products to extract (default: 200)")
    parser.add_argument("--max-depth", type=int, default=2, help="Max crawl depth (default: 2)")
    parser.add_argument(
        "--output",
        type=str,
        default="./out",
        help="Output destination: local dir (./out) or S3 URI (s3://bucket/prefix). Default: ./out",
    )
    parser.add_argument("--region", type=str, default="us-east-1", help="AWS region (default: us-east-1)")
    parser.add_argument("--model-id", type=str, default=None, help="Bedrock model ID override")
    parser.add_argument("-v", "--verbose", action="store_true", help="Enable debug logging")

    args = parser.parse_args()

    if not args.contract and not args.url:
        parser.error("Provide either --contract <path> or --url <url>")

    _setup_logging(verbose=args.verbose)

    contract = _load_contract(args.contract) if args.contract else _contract_from_args(args)

    asyncio.run(_run(contract, args.output, args.region, args.model_id))


if __name__ == "__main__":
    main()
