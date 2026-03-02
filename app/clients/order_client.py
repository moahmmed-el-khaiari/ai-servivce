import requests
from app.config import ORDER_SERVICE_URL


def create_order(order_payload: dict):

    print("\n🔥 PAYLOAD ENVOYÉ AU ORDER-SERVICE:")
    print(order_payload)
    print("🔥 FIN PAYLOAD\n")

    try:
        response = requests.post(
            ORDER_SERVICE_URL,
            json=order_payload,
            timeout=30
        )

        print("📡 STATUS CODE:", response.status_code)
        print("📡 RESPONSE TEXT:", response.text)

        if response.status_code != 200:
            return {
                "error": "Backend error",
                "details": response.text
            }

        return response.json()

    except requests.exceptions.RequestException as e:
        print("🔥 EXCEPTION REQUEST:", str(e))
        return {
            "error": "Connection error",
            "details": str(e)
        }