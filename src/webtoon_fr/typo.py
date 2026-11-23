"""Typographie française : espaces insécables, points de suspension, guillemets."""
from __future__ import annotations

import re

NBSP = " "


def typo_fr(t: str) -> str:
    t = (t or "").strip()
    t = t.replace("...", "…")
    t = re.sub(r"\s*\.\s*\.\s*\.", "…", t)
    # guillemets anglais -> français
    t = re.sub(r'"([^"]+)"', lambda m: f"«{NBSP}{m.group(1).strip()}{NBSP}»", t)
    t = re.sub(r"«\s*", f"«{NBSP}", t)
    t = re.sub(r"\s*»", f"{NBSP}»", t)
    # espace insécable avant ? ! : ;  (pas de doublon, pas entre deux signes)
    t = re.sub(r"(?<=[^\s?!:; \d])\s*([?!:;]+)(?!\d)", lambda m: NBSP + m.group(1), t)
    t = re.sub(r"[ \t]+" + NBSP, NBSP, t)
    t = re.sub(r"[ \t]{2,}", " ", t)
    return t
