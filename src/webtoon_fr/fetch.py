"""1. Récupération des images d'un chapitre.

Trois sources :
- un ou plusieurs liens (un chapitre réparti sur plusieurs pages donne plusieurs liens, dans l'ordre) ;
- une page enregistrée depuis ton navigateur (fichier .html + son dossier d'images, ou un .zip des deux),
  pour les pages qui demandent une connexion ou affichent une vérification anti-robot ;
- des images importées directement (gérées par le pipeline, sans filtrage).

Ce que fait la récupération par lien :
- lit toutes les façons de déclarer une image : src, data-* (lazy-loading), srcset, <picture>, <noscript>,
  fond CSS, et en dernier recours les adresses d'images écrites dans les scripts de la page ;
- si la page se remplit en JavaScript, l'ouvre dans un navigateur sans interface (Playwright, ou Chrome / Edge
  déjà installés), la fait défiler jusqu'en bas et capte les images au passage ;
- télécharge avec les cookies et le Referer de la page, respecte Retry-After (429, 503), relance les
  téléchargements tronqués, garde chaque fichier brut pour qu'une relance ne retélécharge que ce qui manque ;
- garde les images de la largeur dominante (les pages du chapitre) et écarte publicités, vignettes,
  icônes et doublons, avec la raison de chaque exclusion ;
- s'arrête avec la liste exacte des images manquantes plutôt que de laisser un trou dans le chapitre ;
- ne contourne jamais une connexion, une vérification anti-robot ou une protection : elle l'explique.

Tout est consigné dans source/rapport.json, que l'interface affiche à l'étape de vérification.
"""
from __future__ import annotations

import email.utils
import hashlib
import io
import json
import re
import shutil
import tempfile
import time
import zipfile
from collections import Counter
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from pathlib import Path
from urllib.parse import unquote, urljoin, urlparse

import httpx
from bs4 import BeautifulSoup
from PIL import Image

from .config import config

UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/131.0 Safari/537.36")
EXCLURE = re.compile(r"logo|avatar|banner|banniere|icon|favicon|sprite|emoji|badge|placeholder|loading|spinner|"
                     r"/ads?/|[_-]ads?[_./-]|advert|pixel|tracking|thumb(nail)?s?/", re.I)
EXT_IMAGE = re.compile(r"\.(jpe?g|png|webp|avif|gif|bmp)(\?|#|$)", re.I)
URL_TEXTE = re.compile(r"https?:(?:\\?/){2}[^\s\"'<>()]+?\.(?:jpe?g|png|webp|avif)(?:\?[^\s\"'<>()\\]*)?", re.I)
URL_CSS = re.compile(r"url\(\s*['\"]?([^'\")]+)['\"]?\s*\)")
ATTRS_PRIORITAIRES = ["data-src", "data-lazy-src", "data-original", "data-url", "data-lazy", "data-echo",
                      "data-original-src", "data-hi-res", "data-full", "data-image", "data-img", "data-cfsrc"]
MARQUES_PROTECTION = ["cf-chl", "challenge-platform", "just a moment", "attention required", "captcha",
                      "are you a robot", "ddos-guard", "verify you are human", "checking your browser",
                      "vérifiez que vous êtes humain"]
MARQUES_CONNEXION = ["/login", "/signin", "/sign-in", "/connexion", "/account/login", "/auth"]

ACCEPT_IMAGE = "image/avif,image/webp,image/apng,image/*,*/*;q=0.8"


class ErreurRecuperation(Exception):
    def __init__(self, message: str, cause: str = "autre", rapport: dict | None = None):
        super().__init__(message)
        self.cause = cause
        self.rapport = rapport


