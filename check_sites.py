# -*- coding: utf-8 -*-
"""Массовая проверка доменов: жив ли сайт и продаются ли там элементы питания.

Стратегия на домен (останавливаемся на первом надёжном доказательстве):
  1. GET / — код ответа, редиректы, заголовок.
  2. Поиск батарейных сигналов в HTML главной.
  3. robots.txt -> sitemap -> грепаем URL по батарейным слагам (самый надёжный сигнал).
  4. Типовые URL поиска по сайту.
  5. Типовые URL категорий.
Всё через HTTPS_PROXY; Chromium в этой среде через прокси не проходит.
"""
import asyncio, re, json, sys, os
import httpx

ROOT = "/home/user/aitim"
CA = "/root/.ccr/ca-bundle.crt"
UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36")
HEADERS = {"User-Agent": UA, "Accept-Language": "ru-RU,ru;q=0.9,en;q=0.8",
           "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8"}

# сильные признаки категории элементов питания
STRONG_TXT = re.compile(
    r"батарейк|элемент(?:ы|ов|а)?\s+питания|источник(?:и|ов)?\s+питания|"
    r"\bLR6\b|\bLR03\b|\bCR2032\b|\bCR2025\b|\bAA\s*\(LR6\)|крона\s*9\s*[вv]", re.I)
STRONG_SLUG = re.compile(
    r"batarejk|batareyk|batareik|batarei?ki|elementy[-_]pitani|element[-_]pitani|"
    r"istochniki[-_]pitani|/batteries?/|akkumulyatornye[-_]batare", re.I)
WEAK_TXT = re.compile(r"аккумулятор|элемент питания|battery|батаре", re.I)

SEARCH_PATHS = ["/search?q=батарейки", "/search/?q=батарейки", "/?s=батарейки",
                "/catalog/search?q=батарейки", "/search?text=батарейки",
                "/site_search?search_query=батарейки"]
CAT_PATHS = ["/catalog/batareyki/", "/catalog/elementy-pitaniya/", "/batareyki/",
             "/elementy-pitaniya/", "/catalog/batarejki/"]

SEM = asyncio.Semaphore(14)
TIMEOUT = httpx.Timeout(20.0, connect=15.0)


async def get(client, url):
    async with SEM:
        try:
            r = await client.get(url, headers=HEADERS, follow_redirects=True)
            return r
        except Exception as e:
            return e


def title_of(html):
    m = re.search(r"<title[^>]*>(.*?)</title>", html, re.S | re.I)
    return re.sub(r"\s+", " ", m.group(1)).strip()[:120] if m else ""


async def sitemap_urls(client, root):
    """Собираем до ~3 sitemap и ищем батарейные слаги в URL."""
    found = []
    smaps = []
    r = await get(client, root + "/robots.txt")
    if isinstance(r, httpx.Response) and r.status_code == 200:
        smaps += re.findall(r"(?im)^\s*sitemap:\s*(\S+)", r.text)[:4]
    if not smaps:
        smaps = [root + "/sitemap.xml"]
    seen = 0
    for sm in smaps[:4]:
        if seen >= 3:
            break
        r = await get(client, sm)
        if not isinstance(r, httpx.Response) or r.status_code != 200:
            continue
        seen += 1
        body = r.text[:3_000_000]
        hits = STRONG_SLUG.findall(body)
        if hits:
            m = re.findall(r"<loc>([^<]*(?:batarejk|batareyk|batareik|elementy[-_]pitani|"
                           r"element[-_]pitani|batteries)[^<]*)</loc>", body, re.I)
            found += m[:3]
            if found:
                return found
        # индекс карт: спускаемся в первую вложенную
        if "<sitemapindex" in body[:2000].lower() and seen < 3:
            nested = re.findall(r"<loc>([^<]+)</loc>", body)[:6]
            for n in nested:
                if seen >= 3:
                    break
                r2 = await get(client, n)
                if isinstance(r2, httpx.Response) and r2.status_code == 200:
                    seen += 1
                    m = re.findall(r"<loc>([^<]*(?:batarejk|batareyk|batareik|"
                                   r"elementy[-_]pitani|batteries)[^<]*)</loc>", r2.text, re.I)
                    if m:
                        return m[:3]
    return found


async def check(client, domain):
    out = {"domain": domain, "alive": False, "http": "", "final": "", "title": "",
           "battery": "нет данных", "evidence": "", "method": ""}
    for scheme in ("https://", "http://"):
        r = await get(client, scheme + domain)
        if isinstance(r, httpx.Response):
            out["http"] = r.status_code
            out["final"] = str(r.url)
            out["alive"] = r.status_code < 400
            html = r.text if r.headers.get("content-type", "").startswith("text") else ""
            out["title"] = title_of(html)
            if out["alive"] and html:
                m = STRONG_TXT.search(html)
                if m:
                    out.update(battery="да", method="главная",
                               evidence=f"на главной: «{m.group(0)[:40]}»")
                    return out
            break
        out["http"] = type(r).__name__
    if not out["alive"]:
        code = out["http"]
        if isinstance(code, int) and code in (401, 403, 405, 429, 432, 503, 202):
            out["battery"] = "закрыт ботозащитой"
        elif isinstance(code, int):
            out["battery"] = f"ошибка HTTP {code}"
        else:
            out["battery"] = "сайт не отвечает"
        return out

    root = out["final"].rstrip("/")
    if root.count("/") > 2:
        root = "/".join(root.split("/")[:3])

    urls = await sitemap_urls(client, root)
    if urls:
        out.update(battery="да", method="sitemap", evidence=urls[0][:150])
        return out

    for p in SEARCH_PATHS[:3]:
        r = await get(client, root + p)
        if isinstance(r, httpx.Response) and r.status_code == 200:
            m = STRONG_TXT.search(r.text)
            if m and len(STRONG_TXT.findall(r.text)) >= 3:
                out.update(battery="да", method="поиск по сайту",
                           evidence=(root + p)[:150])
                return out

    for p in CAT_PATHS:
        r = await get(client, root + p)
        if isinstance(r, httpx.Response) and r.status_code == 200 and STRONG_TXT.search(r.text):
            out.update(battery="да", method="категория", evidence=(root + p)[:150])
            return out

    out["battery"] = "не найдено"
    return out


async def main():
    domains = json.load(open(f"{ROOT}/research/all_domains.json"))
    limits = httpx.Limits(max_connections=20, max_keepalive_connections=8)
    async with httpx.AsyncClient(timeout=TIMEOUT, limits=limits, verify=CA,
                                 proxy=os.environ.get("HTTPS_PROXY")) as client:
        res = []
        tasks = [check(client, d) for d in domains]
        for i, fut in enumerate(asyncio.as_completed(tasks), 1):
            res.append(await fut)
            if i % 25 == 0:
                print(f"  {i}/{len(domains)}", flush=True)
        json.dump(res, open(f"{ROOT}/research/site_check.json", "w"),
                  ensure_ascii=False, indent=1)
    ok = sum(1 for x in res if x["alive"])
    bat = sum(1 for x in res if x["battery"] == "да")
    print(f"\nготово: живых {ok}/{len(res)}, батарейки подтверждены у {bat}")


if __name__ == "__main__":
    asyncio.run(main())
