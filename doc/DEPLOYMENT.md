# Deploy to AWS Bedrock AgentCore

This guide deploys the Product Scraper Agent to AWS Bedrock AgentCore using the **CodeBuild mode** (recommended default).

> **Docker is NOT required.** The `agentcore deploy` command zips your source code, uploads it to S3, and triggers AWS CodeBuild to build an ARM64 container image entirely in the cloud. Nothing is built locally.

## Deployment Modes

| Mode | Command | When to use |
|------|---------|-------------|
| **CodeBuild (default, recommended)** | `agentcore deploy` | All real deployments — no Docker needed |
| Local dev only | `agentcore deploy --local` | Local testing only |
| Local build + cloud deploy | `agentcore deploy --local-build` | Requires local Docker |

## Prerequisites

| Requirement | Check |
|-------------|-------|
| Python 3.10+ | `python3 --version` |
| AWS CLI v2 | `aws --version` |
| AWS credentials configured | `aws sts get-caller-identity` |
| AgentCore Starter Toolkit | `agentcore --help` |

## Step 1: Install AgentCore Starter Toolkit

```bash
cd /Users/jhon.quintero/development/coe-product-agent

# Create and activate virtual environment
python3 -m venv .venv
source .venv/bin/activate

# Install the CLI
pip install bedrock-agentcore-starter-toolkit

# Verify
agentcore --help
```

Alternative with `uv` (faster):
```bash
uv venv --python 3.11
source .venv/bin/activate
uv pip install bedrock-agentcore-starter-toolkit
```

## Step 2: Configure AWS Identity

```bash
# Confirm you are logged into the correct AWS account
aws sts get-caller-identity
# Expected: shows your account ID, user ARN, and user ID
```

## Step 3: Update `.bedrock_agentcore.yaml`

Replace the `ACCOUNT_ID` placeholder with your 12-digit AWS account number:

```bash
# macOS
sed -i '' 's/ACCOUNT_ID/123456789012/g' .bedrock_agentcore.yaml

# Linux
sed -i 's/ACCOUNT_ID/123456789012/g' .bedrock_agentcore.yaml

# Verify
grep 'account:' .bedrock_agentcore.yaml
```

Also update `source_path` to your local project path:
```yaml
source_path: /Users/jhon.quintero/development/coe-product-agent
```

> **AWS Resources** — the config already has `execution_role_auto_create: true` and `ecr_auto_create: true`. AgentCore will create the IAM role and ECR repository automatically on first deploy.

## Step 4: Deploy via CodeBuild (No Docker Required)

```bash
cd /Users/jhon.quintero/development/coe-product-agent
source .venv/bin/activate

# Deploy — CodeBuild builds the ARM64 image in the cloud
agentcore deploy -a coe_product_scraper_agent
```

What happens during deployment:

```
PHASE 1 — Setup
  • Reads .bedrock_agentcore.yaml
  • Zips source code → uploads to S3
  • Triggers AWS CodeBuild project

PHASE 2 — CodeBuild (ARM64 image built in cloud)
  QUEUED → PROVISIONING → DOWNLOAD_SOURCE → BUILD → POST_BUILD → FINALIZING → COMPLETED
  (typically 2–5 minutes)

PHASE 3 — Agent & Observability
  • Creates/updates AgentCore runtime with new image
  • Configures CloudWatch Logs + X-Ray traces
  • Polls until endpoint is ready

PHASE 4 — Deployment Success
  Agent ARN: arn:aws:bedrock-agentcore:us-east-1:ACCOUNT:runtime/...
  ECR URI:   ACCOUNT.dkr.ecr.us-east-1.amazonaws.com/...
```

> No `docker login` or local Docker build needed at any point.

## Step 5: Verify Deployment

```bash
# List all deployed agents
agentcore list

# Check specific agent status
agentcore status

# Stream live CloudWatch logs
aws logs tail /aws/bedrock-agentcore/coe_product_scraper_agent --follow
```

## Step 6: Test the Agent

### Quick smoke test via agentcore invoke