MESSAGES = {
    "protection": ("Le site affiche une vérification anti-robot (par exemple Cloudflare). L'outil ne la contourne pas. "
                   "Ouvre la page dans ton navigateur, fais défiler jusqu'en bas pour charger toutes les images, "
                   "puis enregistre-la (Ctrl+S, « Page web complète ») et importe-la avec « Page enregistrée ». "
                   "Tu peux aussi importer directement tes images."),
    "connexion": ("La page demande une connexion. L'outil ne se connecte pas à ta place. Connecte-toi dans ton navigateur, "
                  "fais défiler le chapitre jusqu'en bas, enregistre la page (Ctrl+S, « Page web complète ») "
                  "et importe-la avec « Page enregistrée »."),
    "canvas": ("Ce site dessine les pages dans un canvas au lieu d'images, ce qui empêche de les récupérer. "
               "L'outil ne contourne pas ce choix du site : importe tes fichiers d'origine avec « Images »."),
    "navigateur": ("La page charge ses images en JavaScript et aucun navigateur n'est disponible pour l'ouvrir. "
                   "Installe Playwright (pip install playwright puis python -m playwright install chromium), "
                   "ou installe Google Chrome ou Microsoft Edge, puis relance."),
}


@dataclass
class Candidat:
    url: str
    ordre: int
    origine: str                      # html | noscript | fond | script | navigateur | page_enregistree
    page: int = 1                     # numéro du lien d'origine (chapitre sur plusieurs pages)
    contenu: bytes | None = None
    erreur: str | None = None
    infos: dict = field(default_factory=dict)


# --- extraction des adresses d'images -------------------------------------------------

def _ressemble_image(v: str) -> bool:
    v = v.strip()
    if not v or v.startswith(("data:", "javascript:", "#", "blob:")):
        return False
    return bool(EXT_IMAGE.search(v)) or "/image" in v.lower() or "img" in v.lower()


def _srcset_max(v: str) -> str | None:
    meilleur = None
    for part in v.split(","):
        bits = part.strip().split()
        if not bits:
            continue
        poids = 1.0
        if len(bits) > 1:
            m = re.match(r"([\d.]+)([wx])", bits[1])
            if m:
                poids = float(m.group(1)) * (1 if m.group(2) == "w" else 1000)
        if meilleur is None or poids > meilleur[0]:
            meilleur = (poids, bits[0])
    return meilleur[1] if meilleur else None


def _url_img(tag) -> str | None:
    """Meilleure adresse d'un <img> : attributs de chargement différé, puis srcset, puis src."""
    for a in ATTRS_PRIORITAIRES:
        v = tag.get(a)
        if isinstance(v, str) and _ressemble_image(v):
            return v.strip()
    for a in ("data-srcset", "data-lazy-srcset", "srcset"):
        v = tag.get(a)
        if isinstance(v, str) and v.strip():
            u = _srcset_max(v)
            if u and not u.startswith("data:"):
                return u
    for a, v in tag.attrs.items():  # attribut data-* non standard
        if a.startswith("data-") and isinstance(v, str) and EXT_IMAGE.search(v) and not v.startswith("data:"):
            return v.strip()
    v = tag.get("src")
    if isinstance(v, str) and not v.startswith("data:") and v.strip():
        return v.strip()
    return None


def _petite_declaree(tag) -> bool:
    for a in ("width", "height"):
        v = tag.get(a)
        if isinstance(v, str) and v.isdigit() and int(v) < 150:
            return True
    return False


def extraire_candidats(html: str, base: str, page: int = 1) -> list[Candidat]:
    soup = BeautifulSoup(html, "html.parser")
    for t in soup(["header", "footer", "nav"]):
        t.decompose()
    vus, res = set(), []

    def ajouter(u: str | None, origine: str):
        if not u:
            return
        u = urljoin(base, u.strip())
        if u in vus or EXCLURE.search(u) or re.search(r"\.svg(\?|$)", u, re.I):
            return
        vus.add(u)
        res.append(Candidat(u, len(res), origine, page))

    for el in soup.find_all(True):
        if el.name == "img":
            if not _petite_declaree(el):
                ajouter(_url_img(el), "html")
        elif el.name == "source" and el.parent is not None and el.parent.name == "picture":
            v = el.get("data-srcset") or el.get("srcset")
            if isinstance(v, str):
                ajouter(_srcset_max(v), "html")
        elif el.name == "noscript":
            interne = BeautifulSoup(el.decode_contents(), "html.parser")
            for img in interne.find_all("img"):
                ajouter(_url_img(img), "noscript")
        style = el.get("style") if el.name != "img" else None
        if isinstance(style, str) and "url(" in style:
            for u in URL_CSS.findall(style):
                if _ressemble_image(u):
                    ajouter(u, "fond")
    # une même image déclarée dans <picture> puis dans <img> : on garde l'ordre de la première
    return res


