"""3. OCR anglais (RapidOCR : modèles PaddleOCR au format ONNX, inclus dans le paquet).

Inclut la cascade de relecture pour le texte clair, coloré ou avec contour :
image inversée, niveaux de gris avec contraste renforcé (CLAHE), agrandissement x3.
"""
from __future__ import annotations

import hashlib
import logging

import cv2
import numpy as np

from .config import config

_moteur = None


def moteur_ocr():
    global _moteur
    if _moteur is None:
        logging.getLogger("RapidOCR").setLevel(logging.WARNING)
        from rapidocr import RapidOCR
        try:
            _moteur = RapidOCR(params={"Global.log_level": "warning", "Det.limit_side_len": 1600,
                                       "Det.limit_type": "max", "Det.box_thresh": 0.5})
        except Exception:
            _moteur = RapidOCR()
    return _moteur


def lire(image_rgb: np.ndarray, cache=None) -> list[tuple[list[int], str, float]]:
    """Lit une image RGB, renvoie [(boite, texte, score)]."""
    cle = None
    if cache is not None:
        cle = "ocr-" + hashlib.sha256(image_rgb.tobytes() + str(image_rgb.shape).encode()).hexdigest()
        trouve = cache.lire(cle)
        if trouve is not None:
            return [(b, t, s) for b, t, s in trouve]
    bgr = cv2.cvtColor(image_rgb, cv2.COLOR_RGB2BGR) if image_rgb.ndim == 3 else cv2.cvtColor(image_rgb, cv2.COLOR_GRAY2BGR)
    r = moteur_ocr()(bgr)
    sortie = []
    if r is not None and getattr(r, "boxes", None) is not None and r.txts:
        for poly, t, s in zip(r.boxes, r.txts, r.scores):
            p = np.asarray(poly)
            sortie.append(([int(p[:, 0].min()), int(p[:, 1].min()), int(p[:, 0].max()), int(p[:, 1].max())],
                           str(t), float(s)))
    if cache is not None:
        cache.ecrire(cle, sortie)
    return sortie


def _variantes(crop: np.ndarray):
    gris = cv2.cvtColor(crop, cv2.COLOR_RGB2GRAY)
    yield "inversee", 1.0, 255 - crop
    clahe = cv2.createCLAHE(clipLimit=3.0, tileGridSize=(8, 8)).apply(gris)
    yield "clahe", 1.0, cv2.cvtColor(clahe, cv2.COLOR_GRAY2RGB)
    yield "x3", 3.0, cv2.resize(crop, None, fx=3, fy=3, interpolation=cv2.INTER_CUBIC)


def relire(bande: np.ndarray, boite: list[int], score_actuel: float, cache=None):
    """Relit une zone. Renvoie (lignes [(boite globale, texte, score)], variante) ou None si pas mieux."""
    cfg = config()["ocr"]
    H, W = bande.shape[:2]
    m = 8
    x0, y0, x1, y1 = max(0, boite[0] - m), max(0, boite[1] - m), min(W, boite[2] + m), min(H, boite[3] + m)
    crop = np.ascontiguousarray(bande[y0:y1, x0:x1])
    candidats = []
    if crop.shape[0] < cfg["agrandir_sous_px"]:
        candidats.append(("x2", 2.0, cv2.resize(crop, None, fx=2, fy=2, interpolation=cv2.INTER_CUBIC)))
    if score_actuel < cfg["seuil_confiance"]:
        candidats.extend(_variantes(crop))
    meilleur = None
    for nom, echelle, img in candidats:
        res = lire(np.ascontiguousarray(img), cache)
        res = [r for r in res if any(c.isalnum() for c in r[1])]
        if not res:
            continue
        sc = float(np.mean([r[2] for r in res]))
        if meilleur is None or sc > meilleur[0]:
            lignes = [([int(b[0] / echelle) + x0, int(b[1] / echelle) + y0, int(b[2] / echelle) + x0,
                        int(b[3] / echelle) + y0], t, s) for b, t, s in res]
            meilleur = (sc, lignes, nom)
    if meilleur and meilleur[0] > score_actuel + 0.02:
        return meilleur[1], meilleur[2]
    return None
