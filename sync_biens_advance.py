#!/usr/bin/env python3
"""
Extraction propre des biens de Wilmar Filliatre depuis Advance Immobilier :
- Force le chargement des vraies photos de chaque annonce
- Sépare clairement le prix HAI et le détail des honoraires
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
    a_un_id = bool(re.search(r"[-_/]\d{4,}", chemin))
    a_un_mot_cle = any(
        k in chemin
        for k in ("/bien", "/annonce", "/vente", "/location", "/property", "/maison", "/appartement", "/terrain")
    )
    return a_un_id or a_un_mot_cle


def trouver_meilleure_photo(soup, url_annonce):
    # 1. Chercher la plus grande photo réelle dans les balises <img> ou <source>
    mots_interdits = ("logo", "icon", "avatar", "svg", "badge", "top100", "excellence", "placeholder", "spinner")
    candidats = []

    for img in soup.find_all("img"):
        for attr in ("data-src", "data-lazy-src", "data-original", "src"):
            val = img.get(attr)
            if not val or val.startswith("data:"):
                continue
            url_img = urljoin(url_annonce, val.strip())
            if any(m in url_img.lower() for m in mots_interdits):
                continue
            # Privilégier les images hébergées sur les serveurs médias / annonces
            score = 0
            if any(k in url_img.lower() for k in ("upload", "media", "photo", "bien", "property", "annonce", "storage", "wp-content", "cloudfront", "s3")):
                score += 10
            if url_img.lower().endswith((".jpg", ".jpeg", ".webp", ".png")):
                score += 5
            candidats.append((score, url_img))

    if candidats:
        candidats.sort(key=lambda x: x[0], reverse=True)
        return candidats[0][1]

    # 2. Repli sur og:image
    og_img = soup.find("meta", property="og:image")
    if og_img and og_img.get("content"):
        url_og = urljoin(url_annonce, og_img["content"].strip())
        if not any(m in url_og.lower() for m in mots_interdits):
            return url_og

    return ""


def analyser_fiche_annonce(page, url_annonce):
    try:
        page.goto(url_annonce, wait_until="networkidle", timeout=45000)
        page.wait_for_timeout(1500)
        page.evaluate("window.scrollTo(0, 600)")
        page.wait_for_timeout(1000)
    except Exception:
        return None

    html = page.content()
    soup = BeautifulSoup(html, "html.parser")
    texte_page = nettoyer_texte(soup.get_text(" "))

    if re.search(r"\b(bien\s+vendu|sous\s+compromis|déjà\s+loué|bien\s+loué)\b", texte_page, re.IGNORECASE):
        return None

    # 1. Titre
    h1 = soup.find("h1")
    titre = nettoyer_texte(h1.get_text(" ")) if h1 else ""
    if not titre:
        og_title = soup.find("meta", property="og:title")
        titre = nettoyer_texte(og_title["content"]).split("|")[0].strip() if og_title and og_title.get("content") else "Bien immobilier"

    # 2. Photo principale
    img_url = trouver_meilleure_photo(soup, url_annonce)

    # 3. Ville & Code postal
    match_cp = re.search(r"\b((?:31|82|09|11|32|65|81)\d{3})\s+([A-ZÀ-Ÿa-zà-ÿ][A-ZÀ-Ÿa-zà-ÿ\s'-]{1,35})", texte_page)
    cp = match_cp.group(1) if match_cp else ""
    ville = nettoyer_texte(match_cp.group(2)).split(" - ")[0].split(" Réf")[0].split(" Surface")[0] if match_cp else ""

    # 4. Prix net et lisible + mention honoraires séparée
    match_prix = re.search(r"\b(\d{1,3}(?:[\s\u00a0]\d{3})+|\d{4,7})\s*€", texte_page)
    if match_prix:
        chiffres = re.sub(r"[^\d]", "", match_prix.group(1))
        prix_formate = f"{int(chiffres):,}".replace(",", " ") + " €"
    else:
        prix_formate = "Prix sur demande"

    est_location = bool(
        re.search(r"(/location|\blocation\b|\b[àa]\s+louer\b|/mois|\bloyer\b)", f"{url_annonce} {titre}", re.IGNORECASE)
    )

    if est_location and prix_formate != "Prix sur demande":
        prix_formate += " / mois CC"
    elif not est_location and prix_formate != "Prix sur demande":
        prix_formate += " HAI"

    # Extraction propre du taux ou de la charge des honoraires
    honoraires_detail = ""
    match_taux = re.search(r"honoraires[^.]{0,60}?(\d+(?:[.,]\d+)?\s*%)", texte_page, re.IGNORECASE)
    match_charge = re.search(r"honoraires\s+(?:à\s+la\s+)?charge\s+(acquéreur|vendeur)", texte_page, re.IGNORECASE)

    if match_charge and match_taux:
        honoraires_detail = f"Honoraires {match_taux.group(1)} TTC charge {match_charge.group(1).lower()}"
    elif match_charge:
        honoraires_detail = f"Honoraires à la charge du {match_charge.group(1).lower()}"
    elif match_taux:
        honoraires_detail = f"Honoraires {match_taux.group(1)} TTC charge acquéreur"

    # 5. Surface, Pièces, DPE
    match_surf = re.search(r"(\d+(?:[.,]\d+)?)\s*m²", texte_page, re.IGNORECASE)
    surface = f"{match_surf.group(1)} m²" if match_surf else ""

    match_pieces = re.search(r"(\d+)\s*pi[èe]ces?\b|\b(T[1-7])\b", texte_page, re.IGNORECASE)
    if match_pieces:
        pieces = f"{match_pieces.group(1)} pièces" if match_pieces.group(1) else match_pieces.group(2).upper()
    else:
        pieces = ""

    match_dpe = re.search(r"\b(?:DPE|Classe\s+énergie)\s*:?\s*([A-G])\b", texte_page, re.IGNORECASE)
    dpe = match_dpe.group(1).upper() if match_dpe else ""

    return {
        "titre": titre,
        "ville": ville,
        "cp": cp,
        "prix": prix_formate,
        "honoraires": honoraires_detail,
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

    print(f"Terminé : {len(tous_biens)} bien(s) enregistré(s).")


if __name__ == "__main__":
    main()