def extraire_urls_scripts(html: str, base: str, page: int = 1) -> list[Candidat]:
    """Adresses d'images écrites dans les scripts (données JSON des lecteurs en JavaScript)."""
    soup = BeautifulSoup(html, "html.parser")
    res, vus = [], set()
    for s in soup.find_all("script"):
        texte = s.string or s.get_text() or ""
        # barres échappées des données JSON : \/ , \\/ , \u002F
        texte = re.sub(r"\\+/", "/", texte).replace("\\u002F", "/").replace("\\u002f", "/")
        for m in URL_TEXTE.finditer(texte):
            u = m.group(0)
            if u in vus or EXCLURE.search(u):
                continue
            vus.add(u)
            res.append(Candidat(urljoin(base, u), len(res), "script", page))
    return res


def diagnostiquer(statut: int, html: str, url_finale: str) -> str | None:
    bas = html[:60000].lower()
    if any(m in bas for m in MARQUES_PROTECTION) and (statut in (403, 429, 503) or bas.count("<img") < 3):
        return "protection"
    if statut == 401 or any(m in urlparse(url_finale).path.lower() for m in MARQUES_CONNEXION):
        return "connexion"
    if 'type="password"' in bas and bas.count("<img") < 5:
        return "connexion"
    return None


# --- téléchargement -----------------------------------------------------------------------

def _attente_retry_after(v: str | None, defaut: float) -> float:
    if not v:
        return defaut
    try:
        return min(90.0, float(v))
    except ValueError:
        try:
            d = email.utils.parsedate_to_datetime(v)
            return max(1.0, min(90.0, d.timestamp() - time.time()))
        except Exception:
            return defaut


def telecharger(client: httpx.Client, url: str, referer: str) -> tuple[bytes | None, str | None]:
    cfg = config()["recuperation"]
    entetes = {"Referer": referer, "Accept": ACCEPT_IMAGE, "Sec-Fetch-Dest": "image", "Sec-Fetch-Mode": "no-cors"}
    derniere = "échec inconnu"
    for essai in range(cfg["essais"]):
        try:
            time.sleep(cfg["pause_s"])
            r = client.get(url, headers=entetes, follow_redirects=True)
            if r.status_code == 200:
                data = r.content
                attendu = r.headers.get("content-length")
                if attendu and not r.headers.get("content-encoding") and attendu.isdigit() and int(attendu) != len(data):
                    derniere = f"téléchargement tronqué ({len(data)} octets sur {attendu})"
                    continue
                if len(data) > cfg["taille_max_mo"] * 1024 * 1024:
                    return None, "fichier trop lourd"
                return data, None
            if r.status_code in (404, 410):
                return None, f"introuvable sur le serveur (HTTP {r.status_code})"
            if r.status_code in (401, 403):
                derniere = f"accès refusé par le serveur d'images (HTTP {r.status_code})"
                if essai >= 1:
                    return None, derniere
            elif r.status_code in (429, 503):
                attente = _attente_retry_after(r.headers.get("retry-after"), 5 * (essai + 1))
                derniere = f"le site demande de ralentir (HTTP {r.status_code})"
                time.sleep(attente)
                continue
            else:
                derniere = f"HTTP {r.status_code}"
        except httpx.TimeoutException:
            derniere = "délai dépassé"
        except httpx.HTTPError as e:
            derniere = f"erreur réseau : {e.__class__.__name__}"
        time.sleep(min(30, 2 ** essai))
    return None, derniere


# --- navigateur sans interface ---------------------------------------------------------------

