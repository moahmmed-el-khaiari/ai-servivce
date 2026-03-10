from multiprocessing import context

import requests
import json
import re
from app.config import OPENROUTER_API_KEY, DEEPSEEK_MODEL, OPENROUTER_URL


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

    # Sécuriser structure
    data.setdefault("products", [])
    data.setdefault("menus", [])

    for p in data["products"]:
        if not p.get("size") or p.get("size") not in ["S", "M", "L", "XL"]:
            p["size"] = None
        p.setdefault("extraSauces", [])

    return data

def extract_order_intent(message: str):

    prompt = f"""
You are a strict JSON extractor for restaurant orders.

SIZE MAPPING RULES (very important):
- petit / petite / small / taille S → "S"
- moyen / moyenne / medium / taille M / taille moyenne → "M"
- grand / grande / large / taille L / taille grande → "L"
- très grand / tres grand / extra large / XL / taille XL → "XL"
- If no size mentioned → null

Rules:
- Keep product names exactly as spoken.
- If a product name contains multiple words, keep it as ONE product.
- If food item found → add it to products.
- If menu found → add it to menus.
- If user specifies size (S, M, L), include it.
- If no size specified, set size to null.
- If sauces mentioned, include them in extraSauces array.
- If no sauces mentioned, use empty array.
- Return ONLY valid JSON.
- No explanation.

Format:

{{
  "products": [
    {{
      "name": "string",
      "quantity": number,
      "size": "S | M | L ",
      "extraSauces": ["sauce name"]
    }}
  ],
  "menus": [
    {{
      "name": "string",
      "quantity": number
    }}
  ]
}}
Examples:
- "je voudrais un café de taille moyenne" → {{"products": [{{"name": "café", "quantity": 1, "size": "M", "extraSauces": []}}], "menus": []}}
- "un café moyen" → {{"products": [{{"name": "café", "quantity": 1, "size": "M", "extraSauces": []}}], "menus": []}}
- "deux pizzas grandes" → {{"products": [{{"name": "pizza", "quantity": 2, "size": "L", "extraSauces": []}}], "menus": []}}
- "une pizza petite" → {{"products": [{{"name": "pizza", "quantity": 1, "size": "S", "extraSauces": []}}], "menus": []}}
- "je veux un café" → {{"products": [{{"name": "café", "quantity": 1, "size": null, "extraSauces": []}}], "menus": []}}


Sentence:
{message}
"""

    headers = {
        "Authorization": f"Bearer {OPENROUTER_API_KEY}",
        "Content-Type": "application/json"
    }

    payload = {
        "model": DEEPSEEK_MODEL,
        "messages": [
            {
                "role": "system",
                "content": "You extract structured restaurant orders strictly into JSON."
            },
            {
                "role": "user",
                "content": prompt
            }
        ],
        "temperature": 0,
        "max_tokens": 150
    }

    response = requests.post(
        OPENROUTER_URL,
        headers=headers,
        json=payload,
        timeout=30
    )

    response.raise_for_status()
    data = response.json()

    content = data["choices"][0]["message"]["content"]

    return clean_json_response(content)
def quick_extract(message: str):

    pattern = r"(\d+)\s+(.+)"
    match = re.match(pattern, message.strip())

    if match:
        quantity = int(match.group(1))
        name = match.group(2).strip()

        return {
            "products": [{
                "name": name,
                "quantity": quantity,
                "size": None,
                "extraSauces": []
            }],
            "menus": []
        }

    return None

def generate_reply(context: dict) -> str:
    """
    Generate natural user-facing reply using DeepSeek.
    The business logic is already decided by backend.
    This function ONLY reformulates.
    """

    system_prompt = """
You are a professional restaurant AI assistant.

STRICT RULES:

LANGUAGE & TONE:
- Always respond in French.
- Always use a warm, commercial, restaurant-style tone.
- Be polite, welcoming and friendly.
- Speak like a real restaurant employee.
- Use natural conversational French.
- Keep responses concise and clear.
- You may use light commercial persuasion when appropriate.

DATA PROTECTION RULES:
- Always respect numbers exactly.
- Never modify totals.
- Never change payment links.
- Never remove URLs.
- Never modify phone numbers.
- Never modify quantities.
- Never modify product names.
- Never change business data.
- Never invent products.
- Never invent prices.

BEHAVIOR RULES:
- Only reformulate the provided fallback_message naturally.
- Never explain internal logic.
- Never mention technical details.
- Never mention JSON or backend systems.
- Only return the final message text.
- Do not add explanations.

Your role is ONLY to reformulate professionally and commercially.
"""

    user_prompt = f"""
Context data:
{json.dumps(context, indent=2)}

Generate a natural message to send to the customer.
Only return the message text.
No explanations.
"""

    headers = {
        "Authorization": f"Bearer {OPENROUTER_API_KEY}",
        "Content-Type": "application/json"
    }

    payload = {
        "model": DEEPSEEK_MODEL,
        "messages": [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_prompt}
        ],
        "temperature": 0.6,   # un peu de variation naturelle
        "max_tokens": 150
    }

    try:
        response = requests.post(
            OPENROUTER_URL,
            headers=headers,
            json=payload,
            timeout=30
        )

        response.raise_for_status()
        data = response.json()

        return data["choices"][0]["message"]["content"].strip()

    except Exception as e:
        print("LLM reply error:", e)
        return context.get("fallback_message", "Merci pour votre commande.")