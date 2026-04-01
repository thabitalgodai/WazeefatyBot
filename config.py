import os
from dotenv import load_dotenv

load_dotenv()

# ==================== إعدادات البوت ====================
TOKEN = os.getenv('BOT_TOKEN', '8717634878:AAGF2a6We5IRrMqDXMBM3REZaSEwsWHn19c')
ADMIN_USER_ID = int(os.getenv('ADMIN_USER_ID', 7995446312))
DESTINATION_GROUP_ID = int(os.getenv('DESTINATION_GROUP_ID', -1003613443224))

# ==================== إعدادات Firebase ====================
FIREBASE_CONFIG = {
    "projectId": "mozkrh2",
    "storageBucket": "mozkrh2.appspot.com"
}

# ==================== كلمة مرور الأدمن الافتراضية ====================
DEFAULT_ADMIN_PASSWORD = "123456789"

# ==================== باقات الاشتراك ====================
SUBSCRIPTIONS = {
    "free": {"points": 3, "price": 0, "duration": 1, "name": "تجريبي - مجاني"},
    "monthly": {"points": -1, "price": 9, "duration": 30, "name": "شهرية"},
    "half_year": {"points": -1, "price": 49, "duration": 180, "name": "نصف سنوية"},
    "yearly": {"points": -1, "price": 99, "duration": 365, "name": "سنوية"}
}

# ==================== النقاط المجانية اليومية ====================
FREE_POINTS_DAILY = 3

# ==================== روابط الدعم ====================
SUPPORT_LINK = "https://wa.me/966569127524"