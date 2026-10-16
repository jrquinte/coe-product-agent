# Use AWS ECR Public Gallery Python image (ARM64 compatible, no rate limits)
FROM public.ecr.aws/docker/library/python:3.11-slim

# Set working directory
WORKDIR /app

# All environment variables in one layer
ENV PYTHONUNBUFFERED=1 \
    PORT=8000 \
    PYTHONPATH=/app \
    DOCKER_CONTAINER=1 \
    AWS_REGION=us-west-2 \
    AWS_DEFAULT_REGION=us-west-2

# Install system dependencies required for building Python packages
RUN apt-get update && apt-get install -y \
    gcc \
    g++ \
    libpq-dev \
    libxml2-dev \
    libxslt1-dev \
    zlib1g-dev \
    libssl-dev \
    && rm -rf /var/lib/apt/lists/*

# Copy runtime requirements file  
COPY requirements.txt .

# Install Python dependencies
RUN pip install --no-cache-dir -r requirements.txt

# Best-effort browser bootstrap: do not fail image build if Playwright CDN/apt cert chain is blocked.
# Browser fallback remains optional at runtime; core scraping path still works without Chromium.
RUN if command -v playwright >/dev/null 2>&1; then \
            playwright install chromium || echo "WARN: Playwright Chromium install failed; browser fallback will be unavailable until fixed."; \
            playwright install-deps chromium || echo "WARN: Playwright system deps install failed; browser fallback may be unavailable."; \
        else \
            echo "WARN: Playwright CLI not found; skipping browser bootstrap."; \
        fi

# Install OpenTelemetry distro (CRITICAL for AgentCore Runtime health checks!)
RUN pip install --no-cache-dir aws-opentelemetry-distro==0.12.2

# Create non-root user
RUN useradd -m -u 1000 bedrock_agentcore
USER bedrock_agentcore

# Expose ports
EXPOSE 9000
EXPOSE 8000
EXPOSE 8080

# Copy entire project
COPY --chown=bedrock_agentcore:bedrock_agentcore . .

# Run with OpenTelemetry instrumentation
CMD ["opentelemetry-instrument", "python", "-m", "agent.product_scraper"]
