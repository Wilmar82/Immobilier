#!/usr/bin/env python3
"""
Extraction mot pour mot des avis Google Business Profile de Wilmar Filliatre
vers avis_google.json.
"""

import json
import re
from datetime import datetime
from playwright.sync_api import sync_playwright

URL_AVIS_GOOGLE = (
    "https://www.google.com/search?sca_esv=825d62776290db59"
    "&sxsrf=AKL5yDiDqPfI_DjFrlupZUGWZNLeqvNdQw:1794612813350"
    "&q=avis+filliatre+wilmar"
    "&si=AE1YtOqiKPb2pdLXAi8quZ9vbH4aVq_aX8-3H9iFBLpRNK95czf49v5SihdLU-Gc-BFPq3odj0ORmnt8VIgYPj0-xZpLA8WrMF8yCfhWGGOW7SmALRWlxoI%3D"
    "&uds=AE2AFgZ2jEs7pfCtrwfpxOWhzAe2u_x7x2hCs08QdAjSY2bbdxp-KOJrmfY5KSgZuwner-VV5Lj9ZqniJkgPKpy3ANnAW_jpiw9ybxKpLVdRv-gUml9QdR_9XcIUOD2o0Q_9FCkGv32-"
    "&sa=X&ved=2ahUKEwi91_7ZjYaYAxUBVaQEHS3SAEoQ3PALegQIJRAF&biw=1470&bih=835&dpr=2"
)

FICHIER_SORTIE = "avis_google.json"


def main():
    avis_extraits = []

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        context = browser.new_context(
            user_agent=(
                "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36"
            ),
            locale="fr-FR",
            viewport={"width": 1470, "height": 900},
        )
        page = context.new_page()

        try:
            page.goto(URL_AVIS_GOOGLE, wait_until="networkidle", timeout=60000)
            page.wait_for_timeout(3000)

            for label in ("Tout accepter", "J'accepte", "Accept all"):
                btn = page.locator(f"button:has-text('{label}')")
                if btn.count() > 0:
                    try:
                        btn.first.click()
                        page.wait_for_timeout(2500)
                        break
                    except Exception:
                        pass

            # Déplier intégralement tous les avis tronqués ("Plus")
            for btn_plus in page.locator("a:has-text('Plus'), button:has-text('Plus')").all()[:20]:
                try:
                    btn_plus.click()
                    page.wait_for_timeout(300)
                except Exception:
                    pass

            blocs = page.locator("[data-review-id], .gws-localreviews__google-review").all()
            vus = set()

            for bloc in blocs:
                try:
                    auteur_el = bloc.locator("a[href*='contrib'], .TSUbDb, .Vpc5Fe").first
                    auteur = auteur_el.inner_text().strip() if auteur_el.count() > 0 else "Client Google"
                    auteur = auteur.split("\n")[0].strip() or "Client Google"

                    texte_el = bloc.locator("span[data-expandable-section], .wiI7pd, .Jtu6Td").first
                    commentaire = texte_el.inner_text().strip() if texte_el.count() > 0 else ""

                    if len(commentaire) < 10 or commentaire in vus:
                        continue

                    vus.add(commentaire)
                    avis_extraits.append({
                        "auteur": auteur,
                        "secteur": "Avis vérifié Google",
                        "texte": commentaire,
                    })
                except Exception:
                    continue

        except Exception as e:
            print(f"Erreur accès Google : {e}")
        finally:
            browser.close()

    if avis_extraits:
        payload = {
            "mis_a_jour": datetime.now().strftime("%d/%m/%Y"),
            "total": len(avis_extraits),
            "avis": avis_extraits,
        }
        with open(FICHIER_SORTIE, "w", encoding="utf-8") as f:
            json.dump(payload, f, ensure_ascii=False, indent=2)
        print(f"{len(avis_extraits)} avis enregistré(s) mot pour mot.")
    else:
        print("Aucun avis récupéré depuis Google (blocage anti-bot).")


if __name__ == "__main__":
    main()
