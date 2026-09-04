# -*- coding: utf-8 -*-
"""Третий проход по доменам под ботозащитой: YML-фиды и глубокий разбор sitemap.

Логика: ботозащита ставится на HTML-страницы для людей, а машинные выгрузки
(товарные фиды для агрегаторов и карты сайта для поисковиков) отдаются свободно,
иначе магазин выпадет из Яндекс.Маркета и поисковой выдачи.
"""
import asyncio, json, os, re, gzip
import httpx

exec(open("/home/user/aitim/check_sites.py").read().split("async def main")[0])

# типовые пути товарных выгрузок
YML_PATHS = [
    "/yandex-market.yml", "/yandex_market.yml", "/yml.xml", "/yml/yml.xml",
    "/export/yandex.yml", "/export/products.xml", "/export/yml.xml", "/export/market.xml",
    "/market.xml", "/price.xml", "/catalog.yml", "/feed.yml", "/feed/yml",
    "/yandex.yml", "/upload/yml/yml.xml", "/bitrix/catalog_export/yandex.xml",
    "/bitrix/catalog_export/yandex_market.xml", "/bitrix/catalog_export/export.xml",
    "/google-merchant.xml", "/feeds/yandex.xml", "/shop/yml", "/yml-feed.xml",
    "/products.xml", "/offers.xml",
]
YML_HINT = re.compile(r"yml|yandex[-_]?market|market\.xml|price\.xml|offers?\.xml|"
                      r"catalog_export|merchant|feed", re.I)
# батарейные признаки внутри фида
FEED_RX = re.compile(
    r"батарейк|элемент(?:ы|ов|а)?\s+питания|источник(?:и|ов)?\s+питания|"
    r"\bLR6\b|\bLR03\b|\bCR2032\b|\bCR2025\b|\bLR14\b|\bLR20\b|\b6LR61\b|"
    r"duracell|energizer|varta|gp\s+super|gp\s+ultra|camelion|robiton|smartbuy", re.I)


async def fetch_text(client, url, cap=6_000_000):
    r = await get(client, url)
    if not isinstance(r, httpx.Response) or r.status_code != 200:
        return "", (r.status_code if isinstance(r, httpx.Response) else "")
    raw = r.content[:cap]
    if url.endswith(".gz") or r.headers.get("content-encoding") == "gzip":
        try:
            raw = gzip.decompress(raw)
        except Exception:
            pass
    return raw.decode("utf-8", "ignore"), 200


async def try_yml(client, root):
    """Ищем товарный фид и батарейки в нём."""
    cands = list(YML_PATHS)
    # robots.txt иногда указывает на фиды и карты
    t, _ = await fetch_text(client, root + "/robots.txt", 300_000)
    if t:
        for u in re.findall(r"(?im)^\s*(?:sitemap|allow|disallow):\s*(\S+)", t):
            if YML_HINT.search(u):
                cands.insert(0, u if u.startswith("http") else root + u)
    for p in cands[:26]:
        url = p if p.startswith("http") else root + p
        body, code = await fetch_text(client, url)
        if code != 200 or len(body) < 400:
            continue
        low = body[:4000].lower()
        if not any(k in low for k in ("<yml_catalog", "<offers", "<offer ", "<shop>",
                                      "<rss", "<channel", "<products")):
            continue
        m = FEED_RX.search(body)
        if m:
            return url, f"фид {url.split('/')[-1]}: «{m.group(0).strip()[:38]}»"
        return url, ""          # фид есть, батареек в нём нет
    return "", ""


async def deep_sitemap(client, root):
    """Расширенный обход карты сайта: больше бюджета, вложенные индексы, .gz."""
    smaps = []
    t, _ = await fetch_text(client, root + "/robots.txt", 300_000)
    if t:
        smaps += re.findall(r"(?im)^\s*sitemap:\s*(\S+)", t)
    smaps += [root + "/sitemap.xml", root + "/sitemap_index.xml",
              root + "/sitemap.xml.gz", root + "/sitemaps.xml"]
    queue, seen, budget = list(dict.fromkeys(smaps)), set(), 14
    while queue and budget > 0:
        sm = queue.pop(0)
        if sm in seen:
            continue
        seen.add(sm); budget -= 1
        body, code = await fetch_text(client, sm)
        if code != 200 or not body:
            continue
        hit = LOC_RX.findall(body)
        if hit:
            return hit[0]
        if "<sitemapindex" in body[:4000].lower():
            nested = re.findall(r"<loc>([^<]+)</loc>", body)
            pri = [u for u in nested if SLUG_RX.search(u)]
            if pri:
                return pri[0]
            rest = [u for u in nested if re.search(r"catalog|product|tovar|goods|categ", u, re.I)]
            queue = (rest[:8] or nested[:8]) + queue
    return ""


async def deep(client, rec):
    root = "/".join(rec["final"].split("/")[:3]) if rec["final"] else "https://" + rec["domain"]
    rec = dict(rec)

    feed_url, ev = await try_yml(client, root)
    if ev:
        return dict(rec, battery="да", method="YML-фид", evidence=ev[:150], feed=feed_url)
    rec["feed"] = feed_url

    u = await deep_sitemap(client, root)
    if u:
        return dict(rec, battery="да", method="sitemap (глубокий)", evidence=u[:150])

    if feed_url:
        rec["battery"] = "фид есть, батареек нет"
        rec["evidence"] = f"фид доступен: {feed_url}"
    return rec


async def main():
    recs = json.load(open("/home/user/aitim/research/site_check.json"))
    blocked = set(json.load(open("/home/user/aitim/research/blocked.json")))
    todo = [r for r in recs if r["domain"] in blocked]
    print(f"третий проход по {len(todo)} доменам под ботозащитой", flush=True)
    limits = httpx.Limits(max_connections=100, max_keepalive_connections=30)
    async with httpx.AsyncClient(timeout=TIMEOUT, limits=limits, verify=CA,
                                 proxy=os.environ.get("HTTPS_PROXY")) as client:
        out = {}
        for i, fut in enumerate(asyncio.as_completed([deep(client, r) for r in todo]), 1):
            r = await fut
            out[r["domain"]] = r
            if i % 10 == 0:
                print(f"  {i}/{len(todo)}", flush=True)
    merged = [out.get(r["domain"], r) for r in recs]
    json.dump(merged, open("/home/user/aitim/research/site_check.json", "w"),
              ensure_ascii=False, indent=1)
    import collections
    got = [x for x in merged if x["domain"] in blocked and x["battery"] == "да"]
    print(f"\nраскрыто: {len(got)} из {len(todo)}")
    print("итог:", dict(collections.Counter(x["battery"] for x in merged)))


if __name__ == "__main__":
    asyncio.run(main())
