import requests
from app.config import ELEVENLABS_API_KEY

def text_to_speech(text: str):

    idvoice="raMcNf2S8wCmuaBcyI6E" 
     # ID de la voix à utiliser (ex: "Rachel" en anglais)
    idvoice2="21m00Tcm4TlvDq8ikWAM" # ID d'une autre voix (ex: "Domi" en anglais)


    url = f"https://api.elevenlabs.io/v1/text-to-speech/{idvoice}"

    headers = {
        "xi-api-key": ELEVENLABS_API_KEY,
        "Content-Type": "application/json"
    }

    data = {
        "text": text,
        "model_id": "eleven_multilingual_v2"
    }

    response = requests.post(url, json=data, headers=headers)

    return response.content