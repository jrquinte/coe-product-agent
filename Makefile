PYTHON     := /Library/Frameworks/Python.framework/Versions/3.11/bin/python3.11
VENV       := .venv
BIN        := $(VENV)/bin
PY         := $(BIN)/python
PIP        := $(BIN)/pip
# agentcore is installed in the user bin, not the venv — resolve at runtime
AGENTCORE  := $(shell which agentcore 2>/dev/null || echo $(HOME)/Library/Python/3.11/bin/agentcore)

.PHONY: help install test lint format clean docker-build agentcore-deploy agentcore-status agentcore-invoke agentcore-debug

help:
	@echo "Makefile commands:"
	@echo "  make install             Create .venv and install all dependencies"
	@echo "  make run                 Run agent with examples/request_contract.json (local output)"
	@echo "  make run-s3              Run agent with examples/request_contract_s3.json (S3 output)"
	@echo "  make test                Run all tests"
	@echo "  make test-unit           Run unit tests only"
	@echo "  make lint                Run linters (ruff, mypy)"
	@echo "  make format              Auto-format code (black, ruff)"
	@echo "  make clean               Remove build artifacts and .venv"
	@echo "  make docker-build        Build Docker image locally (optional, dev only)"
	@echo "  make agentcore-deploy    Deploy to AWS AgentCore via CodeBuild (no local Docker needed)"
	@echo "  make agentcore-status    Show deployed agent status"
	@echo "  make agentcore-invoke    Send a test request to the deployed agent"
	@echo "  make agentcore-debug     Show latest CodeBuild failure details and log location"

$(VENV):
	$(PYTHON) -m venv --system-site-packages $(VENV)

install: $(VENV)
	$(PIP) install --upgrade pip
	$(PIP) install bedrock-agentcore-starter-toolkit
	$(PIP) install -r requirements.txt

run: $(VENV)
	$(PY) scripts/run_agent.py --contract examples/request_contract.json

run-s3: $(VENV)
	$(PY) scripts/run_agent.py --contract examples/request_contract_s3.json --output s3://coe-aws-agent-product/products/

test: $(VENV)
	$(BIN)/pytest tests/ -v --cov=src --cov-report=term-missing

test-unit: $(VENV)
	$(BIN)/pytest tests/ -v -m "not integration" --tb=short

lint: $(VENV)
	$(BIN)/ruff check src tests
	$(BIN)/mypy src --ignore-missing-imports

format: $(VENV)
	$(BIN)/black src tests
	$(BIN)/ruff check --fix src tests

clean:
	find . -type d -name __pycache__ -exec rm -rf {} +
	find . -type f -name "*.pyc" -delete
	find . -name ".pytest_cache" -exec rm -rf {} +
	rm -rf build dist *.egg-info .coverage htmlcov $(VENV)

docker-build:
	# Optional: local build for development testing only (not needed for deployment)
	docker build -t coe-product-scraper-agent:latest .

agentcore-deploy: $(VENV)
	# Builds ARM64 image in the cloud via AWS CodeBuild — no local Docker needed
	$(AGENTCORE) deploy -a coe_product_scraper_agent

agentcore-status: $(VENV)
	$(AGENTCORE) status

agentcore-invoke: $(VENV)
	$(AGENTCORE) invoke '{"request_id": "test-001", "tenant_id": "test", "source_url": "https://example.com/products", "crawl_ts": "2026-08-22T10:00:00Z", "max_products": 5, "max_depth": 1, "allowed_domains": ["example.com"], "use_browser_fallback": false, "include_raw_html": false, "source_meta": {"country": "US", "currency_hint": "USD"}}'

agentcore-debug: $(VENV)
	@BUILD_ID=$$(aws codebuild list-builds-for-project \
		--project-name bedrock-agentcore-agent_product_scraper-builder \
		--region us-west-2 \
		--sort-order DESCENDING \
		--max-items 1 \
		--query 'ids[0]' \
		--output text); \
	if [ -z "$$BUILD_ID" ] || [ "$$BUILD_ID" = "None" ]; then \
		echo "No CodeBuild build ID found for bedrock-agentcore-agent_product_scraper-builder"; \
		exit 1; \
	fi; \
	echo "Latest build: $$BUILD_ID"; \
	echo "\nPhase summary:"; \
	aws codebuild batch-get-builds \
		--ids "$$BUILD_ID" \
		--region us-west-2 \
		--query 'builds[0].phases[].{phase:phaseType,status:phaseStatus,message:contexts[0].message}' \
		--output table; \
	LOG_GROUP=$$(aws codebuild batch-get-builds --ids "$$BUILD_ID" --region us-west-2 --query 'builds[0].logs.groupName' --output text); \
	LOG_STREAM=$$(aws codebuild batch-get-builds --ids "$$BUILD_ID" --region us-west-2 --query 'builds[0].logs.streamName' --output text); \
	echo "\nCloudWatch log group: $$LOG_GROUP"; \
	echo "CloudWatch log stream: $$LOG_STREAM"; \
	echo "\nLast 80 log events:"; \
	aws logs get-log-events --log-group-name "$$LOG_GROUP" --log-stream-name "$$LOG_STREAM" --limit 80 --query 'events[].message' --output text
