# Historique des versions

## v1.0.1 — 23 novembre 2026

- Correction : les connexions HTTPS utilisent les certificats du système (Windows, macOS). Corrige l'erreur `CERTIFICATE_VERIFY_FAILED` quand un antivirus ou un proxy d'entreprise inspecte les connexions. La vérification reste active.

## v1.0.0 — 23 novembre 2026

Première version.

- Récupération d'un chapitre par lien (images chargées au défilement, repli sur un navigateur sans interface) ou par import d'images.
- Pipeline complet : recollage, détection du texte et des bulles par tuiles, OCR anglais avec relecture des zones peu sûres, ordre de lecture, séparation des bulles liées.
- Traduction par un LLM qui voit les scènes (bulles numérotées), avec glossaire de la série, résumés des chapitres précédents, contrôle anti-décalage, essais espacés et cache.
- Effacement du texte anglais (remplissage ou LaMa), écriture du français dans la forme réelle des bulles, taille minimale lisible, typographie française, onomatopées laissées intactes.
- Corrections manuelles verrouillées et historique des versions de chaque chapitre.
- Interface web : séries, chapitres, suivi des étapes, relecture bulle par bulle, glossaire, contexte, réglages.
