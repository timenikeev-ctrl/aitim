# -*- coding: utf-8 -*-
"""Четвёртый проход: домены, где категория не нашлась с первого взгляда.

Что делаем:
  1. Собираем внутренние ссылки главной, ищем среди них каталожные разделы.
  2. Заходим в каталог на 1–2 уровня и грепаем батарейные признаки и слаги.
  3. Пробуем расширенный набор поисковых URL.
  4. Заодно определяем, интернет-магазин ли это вообще (корзина, цены, «купить»),
     чтобы отличать «магазин без батареек» от корпоративного сайта-визитки.
"""
import asyncio, json, os, re
from urllib.parse import urljoin, urlparse
import httpx

exec(open("/home/user/aitim/check_sites.py").read().split("async def main")[0])

CAT_LINK = re.compile(r"catalog|katalog|shop|magazin|product|tovar|goods|categ|"
                      r"kategor|assortiment|price|prays", re.I)
SHOP_SIGN = re.compile(r"корзин|в корзину|добавить в корзину|оформить заказ|"
                       r"add[-_]?to[-_]?cart|/cart|/basket|руб\.|₽|цена", re.I)
DEEP_SEARCH = ["/search?q=батарейки", "/search/?q=батарейки", "/?s=батарейки",
               "/search?text=батарейки", "/catalog/search?q=батарейки",
               "/search?query=батарейки", "/poisk/?q=батарейки",
               "/index.php?route=product/search&search=батарейки",
               "/site_search?search_query=батарейки", "/search/index.php?q=батарейки"]


def internal_links(html, root):
    host = urlparse(root).netloc.replace("www.", "")
    out = []
    for href in re.findall(r'href=["\']([^"\'#]+)["\']', html):
        if href.startswith(("mailto:", "tel:", "javascript:")):
            continue
        u = urljoin(root + "/", href)
        p = urlparse(u)
        if p.scheme in ("http", "https") and p.netloc.replace("www.", "").endswith(host):
            out.append(u.split("?")[0].rstrip("/"))
    return list(dict.fromkeys(out))


async def deep(client, rec):
    root = "/".join(rec["final"].split("/")[:3]) if rec["final"] else "https://" + rec["domain"]
    rec = dict(rec)

    r = await get(client, root)
    html = txt_of(r) if isinstance(r, httpx.Response) else ""
    rec["is_shop"] = "да" if SHOP_SIGN.search(html) else "нет"

    links = internal_links(html, root)
    # прямое попадание слага в ссылках главной
    for u in links:
        if SLUG_RX.search(u):
            return dict(rec, battery="да", method="ссылка на главной (4-й проход)", evidence=u[:150])

    # заходим в каталожные разделы
    cats = [u for u in links if CAT_LINK.search(u)][:12]
    lvl2 = []
    for u in cats:
        r = await get(client, u)
        if not isinstance(r, httpx.Response) or r.status_code != 200:
            continue
        t = txt_of(r)
        if STRONG_TXT.search(t):
            return dict(rec, battery="да", method="раздел каталога (4-й проход)", evidence=u[:150])
        h = HREF_RX.search(t)
        if h:
            return dict(rec, battery="да", method="ссылка в каталоге (4-й проход)",
                        evidence=urljoin(u + "/", h.group(1))[:150])
        if rec["is_shop"] == "нет" and SHOP_SIGN.search(t):
            rec["is_shop"] = "да"
        lvl2 += [x for x in internal_links(t, root) if CAT_LINK.search(x)][:6]

    # второй уровень каталога
    for u in list(dict.fromkeys(lvl2))[:14]:
        if SLUG_RX.search(u):
            return dict(rec, battery="да", method="подраздел каталога (4-й проход)", evidence=u[:150])
        r = await get(client, u)
        if isinstance(r, httpx.Response) and r.status_code == 200:
            t = txt_of(r)
            if STRONG_TXT.search(t):
                return dict(rec, battery="да", method="подраздел каталога (4-й проход)", evidence=u[:150])

    # расширенный поиск по сайту
    for p in DEEP_SEARCH:
        r = await get(client, root + p)
        if isinstance(r, httpx.Response) and r.status_code == 200:
            t = txt_of(r)
            if len(STRONG_TXT.findall(t)) >= 3:
                return dict(rec, battery="да", method="поиск по сайту (4-й проход)",
                            evidence=(root + p)[:150])

    rec["battery"] = ("магазин, батареек не найдено" if rec["is_shop"] == "да"
                      else "не интернет-магазин")
    return rec


async def main():
    recs = json.load(open("/home/user/aitim/research/site_check.json"))
    todo_d = set(json.load(open("/home/user/aitim/research/notfound_rest.json")))
    todo = [r for r in recs if r["domain"] in todo_d]
    print(f"четвёртый проход по {len(todo)} доменам", flush=True)
    limits = httpx.Limits(max_connections=110, max_keepalive_connections=35)
    async with httpx.AsyncClient(timeout=TIMEOUT, limits=limits, verify=CA,
                                 proxy=os.environ.get("HTTPS_PROXY")) as client:
        out = {}
        for i, fut in enumerate(asyncio.as_completed([deep(client, r) for r in todo]), 1):
            r = await fut
            out[r["domain"]] = r
            if i % 15 == 0:
                print(f"  {i}/{len(todo)}", flush=True)
    merged = [out.get(r["domain"], r) for r in recs]
    json.dump(merged, open("/home/user/aitim/research/site_check.json", "w"),
              ensure_ascii=False, indent=1)
    import collections
    got = sum(1 for x in merged if x["domain"] in todo_d and x["battery"] == "да")
    print(f"\nраскрыто: {got} из {len(todo)}")
    print("итог:", dict(collections.Counter(x["battery"] for x in merged)))


if __name__ == "__main__":
    asyncio.run(main())
