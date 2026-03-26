from datetime import datetime, time
import pytz
 
# =============================
# CONFIG HORAIRES
# =============================
TIMEZONE    = "Africa/Casablanca"
OPEN_TIME   = time(11, 0)   # 11h00
CLOSE_TIME  = time(23, 0)   # 23h00
 
# 0=Lundi, 1=Mardi, ..., 6=Dimanche
# Ex: [6] = fermé le dimanche
CLOSED_DAYS = []
 
# Dates fermées (format YYYY-MM-DD)
HOLIDAYS = [
    "2026-01-01",   # Nouvel An
    "2026-03-03",   # Fête du Trône
    "2026-07-30",   # Fête du Trône
]
 
 
def is_open() -> bool:
    """Retourne True si le restaurant est ouvert maintenant."""
    tz  = pytz.timezone(TIMEZONE)
    now = datetime.now(tz)
 
    # Vérifier jour fermé
    if now.weekday() in CLOSED_DAYS:
        print(f"[Schedule] Fermé — jour {now.strftime('%A')}")
        return False
 
    # Vérifier jour férié
    today = now.strftime("%Y-%m-%d")
    if today in HOLIDAYS:
        print(f"[Schedule] Fermé — jour férié {today}")
        return False
 
    # Vérifier heure d'ouverture
    current_time = now.time().replace(second=0, microsecond=0)
    if not (OPEN_TIME <= current_time <= CLOSE_TIME):
        print(f"[Schedule] Fermé — heure {now.strftime('%H:%M')} hors {OPEN_TIME}–{CLOSE_TIME}")
        return False
 
    print(f"[Schedule] ✅ Ouvert — {now.strftime('%A %H:%M')}")
    return True
 
 
def get_hours_message() -> str:
    """Retourne un message vocal avec les horaires d'ouverture."""
    return "Nous sommes ouverts du lundi au dimanche de onze heures à vingt-trois heures."