# -*- coding: utf-8 -*-
"""Второй проход: добиваем домены со статусами «закрыт ботозащитой» и «не найдено».

У сайтов под ботозащитой robots.txt и sitemap.xml обычно отдаются без проверки,
поэтому категорию можно подтвердить в обход защиты главной страницы.
Для «не найдено» расширяем перебор поисковых и категорийных URL и грепаем /catalog/.
"""
import asyncio, json, os, re
import httpx

exec(open("/home/user/aitim/check_sites.py").read().split("async def main")[0])

EXTRA_CAT = ["/catalog/", "/shop/", "/products/", "/catalog/elektrika/",
             "/catalog/batarei/", "/tovary/batareyki/", "/collection/batareyki",
             "/category/elementy-pitaniya/", "/catalog/istochniki-pitaniya/"]
EXTRA_SEARCH = ["/search?query=батарейки", "/catalog/?q=батарейки",
                "/index.php?route=product/search&search=батарейки",
                "/search/?text=батарейки", "/poisk?q=батарейки"]


async def deep(client, rec):
    d, root = rec["domain"], ""
    if rec["final"]:
        root = "/".join(rec["final"].split("/")[:3])
    else:
        root = "https://" + d

    # 1. robots + sitemap — обычно не под защитой
    u = await from_sitemap(client, root)
    if u:
        return dict(rec, battery="да", method="sitemap (2-й проход)", evidence=u[:150])

    # 2. поиск по сайту, расширенный набор
    for p in SEARCH_PATHS + EXTRA_SEARCH:
        r = await get(client, root + p)
        if isinstance(r, httpx.Response) and r.status_code == 200:
            t = txt_of(r)
            if len(STRONG_TXT.findall(t)) >= 3:
                return dict(rec, battery="да", method="поиск по сайту (2-й проход)",
                            evidence=(root + p)[:150])

    # 3. категорийные URL
    for p in CAT_PATHS + EXTRA_CAT:
        r = await get(client, root + p)
        if isinstance(r, httpx.Response) and r.status_code == 200:
            t = txt_of(r)
            if STRONG_TXT.search(t):
                return dict(rec, battery="да", method="категория (2-й проход)",
                            evidence=(root + p)[:150])
            h = HREF_RX.search(t)
            if h:
                return dict(rec, battery="да", method="ссылка в каталоге (2-й проход)",
                            evidence=h.group(1)[:150])
    return rec


async def main():
    recs = json.load(open("/home/user/aitim/research/site_check.json"))
    todo = [r for r in recs if r["battery"] in ("закрыт ботозащитой", "не найдено")]
    print(f"второй проход по {len(todo)} доменам", flush=True)
    limits = httpx.Limits(max_connections=120, max_keepalive_connections=40)
    async with httpx.AsyncClient(timeout=TIMEOUT, limits=limits, verify=CA,
                                 proxy=os.environ.get("HTTPS_PROXY")) as client:
        out = {}
        for i, fut in enumerate(asyncio.as_completed([deep(client, r) for r in todo]), 1):
            r = await fut
            out[r["domain"]] = r
            if i % 25 == 0:
                print(f"  {i}/{len(todo)}", flush=True)
    merged = [out.get(r["domain"], r) for r in recs]
    json.dump(merged, open("/home/user/aitim/research/site_check.json", "w"),
              ensure_ascii=False, indent=1)
    import collections
    print("\nитог:", dict(collections.Counter(x["battery"] for x in merged)))


if __name__ == "__main__":
    asyncio.run(main())
