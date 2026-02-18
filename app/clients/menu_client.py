import requests
from app.config import MENU_SERVICE_URL

def search_menu_by_name(name: str):
    response = requests.get(
        f"{MENU_SERVICE_URL}/search",
        params={"name": name}
    )
    response.raise_for_status()
    return response.json()

def get_menu_by_id(menu_id: int):
    response = requests.get(f"{MENU_SERVICE_URL}/{menu_id}")
    response.raise_for_status()
    return response.json()
