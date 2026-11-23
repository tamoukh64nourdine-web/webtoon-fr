"""7. Rendu : écriture du texte français dans la forme réelle de chaque bulle.

- Chaque ligne a sa propre largeur, mesurée dans le masque de la bulle à sa hauteur.
- Taille de police maximale qui fait tenir le texte complet, jamais en dessous du minimum
  (1,75 % de la largeur de la bande : 14 px pour 800 px).
- Si le texte ne tient pas au minimum, cascade : césure française (toujours active),
  marge réduite, interligne resserré, police condensée, puis léger débordement + bulle à vérifier.
- Le texte n'est jamais raccourci.
"""
from __future__ import annotations

import re
from functools import lru_cache
from pathlib import Path

import cv2
import numpy as np
import pyphen
from PIL import Image, ImageDraw, ImageFont

from .config import chemin, config
from .models import Bulle
from .typo import NBSP, typo_fr

_cesure = pyphen.Pyphen(lang="fr_FR")


class ErreurPolice(Exception):
    pass


def verifier_licence(nom: str) -> None:
    f = chemin("fonts") / "LICENCES.md"
    if not f.exists():
        raise ErreurPolice("fonts/LICENCES.md manquant : impossible de vérifier la licence des polices.")
    for ligne in f.read_text("utf-8").splitlines():
        if ligne.strip().startswith("-") and nom in ligne:
            if re.search(r"commercial:\s*oui", ligne, re.I):
                return
            raise ErreurPolice(f"La police {nom} n'autorise pas l'usage commercial (fonts/LICENCES.md).")
    raise ErreurPolice(f"La police {nom} n'est pas listée dans fonts/LICENCES.md : ajoute sa licence avant de l'utiliser.")


@lru_cache(maxsize=256)
def police(nom: str, taille: int) -> ImageFont.FreeTypeFont:
    return ImageFont.truetype(str(chemin("fonts") / nom), taille)


def verifier_glyphes(nom: str) -> list[str]:
    """Caractères français absents de la police."""
    from fontTools.ttLib import TTFont  # type: ignore
    cmap = TTFont(str(chemin("fonts") / nom)).getBestCmap()
    return [c for c in "éèêëàâäîïôöùûüçœæÉÈÊÀÂÎÔÙÛÇŒ«»…" + NBSP if ord(c) not in cmap]


# --- mise en ligne ---------------------------------------------------------------

def _plage(m: np.ndarray, y: int, lh: int, cx: int) -> tuple[int, int]:
    """Plage horizontale libre sur toute la hauteur de la ligne, autour de cx."""
    if y < 0 or y + lh > m.shape[0]:
        return 0, 0
    libre = m[y:y + lh].all(axis=0)
    if not libre.any():
        return 0, 0
    if not libre[cx]:
        xs = np.nonzero(libre)[0]
        cx = int(xs[np.argmin(np.abs(xs - cx))])
    g = cx
    while g > 0 and libre[g - 1]:
        g -= 1
    d = cx
    while d < len(libre) - 1 and libre[d + 1]:
        d += 1
    return g, d - g + 1


def _coupures(mot: str):
    """Coupures possibles d'un mot (préfixe avec tiret, reste), les plus longues d'abord."""
    pos = list(_cesure.positions(mot.replace(NBSP, " ")))
    for p in sorted(pos, reverse=True):
        if 2 <= p <= len(mot) - 2:
            yield mot[:p] + "-", mot[p:]


