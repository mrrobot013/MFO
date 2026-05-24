"""Прослеживание цепочки редиректов внутри ссылок из SMS.

Алгоритм:
1. Достать все URL из текста SMS регуляркой.
2. Для каждого URL: пройти HTTP 30x редиректы + meta-refresh.
3. Опционально (use_browser=True) — добить через Selenium, если есть JS-редиректы.
4. Вернуть финальный URL после всех редиректов.

Возвращаем None, если URL в SMS нет.
"""
from __future__ import annotations

import re
import time as _time
from dataclasses import dataclass

import httpx
from loguru import logger

# Жадно цепляем http(s):// + любые символы до пробела/кавычки/конца.
URL_RE = re.compile(
    r"https?://[^\s<>\"'\)\(\]\[]+",
    re.IGNORECASE,
)
BARE_URL_RE = re.compile(
    r"(?<![@\w.-])"
    r"((?:[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?\.)+[a-z]{2,}"
    r"(?:/[^\s<>\"'\)\(\]\[]*)?)",
    re.IGNORECASE,
)


@dataclass
class RedirectTrace:
    original: str
    final: str
    hops: list[str]
    status_code: int | None = None


def extract_urls(text: str) -> list[str]:
    """Все URL в тексте SMS (без дублей, в порядке встречания)."""
    seen: set[str] = set()
    result: list[str] = []
    for m in URL_RE.finditer(text or ""):
        url = m.group(0).rstrip(".,;:)]}»")
        if url not in seen:
            seen.add(url)
            result.append(url)
    for m in BARE_URL_RE.finditer(text or ""):
        raw = m.group(1).rstrip(".,;:)]}»")
        if raw.lower().startswith(("http://", "https://")):
            url = raw
        else:
            url = f"https://{raw}"
        if url not in seen:
            seen.add(url)
            result.append(url)
    return result


_META_REFRESH_RX = re.compile(
    r'<meta[^>]+http-equiv=["\']?refresh["\']?[^>]*content=["\']?\s*\d+\s*;\s*url=([^"\'>\s]+)',
    re.IGNORECASE,
)
_JS_REDIRECT_RX = re.compile(
    r'(?:location\.(?:href|replace|assign)\s*=\s*|window\.location\s*=\s*)["\']([^"\']+)["\']',
    re.IGNORECASE,
)


async def follow_http(url: str, user_agent: str, timeout: float = 15.0) -> RedirectTrace:
    """HTTP 30x + meta-refresh + простые JS-редиректы (без выполнения JS)."""
    hops: list[str] = [url]
    headers = {"User-Agent": user_agent}
    async with httpx.AsyncClient(follow_redirects=False, timeout=timeout, headers=headers) as cli:
        current = url
        status_code: int | None = None
        for _ in range(20):
            try:
                r = await cli.get(current)
                status_code = r.status_code
            except Exception as e:
                logger.debug(f"HTTP follow stopped on {current}: {e}")
                break

            if r.is_redirect or r.status_code in (301, 302, 303, 307, 308):
                loc = r.headers.get("location")
                if not loc:
                    break
                current = str(httpx.URL(current).join(loc))
                hops.append(current)
                continue

            # meta refresh / JS location
            body = (r.text or "")[:30000]
            m = _META_REFRESH_RX.search(body) or _JS_REDIRECT_RX.search(body)
            if m:
                nxt = m.group(1).strip()
                if nxt and nxt != current:
                    current = str(httpx.URL(current).join(nxt))
                    hops.append(current)
                    status_code = None
                    continue
            break
    return RedirectTrace(original=url, final=hops[-1], hops=hops, status_code=status_code)


def follow_selenium(url: str, user_agent: str) -> RedirectTrace:
    """Полный обход через Selenium (учитывает JS-редиректы)."""
    from src.form.browser import _make_undetected_driver  # noqa: WPS437

    driver = _make_undetected_driver(headless=True, user_agent=user_agent, user_data_dir=None)
    hops: list[str] = [url]
    try:
        driver.get(url)
        _time.sleep(4)
        final = driver.current_url
        if final and final != hops[-1]:
            hops.append(final)
    except Exception as e:
        logger.debug(f"selenium follow error: {e}")
        final = hops[-1]
    finally:
        try:
            driver.quit()
        except Exception:
            pass
    return RedirectTrace(original=url, final=hops[-1], hops=hops)


async def resolve_final_url(
    text: str, user_agent: str, use_browser: bool = False
) -> tuple[str | None, list[RedirectTrace]]:
    """Найти URL в SMS и вернуть финальный после всех редиректов."""
    urls = extract_urls(text)
    traces: list[RedirectTrace] = []
    for url in urls:
        t = await follow_http(url, user_agent=user_agent)
        if use_browser and t.final == url:
            import asyncio as _aio
            t = await _aio.to_thread(follow_selenium, url, user_agent)
        traces.append(t)
        logger.info(f"redirect: {url}  →  {t.final}  ({len(t.hops)} hops)")
    final = traces[0].final if traces else None
    return final, traces
