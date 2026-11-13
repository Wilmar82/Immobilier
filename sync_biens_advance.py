#!/usr/bin/env python3
"""
Extraction automatique complète des biens de Wilmar Filliatre
depuis Advance Immobilier (liste + ouverture de chaque fiche annonce
pour récupérer ville, prix, surface, DPE, photo et barème d'honoraires).
"""

import json
import re
from datetime import datetime
from urllib.parse import urljoin, urlparse

from bs4 import BeautifulSoup
from playwright.sync_api import sync_playwright

URLS_CONSEILLERS = [
    "https://www.advance-immobilier.com/contributor/person/wilmar-filliatre",
]

FICHIER_SORTIE = "biens_advance.json"

# Mots-clés d'URL à ignorer (pages institutionnelles, contact, mentions légales...)
EXCLUSIONS_URL = (
    "/contributor/",
    "/contact",
    "/agence",
    "/agences",
    "/estimation",
    "/recrutement",
    "/mentions-legales",
    "/politique-",
    "/cookies",
    "/actualites",
    "/blog",
    "/favoris",
    "/alerte",
    "/recherche",
    "#",
    "mailto:",
    "tel:",
    "javascript:",
)


def nettoyer_texte(txt):
    return re.sub(r"\s+", " ", str(txt or "")).strip()


def est_lien_annonce(href, base_url):
    if not href:
        return False
    lien_complet = urljoin(base_url, href)
    parsed = urlparse(lien_complet)
    if "advance-immobilier.com" not in parsed.netloc:
        return False
    chemin = parsed.path.lower().rstrip("/")
    if not chemin or chemin == "/":
        return False
    if any(ex in lien_complet.lower() for ex in EXCLUSIONS_URL):
        return False
    # Un lien d'annonce contient généralement un identifiant numérique ou un segment bien/vente/location/property
    a_un_id = bool(re.search(r"[-_/]\d{4,}", chemin))
    a_un_mot_cle = any(
        k in chemin
        for k in ("/bien", "/annonce", "/vente", "/location", "/property", "/maison", "/appartement", "/terrain")
    )
    return a_un_id or a_un_mot_cle


