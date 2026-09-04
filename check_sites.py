# -*- coding: utf-8 -*-
"""Массовая проверка доменов: жив ли сайт и продаются ли там элементы питания.

Порядок проверки на домен (останавливаемся на первом доказательстве):
  1. GET / — код, редиректы, заголовок; текстовые признаки в HTML.
  2. Ссылки в вёрстке главной с батарейными слагами в href или анкоре.
  3. robots.txt -> sitemap (в т.ч. .gz и вложенные индексы) -> грепаем <loc>.
  4. Поиск по сайту типовыми URL.
  5. Типовые URL категорий.
Chromium через прокси этой среды не проходит, поэтому работаем HTTP-слоем.
"""
import asyncio, re, json, os, gzip, io
import httpx

ROOT = "/home/user/aitim"
CA = "/root/.ccr/ca-bundle.crt"
UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36")
HEADERS = {"User-Agent": UA, "Accept-Language": "ru-RU,ru;q=0.9,en;q=0.8",
           "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8"}

STRONG_TXT = re.compile(
    r"батарейк|элемент(?:ы|ов|а)?\s+питания|источник(?:и|ов)?\s+питания|"
    r"\bLR6\b|\bLR03\b|\bCR2032\b|\bCR2025\b|крона\s*9\s*[вv]", re.I)
SLUG = (r"batarejk|batareyk|batareik|batarei?ki|elementy[-_]pitani|element[-_]pitani|"
        r"istochniki[-_]pitani|akkumulyatornye[-_]batare|/batteries?/|batareyki")
SLUG_RX = re.compile(SLUG, re.I)
HREF_RX = re.compile(r'href=["\']([^"\']*(?:%s)[^"\']*)["\']' % SLUG, re.I)
ANCHOR_RX = re.compile(r'href=["\']([^"\']+)["\'][^>]*>\s*[^<]{0,40}'
                       r'(?:батарейк|элемент[а-я]*\s+питания)', re.I)
LOC_RX = re.compile(r"<loc>([^<]*(?:%s)[^<]*)</loc>" % SLUG, re.I)

SEARCH_PATHS = ["/search?q=батарейки", "/search/?q=батарейки", "/?s=батарейки",
                "/catalog/search?q=батарейки", "/search?text=батарейки"]
CAT_PATHS = ["/catalog/batareyki/", "/catalog/elementy-pitaniya/", "/batareyki/",
             "/elementy-pitaniya/", "/catalog/batarejki/", "/category/batareyki/"]

SEM = asyncio.Semaphore(24)
TIMEOUT = httpx.Timeout(connect=12.0, read=18.0, write=10.0, pool=90.0)


async def get(client, url):
    async with SEM:
        try:
            return await client.get(url, headers=HEADERS, follow_redirects=True)
        except Exception as e:
            return e


def txt_of(r):
    ct = r.headers.get("content-type", "")
    if "gzip" in r.headers.get("content-encoding", "") or r.url.path.endswith(".gz"):
        try:
            return gzip.decompress(r.content).decode("utf-8", "ignore")
        except Exception:
            pass
    if any(t in ct for t in ("text", "xml", "json", "html")) or not ct:
        try:
            return r.text
        except Exception:
            return ""
    return ""


def title_of(html):
    m = re.search(r"<title[^>]*>(.*?)</title>", html, re.S | re.I)
    return re.sub(r"\s+", " ", m.group(1)).strip()[:120] if m else ""


async def from_sitemap(client, root):
    smaps, budget = [], 6
    r = await get(client, root + "/robots.txt")
    if isinstance(r, httpx.Response) and r.status_code == 200:
        smaps += re.findall(r"(?im)^\s*sitemap:\s*(\S+)", r.text)[:5]
    smaps += [root + "/sitemap.xml", root + "/sitemap_index.xml"]
    queue, seen = list(dict.fromkeys(smaps)), set()
    while queue and budget > 0:
        sm = queue.pop(0)
        if sm in seen:
            continue
        seen.add(sm); budget -= 1
        r = await get(client, sm)
        if not isinstance(r, httpx.Response) or r.status_code != 200:
            continue
        body = txt_of(r)[:4_000_000]
        hit = LOC_RX.findall(body)
        if hit:
            return hit[0]
        if "<sitemapindex" in body[:3000].lower():
            nested = re.findall(r"<loc>([^<]+)</loc>", body)
            pri = [u for u in nested if SLUG_RX.search(u) or "catalog" in u.lower()
                   or "product" in u.lower()]
            queue = (pri[:4] or nested[:4]) + queue
    return ""


async def check(client, domain):
    out = {"domain": domain, "alive": False, "http": "", "final": "", "title": "",
           "battery": "нет данных", "evidence": "", "method": ""}
    resp = None
    for scheme in ("https://", "http://"):
        r = await get(client, scheme + domain)
        if isinstance(r, httpx.Response):
            resp = r
            break
        out["http"] = type(r).__name__
    if resp is None:
        out["battery"] = "сайт не отвечает"
        return out

    out["http"] = resp.status_code
    out["final"] = str(resp.url)
    out["alive"] = resp.status_code < 400
    html = txt_of(resp)
    out["title"] = title_of(html)

    if not out["alive"]:
        c = resp.status_code
        out["battery"] = ("закрыт ботозащитой" if c in (401, 403, 405, 429, 432, 444, 453, 498, 503)
                          else f"ошибка HTTP {c}")
        return out

    m = STRONG_TXT.search(html)
    if m:
        out.update(battery="да", method="текст главной",
                   evidence=f"«{m.group(0).strip()[:40]}»")
        return out
    h = HREF_RX.search(html) or ANCHOR_RX.search(html)
    if h:
        out.update(battery="да", method="ссылка в меню", evidence=h.group(1)[:150])
        return out

    root = "/".join(out["final"].split("/")[:3])

    u = await from_sitemap(client, root)
    if u:
        out.update(battery="да", method="sitemap", evidence=u[:150])
        return out

    for p in SEARCH_PATHS[:3]:
        r = await get(client, root + p)
        if isinstance(r, httpx.Response) and r.status_code == 200:
            t = txt_of(r)
            if len(STRONG_TXT.findall(t)) >= 3:
                out.update(battery="да", method="поиск по сайту", evidence=(root + p)[:150])
                return out

    for p in CAT_PATHS:
        r = await get(client, root + p)
        if isinstance(r, httpx.Response) and r.status_code == 200 and STRONG_TXT.search(txt_of(r)):
            out.update(battery="да", method="категория", evidence=(root + p)[:150])
            return out

    out["battery"] = "не найдено"
    return out


async def main():
    domains = json.load(open(f"{ROOT}/research/all_domains.json"))
    limits = httpx.Limits(max_connections=120, max_keepalive_connections=40)
    async with httpx.AsyncClient(timeout=TIMEOUT, limits=limits, verify=CA,
                                 proxy=os.environ.get("HTTPS_PROXY")) as client:
        res = []
        for i, fut in enumerate(asyncio.as_completed([check(client, d) for d in domains]), 1):
            res.append(await fut)
            if i % 25 == 0:
                print(f"  {i}/{len(domains)}", flush=True)
        json.dump(res, open(f"{ROOT}/research/site_check.json", "w"),
                  ensure_ascii=False, indent=1)
    import collections
    c = collections.Counter(x["battery"] for x in res)
    print("\nготово:", dict(c))


if __name__ == "__main__":
    asyncio.run(main())
