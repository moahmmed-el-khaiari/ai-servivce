import requests
import json
from app.config import OPENROUTER_API_KEY, DEEPSEEK_MODEL, OPENROUTER_URL


def extract_order_intent(message: str):

    prompt = f"""
Extract food items and quantities from this sentence.
Return ONLY valid JSON:

{{
  "products": [{{"name": "", "quantity": 0}}],
  "menus": [{{"name": "", "quantity": 0}}]
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
            {"role": "system", "content": "You extract restaurant orders into strict JSON only."},
            {"role": "user", "content": prompt}
        ],
        "temperature": 0
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

    # 🔥 sécurisation JSON
    try:
        return json.loads(content)
    except:
        # nettoyer si LLM ajoute texte
        start = content.find("{")
        end = content.rfind("}") + 1
        clean_json = content[start:end]
        return json.loads(clean_json)
