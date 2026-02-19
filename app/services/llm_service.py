import requests
import json
import re
from app.config import OPENROUTER_API_KEY, DEEPSEEK_MODEL, OPENROUTER_URL


def clean_json_response(text: str):
    """
    Extract JSON block from LLM response safely
    """
    try:
        # Try direct load
        return json.loads(text)
    except:
        # Extract JSON between { }
        match = re.search(r"\{.*\}", text, re.DOTALL)
        if match:
            try:
                return json.loads(match.group())
            except:
                pass

    return {"products": [], "menus": []}


def extract_order_intent(message: str):

    prompt = f"""
You are a strict JSON extractor for restaurant orders.

Rules:
- If a product name contains multiple words (example: "flan tunus"), keep it as ONE single product.
- If food item found → add it to products
- If menu found → add it to menus
- If nothing found → return empty arrays
- Return ONLY JSON
- No explanation
- No text outside JSON

Format:

{{
  "products": [{{"name": "string", "quantity": number}}],
  "menus": [{{"name": "string", "quantity": number}}]
}}

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
        "max_tokens": 300
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
            "products": [{"name": name, "quantity": quantity}],
            "menus": []
        }

    return None