JS_IMAGES = r"""() => {
  const out = [], vus = new Set();
  const add = (u, w, h, o) => { if (!u || u.startsWith('data:') || vus.has(u)) return; vus.add(u); out.push({u, w, h, o}); };
  for (const el of document.querySelectorAll('img, div, section, figure, span, li, a, picture')) {
    if (el.tagName === 'IMG') { add(el.currentSrc || el.src, el.naturalWidth, el.naturalHeight, 'navigateur'); continue; }
    const bg = getComputedStyle(el).backgroundImage;
    if (bg && bg !== 'none') { const m = bg.match(/url\(["']?([^"')]+)["']?\)/); if (m) add(new URL(m[1], location.href).href, 0, 0, 'fond'); }
  }
  const canvas = [...document.querySelectorAll('canvas')].filter(c => c.width >= 400 && c.height >= 300).length;
  return {images: out, canvas, html: document.documentElement.outerHTML.length};
}"""


def _lancer_navigateur(p):
    for options in ({}, {"channel": "chrome"}, {"channel": "msedge"}):
        try:
            return p.chromium.launch(**options)
        except Exception:
            continue
    return None


def recuperer_par_navigateur(url: str, page_num: int, progres) -> tuple[list[Candidat], str | None]:
    try:
        from playwright.sync_api import sync_playwright
    except ImportError:
        return [], "navigateur"
    captures: dict[str, bytes] = {}
    with sync_playwright() as p:
        nav = _lancer_navigateur(p)
        if nav is None:
            return [], "navigateur"
        try:
            ctx = nav.new_context(user_agent=UA, viewport={"width": 1280, "height": 900}, locale="fr-FR")
            page = ctx.new_page()

            def sur_reponse(rep):
                try:
                    if rep.status == 200 and (rep.request.resource_type == "image"
                                              or rep.headers.get("content-type", "").startswith("image/")):
                        captures[rep.url] = rep.body()
                except Exception:
                    pass

            page.on("response", sur_reponse)
            progres("Ouverture de la page dans le navigateur sans interface…")
            rep = page.goto(url, wait_until="domcontentloaded", timeout=60000)
            page.wait_for_timeout(1500)
            cause = diagnostiquer(rep.status if rep else 200, page.content(), page.url)
            if cause:
                return [], cause
            progres("Défilement jusqu'en bas pour charger toutes les images…")
            stable, hauteur = 0, 0
            for _ in range(600):
                page.mouse.wheel(0, 900)
                page.wait_for_timeout(250)
                h = page.evaluate("document.documentElement.scrollHeight")
                bas = page.evaluate("window.scrollY + window.innerHeight")
                if bas >= h - 5:
                    page.wait_for_timeout(1200)
                    h2 = page.evaluate("document.documentElement.scrollHeight")
                    stable = stable + 1 if h2 == h == hauteur else 0
                    hauteur = h2
                    if stable >= 2:
                        break
            try:
                page.wait_for_load_state("networkidle", timeout=15000)
            except Exception:
                pass
            vues, canvas = [], 0
            for frame in page.frames:
                try:
                    r = frame.evaluate(JS_IMAGES)
                    vues += r["images"]
                    canvas += r["canvas"]
                except Exception:
                    continue
            larges = [v for v in vues if (v.get("w") or 0) >= 400]
            if canvas >= 3 and len(larges) < 3:
                return [], "canvas"
            res = []
            for v in vues:
                u = v["u"]
                if EXCLURE.search(u) or (0 < (v.get("w") or 0) < 150):
                    continue
                c = Candidat(u, len(res), v.get("o", "navigateur"), page_num)
                c.contenu = captures.get(u)
                if c.contenu is None:  # pas capté au passage : téléchargé avec les cookies du navigateur
                    try:
                        r = ctx.request.get(u, headers={"Referer": page.url, "Accept": ACCEPT_IMAGE}, timeout=60000)
                        if r.status == 200:
                            c.contenu = r.body()
                        else:
                            c.erreur = f"HTTP {r.status}"
                    except Exception as e:
                        c.erreur = f"erreur réseau : {e.__class__.__name__}"
                res.append(c)
            return res, None
        finally:
            nav.close()


