"""4. Regroupement et ordre de lecture : les régions détectées deviennent des bulles prêtes à traduire."""
from __future__ import annotations

import re
from pathlib import Path

import cv2
import numpy as np
from PIL import Image

from .config import config
from .detect import Region, iou
from .models import Bulle
from .ocr import relire


def _assembler(lignes) -> str:
    """Lignes d'une bulle -> texte, dans l'ordre de lecture, césures recollées."""
    lignes = sorted(lignes, key=lambda l: (l.boite[1], l.boite[0]))
    rangs, courant = [], []
    for l in lignes:
        if courant and l.boite[1] > np.mean([c.boite[1] for c in courant]) + 0.5 * l.h:
            rangs.append(courant)
            courant = []
        courant.append(l)
    if courant:
        rangs.append(courant)
    morceaux = [" ".join(c.texte for c in sorted(r, key=lambda c: c.boite[0])) for r in rangs]
    texte = ""
    for m in morceaux:
        m = m.strip()
        if not texte:
            texte = m
        elif re.search(r"[A-Za-z]-$", texte) and m[:1].isalpha():
            texte = texte[:-1] + m
        else:
            texte += " " + m
    return re.sub(r"\s+", " ", texte).strip()


def _est_majuscules(t: str) -> bool:
    lettres = [c for c in t if c.isalpha()]
    return len(lettres) >= 2 and sum(c.isupper() for c in lettres) / len(lettres) > 0.85


def normaliser_casse(t: str, noms: list[str]) -> str:
    s = t.lower()
    s = re.sub(r"(^|[.!?…]\s+|[\"“(]\s*)([a-z])", lambda m: m.group(1) + m.group(2).upper(), s)
    s = re.sub(r"\bi\b", "I", s)
    s = re.sub(r"\bi'(m|ll|ve|d)\b", lambda m: "I'" + m.group(1), s)
    for n in noms:
        s = re.sub(rf"\b{re.escape(n.lower())}\b", n, s)
    return s


def _style(bande: np.ndarray, r: Region) -> dict:
    fond = np.array(r.fond, np.int16)
    pix = []
    for l in r.lignes:
        x0, y0, x1, y1 = l.boite
        z = bande[y0:y1, x0:x1].reshape(-1, 3).astype(np.int16)
        d = np.abs(z - fond).max(axis=1)
        pix.append(z[d > 60])
    pix = np.concatenate(pix) if pix else np.zeros((0, 3))
    if len(pix) < 10:
        couleur = np.array([20, 20, 20]) if fond.mean() > 128 else np.array([245, 245, 245])
    else:
        couleur = np.median(pix, axis=0)
    lum_t, lum_f = couleur.mean(), fond.mean()
    contour = None
    if abs(lum_t - lum_f) < 80:
        contour = "#000000" if lum_t > 128 else "#ffffff"
    hexa = lambda c: "#%02x%02x%02x" % tuple(int(max(0, min(255, v))) for v in c)
    return {"couleur": hexa(couleur), "contour": contour, "fond": hexa(fond)}


def construire_bulles(bande: np.ndarray, regions: list[Region], dossier_masques: Path, noms: list[str],
                      progres=lambda *a: None, cache=None) -> list[Bulle]:
    cfg = config()
    dossier_masques.mkdir(parents=True, exist_ok=True)
    for f in dossier_masques.glob("*.png"):
        f.unlink()

    # cascade OCR sur les régions peu sûres ou trop petites
    for i, r in enumerate(regions):
        sc = float(np.mean([l.score for l in r.lignes]))
        hmin = min(l.h for l in r.lignes)
        if sc < cfg["ocr"]["seuil_confiance"] or hmin < 18:
            boite = [min(l.boite[0] for l in r.lignes), min(l.boite[1] for l in r.lignes),
                     max(l.boite[2] for l in r.lignes), max(l.boite[3] for l in r.lignes)]
            res = relire(bande, boite, sc, cache)
            if res:
                from .detect import Ligne
                r.lignes = [Ligne(b, t, s) for b, t, s in res[0]]
        if i % 10 == 0:
            progres(f"Relecture OCR : {i}/{len(regions)}", i / max(1, len(regions)))

    # ordre de lecture : haut de la bulle, rangées, gauche -> droite
    regions = [r for r in regions if r.lignes]
    regions.sort(key=lambda r: r.boite[1])
    ordre, i = [], 0
    while i < len(regions):
        rang = [regions[i]]
        j = i + 1
        while j < len(regions):
            a, b = rang[0].boite, regions[j].boite
            hmin = min(a[3] - a[1], b[3] - b[1])
            if abs(b[1] - a[1]) < 0.3 * hmin:
                rang.append(regions[j])
                j += 1
            else:
                break
        ordre.extend(sorted(rang, key=lambda r: r.boite[0]))
        i = j

    hauteurs = [np.median([l.h for l in r.lignes]) for r in ordre]
    mediane = float(np.median(hauteurs)) if hauteurs else 0
    bulles = []
    for n, (r, hl) in enumerate(zip(ordre, hauteurs), start=1):
        brut = _assembler(r.lignes)
        maj = _est_majuscules(brut)
        texte = normaliser_casse(brut, noms) if maj else brut
        nom_masque = f"{n:03d}.png"
        Image.fromarray((r.masque * 255).astype(np.uint8)).save(dossier_masques / nom_masque)
        sc = float(np.mean([l.score for l in r.lignes]))
        typ = r.type
        # onomatopée dessinée : texte libre court, grand ou détouré -> laissé intact (v1)
        mots = re.findall(r"[A-Za-z]+", brut)
        if typ == "texte_libre" and len(r.lignes) == 1 and len(mots) <= 2 and (
                (mediane and hl > 1.4 * mediane) or _style(bande, r)["contour"]):
            typ = "onomatopee"
        b = Bulle(
            id=n, type=typ, boite=[int(v) for v in r.boite], masque=f"masques/{nom_masque}",
            lignes=[[int(v) for v in l.boite] for l in r.lignes], anglais=texte, majuscules=maj,
            emphase=bool(mediane and hl > cfg["rendu"]["seuil_emphase"] * mediane),
            confiance_ocr=round(sc, 3), groupe=r.groupe, style=_style(bande, r),
            a_verifier=sc < cfg["ocr"]["seuil_confiance"],
        )
        if b.a_verifier:
            b.notes.append("Confiance OCR faible.")
        bulles.append(b)
    return bulles


def reporter_verrouillees(nouvelles: list[Bulle], anciennes: list[Bulle]) -> list[Bulle]:
    """Les bulles verrouillées de l'ancienne analyse remplacent leurs équivalentes."""
    verrou = [a for a in anciennes if a.verrouille]
    if not verrou:
        return nouvelles
    res = []
    utilisees = set()
    for nb in nouvelles:
        v = next((a for a in verrou if id(a) not in utilisees and iou(a.boite, nb.boite) > 0.6), None)
        if v:
            utilisees.add(id(v))
            v.id = nb.id
            v.masque, v.lignes = nb.masque, nb.lignes
            res.append(v)
        else:
            res.append(nb)
    return res
