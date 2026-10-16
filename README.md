# Product Scraper Agent

AWS Bedrock AgentCore-ready product scraping agent built with Strands framework. Discovers product links and extracts normalized product data from e-commerce sites.

## Features

- **Contract-First API**: Strict OpenAPI 3.1 schema for request/response contracts
- **Two-Phase Workflow**:
  - **Discovery**: Identifies product links from a seed category/search page
  - **Extraction**: Loops through each product URL and extracts structured data
- **Model Routing**: Uses Nova 2 Lite for primary extraction, Sonnet 4.5 for validation (scalable)
- **Concurrency Control**: Bounded parallel extraction with configurable limits
- **Quality Gates**: Schema validation and deduplication
- **Full Observability**: Token counts, latency, cache hits, error tracking
- **AgentCore Ready**: Configured for AWS Bedrock AgentCore deployment

## Architecture

```
Request → Discovery → Product URLs → Concurrent Extraction Loop
                                       ↓
                                    Validation
                                       ↓
                                   Deduplication
                                       ↓
                                  Aggregated JSON
```

## Project Structure

```
coe-product-agent/
├── agent/
│   ├── __init__.py
│   └── product_scraper.py         # AgentCore entrypoint
├── src/
│   ├── __init__.py
│   ├── schemas.py                 # Pydantic models matching API contract
│   ├── discovery.py               # Link discovery logic
│   ├── extraction.py              # Bedrock-based extraction
│   ├── orchestrator.py            # Workflow orchestration
│   └── output_writer.py           # Writes result JSON to local dir or S3
├── scripts/
│   └── run_agent.py               # CLI runner — loads contract, writes result JSON
├── examples/
│   ├── request_contract.json      # Sample contract (local output)
│   └── request_contract_s3.json   # Sample contract (S3 output)
├── doc/
│   ├── ProductScraperAgent_Design.md
│   ├── product-scraper-agent.openapi.yaml
│   └── DEPLOYMENT.md
├── tests/
├── .bedrock_agentcore.yaml        # AgentCore deployment config
├── Dockerfile                     # Container image (built by CodeBuild in the cloud)
├── Makefile                       # Common task shortcuts
├── requirements.txt
├── pyproject.toml
└── .env.example
```

## Installation

```bash
cd coe-product-agent

python3.11 -m venv .venv
source .venv/bin/activate

# Runtime + AgentCore CLI
pip install bedrock-agentcore-starter-toolkit
pip install -r requirements.txt
```

## Running the Agent

The agent is driven by a **request contract JSON** (equivalent to a `work_order`).
The contract carries the site URL and crawl settings for that specific run.

### From a contract file (recommended)

```bash
# Write result to ./out/ (local)
python scripts/run_agent.py --contract examples/request_contract.json

# Write result to S3
python scripts/run_agent.py --contract examples/request_contract.json \
  --output s3://my-bucket/products/

# Quick ad-hoc run from a URL
python scripts/run_agent.py --url https://example.com/products --max-products 50
```

### Makefile shortcuts

```bash
make run       # run with examples/request_contract.json → ./out/
make run-s3    # run with examples/request_contract_s3.json → s3://
```

### Contract file format

```json
{
  "request_id": "uuid",
  "tenant_id": "retail-us",
  "site_name": "Example Store",
  "source_url": "https://example.com/category/laptops",
  "crawl_ts": "2026-08-22T10:00:00Z",
  "max_products": 120,
  "max_depth": 2,
  "allowed_domains": ["example.com"],
  "use_browser_fallback": false,
  "include_raw_html": false,
  "source_meta": { "country": "US", "currency_hint": "USD" }
}
```

Result JSON is written to `./out/<site_name>_<timestamp>.json` (or S3 when `--output s3://...`).

## Configuration

### Environment Variables

```bash
AWS_REGION=us-east-1
BEDROCK_MODEL_ID=us.amazon.nova-2-lite-v1:0      # or use BEDROCK_INFERENCE_PROFILE_ARN
OUTPUT_DESTINATION=./out                          # local dir or s3://bucket/prefix
```

See [.env.example](.env.example) for the full list.

## Running Tests

```bash
# Unit tests
pytest tests/unit -v

# Integration tests (requires AWS creds)
pytest tests/integration -v

# Coverage
pytest --cov=src tests/
```

## Deployment to AWS AgentCore

> **Docker is NOT required.** `agentcore deploy` uses AWS CodeBuild to build the ARM64 container image entirely in the cloud.

```bash
# Install CLI
pip install bedrock-agentcore-starter-toolkit

# Configure AWS credentials
export AWS_PROFILE=your-profile

# Update account ID in config
sed -i '' 's/ACCOUNT_ID/YOUR_ACCOUNT_ID/g' .bedrock_agentcore.yaml

# Deploy (CodeBuild builds the image in the cloud)
agentcore deploy -a coe_product_scraper_agent
```

CodeBuild phases: `QUEUED → PROVISIONING → DOWNLOAD_SOURCE → BUILD → POST_BUILD → FINALIZING → COMPLETED` (~2–5 min)

### Test deployed agent

```bash
# Quick invoke via CLI
agentcore invoke '{"source_url": "https://example.com/products", ...}'

# Or invoke via AgentCore endpoint
curl -X POST https://your-agentcore-endpoint/invocations \
  -H "Content-Type: application/json" \
  -d @examples/request_contract.json
```

See [doc/DEPLOYMENT.md](doc/DEPLOYMENT.md) for the full step-by-step guide.

## Performance Tuning

### Concurrency
Increase `max_concurrent` in `ProductScraperOrchestrator` if you need higher throughput (watch for Bedrock throttling).

### Model Selection
- **Nova 2 Lite**: Best coverage/cost ratio (~60% the cost of Sonnet)
- **Sonnet 4.5**: Best quality (100% field preservation)
- Routing: Simple pages → Nova, Complex → Sonnet (future)

### Caching
Enable semantic cache layer to skip re-extraction of equivalent products:
```python
# Future: add DynamoDB cache backend
```

## Monitoring

### CloudWatch Dashboards

Pre-built dashboards track:
- Extraction latency p50, p95, p99
- Token counts (input/output)
- Cost per product
- Error rates and types
- Cache hit ratio
- Concurrency utilization

### Logs

All logs shipped to CloudWatch with structured JSON:
```json
{
  "timestamp": "2026-08-22T10:00:00Z",
  "level": "INFO",
  "logger": "src.orchestrator",
  "request_id": "uuid",
  "message": "Phase 1: Link Discovery",
  "duration_ms": 1234
}
```

## Troubleshooting

### Timeout Errors
Increase chunk size or reduce HTML content:
```python
# In extraction.py: _truncate_html()
max_tokens=6000  # Reduce to 4000 if timeouts persist
```

### Low Coverage
Check discovered link patterns in `LinkDiscovery._is_product_url()` — may need site-specific adjustments.

### High Cost
Enable caching and route simpler extractions to Nova 2 Lite first.

## Contributing

1. Write tests first (`tests/`)
2. Follow Black/Ruff linting (`make lint`)
3. Update schemas in `src/schemas.py` if changing contracts
4. Update OpenAPI doc if adding endpoints