def analyser_fiche_annonce(page, url_annonce):
    try:
        page.goto(url_annonce, wait_until="networkidle", timeout=45000)
        page.wait_for_timeout(1500)
    except Exception:
        return None

    html = page.content()
    soup = BeautifulSoup(html, "html.parser")
    texte_page = nettoyer_texte(soup.get_text(" "))

    # Ignorer les biens déjà vendus ou loués
    if re.search(r"\b(bien\s+vendu|sous\s+compromis|déjà\s+loué|bien\s+loué)\b", texte_page, re.IGNORECASE):
        return None

    # 1. Titre principal
    h1 = soup.find("h1")
    titre = nettoyer_texte(h1.get_text(" ")) if h1 else ""
    if not titre:
        og_title = soup.find("meta", property="og:title")
        titre = nettoyer_texte(og_title["content"]) if og_title and og_title.get("content") else "Bien immobilier"

    # 2. Image principale (og:image en priorité, sinon plus grande image de la page)
    img_url = ""
    og_img = soup.find("meta", property="og:image")
    if og_img and og_img.get("content"):
        img_url = urljoin(url_annonce, og_img["content"])
    if not img_url:
        for img in soup.find_all("img"):
            src = img.get("data-src") or img.get("src") or ""
            if src and not src.startswith("data:") and not any(x in src.lower() for x in ("logo", "icon", "avatar", "svg")):
                img_url = urljoin(url_annonce, src)
                break

    # 3. Ville & Code postal (départements 82, 31 et limitrophes)
    match_cp = re.search(r"\b((?:31|82|09|11|32|65|81)\d{3})\s+([A-ZÀ-Ÿa-zà-ÿ][A-ZÀ-Ÿa-zà-ÿ\s'-]{1,35})", texte_page)
    cp = match_cp.group(1) if match_cp else ""
    ville = nettoyer_texte(match_cp.group(2)).split(" - ")[0].split(" Réf")[0] if match_cp else ""

    # 4. Prix HAI + Honoraires (pourcentage et charge acquéreur / vendeur)
    match_prix = re.search(r"(\d[\d\s\u00a0]{2,12}\s*€(?:\s*HAI|\s*CC|\s*/\s*mois)?)", texte_page, re.IGNORECASE)
    prix_base = nettoyer_texte(match_prix.group(1)) if match_prix else "Prix sur demande"

    match_hon = re.search(
        r"(honoraires[^.]{5,120}(?:acquéreur|vendeur)[^.]{0,80})",
        texte_page,
        re.IGNORECASE,
    )
    mention_hon = nettoyer_texte(match_hon.group(1)) if match_hon else ""
    prix_complet = f"{prix_base} ({mention_hon})" if mention_hon and mention_hon.lower() not in prix_base.lower() else prix_base

    # 5. Surface, Pièces, DPE
    match_surf = re.search(r"(\d+(?:[.,]\d+)?\s*m²)", texte_page, re.IGNORECASE)
    surface = nettoyer_texte(match_surf.group(1)) if match_surf else ""

    match_pieces = re.search(r"(\d+\s*pi[èe]ces?|\bT[1-7]\b)", texte_page, re.IGNORECASE)
    pieces = nettoyer_texte(match_pieces.group(1)) if match_pieces else ""

    match_dpe = re.search(r"\b(?:DPE|Classe\s+énergie)\s*:?\s*([A-G])\b", texte_page, re.IGNORECASE)
    dpe = match_dpe.group(1).upper() if match_dpe else ""

    # 6. Type de transaction (vente ou location)
    est_location = bool(
        re.search(r"(/location|\blocation\b|\b[àa]\s+louer\b|/mois|\bloyer\b)", f"{url_annonce} {titre} {prix_base}", re.IGNORECASE)
    )

    return {
        "titre": titre,
        "ville": ville,
        "cp": cp,
        "prix": prix_complet,
        "transaction": "location" if est_location else "vente",
        "surface": surface,
        "pieces": pieces,
        "dpe": dpe,
        "image": img_url,
        "url": url_annonce,
    }


def main():
    tous_biens = []
    urls_annonces = []

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        context = browser.new_context(
            user_agent=(
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36"
            ),
            locale="fr-FR",
            viewport={"width": 1366, "height": 900},
        )
        page = context.new_page()

        for url_conseiller in URLS_CONSEILLERS:
            page.goto(url_conseiller, wait_until="networkidle", timeout=60000)
            page.wait_for_timeout(3000)

            # Défilement progressif + clic éventuel sur "Voir plus" / pagination
            for _ in range(5):
                page.evaluate("window.scrollTo(0, document.body.scrollHeight)")
                page.wait_for_timeout(1200)
                bouton_plus = page.locator("button:has-text('Voir plus'), a:has-text('Voir plus'), button:has-text('Afficher plus')")
                if bouton_plus.count() > 0:
                    try:
                        bouton_plus.first.click()
                        page.wait_for_timeout(1500)
                    except Exception:
                        pass

            soup_liste = BeautifulSoup(page.content(), "html.parser")
            for a in soup_liste.find_all("a", href=True):
                if est_lien_annonce(a["href"], url_conseiller):
                    lien_propre = urljoin(url_conseiller, a["href"]).split("#")[0]
                    if lien_propre not in urls_annonces:
                        urls_annonces.append(lien_propre)

        print(f"{len(urls_annonces)} lien(s) d'annonce détecté(s) sur la page Advance.")

        for lien in urls_annonces:
            bien = analyser_fiche_annonce(page, lien)
            if bien:
                tous_biens.append(bien)

        browser.close()

    resultat = {
        "mis_a_jour": datetime.now().strftime("%d/%m/%Y à %H:%M"),
        "total": len(tous_biens),
        "biens": tous_biens,
    }

    with open(FICHIER_SORTIE, "w", encoding="utf-8") as f:
        json.dump(resultat, f, ensure_ascii=False, indent=2)

    print(f"Terminé : {len(tous_biens)} bien(s) enregistré(s) dans {FICHIER_SORTIE}.")


if __name__ == "__main__":
    main()
