import unicodedata
import re

def normalize_text(text: str):

    text = text.lower()

    text = unicodedata.normalize("NFD", text)
    text = text.encode("ascii", "ignore").decode("utf-8")

    text = re.sub(r"[^a-z0-9 ]", "", text)

    text = re.sub(r"\s+", " ", text).strip()

    return text