# --- tri, filtrage et matérialisation ----------------------------------------------------------

def _cle_brut(url: str) -> str:
    return hashlib.sha1(url.encode()).hexdigest()[:16]


def _analyser(c: Candidat) -> None:
    try:
        ancienne = Image.MAX_IMAGE_PIXELS
        Image.MAX_IMAGE_PIXELS = 1_000_000_000
        try:
            im = Image.open(io.BytesIO(c.contenu))
            im.load()
        finally:
            Image.MAX_IMAGE_PIXELS = ancienne
        c.infos.update(largeur=im.width, hauteur=im.height, format=(im.format or "").lower(),
                       hash=hashlib.sha256(c.contenu).hexdigest())
    except Exception as e:
        c.erreur = f"image illisible ou tronquée ({e.__class__.__name__})"
        c.contenu = None


def filtrer(candidats: list[Candidat]) -> list[dict]:
    """Décide pour chaque candidat : gardée, écartée (avec raison) ou manquante."""
    cfg = config()["recuperation"]
    lisibles = [c for c in candidats if c.contenu is not None and c.infos.get("largeur")]
    larges = [c.infos["largeur"] for c in lisibles if c.infos["largeur"] >= cfg["largeur_min"] and c.infos["hauteur"] >= 40]
    ref = Counter(round(w / 10) * 10 for w in larges).most_common(1)[0][0] if larges else None
    if ref:  # largeur dominante exacte la plus proche de l'arrondi
        ref = Counter(w for w in larges if abs(w - ref) <= 10).most_common(1)[0][0]
    vus: dict[str, int] = {}
    decisions = []
    for c in candidats:
        d = {"ordre": c.ordre, "page": c.page, "url": c.url, "origine": c.origine,
             "largeur": c.infos.get("largeur"), "hauteur": c.infos.get("hauteur"), "statut": "gardee", "raison": None}
        if c.contenu is None:
            d["statut"], d["raison"] = "echec", c.erreur or "non téléchargée"
        elif c.infos["largeur"] < cfg["largeur_min"] or c.infos["hauteur"] < 40:
            d["statut"], d["raison"] = "ecartee", f"trop petite ({c.infos['largeur']}×{c.infos['hauteur']} px) : icône ou image de remplacement"
        elif ref and abs(c.infos["largeur"] - ref) / ref > cfg["tolerance_largeur"]:
            d["statut"], d["raison"] = "ecartee", f"largeur {c.infos['largeur']} px au lieu de {ref} px : probablement une publicité ou une vignette"
        elif c.infos["hash"] in vus:
            d["statut"], d["raison"] = "ecartee", f"doublon de l'image {vus[c.infos['hash']] + 1}"
        else:
            vus[c.infos["hash"]] = len(decisions)
        decisions.append(d)
    # un échec entre deux images gardées = un trou dans le chapitre
    gardees = [i for i, d in enumerate(decisions) if d["statut"] == "gardee"]
    if gardees:
        for i in range(gardees[0], gardees[-1] + 1):
            if decisions[i]["statut"] == "echec":
                decisions[i]["statut"] = "manquante"
    return decisions


def materialiser(dossier_source: Path, rapport: dict) -> list[Path]:
    """Recrée source/001.ext… à partir de source/brut/ selon les décisions du rapport."""
    brut = dossier_source / "brut"
    for f in dossier_source.iterdir():
        if f.is_file() and re.fullmatch(r"\d{3}\.\w+", f.name):
            f.unlink()
    sortie = []
    for d in rapport["images"]:
        if d["statut"] != "gardee" or not d.get("fichier"):
            continue
        src = brut / d["fichier"]
        ext = src.suffix if src.suffix else ".png"
        dst = dossier_source / f"{len(sortie) + 1:03d}{ext}"
        shutil.copy2(src, dst)
        sortie.append(dst)
    (dossier_source / "rapport.json").write_text(json.dumps(rapport, indent=2, ensure_ascii=False), "utf-8")
    return sortie


