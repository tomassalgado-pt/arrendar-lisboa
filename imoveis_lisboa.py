#!/usr/bin/env python3
"""
Pesquisa de apartamentos para arrendar em Lisboa em vários portais.

Uso:
    pip install requests beautifulsoup4 lxml
    python imoveis_lisboa.py --max-preco 1400 --tipologia 1 2 --paginas 3

    # Para portais com proteção anti-bot (Idealista, Imovirtual):
    pip install playwright && playwright install chromium
    python imoveis_lisboa.py --browser --max-preco 1400

Resultado: imoveis_lisboa.csv (ordenado por preço, com duplicados entre portais agrupados).
"""
import argparse
import csv
import json
import os
import random
import re
import smtplib
import ssl
import sys
import time
from datetime import datetime, timezone
from email.message import EmailMessage
from pathlib import Path
from urllib.parse import urljoin

import requests
from bs4 import BeautifulSoup

UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/124.0 Safari/537.36"
)

# url: {page} é o número da página. link: regex do href de um anúncio.
# NOTA: URLs e padrões de link mudam com frequência; ajustar aqui se um portal falhar.
SITES = {
    "idealista": {
        "url": "https://www.idealista.pt/arrendar-casas/lisboa/pagina-{page}",
        "link": r"/imovel/\d+",
        "base": "https://www.idealista.pt",
    },
    "imovirtual": {
        "url": "https://www.imovirtual.com/pt/resultados/arrendar/apartamento/lisboa?page={page}",
        "link": r"/pt/anuncio/",
        "base": "https://www.imovirtual.com",
    },
    "casa_sapo": {
        "url": "https://casa.sapo.pt/alugar-apartamentos/lisboa/?pn={page}",
        "link": r"/(?:alugar|arrendar)[^\"']*\d{5,}|/imovel/|/detalhes/",
        "base": "https://casa.sapo.pt",
    },
    "supercasa": {
        "url": "https://supercasa.pt/arrendar-casas/lisboa/pagina-{page}",
        "link": r"/arrendar-[^/]+/[^/]+/.+-\d+|/imovel/",
        "base": "https://supercasa.pt",
    },
    "olx": {
        "url": "https://www.olx.pt/imoveis/apartamento-casa-para-arrendar/lisboa/?page={page}",
        "link": r"/d/anuncio/",
        "base": "https://www.olx.pt",
    },
    "custojusto": {
        "url": "https://www.custojusto.pt/lisboa/imobiliario/apartamentos-casas-para-arrendar?o={page}",
        "link": r"/lisboa/imobiliario/.+-\d+$|/imobiliario/.+-\d+",
        "base": "https://www.custojusto.pt",
    },
    "remax": {
        "url": "https://www.remax.pt/pt/arrendar/apartamento/lisboa/r/r?page={page}",
        "link": r"/pt/imoveis/",
        "base": "https://www.remax.pt",
    },
    "century21": {
        "url": "https://www.century21.pt/comprar/?ptype=2&cat=2&dist=11&conc=1106&page={page}",
        "link": r"/imoveis?/|/comprar/.+\d{5,}",
        "base": "https://www.century21.pt",
    },
}

PRICE_RE = re.compile(r"(\d{1,3}(?:[ .\u00a0]\d{3})+|\d{3,5})\s*€|€\s*(\d{1,3}(?:[ .\u00a0]\d{3})+|\d{3,5})")
AREA_RE = re.compile(r"(\d{2,4}(?:[.,]\d+)?)\s*m(?:²|2)", re.I)
TIPO_RE = re.compile(r"\bT\s?(\d)\b", re.I)


def fetch_requests(session, url):
    r = session.get(url, headers={"User-Agent": UA, "Accept-Language": "pt-PT,pt;q=0.9"}, timeout=25)
    if r.status_code in (403, 429):
        raise RuntimeError(f"bloqueado (HTTP {r.status_code}) - tente --browser")
    r.raise_for_status()
    return r.text


class BrowserFetcher:
    def __init__(self):
        from playwright.sync_api import sync_playwright
        self._pw = sync_playwright().start()
        self._browser = self._pw.chromium.launch(headless=True)
        self._ctx = self._browser.new_context(user_agent=UA, locale="pt-PT")

    def get(self, url):
        page = self._ctx.new_page()
        try:
            page.goto(url, wait_until="domcontentloaded", timeout=45000)
            page.wait_for_timeout(2500)
            return page.content()
        finally:
            page.close()

    def close(self):
        self._browser.close()
        self._pw.stop()


