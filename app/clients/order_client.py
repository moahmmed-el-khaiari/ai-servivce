import requests
from app.config import ORDER_SERVICE_URL

def create_order(order_payload: dict):
    response = requests.post(
        ORDER_SERVICE_URL,
        json=order_payload
    )
    response.raise_for_status()
    return response.json()
