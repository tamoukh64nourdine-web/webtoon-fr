"""5. Traduction par un LLM multimodal : il voit les scènes, bulles numérotées dessus.

- Un appel par chapitre, ou par lots si le chapitre dépasse les limites du fournisseur.
- Glossaire de la série et résumés des chapitres précédents (selon leur numéro) injectés.
- Contrôle anti-décalage : le LLM recopie les 3 premiers mots anglais de chaque bulle.
- Délai maximal, 3 essais espacés, sauvegarde de chaque lot dès sa réception.
- Aucune limite de longueur : la traduction est complète, c'est le rendu qui s'adapte.
"""
from __future__ import annotations

import base64
import difflib
import hashlib
import io
import json
import re
import time

import httpx
import numpy as np
from PIL import Image, ImageDraw, ImageFont

from .config import chemin, config, reglages
from .ingest import segments
from .models import Bulle, Chapitre, lire_glossaire, resumes_precedents


class ErreurTraduction(Exception):
    pass


SYSTEME = """Tu es un traducteur professionnel de webtoons et de manhwa, de l'anglais vers le français.
Tu traduis en français naturel et oral, fidèle au sens, au ton et à l'intrigue — jamais mot à mot.
Tu traduis TOUT le texte de chaque bulle, sans rien raccourcir ni résumer : la mise en page s'adapte.

Tu reçois les images des scènes du chapitre. Chaque bulle à traduire porte un numéro dans un cercle rouge.
Sers-toi des images pour savoir qui parle (queue des bulles, visages, action) et à qui.
Le registre tu/vous se choisit selon la relation entre locuteur et destinataire ; le glossaire fait foi
quand il fixe une paire de personnages. Respecte strictement les noms et termes du glossaire.

Si le texte anglais lu par l'OCR contient une erreur évidente visible sur l'image, renvoie la version
corrigée dans "anglais_corrige" et traduis le texte corrigé ; sinon mets null.
Les bulles d'un même "groupe" sont les morceaux d'une même bulle liée : même locuteur, phrase qui continue ;
traduis chaque morceau séparément pour qu'il reste à sa place.
Les bulles marquées "fixe" sont déjà traduites : ne les renvoie pas, sers-t'en comme contexte.

Réponds UNIQUEMENT avec un objet JSON valide, sans texte autour, de la forme :
{"bulles": [{"id": 1, "debut": "<3 premiers mots anglais de la bulle, recopiés>", "francais": "...",
  "locuteur": "nom ou null", "destinataire": "nom ou null", "registre": "tu|vous|null",
  "anglais_corrige": null, "ordre_suspect": false}],
 "resume": "<résumé de cette partie du chapitre en 2 à 4 phrases, en français>",
 "glossaire_propose": {"noms": ["..."], "termes": {"anglais": "français"},
  "registres": [{"a": "...", "b": "...", "registre": "tu|vous"}]}}"""


# --- images des scènes ------------------------------------------------------

def _police_numeros(taille: int):
    f = chemin("fonts") / config()["rendu"]["police"]
    try:
        return ImageFont.truetype(str(f), taille)
    except OSError:
        return ImageFont.load_default()


def scenes(bande: np.ndarray, bulles: list[Bulle]) -> list[dict]:
    cfg = config()["traduction"]
    res = []
    for y0, y1, _, _ in segments(bande, cfg["hauteur_scene"]):
        ids = [b.id for b in bulles if y0 <= (b.boite[1] + b.boite[3]) / 2 < y1]
        res.append({"y0": y0, "y1": y1, "ids": ids})
    return res


