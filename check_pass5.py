# -*- coding: utf-8 -*-
"""Пятый проход: разбор доменов, ошибочно помеченных «закрыт ботозащитой».

Оказалось, что 405 в прошлом проходе возвращал сам агент-прокси на plain-HTTP
запрос (фолбэк на http:// после неудачи по https), а не сайты. Реальные причины:
  * сайт живёт только на www. (или наоборот);
  * сертификат не проходит проверку;
  * 502 от egress-прокси — транзиентная сетевая ошибка, лечится ретраем.
Разбираем эти случаи по отдельности и только остаток считаем настоящей защитой.
TLS-проверку не отключаем: такие домены выносим в отдельный статус.
"""
import asyncio, json, os, re
import httpx

exec(open("/home/user/aitim/check_sites.py").read().split("async def main")[0])


async def fetch_variant(client, host):
    """Пробуем www/без www, с ретраем на транзиентных ошибках прокси."""
    for attempt in range(3):
        try:
            r = await client.get(host, headers=HEADERS, follow_redirects=True)
            if r.status_code == 502 and attempt < 2:
                await asyncio.sleep(1.5 * (attempt + 1))
                continue
            return r, ""
        except Exception as e:
            msg = str(e)
            if "CERTIFICATE_VERIFY_FAILED" in msg:
                return None, "tls"
            if attempt < 2:
                await asyncio.sleep(1.0 * (attempt + 1))
                continue
            return None, type(e).__name__
    return None, "retry_exhausted"


async def deep(client, rec):
    """Обёртка с жёстким лимитом времени: один зависший домен не держит весь проход."""
    try:
        return await asyncio.wait_for(_deep(client, rec), timeout=120)
    except asyncio.TimeoutError:
        r = dict(rec)
        r["battery"] = "недоступен через прокси"
        r["evidence"] = "превышен лимит 120 с на проверку домена"
        return r


async def _deep(client, rec):
    d = rec["domain"]
    bare = d[4:] if d.startswith("www.") else d
    variants = [f"https://{bare}", f"https://www.{bare}"]
    best, why = None, ""
    async with SEM:
        for v in variants:
            r, err = await fetch_variant(client, v)
            if r is not None and r.status_code < 400:
                best = r
                break
            if r is not None and (best is None or r.status_code < 500):
                best, why = r, ""
            if err:
                why = err

    rec = dict(rec)
    if best is None:
        rec["battery"] = ("сертификат не проходит проверку" if why == "tls"
                          else "недоступен через прокси")
        rec["evidence"] = f"HTTPS не установлен ({why}); ни {bare}, ни www.{bare}"
        rec["http"] = why or "—"
        return rec

    rec["http"] = best.status_code
    rec["final"] = str(best.url)
    html = txt_of(best)
    rec["title"] = title_of(html) or rec.get("title", "")
    if best.status_code >= 400:
        rec["battery"] = "закрыт ботозащитой"
        rec["evidence"] = f"HTTP {best.status_code} на обоих вариантах хоста"
        return rec

    rec["alive"] = True
    m = STRONG_TXT.search(html)
    if m:
        return dict(rec, battery="да", method="текст главной (www-вариант)",
                    evidence=f"{best.url} — «{m.group(0).strip()[:38]}»")
    h = HREF_RX.search(html) or ANCHOR_RX.search(html)
    if h:
        return dict(rec, battery="да", method="ссылка в меню (www-вариант)",
                    evidence=h.group(1)[:150])

    root = "/".join(str(best.url).split("/")[:3])
    u = await from_sitemap(client, root)
    if u:
        return dict(rec, battery="да", method="sitemap (www-вариант)", evidence=u[:150])
    for p in SEARCH_PATHS[:4]:
        r = await get(client, root + p)
        if isinstance(r, httpx.Response) and r.status_code == 200:
            t = txt_of(r)
            if len(STRONG_TXT.findall(t)) >= 3:
                return dict(rec, battery="да", method="поиск по сайту (www-вариант)",
                            evidence=(root + p)[:150])
    for p in CAT_PATHS:
        r = await get(client, root + p)
        if isinstance(r, httpx.Response) and r.status_code == 200 and STRONG_TXT.search(txt_of(r)):
            return dict(rec, battery="да", method="категория (www-вариант)", evidence=(root + p)[:150])

    rec["battery"] = "магазин, батареек не найдено" if re.search(
        r"корзин|/cart|/basket|₽|руб\.", html, re.I) else "не интернет-магазин"
    rec["evidence"] = f"открылся как {best.url}, категория не найдена"
    return rec


async def main():
    recs = json.load(open("/home/user/aitim/research/site_check.json"))
    todo = [r for r in recs
            if r["battery"] in ("закрыт ботозащитой",) or str(r["battery"]).startswith("ошибка HTTP")]
    print(f"пятый проход по {len(todo)} доменам", flush=True)
    limits = httpx.Limits(max_connections=90, max_keepalive_connections=30)
    async with httpx.AsyncClient(timeout=TIMEOUT, limits=limits, verify=CA,
                                 proxy=os.environ.get("HTTPS_PROXY")) as client:
        out = {}
        for i, fut in enumerate(asyncio.as_completed([deep(client, r) for r in todo]), 1):
            r = await fut
            out[r["domain"]] = r
            if i % 10 == 0:
                print(f"  {i}/{len(todo)}", flush=True)
                json.dump(list(out.values()),
                          open("/home/user/aitim/research/pass5_partial.json", "w"),
                          ensure_ascii=False)
    merged = [out.get(r["domain"], r) for r in recs]
    json.dump(merged, open("/home/user/aitim/research/site_check.json", "w"),
              ensure_ascii=False, indent=1)
    import collections
    got = sum(1 for x in merged if x["domain"] in out and x["battery"] == "да")
    print(f"\nраскрыто: {got} из {len(todo)}")
    print("итог:", dict(collections.Counter(x["battery"] for x in merged)))


if __name__ == "__main__":
    asyncio.run(main())