```bash
agentcore invoke '{"request_id": "test-001", "tenant_id": "test", "source_url": "https://example.com/products", "crawl_ts": "2026-08-22T10:00:00Z", "max_products": 5, "max_depth": 1, "allowed_domains": ["example.com"], "use_browser_fallback": false, "include_raw_html": false, "source_meta": {"country": "US", "currency_hint": "USD"}}'
```

### Full curl test

```bash
cat > request.json <<'EOF'
{
  "request_id": "test-123",
  "tenant_id": "test-tenant",
  "source_url": "https://example.com/products",
  "crawl_ts": "2026-08-22T10:00:00Z",
  "max_products": 10,
  "max_depth": 1,
  "allowed_domains": ["example.com"],
  "use_browser_fallback": false,
  "include_raw_html": false,
  "source_meta": {
    "country": "US",
    "currency_hint": "USD"
  }
}
EOF

curl -X POST https://your-agentcore-endpoint/invocations \
  -H "Content-Type: application/json" \
  -d @request.json
```

## Monitoring in Production

### CloudWatch Dashboard

View pre-built dashboard:
```bash
aws cloudwatch describe-dashboards \
  --dashboard-name-prefix coe-product-scraper
```

### Key Metrics

- **Latency**: Extract time per product
- **Token Usage**: Input/output tokens by model
- **Cost Estimation**: Tokens × model pricing
- **Error Rate**: Failed extractions
- **Throughput**: Products per minute
- **Cache Hit Ratio**: Semantic dedup effectiveness

### Logs

```bash
# Stream live logs
aws logs tail /aws/bedrock-agentcore/coe_product_scraper_agent --follow

# Search for errors
aws logs filter-log-events \
  --log-group /aws/bedrock-agentcore/coe_product_scraper_agent \
  --filter-pattern "[ERROR]"
```

## Redeployment After Code Changes

Every time you change source code, simply run deploy again:

```bash
source .venv/bin/activate
agentcore deploy -a coe_product_scraper_agent
# CodeBuild rebuilds the ARM64 image from scratch in the cloud
```

## Scaling and Optimization

### Increase Concurrency
Edit `src/orchestrator.py`:
```python
ProductScraperOrchestrator(
    request=req,
    max_concurrent=10  # Increase from 5
)
```

### Switch to Sonnet (Higher Quality)

Update `BEDROCK_MODEL_ID` in your `.bedrock_agentcore.yaml` environment or override at runtime via AWS Console.

### Enable Browser Rendering (Optional)
Update request:
```json
{
  "use_browser_fallback": true
}
```

## Troubleshooting

### Agent fails to start
```bash
# Check logs
aws logs tail /aws/bedrock-agentcore/coe_product_scraper_agent

# Restart
agentcore restart coe_product_scraper_agent
```

### Timeout errors
Reduce HTML size or concurrency:
```python
# In src/extraction.py
html_truncated = self._truncate_html(html, max_tokens=4000)  # Reduce from 6000
```

### High costs
Enable caching or reduce model tier:
```bash
agentcore set-env BEDROCK_MODEL_ID=us.amazon.nova-2-lite-v1:0
```

### Out of memory
Reduce batch size:
```bash
# In orchestrator call
max_concurrent=3  # Reduce from 5
```

## Rollback

```bash
# Re-deploy a previous image tag from ECR via the AWS Console,
# or redeploy from a previous git commit:
git checkout <previous-sha>
agentcore deploy -a coe_product_scraper_agent
```

## Delete Agent

```bash
# Removes the AgentCore runtime (does NOT delete ECR or IAM resources)
agentcore delete coe_product_scraper_agent
```

## Next Steps

1. **Data Persistence**: Connect to S3 or database to store results
2. **Async Webhooks**: Add async job submission and webhook callbacks
3. **Cost Optimization**: Implement semantic caching with DynamoDB
4. **Advanced Routing**: Route complex extractions to Sonnet 4.5 automatically
5. **Observability**: Set up CloudWatch dashboards and alarms

## Reference

- Official docs: https://docs.aws.amazon.com/bedrock/latest/userguide/agentcore-toolkit-gs.html
- PyPI: `bedrock-agentcore-starter-toolkit`
- GitHub: `aws/bedrock-agentcore-starter-toolkit`

```bash
agentcore --help
aws bedrock list-foundation-models --region us-east-1
```
