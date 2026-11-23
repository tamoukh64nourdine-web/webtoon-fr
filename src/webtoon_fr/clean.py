"""6. Nettoyage : effacement du texte anglais.

- Intérieur uni (écart-type faible) : remplissage à la couleur médiane, instantané.
- Sinon : inpainting LaMa (ONNX, téléchargé au premier usage) sur la zone découpée uniquement.
  Si LaMa n'est pas disponible : inpainting OpenCV (Telea), moins propre mais sans téléchargement.
"""
from __future__ import annotations

import urllib.request
from pathlib import Path

import cv2
import numpy as np
from PIL import Image

from .config import chemin, config
from .models import Bulle

_lama = None


def _charger_lama(progres=lambda *a: None):
    global _lama
    if _lama is not None:
        return _lama or None
    try:
        import onnxruntime as ort
        dossier = chemin("modeles")
        dossier.mkdir(parents=True, exist_ok=True)
        f = dossier / "lama_fp32.onnx"
        if not f.exists():
            progres("Téléchargement du modèle LaMa (une seule fois, environ 200 Mo)…")
            tmp = f.with_suffix(".part")
            urllib.request.urlretrieve(config()["nettoyage"]["lama_url"], tmp)
            tmp.replace(f)
        _lama = ort.InferenceSession(str(f), providers=["CPUExecutionProvider"])
    except Exception as e:
        progres(f"LaMa indisponible ({str(e)[:120]}) : inpainting OpenCV utilisé.")
        _lama = False
    return _lama or None


def _inpaint(crop: np.ndarray, masque: np.ndarray, progres) -> np.ndarray:
    sess = _charger_lama(progres)
    if sess is None:
        return cv2.cvtColor(cv2.inpaint(cv2.cvtColor(crop, cv2.COLOR_RGB2BGR), masque.astype(np.uint8) * 255, 5,
                                        cv2.INPAINT_TELEA), cv2.COLOR_BGR2RGB)
    h, w = crop.shape[:2]
    img = cv2.resize(crop, (512, 512), interpolation=cv2.INTER_AREA).astype(np.float32) / 255.0
    m = (cv2.resize(masque.astype(np.uint8), (512, 512), interpolation=cv2.INTER_NEAREST) > 0).astype(np.float32)
    entrees = sess.get_inputs()
    flux = {entrees[0].name: img.transpose(2, 0, 1)[None], entrees[1].name: m[None, None]}
    sortie = sess.run(None, flux)[0][0].transpose(1, 2, 0)
    if sortie.max() <= 1.5:
        sortie = sortie * 255.0
    sortie = cv2.resize(np.clip(sortie, 0, 255).astype(np.uint8), (w, h), interpolation=cv2.INTER_CUBIC)
    res = crop.copy()
    res[masque] = sortie[masque]
    return res


def masque_texte(bande: np.ndarray, b: Bulle, masque_bulle: np.ndarray | None) -> np.ndarray:
    """Masque des pixels de texte dans la boîte de la bulle (repère de la boîte)."""
    x0, y0, x1, y1 = b.boite
    zone = bande[y0:y1, x0:x1].astype(np.int16)
    fond = np.array([int(b.style["fond"][i:i + 2], 16) for i in (1, 3, 5)], np.int16)
    diff = np.abs(zone - fond).max(axis=2) > 40
    dans_lignes = np.zeros(diff.shape, bool)
    for l in b.lignes:
        lx0, ly0, lx1, ly1 = l[0] - x0 - 3, l[1] - y0 - 3, l[2] - x0 + 3, l[3] - y0 + 3
        dans_lignes[max(0, ly0):max(0, ly1), max(0, lx0):max(0, lx1)] = True
    m = diff & dans_lignes
    k = config()["nettoyage"]["dilatation_px"] * 2 + 1
    m = cv2.dilate(m.astype(np.uint8), cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (k, k))) > 0
    if masque_bulle is not None and b.type != "texte_libre":
        m &= masque_bulle
    return m


def nettoyer(bande: np.ndarray, bulles: list[Bulle], dossier: Path, progres=lambda *a: None) -> np.ndarray:
    cfg = config()["nettoyage"]
    out = bande.copy()
    for i, b in enumerate(bulles):
        if i % 5 == 0:
            progres(f"Nettoyage : {i}/{len(bulles)}", i / max(1, len(bulles)))
        if not b.lignes or b.type == "onomatopee":
            continue
        x0, y0, x1, y1 = b.boite
        mb = None
        if b.masque and (dossier / b.masque).exists():
            mb = np.asarray(Image.open(dossier / b.masque)) > 127
            if mb.shape != (y1 - y0, x1 - x0):
                mb = None
        mt = masque_texte(out, b, mb)
        if not mt.any():
            continue
        zone = out[y0:y1, x0:x1]
        interieur = (mb if mb is not None else np.ones(mt.shape, bool)).copy()
        interieur = cv2.erode(interieur.astype(np.uint8), np.ones((5, 5), np.uint8)) > 0
        autour = interieur & ~(cv2.dilate(mt.astype(np.uint8), np.ones((7, 7), np.uint8)) > 0)
        if b.type == "texte_libre":
            # anneau autour du texte pour juger si le fond est uni
            anneau = cv2.dilate(mt.astype(np.uint8), np.ones((15, 15), np.uint8)) > 0
            autour = anneau & ~mt
        pix = zone[autour]
        uni = len(pix) > 30 and float(pix.astype(np.float32).std(axis=0).max()) < cfg["ecart_type_uni"]
        if uni:
            zone[mt] = np.median(pix, axis=0).astype(np.uint8)
        else:
            pad = 24
            H, W = out.shape[:2]
            cx0, cy0, cx1, cy1 = max(0, x0 - pad), max(0, y0 - pad), min(W, x1 + pad), min(H, y1 + pad)
            grand = np.zeros((cy1 - cy0, cx1 - cx0), bool)
            grand[y0 - cy0:y1 - cy0, x0 - cx0:x1 - cx0] = mt
            out[cy0:cy1, cx0:cx1] = _inpaint(out[cy0:cy1, cx0:cx1].copy(), grand, progres)
    return out
