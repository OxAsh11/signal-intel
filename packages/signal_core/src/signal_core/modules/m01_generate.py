"""M1 Brief Studio — generate a Brief from a website (P7)."""

from __future__ import annotations

import asyncio
import json
import re
from typing import Any
from urllib.parse import urljoin, urlparse
from uuid import UUID

import aiohttp
from psycopg_pool import AsyncConnectionPool
from pydantic import BaseModel

from signal_core.ai_gateway.gateway import AiGateway
from signal_core.ai_gateway.outputs import BriefDraft
from signal_core.ai_gateway.prompts import Capability, load_prompt
from signal_core.brief_columns import clean_columns
from signal_core.connectors.base import FetchBlocked, FetchDisallowed, FetchUnresolvable
from signal_core.logging import get_logger
from signal_core.metering import CallContext

log = get_logger(__name__)

PROMPT_NAME = "brief_intake"
PROMPT_VERSION = 4

STEPS = [
    "reading_site",
    "drafting_icp",
    "drafting_columns",
    "drafting_rubric",
    "choosing_sources",
    "done",
]

PAGE_KEYWORDS = ["about", "product", "solution", "customer", "industr", "case", "platform"]
MAX_PAGES = 5
PER_PAGE_BYTES = 400 * 1024
TEXT_CAP = 12000
TOTAL_BUDGET_S = 20.0
PAGE_TIMEOUT_S = 10.0


class BriefGenerateInput(BaseModel):
    brief_id: UUID
    domain: str
    description: str
    org_id: UUID
    workspace_id: UUID


def _normalise_domain(raw: str) -> str:
    raw = raw.strip()
    if "://" in raw:
        parsed = urlparse(raw)
        host = parsed.hostname or ""
    else:
        parsed = urlparse("https://" + raw)
        host = parsed.hostname or raw.split("/")[0]
    host = host.strip().lower()
    if host.startswith("www."):
        host = host[4:]
    return host


def _step_index(steps: list[dict[str, Any]], name: str) -> int:
    for i, s in enumerate(steps):
        if s["step"] == name:
            return i
    return -1


async def _write_steps(
    pool: AsyncConnectionPool, brief_id: UUID, steps: list[dict[str, Any]]
) -> None:
    async with pool.connection() as conn:
        sql = "select app.update_intake_steps(%s, %s)"
        await conn.execute(sql, (str(brief_id), json.dumps(steps)))


async def _fetch_html(url: str) -> bytes:
    timeout = aiohttp.ClientTimeout(total=PAGE_TIMEOUT_S)
    async with aiohttp.ClientSession(timeout=timeout) as session:
        async with session.get(url, allow_redirects=True) as resp:
            if resp.status >= 400:
                raise RuntimeError(f"http {resp.status}")
            data = await resp.read()
            if len(data) > PER_PAGE_BYTES:
                data = data[:PER_PAGE_BYTES]
            return data


def _strip_tags(html: bytes) -> tuple[str, str]:
    text = html.decode("utf-8", errors="replace")
    title_m = re.search(r"<title[^>]*>(.*?)</title>", text, re.I | re.S)
    title = title_m.group(1).strip() if title_m else ""
    # drop scripts/styles
    text = re.sub(r"<script.*?</script>", " ", text, flags=re.I | re.S)
    text = re.sub(r"<style.*?</style>", " ", text, flags=re.I | re.S)
    text = re.sub(r"<[^>]+>", " ", text)
    text = re.sub(r"\s+", " ", text).strip()
    return title, text


def _extract_links(html: bytes, base_url: str, host: str) -> list[str]:
    text = html.decode("utf-8", errors="replace")
    found: list[str] = []
    seen: set[str] = set()
    for m in re.finditer(r'href\s*=\s*["\']([^"\']+)["\']', text, re.I):
        href = m.group(1).strip()
        url = urljoin(base_url, href)
        parsed = urlparse(url)
        if parsed.scheme not in ("http", "https"):
            continue
        hostname = (parsed.hostname or "").lower()
        if hostname.startswith("www."):
            hostname = hostname[4:]
        if hostname != host:
            continue
        if url in seen:
            continue
        seen.add(url)
        path = (parsed.path or "").lower()
        if any(k in path for k in PAGE_KEYWORDS):
            found.append(url)

    # preference order: sort by first keyword index
    def rank(u: str) -> int:
        p = urlparse(u).path.lower()
        for i, k in enumerate(PAGE_KEYWORDS):
            if k in p:
                return i
        return len(PAGE_KEYWORDS)

    found.sort(key=rank)
    return found


