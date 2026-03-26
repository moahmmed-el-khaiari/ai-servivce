import asyncio
import aiohttp
import requests
from app.config import ORDER_SERVICE_URL


async def get_last_order_async(phone: str) -> dict | None:
    """
    Version async — non-bloquante.
    Appelee depuis twilio_voice_new.py via asyncio directement.
    """
    try:
        clean_phone = phone.replace("whatsapp:", "").strip()
        url = f"{ORDER_SERVICE_URL}/phone/{clean_phone}/last"
        print(f"[History] GET {url}")

        async with aiohttp.ClientSession() as session:
            async with session.get(url, timeout=aiohttp.ClientTimeout(total=5)) as resp:
                if resp.status == 200:
                    order = await resp.json()
                    items = order.get("items", [])
                    noms  = ", ".join(i.get("productName", "") for i in items)
                    print(f"[History] ✅ Commande trouvée — id={order.get('id')}, total={order.get('totalAmount')}€")
                    print(f"[History] ✅ Client connu — {noms}")
                    return order
                if resp.status == 404:
                    print(f"[History] Pas de commande pour {clean_phone}")
                    return None
                print(f"[History] Status inattendu : {resp.status}")
                return None

    except asyncio.TimeoutError:
        print(f"[History] Timeout pour {phone}")
        return None
    except Exception as e:
        print(f"[History] Erreur : {e}")
        return None


def get_last_order(phone: str) -> dict | None:
    """
    Version sync — compatibilite avec voice_order_service (etat WELCOME).
    """
    try:
        return asyncio.run(get_last_order_async(phone))
    except Exception as e:
        print(f"[History] sync fallback error: {e}")
        # Fallback HTTP sync pur
        try:
            clean_phone = phone.replace("whatsapp:", "").strip()
            url = f"{ORDER_SERVICE_URL}/phone/{clean_phone}/last"
            resp = requests.get(url, timeout=5)
            if resp.status_code == 200:
                return resp.json()
            return None
        except Exception as e2:
            print(f"[History] Erreur sync: {e2}")
            return None