def mettre_en_lignes(m: np.ndarray, texte: str, f: ImageFont.FreeTypeFont, taille: int, interligne: float,
                     condens: float):
    lh = max(1, round(taille * interligne))
    if not m.any():
        return None
    # point le plus profond de la bulle (et pas son centre de gravité, qui peut tomber dans une jonction)
    dist = cv2.distanceTransform(np.pad(m, 1).astype(np.uint8), cv2.DIST_L2, 5)[1:-1, 1:-1]
    cy, cx = np.unravel_index(int(np.argmax(dist)), dist.shape)
    cy, cx = int(cy), int(cx)
    largeur = lambda s: f.getlength(s) * condens
    mots = texte.split(" ")
    n_estime = 1
    for _ in range(4):
        y = int(cy - n_estime * lh / 2)
        lignes, i, reste = [], 0, None
        while i < len(mots) or reste:
            x, w = _plage(m, y, lh, cx)
            if w <= 0:
                return None
            courant = ""
            while i < len(mots) or reste:
                mot = reste if reste else mots[i]
                essai = (courant + " " + mot).strip() if courant else mot
                if largeur(essai) <= w:
                    courant = essai
                    if reste:
                        reste = None
                    else:
                        i += 1
                    continue
                # le mot ne tient pas : césure pour remplir la ligne
                coupe = next(((a, b) for a, b in _coupures(mot)
                              if largeur((courant + " " + a).strip()) <= w), None)
                if coupe and (not courant or len(coupe[0]) >= 4):
                    courant = (courant + " " + coupe[0]).strip()
                    if reste:
                        reste = coupe[1]
                    else:
                        reste, i = coupe[1], i + 1
                elif not courant:
                    return None  # même seul, le mot (ou son plus petit morceau) ne tient pas
                break
            lignes.append((y, x, w, courant))
            y += lh
        if len(lignes) == n_estime:
            return lignes, lh
        n_estime = len(lignes)
    return lignes, lh


def _zone(masque: np.ndarray, marge: int) -> np.ndarray:
    m = masque.astype(np.uint8)
    if marge > 0:
        m = cv2.erode(m, cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (2 * marge + 1, 2 * marge + 1)))
    return m > 0


def disposer(masque: np.ndarray, texte: str, nom_police: str, tmin: int, tmax: int, forcee: int | None = None):
    """Trouve la mise en page. Renvoie (lignes, lh, taille, condens, deborde)."""
    cfg = config()["rendu"]
    etapes = [
        (cfg["marge_px"], cfg["interligne"], 1.0),
        (cfg["marge_min_px"], cfg["interligne"], 1.0),
        (cfg["marge_min_px"], cfg["interligne_min"], 1.0),
        (cfg["marge_min_px"], cfg["interligne_min"], cfg["condensation"]),
    ]
    if forcee:
        for marge, inter, cond in etapes:
            r = mettre_en_lignes(_zone(masque, marge), texte, police(nom_police, forcee), forcee, inter, cond)
            if r:
                return r[0], r[1], forcee, cond, False
    else:
        for marge, inter, cond in etapes:
            zone = _zone(masque, marge)
            lo, hi, meilleur = tmin, tmax, None
            while lo <= hi:
                mid = (lo + hi) // 2
                r = mettre_en_lignes(zone, texte, police(nom_police, mid), mid, inter, cond)
                if r:
                    meilleur, lo = (r, mid), mid + 1
                else:
                    hi = mid - 1
            if meilleur:
                (lignes, lh), t = meilleur
                return lignes, lh, t, cond, False
    # dernier recours : débordement léger sur le contour, au minimum de taille
    t = forcee or tmin
    _, inter, cond = etapes[-1]
    pad = 10
    grand = np.pad(masque, pad)
    grand = cv2.dilate(grand.astype(np.uint8), np.ones((2 * pad + 1, 2 * pad + 1), np.uint8)) > 0
    r = mettre_en_lignes(grand, texte, police(nom_police, t), t, inter, cond)
    if r:
        lignes = [(y - pad, x - pad, w, s) for y, x, w, s in r[0]]
        return lignes, r[1], t, cond, True
    # rectangle englobant élargi : le texte est toujours rendu en entier
    h, w = masque.shape
    rect = np.ones((max(h, 1) * 4, max(w, 1) + 2 * pad), bool)
    r = mettre_en_lignes(rect, texte, police(nom_police, t), t, inter, cond)
    off = (rect.shape[0] - h) // 2
    lignes = [(y - off, x - pad, ww, s) for y, x, ww, s in r[0]] if r else [(0, 0, w, texte)]
    return lignes, (r[1] if r else t), t, cond, True


