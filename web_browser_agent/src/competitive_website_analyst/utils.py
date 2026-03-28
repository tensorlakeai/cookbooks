from __future__ import annotations

import json
import re
from datetime import datetime, UTC
from urllib.parse import urlsplit, urlunsplit

from competitive_website_analyst.models import Company


def slugify(value: str) -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", value.lower()).strip("-")
    return slug or "company"


def make_run_id(company_id: str, now: datetime | None = None, suffix: str = "run") -> str:
    timestamp = (now or datetime.now(UTC)).strftime("%Y%m%d")
    return f"{company_id}-{timestamp}-{suffix}"


def normalize_homepage_url(raw_url: str) -> str:
    raw_url = raw_url.strip()
    if not raw_url.startswith("https://"):
        raise ValueError("url must use https")
    parts = urlsplit(raw_url)
    return urlunsplit((parts.scheme, parts.netloc.lower(), parts.path.rstrip("/") or "", "", ""))


def parse_json(value: str) -> object:
    text = value.strip()
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        pass

    fenced = re.findall(r"```(?:json)?\s*(.*?)```", text, flags=re.DOTALL)
    for candidate in fenced:
        candidate = candidate.strip()
        try:
            return json.loads(candidate)
        except json.JSONDecodeError:
            continue

    starts = [idx for idx in (text.find("["), text.find("{")) if idx != -1]
    if starts:
        start = min(starts)
        for end in range(len(text), start, -1):
            candidate = text[start:end].strip()
            if candidate and candidate[-1] in "]}":
                try:
                    return json.loads(candidate)
                except json.JSONDecodeError:
                    continue

    raise ValueError("no JSON object found in agent output")


def validate_companies(payload: object, count: int) -> list[Company]:
    if not isinstance(payload, list):
        raise ValueError("company payload must be a list")

    seen_hosts: set[str] = set()
    results: list[Company] = []

    for index, item in enumerate(payload):
        if not isinstance(item, dict):
            continue
        name = str(item.get("name", "")).strip()
        url = str(item.get("url", "")).strip()
        description = str(item.get("short_description", "")).strip()
        if not name or not url or not description:
            continue
        normalized_url = normalize_homepage_url(url)
        host = urlsplit(normalized_url).netloc
        if host in seen_hosts:
            continue
        seen_hosts.add(host)
        results.append(
            Company(
                id=_dedupe_company_id(slugify(name), {company.id for company in results}),
                name=name,
                url=normalized_url,
                short_description=description,
            )
        )
        if len(results) == count:
            break

    if not results:
        raise ValueError("no valid companies returned")
    return results


def _dedupe_company_id(company_id: str, existing_ids: set[str]) -> str:
    if company_id not in existing_ids:
        return company_id
    index = 2
    while f"{company_id}-{index}" in existing_ids:
        index += 1
    return f"{company_id}-{index}"
