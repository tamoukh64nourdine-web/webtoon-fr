"""1. Récupération des images d'un chapitre depuis l'adresse de sa page.

Prévu pour récupérer tes propres chapitres publiés. Si la page demande une connexion
ou bloque l'accès, l'outil s'arrête avec un message clair, sans rien contourner.
"""
from __future__ import annotations

import hashlib
import io
import re
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from urllib.parse import urljoin

import httpx
from bs4 import BeautifulSoup
from PIL import Image

from .config import config

UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/130.0 Safari/537.36 webtoon-fr/1.0")
EXCLURE = re.compile(r"logo|avatar|banner|banniere|icon|sprite|ads?[/_-]|advert|pub[/_-]|emoji|thumb|placeholder|loading",
                     re.I)
ATTRS = ["data-src", "data-lazy-src", "data-original", "data-url"]


class ErreurRecuperation(Exception):
    pass


def _meilleure_src(img, base: str) -> str | None:
    for a in ATTRS:
        v = img.get(a)
        if v and not v.startswith("data:"):
            return urljoin(base, v.strip())
    srcset = img.get("data-srcset") or img.get("srcset")
    if srcset:
        meilleurs = []
        for part in srcset.split(","):
            bits = part.strip().split()
            if not bits:
                continue
            w = 0
            if len(bits) > 1:
                m = re.match(r"(\d+)", bits[1])
                w = int(m.group(1)) if m else 0
            meilleurs.append((w, bits[0]))
        if meilleurs:
            return urljoin(base, max(meilleurs)[1])
    v = img.get("src")
    if v and not v.startswith("data:"):
        return urljoin(base, v.strip())
    return None


def extraire_urls(html: str, base: str) -> list[str]:
    """Images du conteneur principal, dans l'ordre du document."""
    soup = BeautifulSoup(html, "html.parser")
    imgs = soup.find_all("img")
    if not imgs:
        return []
    # conteneur principal = le parent qui regroupe le plus d'images
    compte: dict[int, tuple[object, int]] = {}
    for img in imgs:
        p = img.parent
        for _ in range(4):
            if p is None:
                break
            k = id(p)
            compte[k] = (p, compte.get(k, (p, 0))[1] + 1)
            p = p.parent
    meilleur = max(compte.values(), key=lambda t: t[1])
    conteneur = meilleur[0] if meilleur[1] >= 3 else soup
    urls, vues = [], set()
    for img in conteneur.find_all("img"):
        u = _meilleure_src(img, base)
        if not u or u in vues or EXCLURE.search(u) or re.search(r"\.(svg|gif)(\?|$)", u, re.I):
            continue
        vues.add(u)
        urls.append(u)
    return urls


def _page_html(url: str, client: httpx.Client) -> str:
    try:
        r = client.get(url, follow_redirects=True)
    except httpx.HTTPError as e:
        raise ErreurRecuperation(f"Page injoignable : {e}") from e
    if r.status_code in (401, 403):
        raise ErreurRecuperation(
            f"Accès refusé par le site (HTTP {r.status_code}). La page demande peut-être une connexion "
            "ou bloque les outils automatiques. Fournis plutôt un dossier d'images.")
    if r.status_code >= 400:
        raise ErreurRecuperation(f"Le site a répondu HTTP {r.status_code}.")
    if re.search(r'type=["\']password["\']', r.text, re.I) and "img" not in r.text[:200000].lower():
        raise ErreurRecuperation("La page demande une connexion. Fournis plutôt un dossier d'images.")
    return r.text


def _html_playwright(url: str) -> str | None:
    try:
        from playwright.sync_api import sync_playwright
    except ImportError:
        return None
    with sync_playwright() as p:
        nav = p.chromium.launch()
        page = nav.new_page(user_agent=UA)
        page.goto(url, wait_until="networkidle", timeout=60000)
        # défilement jusqu'en bas pour déclencher le chargement des images
        hauteur = 0
        for _ in range(200):
            page.mouse.wheel(0, 2500)
            page.wait_for_timeout(250)
            h = page.evaluate("document.body.scrollHeight")
            pos = page.evaluate("window.scrollY + window.innerHeight")
            if pos >= h and h == hauteur:
                break
            hauteur = h
        html = page.content()
        nav.close()
        return html


def recuperer(url: str, dossier_source: Path, progres=lambda *a: None) -> list[Path]:
    cfg = config()["recuperation"]
    dossier_source.mkdir(parents=True, exist_ok=True)
    deja = sorted(dossier_source.glob("[0-9][0-9][0-9].*"))
    marque = dossier_source / "source.txt"
    if deja and marque.exists() and marque.read_text("utf-8").strip() == url:
        progres("Images déjà récupérées pour ce lien, rien à retélécharger.")
        return deja

    headers = {"User-Agent": UA, "Accept-Language": "en,fr;q=0.8"}
    with httpx.Client(headers=headers, timeout=30) as client:
        progres("Lecture de la page…")
        html = _page_html(url, client)
        urls = extraire_urls(html, url)
        if len(urls) < cfg["images_min"]:
            progres("Peu d'images dans le HTML : essai avec un navigateur sans interface…")
            html2 = _html_playwright(url)
            if html2:
                urls = extraire_urls(html2, url)
        if not urls:
            raise ErreurRecuperation(
                "Aucune image de chapitre trouvée sur la page. Si le site charge les images en JavaScript, "
                "installe Playwright (voir README) ou fournis un dossier d'images.")
        progres(f"{len(urls)} images trouvées, téléchargement…")

        def telecharger(u: str) -> bytes | None:
            for essai in range(cfg["essais"]):
                try:
                    time.sleep(cfg["pause_s"])
                    r = client.get(u, headers={"Referer": url}, follow_redirects=True)
                    if r.status_code == 200:
                        return r.content
                    if r.status_code in (401, 403):
                        return None
                except httpx.HTTPError:
                    pass
                time.sleep(1 + 2 * essai)
            return None

        with ThreadPoolExecutor(max_workers=cfg["requetes_paralleles"]) as ex:
            contenus = list(ex.map(telecharger, urls))

    for f in deja:
        f.unlink()
    gardees, hashes = [], set()
    for u, data in zip(urls, contenus):
        if not data or len(data) < cfg["poids_min_ko"] * 1024:
            continue
        h = hashlib.sha256(data).hexdigest()
        if h in hashes:
            continue
        try:
            im = Image.open(io.BytesIO(data))
            im.load()
        except Exception:
            continue
        if im.width < cfg["largeur_min"]:
            continue
        hashes.add(h)
        ext = {"JPEG": "jpg", "PNG": "png", "WEBP": "webp"}.get(im.format or "", "png")
        f = dossier_source / f"{len(gardees) + 1:03d}.{ext}"
        f.write_bytes(data)
        gardees.append((f, im.height))

    if not gardees:
        raise ErreurRecuperation("Aucune image valide téléchargée (toutes trop petites ou refusées).")
    hauteur = sum(h for _, h in gardees)
    marque.write_text(url, "utf-8")
    if len(gardees) < cfg["images_min"] or hauteur < cfg["hauteur_min_bande"]:
        progres(f"Attention : récupération probablement incomplète ({len(gardees)} images, {hauteur} px).")
    else:
        progres(f"{len(gardees)} images récupérées ({hauteur} px de bande).")
    return [f for f, _ in gardees]
