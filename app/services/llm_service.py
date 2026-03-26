import asyncio
import aiohttp
import requests
import json
import re
from app.config import GROQ_API_KEY, GROQ_LLM_URL, GROQ_LLM_MODEL


# =============================
# HELPERS JSON
# =============================

def clean_json_response(text: str):
    try:
        data = json.loads(text)
    except:
        match = re.search(r"\{.*\}", text, re.DOTALL)
        if match:
            try:
                data = json.loads(match.group())
            except:
                return {"products": [], "menus": []}
        else:
            return {"products": [], "menus": []}

    data.setdefault("products", [])
    data.setdefault("menus", [])

    for p in data["products"]:
        if not p.get("size") or p.get("size") not in ["S", "M", "L", "XL"]:
            p["size"] = None
        p.setdefault("extraSauces", [])

    return data


def _build_headers():
    return {
        "Authorization": f"Bearer {GROQ_API_KEY}",
        "Content-Type": "application/json"
    }


# =============================
# APPEL LLM SYNC — HTTP pur (Groq)
# Toutes les fonctions sync utilisent ceci — pas d'asyncio du tout
# Fonctionne dans run_in_executor (thread sans boucle asyncio)
# =============================

def _sync_llm_call(messages: list, max_tokens: int = 300, temperature: float = 0) -> str:
    """Appel LLM HTTP sync pur — fonctionne dans n'importe quel contexte."""
    try:
        response = requests.post(
            GROQ_LLM_URL,
            headers=_build_headers(),
            json={
                "model": GROQ_LLM_MODEL,
                "messages": messages,
                "temperature": temperature,
                "max_tokens": max_tokens,
            },
            timeout=10
        )
        if response.status_code != 200:
            print(f"[LLM] Erreur sync {response.status_code}: {response.text[:100]}")
            return ""
        return response.json()["choices"][0]["message"]["content"].strip()
    except Exception as e:
        print(f"[LLM] Exception sync: {e}")
        return ""


# =============================
# APPEL LLM ASYNC — base (Groq)
# Uniquement pour le prefetch dans twilio_voice_new.py
# =============================

async def _async_llm_call(messages: list, max_tokens: int = 300, temperature: float = 0) -> str:
    """Appel LLM async via Groq — ~200ms TTFB."""
    payload = {
        "model": GROQ_LLM_MODEL,
        "messages": messages,
        "temperature": temperature,
        "max_tokens": max_tokens,
    }
    try:
        async with aiohttp.ClientSession() as session:
            async with session.post(
                GROQ_LLM_URL,
                headers=_build_headers(),
                json=payload,
                timeout=aiohttp.ClientTimeout(total=10)
            ) as resp:
                if resp.status != 200:
                    body = await resp.text()
                    print(f"[LLM] Erreur {resp.status}: {body[:100]}")
                    return ""
                data = await resp.json()
                return data["choices"][0]["message"]["content"].strip()
    except asyncio.TimeoutError:
        print("[LLM] Timeout async")
        return ""
    except Exception as e:
        print(f"[LLM] Exception async: {e}")
        return ""


# =============================
# EXTRACT ORDER INTENT
# =============================

_EXTRACT_SYSTEM = "Tu extrais des commandes de restaurant en JSON strict. Reponds UNIQUEMENT en JSON valide."

def _build_extract_prompt(message: str) -> str:
    return f"""Tu es un extracteur JSON strict pour commandes de restaurant.

REGLES CRITIQUES :
- Garde les noms de produits EXACTEMENT comme prononcés (ex: "pizza poulet", "tacos viande hachee").
- Noms multi-mots = UN seul produit (ex: "pizza poulet" = 1 produit).
- La taille peut venir APRES le produit (ex: "un tacos poulet taille grande" = tacos poulet size L).
- MEME produit avec TAILLES DIFFERENTES = deux entrees separees.
  Ex: "deux tacos poulet, un grand et un moyen" = [tacos poulet qty=1 size=L, tacos poulet qty=1 size=M]
- "en plus", "et aussi", "avec", "et" = produits supplementaires.
- Extraire TOUS les produits mentionnes, y compris desserts et boissons.
- Ne JAMAIS omettre un produit mentionne.
- Retourner UNIQUEMENT du JSON valide, aucune explication.

Format:
{{
  "products": [
    {{"name": "string", "quantity": number, "size": "S | M | L | XL | null", "extraSauces": []}}
  ],
  "menus": [
    {{"name": "string", "quantity": number}}
  ]
}}

Message client:
{message}
"""


def extract_order_intent(message: str) -> dict:
    """
    Version sync — appelée depuis run_in_executor (thread sans boucle asyncio).
    Utilise HTTP sync pur, zéro asyncio.
    """
    messages = [
        {"role": "system", "content": _EXTRACT_SYSTEM},
        {"role": "user",   "content": _build_extract_prompt(message)}
    ]
    raw = _sync_llm_call(messages, max_tokens=400, temperature=0)
    result = clean_json_response(raw)
    print(f"[LLM] extract_order_intent → {len(result.get('products', []))} produit(s)")
    return result


async def extract_order_intent_async(message: str) -> dict:
    """
    Version async — utilisée pour le prefetch dans twilio_voice_new.py.
    """
    messages = [
        {"role": "system", "content": _EXTRACT_SYSTEM},
        {"role": "user",   "content": _build_extract_prompt(message)}
    ]
    raw = await _async_llm_call(messages, max_tokens=400, temperature=0)
    return clean_json_response(raw)


