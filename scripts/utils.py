import json
import logging
import time
import urllib.error
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

USER_AGENT = "TopFossdroidPagesBuilder/1.0 (+https://github.com/)"


def fetch(url, *, headers=None, timeout=45, attempts=3, data=None):
    request_headers = {"User-Agent": USER_AGENT, "Accept": "application/json"}
    request_headers.update(headers or {})
    for attempt in range(attempts):
        try:
            request = urllib.request.Request(url, headers=request_headers, data=data)
            return urllib.request.urlopen(request, timeout=timeout).read()
        except (urllib.error.URLError, TimeoutError, OSError) as error:
            if isinstance(error, urllib.error.HTTPError) and error.code not in (429, 500, 502, 503, 504):
                raise
            if attempt == attempts - 1:
                raise
            logging.warning("Request failed (%s), retrying %s", error, url)
            time.sleep(2 ** attempt)


def fetch_json(url, **kwargs):
    value = json.loads(fetch(url, **kwargs))
    if not isinstance(value, (dict, list)):
        raise ValueError(f"Expected JSON object or array from {url}")
    return value


def fetch_file(url, *, timeout=20, attempts=2, max_bytes=2_000_000):
    headers = {"User-Agent": USER_AGENT, "Accept": "image/*"}
    for attempt in range(attempts):
        try:
            request = urllib.request.Request(url, headers=headers)
            with urllib.request.urlopen(request, timeout=timeout) as response:
                content_type = response.headers.get_content_type().lower()
                if not content_type.startswith("image/"):
                    raise ValueError(f"unexpected content type {content_type}")
                size = response.headers.get("Content-Length")
                if size and int(size) > max_bytes:
                    raise ValueError("icon exceeds 2 MB")
                body = response.read(max_bytes + 1)
                if len(body) > max_bytes:
                    raise ValueError("icon exceeds 2 MB")
                return body, content_type
        except (urllib.error.URLError, TimeoutError, OSError) as error:
            if isinstance(error, urllib.error.HTTPError) and error.code not in (429, 500, 502, 503, 504):
                raise
            if attempt == attempts - 1:
                raise
            logging.warning("Request failed (%s), retrying %s", error, url)
            time.sleep(2 ** attempt)


def write_json(path, value, *, compact=False):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    text = json.dumps(value, ensure_ascii=False, separators=(",", ":") if compact else None, indent=None if compact else 2)
    path.write_text(text + "\n", encoding="utf-8")


def iso_date(value):
    if not value:
        return None
    if isinstance(value, (int, float)):
        return datetime.fromtimestamp(value / 1000, timezone.utc).date().isoformat()
    return str(value)[:10]


def localized(value):
    if not isinstance(value, dict):
        return value
    for key in ("en-US", "en-GB", "en"):
        if value.get(key):
            return value[key]
    return next((item for item in value.values() if item), None)
