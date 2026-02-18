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
# 🌐 MICROSERVICES CONFIG
# =============================
PRODUCT_SERVICE_URL = os.getenv("PRODUCT_SERVICE_URL")
MENU_SERVICE_URL = os.getenv("MENU_SERVICE_URL")
ORDER_SERVICE_URL = os.getenv("ORDER_SERVICE_URL")

# =============================
# 🧠 VALIDATION
# =============================
if not OPENROUTER_API_KEY:
    raise ValueError("OPENROUTER_API_KEY not set in environment")

if not PRODUCT_SERVICE_URL:
    raise ValueError("PRODUCT_SERVICE_URL not set")