def _enregistrer_bruts(candidats: list[Candidat], brut: Path) -> None:
    brut.mkdir(parents=True, exist_ok=True)
    for c in candidats:
        if c.contenu is None:
            continue
        fmt = {"jpeg": ".jpg", "png": ".png", "webp": ".webp", "avif": ".avif", "gif": ".gif", "bmp": ".bmp"}
        ext = fmt.get(c.infos.get("format", ""), ".img")
        nom = _cle_brut(c.url) + ext
        if not (brut / nom).exists():
            (brut / nom).write_bytes(c.contenu)
        c.infos["fichier"] = nom


def _depuis_brut(c: Candidat, brut: Path) -> bool:
    """Réutilise un fichier déjà téléchargé lors d'une tentative précédente."""
    if not brut.exists():
        return False
    for f in brut.glob(_cle_brut(c.url) + ".*"):
        c.contenu = f.read_bytes()
        return True
    return False


def _conclure(candidats: list[Candidat], dossier_source: Path, rapport: dict, progres,
              autoriser_manques: bool) -> list[Path]:
    for c in candidats:
        if c.contenu is not None and not c.infos.get("largeur"):
            _analyser(c)
    _enregistrer_bruts(candidats, dossier_source / "brut")
    decisions = filtrer(candidats)
    for d, c in zip(decisions, candidats):
        d["fichier"] = c.infos.get("fichier")
    rapport["images"] = decisions
    n_g = sum(d["statut"] == "gardee" for d in decisions)
    manquantes = [d for d in decisions if d["statut"] == "manquante"]
    hauteur = sum(d["hauteur"] or 0 for d in decisions if d["statut"] == "gardee")
    rapport["resume"] = {"trouvees": len(decisions), "gardees": n_g, "hauteur_px": hauteur,
                         "ecartees": sum(d["statut"] == "ecartee" for d in decisions),
                         "manquantes": len(manquantes), "echecs_hors_chapitre": sum(d["statut"] == "echec" for d in decisions)}
    avert = []
    cfg = config()["recuperation"]
    if n_g < cfg["images_min"] or hauteur < cfg["hauteur_min_bande"]:
        avert.append(f"Récupération probablement incomplète : {n_g} image(s), {hauteur} px de bande.")
    if manquantes:
        avert.append(f"{len(manquantes)} image(s) du chapitre n'ont pas pu être téléchargées : "
                     + ", ".join(f"n°{d['ordre'] + 1} ({d['raison']})" for d in manquantes[:6])
                     + ". Relance la récupération : seules celles-ci seront retéléchargées.")
    rapport["avertissements"] = avert
    fichiers = materialiser(dossier_source, rapport)
    if not n_g:
        raise ErreurRecuperation("Aucune image de chapitre trouvée sur la page. " + MESSAGES["navigateur"]
                                 if rapport.get("methode") == "html" else "Aucune image de chapitre trouvée.", "vide", rapport)
    if manquantes and not autoriser_manques:
        raise ErreurRecuperation(avert[-1], "manquantes", rapport)
    progres(f"{n_g} images gardées ({hauteur} px de bande), {rapport['resume']['ecartees']} écartée(s).")
    return fichiers


# --- points d'entrée -------------------------------------------------------------------------------

