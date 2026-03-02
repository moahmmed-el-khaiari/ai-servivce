import os
from dotenv import load_dotenv

load_dotenv()

# =============================
# 🔐 LLM CONFIG
# =============================
OPENROUTER_API_KEY = os.getenv("OPENROUTER_API_KEY")
DEEPSEEK_MODEL = os.getenv("DEEPSEEK_MODEL")
OPENROUTER_URL = os.getenv("OPENROUTER_URL")

# =============================
# 🔐 stt + tts + twilo  CONFIG
# =============================
ASSEMBLYAI_API_KEY = os.getenv("ASSEMBLYAI_API_KEY")
ELEVENLABS_API_KEY = os.getenv("ELEVENLABS_API_KEY")

TWILIO_ACCOUNT_SID=os.getenv("TWILIO_ACCOUNT_SID")
TWILIO_AUTH_TOKEN=os.getenv("TWILIO_AUTH_TOKEN")
TWILIO_PHONE_NUMBER=os.getenv("TWILIO_PHONE_NUMBER")
# =============================
# 🌐 MICROSERVICES CONFIG
# =============================
PRODUCT_SERVICE_URL = os.getenv("PRODUCT_SERVICE_URL")
MENU_SERVICE_URL = os.getenv("MENU_SERVICE_URL")
ORDER_SERVICE_URL = os.getenv("ORDER_SERVICE_URL")
SAUCE_SERVICE_URL = os.getenv("SAUCE_SERVICE_URL")

# =============================
# 🧠 VALIDATION
# =============================
if not OPENROUTER_API_KEY:
    raise ValueError("OPENROUTER_API_KEY not set in environment")

if not PRODUCT_SERVICE_URL:
    raise ValueError("PRODUCT_SERVICE_URL not set")