def to_int(s):
    return int(re.sub(r"\D", "", s))


def load_seen(path):
    """Devolve {url: primeira_vez}. 'base' = já existia na primeira execução."""
    p = Path(path)
    if p.exists():
        try:
            d = json.loads(p.read_text(encoding="utf-8"))
            if isinstance(d, list):  # formato antigo
                return {u: "base" for u in d}
            return dict(d)
        except Exception:
            pass
    return {}


def save_seen(path, seen):
    Path(path).write_text(json.dumps(seen, ensure_ascii=False, sort_keys=True), encoding="utf-8")


def send_email(novos, destino):
    """Credenciais por variáveis de ambiente (nunca no código):
    SMTP_USER, SMTP_PASS (palavra-passe de aplicação), opcionais SMTP_HOST, SMTP_PORT.
    """
    user = os.environ.get("SMTP_USER")
    pwd = os.environ.get("SMTP_PASS")
    if not user or not pwd:
        raise SystemExit("Defina SMTP_USER e SMTP_PASS (palavra-passe de aplicação) para enviar alertas.")
    host = os.environ.get("SMTP_HOST", "smtp.gmail.com")
    port = int(os.environ.get("SMTP_PORT", "465"))

    top = novos[:50]
    linhas_txt, linhas_html = [], []
    for i in top:
        det = " · ".join(
            x for x in [
                f'T{i["tipologia"]}' if i["tipologia"] is not None else "",
                f'{i["area_m2"]:.0f} m²' if i["area_m2"] else "",
                i["portal"],
            ] if x
        )
        linhas_txt.append(f'{i["preco"]} € - {det}\n{i["url"]}\n')
        linhas_html.append(
            f'<li><b>{i["preco"]} €</b> - {det}<br><a href="{i["url"]}">{i["url"]}</a></li>'
        )
    extra = f"\n(+{len(novos) - len(top)} no CSV)" if len(novos) > len(top) else ""

    msg = EmailMessage()
    msg["Subject"] = f"{len(novos)} novo(s) apartamento(s) para arrendar em Lisboa"
    msg["From"] = user
    msg["To"] = destino
    msg.set_content("\n".join(linhas_txt) + extra)
    msg.add_alternative(
        "<ul>" + "".join(linhas_html) + "</ul>" + extra.replace("\n", "<br>"), subtype="html"
    )
    with smtplib.SMTP_SSL(host, port, context=ssl.create_default_context()) as s:
        s.login(user, pwd)
        s.send_message(msg)


def parse_listings(html, site, cfg):
    soup = BeautifulSoup(html, "lxml")
    link_re = re.compile(cfg["link"])
    seen, out = set(), []
    for a in soup.find_all("a", href=True):
        if not link_re.search(a["href"]):
            continue
        # sobe até encontrar o "cartão" que contém o preço
        card = a
        for _ in range(6):
            if card.parent is None:
                break
            card = card.parent
            if "€" in card.get_text():
                break
        text = " ".join(card.get_text(" ", strip=True).split())
        m = PRICE_RE.search(text)
        if not m:
            continue
        url = urljoin(cfg["base"], a["href"].split("?")[0])
        if url in seen:
            continue
        seen.add(url)
        preco = to_int(m.group(1) or m.group(2))
        area = AREA_RE.search(text)
        tipo = TIPO_RE.search(text)
        titulo = a.get_text(" ", strip=True) or text[:90]
        out.append({
            "portal": site,
            "titulo": titulo[:120],
            "preco": preco,
            "area_m2": float(area.group(1).replace(",", ".")) if area else None,
            "tipologia": int(tipo.group(1)) if tipo else None,
            "url": url,
        })
    return out