def recuperer(urls: str | list[str], dossier_source: Path, progres=lambda *a: None,
              autoriser_manques: bool | None = None) -> list[Path]:
    """Récupère un chapitre depuis un ou plusieurs liens (dans l'ordre des pages)."""
    if isinstance(urls, str):
        urls = [u for u in re.split(r"\s+", urls.strip()) if u]
    cfg = config()["recuperation"]
    if autoriser_manques is None:
        autoriser_manques = cfg["autoriser_manques"]
    dossier_source.mkdir(parents=True, exist_ok=True)
    marque = dossier_source / "source.txt"
    deja = [f for f in dossier_source.iterdir() if f.is_file() and re.fullmatch(r"\d{3}\.\w+", f.name)]
    rapport_existant = dossier_source / "rapport.json"
    if deja and marque.exists() and marque.read_text("utf-8").split() == urls and rapport_existant.exists():
        r = json.loads(rapport_existant.read_text("utf-8"))
        if not r.get("resume", {}).get("manquantes"):
            progres("Images déjà récupérées pour ce lien, rien à retélécharger.")
            return sorted(deja)

    debut = time.time()
    rapport = {"liens": urls, "methode": "html", "date": time.strftime("%Y-%m-%d %H:%M:%S"), "pages": []}
    brut = dossier_source / "brut"
    tous: list[Candidat] = []
    with httpx.Client(headers={"User-Agent": UA, "Accept-Language": "fr,en;q=0.8"}, timeout=40,
                      follow_redirects=True) as client:
        for n, url in enumerate(urls, start=1):
            progres(f"Lecture de la page {n}/{len(urls)}…")
            try:
                r = client.get(url, headers={"Accept": "text/html,application/xhtml+xml,*/*;q=0.8"})
            except httpx.HTTPError as e:
                raise ErreurRecuperation(f"Page injoignable : {e}", "reseau") from e
            cause = diagnostiquer(r.status_code, r.text, str(r.url))
            if cause:
                raise ErreurRecuperation(MESSAGES[cause], cause)
            if r.status_code >= 400:
                raise ErreurRecuperation(f"Le site a répondu HTTP {r.status_code} pour {url}.", "http")
            cands = extraire_candidats(r.text, str(r.url), n)
            methode = "html"
            if len(cands) < cfg["images_min"]:
                scripts = extraire_urls_scripts(r.text, str(r.url), n)
                if len(scripts) >= cfg["images_min"]:
                    cands, methode = scripts, "script"
            if len(cands) < cfg["images_min"]:
                progres("Peu d'images dans le HTML : ouverture dans un navigateur sans interface…")
                cands_nav, cause = recuperer_par_navigateur(url, n, progres)
                if cause:
                    raise ErreurRecuperation(MESSAGES[cause], cause)
                cands, methode = cands_nav, "navigateur"
            rapport["pages"].append({"url": url, "methode": methode, "candidats": len(cands)})
            if methode != "html":
                rapport["methode"] = methode
            if len(cands) > cfg["images_max"]:
                raise ErreurRecuperation(f"{len(cands)} images sur la page : c'est plus qu'un chapitre "
                                         f"(limite {cfg['images_max']}). Vérifie le lien.", "trop")
            a_telecharger = [c for c in cands if c.contenu is None and not _depuis_brut(c, brut)]
            if a_telecharger:
                progres(f"Page {n} : {len(cands)} images trouvées, téléchargement de {len(a_telecharger)}…")
                faites = [0]

                def tache(c: Candidat):
                    c.contenu, c.erreur = telecharger(client, c.url, url)
                    faites[0] += 1
                    progres(f"Téléchargement : {faites[0]}/{len(a_telecharger)}", faites[0] / len(a_telecharger))

                with ThreadPoolExecutor(max_workers=cfg["requetes_paralleles"]) as ex:
                    list(ex.map(tache, a_telecharger))
            tous.extend(cands)

    for i, c in enumerate(tous):
        c.ordre = i
    marque.write_text("\n".join(urls), "utf-8")
    rapport["duree_s"] = round(time.time() - debut, 1)
    return _conclure(tous, dossier_source, rapport, progres, autoriser_manques)


