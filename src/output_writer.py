"""
Output writer — writes the scrape result JSON to a local directory or S3 bucket.
"""
import json
import logging
import re
from datetime import datetime
from pathlib import Path
from typing import Any, Dict

logger = logging.getLogger(__name__)


class OutputWriter:
    """Writes scrape result JSON to local filesystem or S3."""

    def __init__(self, destination: str) -> None:
        """
        Args:
            destination: local dir path (e.g. './out') or S3 URI (e.g. 's3://bucket/prefix/')
        """
        self._destination = destination.rstrip("/")
        self._is_s3 = destination.startswith("s3://")

    def write(self, result: Dict[str, Any], site_name: str = "site") -> str:
        """
        Write result dict as JSON and return the path/URI of the written file.
        """
        filename = self._filename(site_name)
        if self._is_s3:
            return self._write_s3(result, filename)
        return self._write_local(result, filename)

    # ------------------------------------------------------------------
    def _filename(self, site_name: str) -> str:
        slug = re.sub(r"[^a-zA-Z0-9_-]", "_", site_name).strip("_")
        ts = datetime.utcnow().strftime("%Y%m%d_%H%M%S")
        return f"{slug}_{ts}.json"

    def _write_local(self, result: Dict[str, Any], filename: str) -> str:
        out_dir = Path(self._destination)
        out_dir.mkdir(parents=True, exist_ok=True)
        out_path = out_dir / filename
        out_path.write_text(json.dumps(result, indent=2, default=str), encoding="utf-8")
        logger.info(f"Result written locally: {out_path}")
        return str(out_path)

    def _write_s3(self, result: Dict[str, Any], filename: str) -> str:
        import boto3

        # Parse bucket and prefix from s3://bucket/prefix
        without_scheme = self._destination[len("s3://"):]
        bucket, _, prefix = without_scheme.partition("/")
        key = f"{prefix}/{filename}".lstrip("/")

        body = json.dumps(result, indent=2, default=str).encode("utf-8")
        s3 = boto3.client("s3")
        s3.put_object(
            Bucket=bucket,
            Key=key,
            Body=body,
            ContentType="application/json",
        )
        s3_uri = f"s3://{bucket}/{key}"
        logger.info(f"Result written to S3: {s3_uri}")
        return s3_uri
