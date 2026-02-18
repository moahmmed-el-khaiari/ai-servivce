import requests
from app.config import PRODUCT_SERVICE_URL , MENU_SERVICE_URL

def search_product_by_name(name: str):
    response = requests.get(
        f"{PRODUCT_SERVICE_URL}/search",
        params={"name": name}
    )
    response.raise_for_status()
    return response.json()

def get_product_by_id(product_id: int):
    response = requests.get(f"{PRODUCT_SERVICE_URL}/{product_id}")
    response.raise_for_status()
    return response.json()
def get_all_products():
    response = requests.get(PRODUCT_SERVICE_URL)
    response.raise_for_status()
    return response.json()

def get_all_menus():
    response = requests.get(MENU_SERVICE_URL)
    response.raise_for_status()
    return response.json()