async def run_brief_generate(
    inp: BriefGenerateInput,
    gateway: AiGateway,
    pool: AsyncConnectionPool,
) -> None:
    host = _normalise_domain(inp.domain)
    base_url = f"https://{host}"
    steps: list[dict[str, Any]] = [{"step": s, "status": "pending", "detail": ""} for s in STEPS]
    await _write_steps(pool, inp.brief_id, steps)

    # ---- reading_site ----
    idx = _step_index(steps, "reading_site")
    steps[idx]["status"] = "running"
    await _write_steps(pool, inp.brief_id, steps)

    guard = None
    try:
        from signal_core.connectors.base import FetchGuard

        guard = FetchGuard()
    except Exception:  # pragma: no cover
        guard = None

    pages: list[tuple[str, str, str]] = []
    reason: str | None = None
    try:

        async def _read() -> list[tuple[str, str, str]]:
            nonlocal reason
            out: list[tuple[str, str, str]] = []
            await asyncio.to_thread(guard.check, base_url) if guard else None
            raw = await _fetch_html(base_url)
            title, text = _strip_tags(raw)
            out.append((base_url, title, text))
            links = _extract_links(raw, base_url, host)
            for url in links:
                if len(out) >= MAX_PAGES:
                    break
                try:
                    if guard:
                        await guard.check(url)
                    r = await _fetch_html(url)
                    t, txt = _strip_tags(r)
                    out.append((url, t, txt))
                except Exception:
                    continue
            return out

        pages = await asyncio.wait_for(_read(), timeout=TOTAL_BUDGET_S)
    except (FetchBlocked, FetchDisallowed, FetchUnresolvable) as exc:
        reason = str(exc)
    except TimeoutError:
        reason = "timed out"
    except Exception as exc:
        reason = str(exc) or type(exc).__name__

    if reason is not None or not pages:
        detail = f"Couldn't read {host} ({reason or 'no pages'}). Paste a description instead."
        steps[idx]["status"] = "failed"
        steps[idx]["detail"] = detail
        await _write_steps(pool, inp.brief_id, steps)
        async with pool.connection() as conn:
            await conn.execute("select app.fail_brief_intake(%s)", (str(inp.brief_id),))
        raise RuntimeError(detail)

    steps[idx]["status"] = "done"
    steps[idx]["detail"] = f"Read {len(pages)} page(s)"
    await _write_steps(pool, inp.brief_id, steps)

    # Build site text
    chunks: list[str] = []
    for url, title, text in pages:
        chunks.append(f"URL: {url}\nTitle: {title}\n{text}")
    site_text = "\n\n---\n\n".join(chunks)
    if len(site_text) > TEXT_CAP:
        site_text = site_text[:TEXT_CAP]

    # ---- drafting ----
    for name in ("drafting_icp", "drafting_columns", "drafting_rubric", "choosing_sources"):
        i = _step_index(steps, name)
        steps[i]["status"] = "running"
    await _write_steps(pool, inp.brief_id, steps)

    prompt = load_prompt(PROMPT_NAME, PROMPT_VERSION)
    ctx = CallContext(
        org_id=inp.org_id,
        workspace_id=inp.workspace_id,
        brief_id=inp.brief_id,
        job_id=None,
        job_type="brief.generate",
        job_args={"domain": inp.domain, "description": inp.description},
    )
    try:
        existing = await _existing_predicates(pool, inp.workspace_id)
        shown = [{k: p[k] for k in ("key", "label", "value_type")} for p in existing]
        result = await gateway.run(
            Capability.WRITE,
            prompt,
            {
                "description": inp.description or inp.domain,
                "site_text": site_text,
                "existing_predicates": json.dumps(shown),
            },
            BriefDraft,
            ctx,
        )
        config = result.value.model_dump(mode="json")
    except Exception:
        for name in ("drafting_icp", "drafting_columns", "drafting_rubric", "choosing_sources"):
            i = _step_index(steps, name)
            steps[i]["status"] = "failed"
        await _write_steps(pool, inp.brief_id, steps)
        async with pool.connection() as conn:
            await conn.execute("select app.fail_brief_intake(%s)", (str(inp.brief_id),))
        raise

    raw_name = config.pop("name", None)
    name = raw_name.strip() if isinstance(raw_name, str) else ""
    config["assumptions"] = [
        a.strip() for a in config.get("assumptions") or [] if isinstance(a, str) and a.strip()
    ][:5]
    existing_types = {p["key"]: p["value_type"] for p in existing}
    existing_fills = {p["key"]: p["fill_method"] for p in existing}
    specs, rules, dropped = clean_columns(
        config["predicates"], config["evidence_rules"], existing_types, existing_fills
    )
    for reason in dropped:
        log.warning("brief_generate.column_dropped", brief_id=str(inp.brief_id), reason=reason)
    config["predicates"] = [s.model_dump(mode="json") for s in specs]
    config["evidence_rules"] = [r.model_dump(mode="json") for r in rules]

    for name in ("drafting_icp", "drafting_columns", "drafting_rubric", "choosing_sources"):
        i = _step_index(steps, name)
        steps[i]["status"] = "done"
    di = _step_index(steps, "done")
    steps[di]["status"] = "done"
    await _write_steps(pool, inp.brief_id, steps)

    async with pool.connection() as conn:
        await conn.execute(
            "select app.save_brief_draft(%s, %s)", (str(inp.brief_id), json.dumps(config))
        )
        if name:
            await conn.execute("select app.apply_drafted_name(%s, %s)", (str(inp.brief_id), name))


async def _existing_predicates(
    pool: AsyncConnectionPool, workspace_id: UUID
) -> list[dict[str, str]]:
    async with pool.connection() as conn, conn.cursor() as cur:
        await cur.execute(
            "SELECT key, label, value_type, fill_method FROM public.predicate"
            " WHERE workspace_id = %s ORDER BY key",
            (str(workspace_id),),
        )
        rows = await cur.fetchall()
    return [{"key": r[0], "label": r[1], "value_type": r[2], "fill_method": r[3]} for r in rows]
