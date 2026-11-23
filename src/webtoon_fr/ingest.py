"""1. Recollage : les images du chapitre deviennent une seule bande (tableau NumPy RGB).

Contient aussi le découpage sur lignes vides, utilisé par les tuiles, les scènes et l'export.
"""
from __future__ import annotations

import re
from pathlib import Path

import numpy as np
from PIL import Image

EXTENSIONS = {".jpg", ".jpeg", ".png", ".webp"}


def _cle_naturelle(p: Path):
    return [int(t) if t.isdigit() else t.lower() for t in re.split(r"(\d+)", p.name)]


def lister_images(dossier: Path) -> list[Path]:
    return sorted((p for p in dossier.iterdir() if p.suffix.lower() in EXTENSIONS), key=_cle_naturelle)


def ouvrir(p: Path) -> Image.Image:
    # limite de sécurité de Pillow relevée seulement pour les fichiers du chapitre
    ancienne = Image.MAX_IMAGE_PIXELS
    Image.MAX_IMAGE_PIXELS = 1_000_000_000
    try:
        im = Image.open(p)
        im.load()
    finally:
        Image.MAX_IMAGE_PIXELS = ancienne
    if im.mode in ("RGBA", "LA", "P"):
        im = im.convert("RGBA")
        fond = Image.new("RGB", im.size, "white")
        fond.paste(im, mask=im.split()[-1])
        return fond
    return im.convert("RGB")


def recoller(fichiers: list[Path]) -> tuple[np.ndarray, dict]:
    if not fichiers:
        raise ValueError("Aucune image à traiter.")
    images = [ouvrir(f) for f in fichiers]
    largeur = max(im.width for im in images)
    blocs, decoupes = [], []
    for im in images:
        if im.width != largeur:
            # agrandissement vers la plus grande largeur, jamais de réduction
            im = im.resize((largeur, round(im.height * largeur / im.width)), Image.LANCZOS)
        blocs.append(np.asarray(im, dtype=np.uint8))
        decoupes.append(im.height)
    bande = np.ascontiguousarray(np.concatenate(blocs, axis=0))
    formats = {f.suffix.lower() for f in fichiers}
    info = {
        "largeur": int(largeur),
        "hauteur": int(bande.shape[0]),
        "decoupes": decoupes,
        "format": "png" if formats == {".png"} else "jpg",
        "fichiers": [f.name for f in fichiers],
    }
    return bande, info


def score_vide(bande: np.ndarray) -> np.ndarray:
    """Pour chaque ligne de pixels : écart max-min (faible = ligne vide, unie)."""
    gris = bande.mean(axis=2) if bande.ndim == 3 else bande
    return (gris.max(axis=1) - gris.min(axis=1)).astype(np.float32)


def coupes(bande: np.ndarray, cible: int, fenetre: int = 400, vide: np.ndarray | None = None) -> list[tuple[int, bool]]:
    """Positions de coupe proches de `cible` px, choisies sur des lignes vides si possible.

    Renvoie [(y, propre)] où `propre` indique une coupe sur ligne vide.
    """
    h = bande.shape[0]
    if vide is None:
        vide = score_vide(bande)
    res, y = [], 0
    while h - y > cible + fenetre // 2:
        lo, hi = y + cible - fenetre, y + cible + fenetre // 4
        lo, hi = max(lo, y + 200), min(hi, h - 1)
        segment = vide[lo:hi]
        i = int(np.argmin(segment))
        propre = bool(segment[i] < 6)
        if propre:
            # centre de la zone vide la plus proche de la cible
            vides = np.where(segment < 6)[0]
            i = int(vides[np.argmin(np.abs(vides + lo - (y + cible)))])
            yc = lo + i
        else:
            yc = y + cible
        res.append((yc, propre))
        y = yc
    return res


def segments(bande: np.ndarray, cible: int) -> list[tuple[int, int, bool, bool]]:
    """Segments [(y0, y1, haut_propre, bas_propre)] couvrant toute la bande."""
    cs = coupes(bande, cible)
    bornes = [(0, True)] + cs + [(bande.shape[0], True)]
    return [(bornes[i][0], bornes[i + 1][0], bornes[i][1], bornes[i + 1][1]) for i in range(len(bornes) - 1)]
