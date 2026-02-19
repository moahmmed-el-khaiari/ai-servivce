import requests
from app.config import ORDER_SERVICE_URL


def create_order(order_payload: dict):
   
    try:
        response = requests.post(
            f"{ORDER_SERVICE_URL}",
            json=order_payload,
            timeout=100
        )

        response.raise_for_status()
        
        return response.json()
    


    except requests.exceptions.RequestException as e:
        print("Erreur lors de la création de la commande :", e)
        return {
            "error": "Impossible de créer la commande",
            "details": str(e)
        }
