import requests
from app.config import PRODUCT_SERVICE_URL


def get_products_by_category(category: str):

    try:
        response = requests.get(
            f"{PRODUCT_SERVICE_URL}/category/{category}",
            timeout=5
        )

        response.raise_for_status()
        data = response.json()

        # 🔥 filtrer uniquement available = true
        available_products = [
            p for p in data if p.get("available") == True
        ]

        return available_products

    except Exception as e:
        print("Erreur appel product-service:", e)
        return []