def main():
    ap = argparse.ArgumentParser(description="Apartamentos para arrendar em Lisboa (multi-portal)")
    ap.add_argument("--max-preco", type=int, default=None)
    ap.add_argument("--min-preco", type=int, default=300, help="ignora valores absurdos/parciais")
    ap.add_argument("--tipologia", type=int, nargs="*", help="ex: 1 2 (T1 e T2)")
    ap.add_argument("--min-area", type=float, default=None)
    ap.add_argument("--paginas", type=int, default=3)
    ap.add_argument("--portais", nargs="*", default=list(SITES), choices=list(SITES))
    ap.add_argument("--browser", action="store_true", help="usa Playwright (Chromium) em vez de requests")
    ap.add_argument("--saida", default="imoveis_lisboa.csv")
    ap.add_argument("--alerta-email", metavar="EMAIL", help="envia por email só os anúncios novos")
    ap.add_argument("--estado", default="vistos.json", help="ficheiro com a data em que cada anúncio foi visto pela 1.ª vez")
    ap.add_argument("--meta", metavar="FICHEIRO", help="escreve um JSON com a data da atualização (usado pela página)")
    args = ap.parse_args()

    session = requests.Session()
    fetcher = BrowserFetcher() if args.browser else None
    todos = []

    try:
        for site in args.portais:
            cfg = SITES[site]
            n_site = 0
            for p in range(1, args.paginas + 1):
                url = cfg["url"].format(page=p)
                try:
                    html = fetcher.get(url) if fetcher else fetch_requests(session, url)
                except Exception as e:
                    print(f"[{site}] pág {p}: {e}", file=sys.stderr)
                    break
                items = parse_listings(html, site, cfg)
                if not items:
                    print(f"[{site}] pág {p}: 0 anúncios (layout mudou ou bloqueio)", file=sys.stderr)
                    break
                todos.extend(items)
                n_site += len(items)
                time.sleep(random.uniform(2.5, 5))
            print(f"[{site}] {n_site} anúncios", file=sys.stderr)
    finally:
        if fetcher:
            fetcher.close()

    if not todos:
        # nada recolhido (bloqueio ou layout alterado): não sobrescreve os ficheiros anteriores
        print("Nenhum anúncio recolhido; ficheiros anteriores mantidos.", file=sys.stderr)
        sys.exit(1)

    # filtros
    def ok(i):
        if i["preco"] < args.min_preco:
            return False
        if args.max_preco and i["preco"] > args.max_preco:
            return False
        if args.tipologia and i["tipologia"] not in args.tipologia:
            return False
        if args.min_area and i["area_m2"] and i["area_m2"] < args.min_area:
            return False
        return True

    todos = [i for i in todos if ok(i)]

    # agrupa prováveis duplicados entre portais (mesmo preço + área + tipologia)
    grupos = {}
    for i in todos:
        k = (i["preco"], round(i["area_m2"]) if i["area_m2"] else None, i["tipologia"])
        grupos.setdefault(k, []).append(i)
    linhas = []
    for k, g in grupos.items():
        base = dict(g[0])
        base["outros_portais"] = " | ".join(f'{x["portal"]}: {x["url"]}' for x in g[1:]) if k[1] and len(g) > 1 else ""
        if not (k[1] and len(g) > 1):  # sem área não se agrupa
            linhas.extend({**x, "outros_portais": ""} for x in g)
        else:
            linhas.append(base)
    linhas.sort(key=lambda x: x["preco"])

    # data da 1.ª vez que cada anúncio apareceu (na 1.ª execução fica "base", ou seja, não conta como novo)
    vistos = load_seen(args.estado)
    primeira_execucao = not vistos
    agora = datetime.now(timezone.utc).isoformat(timespec="seconds")
    novos = []
    for l in linhas:
        if l["url"] not in vistos:
            vistos[l["url"]] = "base" if primeira_execucao else agora
            novos.append(l)
        l["visto_em"] = vistos[l["url"]]

    campos = ["portal", "titulo", "preco", "area_m2", "tipologia", "url", "outros_portais", "visto_em"]
    with open(args.saida, "w", newline="", encoding="utf-8-sig") as f:
        w = csv.DictWriter(f, fieldnames=campos)
        w.writeheader()
        w.writerows(linhas)
    print(f"\n{len(linhas)} anúncios guardados em {args.saida} ({len(novos)} novos)")

    if args.meta:
        Path(args.meta).write_text(
            json.dumps({"atualizado_em": agora, "total": len(linhas), "novos": len(novos)}),
            encoding="utf-8",
        )

    if args.alerta_email and novos and not primeira_execucao:
        send_email(novos, args.alerta_email)
        print(f"Email enviado com {len(novos)} anúncio(s) novo(s) para {args.alerta_email}")
    elif args.alerta_email:
        print("Sem anúncios novos (ou 1.ª execução); nenhum email enviado.")

    # guarda o estado só depois de o email ter sido enviado com sucesso
    save_seen(args.estado, vistos)


if __name__ == "__main__":
    main()