# =============================
# LLM MATCH PRODUCTS
# =============================

def _build_match_prompt(transcript: str, product_list: list) -> str:
    products_str = "\n".join(f"- {p['name']}" for p in product_list)
    return f"""Tu es un assistant de restaurant. Un client passe une commande par telephone.

LISTE COMPLETE DES PRODUITS DU MENU :
{products_str}

MESSAGE DU CLIENT (peut contenir des deformations vocales) :
"{transcript}"

REGLES :
1. Identifie UNIQUEMENT les produits presents dans la liste du menu.
2. Si le client dit un nom approchant (ex: "se pouler" -> "Tacos poulet", "piza" -> "Pizza"), utilise le nom EXACT du menu.
3. Tailles : petit/petite -> S, moyen/moyenne -> M, grand/grande -> L, tres grand -> XL, non mentionne -> null.
4. Ignore les mots parasites.
5. Retourne UNIQUEMENT un JSON valide, aucune explication.

FORMAT :
{{"matched": [{{"name": "nom exact du produit", "quantity": 1, "size": "M"}}]}}

Si aucun produit reconnu -> {{"matched": []}}
"""


def llm_match_products(transcript: str, product_list: list) -> list:
    """
    Version sync — appelée depuis run_in_executor via name_to_id_mapper.
    Utilise HTTP sync pur.
    """
    messages = [
        {"role": "system", "content": "Tu extrais des commandes de restaurant. Reponds UNIQUEMENT en JSON valide."},
        {"role": "user",   "content": _build_match_prompt(transcript, product_list)}
    ]
    raw = _sync_llm_call(messages, max_tokens=300, temperature=0)
    raw = re.sub(r"```json|```", "", raw).strip()
    m = re.search(r"\{.*\}", raw, re.DOTALL)
    if m:
        try:
            data = json.loads(m.group())
            matched = data.get("matched", [])
            print(f"[LLM Matcher] {len(matched)} produit(s) reconnu(s)")
            for item in matched:
                print(f"  ✅ '{item['name']}' x{item.get('quantity',1)} taille={item.get('size')}")
            return matched
        except Exception as e:
            print(f"[LLM Matcher] JSON parse error: {e}")
    return []


async def llm_match_products_async(transcript: str, product_list: list) -> list:
    """Version async — si besoin depuis contexte async."""
    messages = [
        {"role": "system", "content": "Tu extrais des commandes de restaurant. Reponds UNIQUEMENT en JSON valide."},
        {"role": "user",   "content": _build_match_prompt(transcript, product_list)}
    ]
    raw = await _async_llm_call(messages, max_tokens=300, temperature=0)
    raw = re.sub(r"```json|```", "", raw).strip()
    m = re.search(r"\{.*\}", raw, re.DOTALL)
    if m:
        try:
            data = json.loads(m.group())
            matched = data.get("matched", [])
            print(f"[LLM Matcher] {len(matched)} produit(s) reconnu(s)")
            for item in matched:
                print(f"  ✅ '{item['name']}' x{item.get('quantity',1)} taille={item.get('size')}")
            return matched
        except Exception as e:
            print(f"[LLM Matcher] JSON parse error: {e}")
    return []


# =============================
# GENERATE REPLY
# =============================

_SYSTEM_REPLY = (
    "Tu es l'IA vocale du restaurant Savoria. "
    "Reponds TOUJOURS en français. Ton chaleureux, max 2 phrases courtes. "
    "Ne modifie jamais les totaux, prix, liens ou noms de produits. "
    "Reformule naturellement le fallback_message fourni. "
    "Retourne UNIQUEMENT le texte final du message."
)


def generate_reply(context: dict) -> str:
    """
    Version sync — appelée depuis run_in_executor.
    Utilise HTTP sync pur.
    """
    user_prompt = (
        f"Données contexte:\n{json.dumps(context, indent=2, ensure_ascii=False)}\n\n"
        "Génère un message naturel à envoyer au client. Retourne uniquement le texte du message."
    )
    messages = [
        {"role": "system", "content": _SYSTEM_REPLY},
        {"role": "user",   "content": user_prompt}
    ]
    result = _sync_llm_call(messages, max_tokens=100, temperature=0.5)
    return result if result and len(result) > 3 else context.get("fallback_message", "Merci.")


async def generate_reply_async(context: dict) -> str:
    """Version async — utilisée depuis twilio_voice_new.py si besoin."""
    user_prompt = (
        f"Données contexte:\n{json.dumps(context, indent=2, ensure_ascii=False)}\n\n"
        "Génère un message naturel à envoyer au client. Retourne uniquement le texte du message."
    )
    messages = [
        {"role": "system", "content": _SYSTEM_REPLY},
        {"role": "user",   "content": user_prompt}
    ]
    result = await _async_llm_call(messages, max_tokens=100, temperature=0.5)
    return result if result and len(result) > 3 else context.get("fallback_message", "Merci.")


# =============================
# QUICK EXTRACT (regex, pas de LLM)
# =============================

def quick_extract(message: str):
    pattern = r"(\d+)\s+(.+)"
    match = re.match(pattern, message.strip())
    if match:
        return {
            "products": [{
                "name": match.group(2).strip(),
                "quantity": int(match.group(1)),
                "size": None,
                "extraSauces": []
            }],
            "menus": []
        }
    return None