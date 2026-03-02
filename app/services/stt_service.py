import requests
import time
from app.config import ASSEMBLYAI_API_KEY

ASSEMBLY_URL = "https://api.assemblyai.com/v2"

headers = {
    "authorization": ASSEMBLYAI_API_KEY,
    "content-type": "application/json"
}

def speech_to_text(audio_url: str):

    # 1️⃣ Create transcription
    response = requests.post(
        f"{ASSEMBLY_URL}/transcript",
        json={"audio_url": audio_url},
        headers=headers
    )

    transcript_id = response.json()["id"]

    # 2️⃣ Poll until ready
    while True:
        poll = requests.get(
            f"{ASSEMBLY_URL}/transcript/{transcript_id}",
            headers=headers
        ).json()

        if poll["status"] == "completed":
            return poll["text"]

        if poll["status"] == "error":
            return ""

        time.sleep(1)