def importer_page_enregistree(fichiers: list[Path], dossier_source: Path, progres=lambda *a: None) -> list[Path]:
    """Lit une page enregistrée depuis le navigateur : .html + son dossier d'images, ou un .zip des deux."""
    tmp = Path(tempfile.mkdtemp(prefix="webtoon-fr-page-"))
    try:
        for f in fichiers:
            if f.suffix.lower() == ".zip":
                with zipfile.ZipFile(f) as z:
                    for m in z.infolist():
                        cible = (tmp / m.filename).resolve()
                        if not str(cible).startswith(str(tmp.resolve())):
                            continue  # chemin dangereux dans l'archive
                        if m.is_dir():
                            cible.mkdir(parents=True, exist_ok=True)
                        else:
                            cible.parent.mkdir(parents=True, exist_ok=True)
                            cible.write_bytes(z.read(m))
            else:
                rel = Path(*[p for p in Path(f.name).parts if p not in ("..", "/", "\\")])
                (tmp / rel).parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(f, tmp / rel)
        pages = sorted(tmp.rglob("*.htm*"), key=lambda p: p.stat().st_size, reverse=True)
        if not pages:
            raise ErreurRecuperation("Aucun fichier .html dans ce que tu as importé. Enregistre la page avec "
                                     "Ctrl+S (« Page web complète ») et importe le .html avec son dossier, ou un .zip des deux.",
                                     "page")
        html_f = pages[0]
        html = html_f.read_text("utf-8", errors="replace")
        base = html_f.parent
        cands = extraire_candidats(html, "http://page.locale/", 1)
        index = {p.name: p for p in tmp.rglob("*") if p.is_file()}
        res = []
        racine = str(tmp.resolve())
        for c in cands:
            locale = c.url.startswith("http://page.locale/")
            chemin_rel = unquote(urlparse(c.url).path).lstrip("/")
            cible = (base / chemin_rel).resolve() if locale else None
            if cible is None or not cible.is_file() or not str(cible).startswith(racine):
                # adresse distante, ou chemin réécrit par le navigateur : on cherche le fichier par son nom
                cible = index.get(Path(chemin_rel).name)
            n = Candidat(chemin_rel if locale else c.url, len(res), "page_enregistree", 1)
            if cible is not None and cible.is_file() and str(cible.resolve()).startswith(racine):
                n.contenu = cible.read_bytes()
            else:
                n.erreur = "image non enregistrée avec la page (fais défiler jusqu'en bas avant d'enregistrer)"
            res.append(n)
        if not res:
            raise ErreurRecuperation("La page enregistrée ne contient aucune image.", "vide")
        dossier_source.mkdir(parents=True, exist_ok=True)
        (dossier_source / "source.txt").write_text(f"page enregistrée : {html_f.name}", "utf-8")
        rapport = {"liens": [], "methode": "page_enregistree", "page": html_f.name,
                   "date": time.strftime("%Y-%m-%d %H:%M:%S"), "pages": [{"url": html_f.name, "methode": "page_enregistree",
                                                                           "candidats": len(res)}]}
        progres(f"{len(res)} images trouvées dans la page enregistrée.")
        return _conclure(res, dossier_source, rapport, progres, config()["recuperation"]["autoriser_manques"])
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def lire_rapport(dossier_source: Path) -> dict | None:
    f = dossier_source / "rapport.json"
    return json.loads(f.read_text("utf-8")) if f.exists() else None


def modifier_selection(dossier_source: Path, garder: list[int], ecarter: list[int]) -> dict:
    """Change à la main le statut d'images (par leur ordre), puis recrée source/."""
    rapport = lire_rapport(dossier_source)
    if not rapport:
        raise ErreurRecuperation("Aucune récupération à modifier pour ce chapitre.", "vide")
    for d in rapport["images"]:
        if d["ordre"] in garder and d.get("fichier"):
            d["statut"], d["raison"] = "gardee", None
            d["manuel"] = True
        elif d["ordre"] in ecarter and d["statut"] == "gardee":
            d["statut"], d["raison"] = "ecartee", "écartée à la main"
            d["manuel"] = True
    gardees = [d for d in rapport["images"] if d["statut"] == "gardee"]
    rapport["resume"]["gardees"] = len(gardees)
    rapport["resume"]["ecartees"] = sum(d["statut"] == "ecartee" for d in rapport["images"])
    rapport["resume"]["hauteur_px"] = sum(d["hauteur"] or 0 for d in gardees)
    materialiser(dossier_source, rapport)
    return rapport
