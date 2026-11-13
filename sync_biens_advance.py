#!/usr/bin/env python3
"""
Synchronisation automatique des vrais biens à la vente et à la location
depuis la page officielle Advance Immobilier de Wilmar Filliatre (via Playwright).
"""

import json
import re
from datetime import datetime
from urllib.parse import urljoin

from bs4 import BeautifulSoup
from playwright.sync_api import sync_playwright

URLS_CONSEILLERS = [
    "https://www.advance-immobilier.com/contributor/person/wilmar-filliatre",
]

FICHIER_SORTIE = "biens_advance.json"


def nettoyer_texte(txt):
    return re.sub(r"\s+", " ", str(txt or "")).strip()


def extraire_biens_playwright(url_source):
    biens = []
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        context = browser.new_context(
            user_agent=(
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/125.0.0.0 Safari/537.36"
            ),
            locale="fr-FR",
            viewport={"width": 1366, "height": 900},
        )
        page = context.new_page()
        page.goto(url_source, wait_until="networkidle", timeout=60000)
        page.wait_for_timeout(3000)

        # Défilement pour déclencher le chargement des images et cartes
        page.evaluate("window.scrollTo(0, document.body.scrollHeight)")
        page.wait_for_timeout(2000)

        html = page.content()
        browser.close()

    soup = BeautifulSoup(html, "html.parser")

    # 1. Recherche des liens d'annonces sur la page
    liens_annonces = []
    for a in soup.find_all("a", href=True):
        href = a["href"]
        if any(
            seg in href.lower()
            for seg in ("/annonce", "/bien", "/vente/", "/location/", "/property/", "/detail")
        ):
            lien_complet = urljoin(url_source, href)
            if lien_complet not in liens_annonces and lien_complet != url_source:
                liens_annonces.append(lien_complet)

    for lien in liens_annonces:
        a_tag = soup.find("a", href=lambda h: h and urljoin(url_source, h) == lien)
        carte = a_tag.find_parent(["article", "li", "div"]) if a_tag else None
        # Remonter d'un niveau si la carte est trop petite
        if carte and len(nettoyer_texte(carte.get_text(" "))) < 25 and carte.parent:
            carte = carte.parent

        texte_carte = nettoyer_texte(carte.get_text(" ")) if carte else nettoyer_texte(a_tag.get_text(" "))
        if len(texte_carte) < 12:
            continue

        if re.search(r"\b(vendu|sous\s+compromis|lou[ée])\b", texte_carte, re.IGNORECASE):
            continue

        match_prix = re.search(r"(\d[\d\s\u00a0]{2,12}\s*€(?:\s*/\s*mois|\s*cc)?)", texte_carte, re.IGNORECASE)
        prix = nettoyer_texte(match_prix.group(1)) if match_prix else ""

        match_cp_ville = re.search(r"\b((?:31|82|09|11|32|65|81)\d{3})\s+([A-ZÀ-Ÿa-zà-ÿ\s'-]{2,35})", texte_carte)
        cp = match_cp_ville.group(1) if match_cp_ville else ""
        ville = nettoyer_texte(match_cp_ville.group(2)) if match_cp_ville else ""

        match_surf = re.search(r"(\d+(?:[.,]\d+)?\s*m²)", texte_carte, re.IGNORECASE)
        surface = nettoyer_texte(match_surf.group(1)) if match_surf else ""

        match_pieces = re.search(r"(\d+\s*pi[èe]ces?|T\d)", texte_carte, re.IGNORECASE)
        pieces = nettoyer_texte(match_pieces.group(1)) if match_pieces else ""

        match_dpe = re.search(r"\bDPE\s*:?\s*([A-G])\b", texte_carte, re.IGNORECASE)
        dpe = match_dpe.group(1).upper() if match_dpe else ""

        img_tag = carte.find("img") if carte else None
        img_url = ""
        if img_tag:
            src = img_tag.get("data-src") or img_tag.get("data-lazy-src") or img_tag.get("src") or ""
            if src and not src.startswith("data:"):
                img_url = urljoin(url_source, src)

        titre_tag = carte.find(["h2", "h3", "h4"]) if carte else None
        titre = nettoyer_texte(titre_tag.get_text(" ")) if titre_tag else texte_carte[:80]

        est_location = bool(
            re.search(r"(/location|\blocation\b|\b[àa]\s+louer\b|/mois|\bcc\b)", f"{lien} {texte_carte}", re.IGNORECASE)
        )

        biens.append({
            "titre": titre,
            "ville": ville,
            "cp": cp,
            "prix": prix,
            "transaction": "location" if est_location else "vente",
            "surface": surface,
            "pieces": pieces,
            "dpe": dpe,
            "image": img_url,
            "url": lien,
        })

    return biens


def main():
    tous_biens = []
    urls_vues = set()

    for url in URLS_CONSEILLERS:
        for bien in extraire_biens_playwright(url):
            if bien["url"] not in urls_vues:
                urls_vues.add(bien["url"])
                tous_biens.append(bien)

    resultat = {
        "mis_a_jour": datetime.now().strftime("%d/%m/%Y à %H:%M"),
        "total": len(tous_biens),
        "biens": tous_biens,
    }

    with open(FICHIER_SORTIE, "w", encoding="utf-8") as f:
        json.dump(resultat, f, ensure_ascii=False, indent=2)

    print(f"{len(tous_biens)} bien(s) synchronisé(s) dans {FICHIER_SORTIE}.")


if __name__ == "__main__":
    main()