def image_scene(bande: np.ndarray, sc: dict, bulles: dict[int, Bulle]) -> str:
    cfg = config()["traduction"]
    im = Image.fromarray(bande[sc["y0"]:sc["y1"]])
    echelle = min(1.0, cfg["cote_max_image"] / max(im.size))
    if echelle < 1:
        im = im.resize((round(im.width * echelle), round(im.height * echelle)), Image.LANCZOS)
    d = ImageDraw.Draw(im)
    r = max(14, round(22 * max(echelle, 0.6)))
    police = _police_numeros(int(r * 1.2))
    for i in sc["ids"]:
        b = bulles[i]
        cx = max(r, min(im.width - r, round(b.boite[0] * echelle) + r // 2))
        cy = max(r, round((b.boite[1] - sc["y0"]) * echelle) + r // 2)
        d.ellipse([cx - r, cy - r, cx + r, cy + r], fill=(220, 30, 40), outline="white", width=2)
        d.text((cx, cy), str(i), font=police, fill="white", anchor="mm")
    buf = io.BytesIO()
    im.save(buf, "JPEG", quality=cfg["qualite_jpg_scene"])
    return base64.b64encode(buf.getvalue()).decode()


# --- prompt -------------------------------------------------------------------

def _texte_glossaire(g: dict) -> str:
    l = []
    if g.get("noms"):
        l.append("Noms (orthographe imposée) : " + ", ".join(f"{k} → {v}" for k, v in g["noms"].items()))
    if g.get("termes"):
        l.append("Termes : " + "; ".join(f"{k} → {v}" for k, v in g["termes"].items()))
    l.append("Honorifiques (hyung, noona, -nim, sunbae…) : " +
             ("à garder tels quels." if g.get("honorifiques", "garder") == "garder" else "à adapter en français."))
    if g.get("registres"):
        l.append("Registres imposés : " + "; ".join(f"{r['a']} ↔ {r['b']} : {r['registre']}" for r in g["registres"]))
    return "\n".join(l)


def _message(chap: Chapitre, lot: list[Bulle], fixes: list[Bulle], contexte_lot: list[Bulle], g: dict) -> str:
    parties = [f"Série : {chap.serie} — chapitre {chap.numero}", "", "GLOSSAIRE", _texte_glossaire(g)]
    precedents = resumes_precedents(chap.serie, chap.numero)
    if precedents:
        parties += ["", "RÉSUMÉ DES CHAPITRES PRÉCÉDENTS"]
        parties += [f"Chapitre {n} : {t}" for n, t in precedents]
    if contexte_lot:
        parties += ["", "FIN DU LOT PRÉCÉDENT (contexte, ne pas retraduire)"]
        parties += [f"#{b.id} : {b.texte_source} → {b.francais}" for b in contexte_lot]
    parties += ["", "BULLES (dans l'ordre de lecture)"]
    tout = sorted(lot + fixes, key=lambda b: b.id)
    for b in tout:
        ligne = {"id": b.id, "type": b.type, "anglais": b.texte_source}
        if b.groupe is not None:
            ligne["groupe"] = b.groupe
        if b.emphase:
            ligne["emphase"] = True
        if b in fixes:
            ligne["fixe"] = b.francais
        parties.append(json.dumps(ligne, ensure_ascii=False))
    return "\n".join(parties)


# --- appels aux fournisseurs ------------------------------------------------------

def _appel_anthropic(r: dict, systeme: str, texte: str, images: list[str], delai: int) -> str:
    import anthropic
    client = anthropic.Anthropic(api_key=r["cle_api"], timeout=delai, max_retries=0)
    contenu = [{"type": "image", "source": {"type": "base64", "media_type": "image/jpeg", "data": im}} for im in images]
    contenu.append({"type": "text", "text": texte})
    rep = client.messages.create(model=r["modele"], max_tokens=32000, system=systeme,
                                 messages=[{"role": "user", "content": contenu}])
    return "".join(getattr(c, "text", "") for c in rep.content)


def _appel_openai(r: dict, systeme: str, texte: str, images: list[str], delai: int) -> str:
    base = (r.get("url_base") or "https://api.openai.com/v1").rstrip("/")
    contenu = [{"type": "image_url", "image_url": {"url": f"data:image/jpeg;base64,{im}"}} for im in images]
    contenu.append({"type": "text", "text": texte})
    corps = {"model": r["modele"], "messages": [{"role": "system", "content": systeme},
                                                {"role": "user", "content": contenu if images else texte}]}
    entetes = {"Authorization": f"Bearer {r['cle_api']}"} if r.get("cle_api") else {}
    rep = httpx.post(f"{base}/chat/completions", json=corps, headers=entetes, timeout=delai)
    rep.raise_for_status()
    return rep.json()["choices"][0]["message"]["content"]


def _extraire_json(texte: str) -> dict:
    texte = re.sub(r"^```(?:json)?|```$", "", texte.strip(), flags=re.M)
    i, j = texte.find("{"), texte.rfind("}")
    if i < 0 or j < 0:
        raise ValueError("pas de JSON dans la réponse")
    return json.loads(texte[i:j + 1])


def _norm(t: str) -> str:
    return " ".join(re.sub(r"[^\w\s']", " ", (t or "").lower()).split()[:3])


def _decalages(lot: list[Bulle], rep: dict[int, dict]) -> list[int]:
    """Ids dont le début recopié ne correspond pas au texte envoyé."""
    mauvais = []
    for b in lot:
        r = rep.get(b.id)
        if r is None:
            mauvais.append(b.id)
            continue
        attendu = _norm(b.texte_source)
        recu = _norm(r.get("debut", ""))
        if attendu and difflib.SequenceMatcher(None, attendu, recu).ratio() < 0.6:
            # le LLM a pu corriger l'OCR : on accepte si le début correspond au texte corrigé
            corr = _norm(r.get("anglais_corrige") or "")
            if not corr or difflib.SequenceMatcher(None, corr, recu).ratio() < 0.6:
                mauvais.append(b.id)
    return mauvais


# --- orchestration --------------------------------------------------------------

def traduire(chap: Chapitre, bande: np.ndarray, progres=lambda *a: None, sauver=lambda: None, cache=None) -> dict:
    r = reglages()
    if not r["cle_api"] and r["fournisseur"] == "anthropic":
        raise ErreurTraduction("Aucune clé API : ajoute-la dans l'onglet Réglages, puis relance la traduction.")
    cfg = config()["traduction"]
    g = lire_glossaire(chap.serie)
    par_id = {b.id: b for b in chap.bulles}
    actives = [b for b in chap.bulles if b.type != "onomatopee"]
    a_traduire = [b for b in actives if not b.verrouille]
    if not a_traduire:
        progres("Toutes les bulles sont verrouillées : rien à traduire.")
        return {}
    les_scenes = [s for s in scenes(bande, actives) if s["ids"]]

    # lots de scènes selon les limites du fournisseur
    lots, courant = [], []
    for s in les_scenes:
        n_img = len(courant) + 1
        n_bul = sum(len(c["ids"]) for c in courant) + len(s["ids"])
        if courant and (n_img > cfg["images_max_par_appel"] or n_bul > cfg["bulles_max_par_appel"]):
            lots.append(courant)
            courant = []
        courant.append(s)
    if courant:
        lots.append(courant)

    appel = _appel_anthropic if r["fournisseur"] == "anthropic" else _appel_openai
    resumes, propositions = [], {"noms": [], "termes": {}, "registres": []}
    precedent: list[Bulle] = []
    for n_lot, lot_scenes in enumerate(lots, start=1):
        ids = [i for s in lot_scenes for i in s["ids"]]
        lot = [par_id[i] for i in ids if not par_id[i].verrouille]
        fixes = [par_id[i] for i in ids if par_id[i].verrouille]
        if not lot:
            precedent = [par_id[i] for i in ids][-cfg["bulles_contexte_lot"]:]
            continue
        images = [image_scene(bande, s, par_id) for s in lot_scenes] if r.get("multimodal", True) else []
        texte = _message(chap, lot, fixes, precedent, g)
        cle = "llm-" + hashlib.sha256((r["fournisseur"] + r["modele"] + SYSTEME + texte + "".join(images)).encode()).hexdigest()
        donnees = cache.lire(cle) if cache else None
        pauses = list(cfg["pauses_essais_s"])
        essai = 0
        while donnees is None:
            essai += 1
            progres(f"Traduction : lot {n_lot}/{len(lots)} ({len(lot)} bulles), essai {essai}…", (n_lot - 1) / len(lots))
            try:
                brut = appel(r, SYSTEME, texte, images, cfg["delai_max_s"])
                candidat = _extraire_json(brut)
                rep = {int(x["id"]): x for x in candidat.get("bulles", []) if "id" in x}
                mauvais = _decalages(lot, rep)
                if mauvais and pauses:
                    raise ValueError(f"décalage ou bulles manquantes : {mauvais[:8]}")
                donnees = candidat
                donnees["_mauvais"] = mauvais
            except Exception as e:  # erreur réseau, délai, JSON invalide, décalage
                if not pauses:
                    raise ErreurTraduction(f"Échec de la traduction du lot {n_lot} après {essai} essais : {e}") from e
                attente = pauses.pop(0)
                progres(f"Lot {n_lot} : {str(e)[:160]} — nouvel essai dans {attente} s.")
                time.sleep(attente)
        if cache:
            cache.ecrire(cle, donnees)

        rep = {int(x["id"]): x for x in donnees.get("bulles", []) if "id" in x}
        for b in lot:
            x = rep.get(b.id)
            if not x:
                b.a_verifier = True
                b.notes.append("Non traduite par le LLM.")
                continue
            b.francais = str(x.get("francais") or "").strip()
            b.locuteur = x.get("locuteur") or None
            b.destinataire = x.get("destinataire") or None
            reg = x.get("registre")
            b.registre = reg if reg in ("tu", "vous") else None
            corr = x.get("anglais_corrige")
            if corr and _norm(corr) != _norm(b.anglais):
                b.anglais_corrige = str(corr)
                b.a_verifier = True
                b.notes.append("Texte anglais corrigé par le LLM : à confirmer.")
            b.ordre_suspect = bool(x.get("ordre_suspect"))
            if b.ordre_suspect:
                b.a_verifier = True
                b.notes.append("Ordre de lecture douteux.")
            if b.id in donnees.get("_mauvais", []):
                b.a_verifier = True
                b.notes.append("Contrôle anti-décalage non concluant.")
        if donnees.get("resume"):
            resumes.append(str(donnees["resume"]).strip())
        gp = donnees.get("glossaire_propose") or {}
        propositions["noms"] += [n for n in gp.get("noms", []) if isinstance(n, str)]
        if isinstance(gp.get("termes"), dict):
            propositions["termes"].update(gp["termes"])
        propositions["registres"] += [x for x in gp.get("registres", []) if isinstance(x, dict)]
        precedent = [par_id[i] for i in ids][-cfg["bulles_contexte_lot"]:]
        sauver()  # chaque lot reçu est enregistré aussitôt
    chap.resume = " ".join(resumes)
    return propositions