def _hex(c: str) -> tuple[int, int, int]:
    return tuple(int(c[i:i + 2], 16) for i in (1, 3, 5))


def rendre(bande: np.ndarray, bulles: list[Bulle], dossier: Path, progres=lambda *a: None) -> np.ndarray:
    cfg = config()["rendu"]
    for nom in {cfg["police"], cfg["police_emphase"]}:
        verifier_licence(nom)
    W = bande.shape[1]
    tmin = max(8, round(cfg["taille_min_ratio"] * W))
    image = Image.fromarray(bande)
    calque = Image.new("RGBA", image.size, (0, 0, 0, 0))
    for i, b in enumerate(bulles):
        if i % 5 == 0:
            progres(f"Rendu du texte : {i}/{len(bulles)}", i / max(1, len(bulles)))
        if not b.francais.strip() or b.type == "onomatopee":
            continue
        x0, y0, x1, y1 = b.boite
        masque = None
        if b.masque and (dossier / b.masque).exists():
            masque = np.asarray(Image.open(dossier / b.masque)) > 127
        if masque is None or masque.shape != (y1 - y0, x1 - x0):
            masque = np.ones((y1 - y0, x1 - x0), bool)
        texte = typo_fr(b.francais)
        if b.majuscules:
            texte = texte.upper()
        nom = cfg["police_emphase"] if b.emphase else cfg["police"]
        tmax = round(cfg["taille_max"] * (1.3 if b.emphase else 1.0) * W / 800)
        if b.lignes:
            # pas plus grand que le lettrage d'origine (hauteur des lignes lues par l'OCR)
            h_orig = float(np.median([l[3] - l[1] for l in b.lignes]))
            tmax = min(tmax, round(h_orig * (1.25 if b.emphase else 1.05)))
        lignes, lh, taille, cond, deborde = disposer(masque, texte, nom, tmin, max(tmin, tmax), b.taille_police)
        if deborde and "Texte trop long : léger débordement." not in b.notes:
            b.a_verifier = True
            b.notes.append("Texte trop long : léger débordement.")
        f = police(nom, taille)
        couleur = _hex(b.style.get("couleur", "#1a1a1a")) + (255,)
        contour = b.style.get("contour")
        epaisseur = max(2, taille // 8) if contour else 0
        for y, x, w, s in lignes:
            larg = f.getlength(s)
            # ligne dessinée à part puis condensée horizontalement si besoin
            marge = epaisseur + 2
            tmp = Image.new("RGBA", (int(larg) + 2 * marge + 2, lh + 2 * marge), (0, 0, 0, 0))
            d = ImageDraw.Draw(tmp)
            d.text((marge, marge + lh / 2), s, font=f, fill=couleur, anchor="lm",
                   stroke_width=epaisseur, stroke_fill=(_hex(contour) + (255,)) if contour else None)
            if cond < 1.0:
                tmp = tmp.resize((max(1, round(tmp.width * cond)), tmp.height), Image.LANCZOS)
            px = x0 + x + (w - larg * cond) / 2 - marge * cond
            _coller_borne(calque, tmp, int(round(px)), int(y0 + y - marge))
    image = Image.alpha_composite(image.convert("RGBA"), calque).convert("RGB")
    return np.asarray(image)


def _coller_borne(calque: Image.Image, tmp: Image.Image, x: int, y: int) -> None:
    """Collage d'un morceau qui dépasse du bord de la bande : on rogne."""
    gx0, gy0 = max(0, x), max(0, y)
    gx1, gy1 = min(calque.width, x + tmp.width), min(calque.height, y + tmp.height)
    if gx1 <= gx0 or gy1 <= gy0:
        return
    morceau = tmp.crop((gx0 - x, gy0 - y, gx1 - x, gy1 - y))
    calque.alpha_composite(morceau, (gx0, gy0))
