import logging
import json
import os
import re
from datetime import datetime, timedelta
from typing import Dict, List, Optional
from functools import wraps

from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import (
    Application, CommandHandler, CallbackQueryHandler, 
    MessageHandler, filters, ContextTypes, ConversationHandler
)
from telegram.constants import ParseMode
import firebase_admin
from firebase_admin import credentials, firestore, storage

import config

# ==================== إعدادات التسجيل ====================
logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    level=logging.INFO
)
logger = logging.getLogger(__name__)

# ==================== تهيئة Firebase ====================
if not firebase_admin._apps:
    try:
        cred = credentials.Certificate("firebase.json")
        firebase_admin.initialize_app(cred, {
            'storageBucket': config.FIREBASE_CONFIG['storageBucket']
        })
        logger.info("✅ تم تهيئة Firebase بنجاح")
    except Exception as e:
        logger.error(f"❌ خطأ في تهيئة Firebase: {e}")
        raise

db = firestore.client()
bucket = storage.bucket()

# ==================== حالات المحادثة ====================
(EMAIL, PASSWORD, USERNAME, CV, JOB_TITLE, COMPANY_NAME, 
 COUNTRY, CITY, JOB_DESCRIPTION, JOB_EMAIL, ADMIN_PASSWORD,
 EDIT_FIELD, EDIT_VALUE, ADD_MODEL_DATA, ADD_CATEGORY, 
 APPLY_JOB_TITLE, APPLY_COMPANY, APPLY_COUNTRY, APPLY_CITY, APPLY_SKILLS) = range(20)
 # ==================== دوال مساعدة ====================

def load_json_file(filename):
    """تحميل ملف JSON"""
    try:
        with open(filename, 'r', encoding='utf-8') as f:
            return json.load(f)
    except FileNotFoundError:
        logger.warning(f"⚠️ الملف {filename} غير موجود")
        return None

def init_firebase_data():
    """تهيئة البيانات الافتراضية في Firebase"""
    try:
        # بيانات التخصصات
        categories_ref = db.collection('config').document('job_categories')
        if not categories_ref.get().exists:
            categories_data = load_json_file('job_categories.json')
            if categories_data:
                categories_ref.set(categories_data)
                logger.info("✅ تم تهيئة بيانات التخصصات")
        
        # بيانات نموذج الذكاء الاصطناعي
        ai_model_ref = db.collection('config').document('ai_model')
        if not ai_model_ref.get().exists:
            ai_data = load_json_file('artificial_intelligence_model.json')
            if ai_data:
                ai_model_ref.set(ai_data)
                logger.info("✅ تم تهيئة بيانات نموذج الذكاء الاصطناعي")
        
        # كلمة مرور الأدمن
        admin_password_ref = db.collection('config').document('admin_password')
        if not admin_password_ref.get().exists:
            admin_password_ref.set({'password': config.DEFAULT_ADMIN_PASSWORD})
            logger.info("✅ تم تهيئة كلمة مرور الأدمن")
            
    except Exception as e:
        logger.error(f"❌ خطأ في تهيئة البيانات: {e}")

def is_admin(user_id: int) -> bool:
    """التحقق من صلاحيات الأدمن"""
    return user_id == config.ADMIN_USER_ID

def get_user_points(user_id: int) -> Dict:
    """الحصول على نقاط المستخدم"""
    try:
        user_ref = db.collection('users').document(str(user_id))
        user_data = user_ref.get()
        
        if not user_data.exists:
            return {'points': config.FREE_POINTS_DAILY, 'last_update': datetime.now().date().isoformat()}
        
        user_dict = user_data.to_dict()
        today = datetime.now().date().isoformat()
        
        if user_dict.get('last_points_update') != today:
            subscription = user_dict.get('subscription', 'free')
            points = config.SUBSCRIPTIONS.get(subscription, {}).get('points', config.FREE_POINTS_DAILY)
            if points == -1:
                points = 999999
            user_dict['points'] = points
            user_dict['last_points_update'] = today
            user_ref.set(user_dict)
        
        return user_dict
    except Exception as e:
        logger.error(f"❌ خطأ في جلب نقاط المستخدم: {e}")
        return {'points': 0, 'last_update': datetime.now().date().isoformat()}

def update_user_points(user_id: int, points_used: int = 1) -> bool:
    """تحديث نقاط المستخدم"""
    try:
        user_ref = db.collection('users').document(str(user_id))
        user_data = user_ref.get()
        
        if not user_data.exists:
            return False
        
        user_dict = user_data.to_dict()
        current_points = user_dict.get('points', 0)
        
        if current_points < points_used:
            return False
        
        user_dict['points'] = current_points - points_used
        user_ref.set(user_dict)
        return True
    except Exception as e:
        logger.error(f"❌ خطأ في تحديث نقاط المستخدم: {e}")
        return False

def check_user_complete(user_id: int) -> tuple:
    """التحقق من اكتمال بيانات المستخدم"""
    try:
        user_ref = db.collection('users').document(str(user_id))
        user_data = user_ref.get()
        
        if not user_data.exists:
            return False, "⚠️ لم تسجل بعد! استخدم /start للتسجيل"
        
        user_dict = user_data.to_dict()
        missing = []
        
        if not user_dict.get('email'):
            missing.append("📧 الإيميل")
        if not user_dict.get('username'):
            missing.append("👤 اسم المستخدم")
        if not user_dict.get('cv_url'):
            missing.append("📎 السيرة الذاتية")
        
        if missing:
            return False, f"⚠️ بياناتك غير مكتملة:\n{chr(10).join(missing)}\n\nاستخدم /settings لإكمال البيانات"
        
        return True, user_dict
    except Exception as e:
        logger.error(f"❌ خطأ في التحقق من بيانات المستخدم: {e}")
        return False, "⚠️ حدث خطأ في التحقق من البيانات"

def get_job_categories() -> List[Dict]:
    """الحصول على قائمة التخصصات"""
    try:
        categories_ref = db.collection('config').document('job_categories')
        categories_data = categories_ref.get()
        
        if categories_data.exists:
            return categories_data.to_dict().get('categories', [])
        
        local_data = load_json_file('job_categories.json')
        return local_data.get('categories', []) if local_data else []
    except Exception as e:
        logger.error(f"❌ خطأ في جلب التخصصات: {e}")
        return []
        
        # ==================== دوال الذكاء الاصطناعي ====================

def match_job_with_user(job: Dict, user: Dict) -> float:
    """مطابقة الوظيفة مع المستخدم"""
    try:
        ai_model_ref = db.collection('config').document('ai_model')
        ai_data = ai_model_ref.get()
        
        if not ai_data.exists:
            ai_data = load_json_file('artificial_intelligence_model.json')
            if not ai_data:
                return 0
        else:
            ai_data = ai_data.to_dict()
        
        weights = ai_data.get('weights', {'city_match': 40, 'category_match': 35, 'experience_match': 15, 'education_match': 10})
        score = 0
        
        # مطابقة المدينة
        job_city = job.get('city', '').lower()
        user_city = user.get('city', '').lower()
        city_synonyms = ai_data.get('city_synonyms', {})
        
        matched = False
        for city, synonyms in city_synonyms.items():
            if job_city in synonyms and user_city in synonyms:
                matched = True
                break
            elif job_city == user_city:
                matched = True
                break
        
        if matched:
            score += weights['city_match']
        
        # مطابقة التخصص
        job_category = job.get('category', '').lower()
        user_category = user.get('category', '').lower()
        category_keywords = ai_data.get('category_keywords', {})
        
        if job_category == user_category:
            score += weights['category_match']
        else:
            for cat, keywords in category_keywords.items():
                if any(keyword in job_category for keyword in keywords) and any(keyword in user_category for keyword in keywords):
                    score += weights['category_match'] * 0.5
                    break
        
        return score
    except Exception as e:
        logger.error(f"❌ خطأ في مطابقة الوظيفة: {e}")
        return 0

# ==================== دالة استخراج المعلومات من النص ====================

def extract_job_info_from_text(text: str) -> Dict:
    """استخراج معلومات الوظيفة من النص باستخدام النموذج الكبير"""
    try:
        ai_model_ref = db.collection('config').document('ai_model')
        ai_data = ai_model_ref.get()
        
        if not ai_data.exists:
            ai_data = load_json_file('artificial_intelligence_model.json')
        else:
            ai_data = ai_data.to_dict()
        
        patterns = ai_data.get('extraction_patterns', {})
        
        job_info = {
            'title': '',
            'company': '',
            'city': '',
            'specialization': '',
            'opportunity_type': '',
            'email': '',
            'description': text[:1000],
            'full_text': text
        }
        
        # استخراج البريد الإلكتروني
        email_pattern = patterns.get('email', {}).get('pattern', r'[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}')
        emails = re.findall(email_pattern, text)
        if emails:
            job_info['email'] = emails[0]
        
        # استخراج المدينة
        city_pattern = patterns.get('city', {}).get('pattern', r'(الرياض|جدة|الدمام|الخبر|المدينة المنورة|مكة المكرمة|الجبيل|تبوك|حائل|أبها)')
        cities = re.findall(city_pattern, text)
        if cities:
            job_info['city'] = cities[0]
        
        # استخراج التخصص
        spec_pattern = patterns.get('specialization', {}).get('pattern', r'(تقنية معلومات|تصميم|محاسبة|موارد بشرية|تسويق|قانون|هندسة|لوجستيك|فني|إدارة)')
        specializations = re.findall(spec_pattern, text)
        if specializations:
            job_info['specialization'] = specializations[0]
        
        # استخراج نوع الفرصة
        opp_pattern = patterns.get('opportunity_type', {}).get('pattern', r'(توظيف|تدريب تعاوني|تمهير)')
        opp_types = re.findall(opp_pattern, text)
        if opp_types:
            job_info['opportunity_type'] = opp_types[0]
        
        # استخراج المسمى الوظيفي (من النص)
        title_patterns = [
            r'مطلوب:\s*\*(.+?)\*',
            r'المسمى الوظيفي:\s*(.+?)(?:\n|$)',
            r'Job Title:\s*(.+?)(?:\n|$)'
        ]
        
        for pattern in title_patterns:
            titles = re.findall(pattern, text, re.IGNORECASE)
            if titles:
                job_info['title'] = titles[0].strip()
                break
        
        # استخراج اسم الشركة (محاولة)
        company_patterns = [
            r'شركة\s+(.+?)(?:\n|$)',
            r'Company:\s*(.+?)(?:\n|$)'
        ]
        
        for pattern in company_patterns:
            companies = re.findall(pattern, text, re.IGNORECASE)
            if companies:
                job_info['company'] = companies[0].strip()
                break
        
        return job_info
        
    except Exception as e:
        logger.error(f"❌ خطأ في استخراج معلومات الوظيفة: {e}")
        return {
            'title': '', 'company': '', 'city': '', 
            'specialization': '', 'opportunity_type': '',
            'email': '', 'description': text[:500], 'full_text': text
        }
        # ==================== دوال الأزرار الرئيسية ====================

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """بدء البوت"""
    try:
        user_id = update.effective_user.id
        
        user_ref = db.collection('users').document(str(user_id))
        if not user_ref.get().exists:
            user_ref.set({
                'user_id': user_id,
                'username': update.effective_user.username,
                'first_name': update.effective_user.first_name,
                'created_at': datetime.now().isoformat(),
                'points': config.FREE_POINTS_DAILY,
                'last_points_update': datetime.now().date().isoformat(),
                'subscription': 'free',
                'notifications': True,
                'city': '',
                'category': ''
            })
            logger.info(f"✅ مستخدم جديد: {user_id}")
        
        if context.args and context.args[0].startswith('ref'):
            try:
                referrer_id = int(context.args[0][3:])
                referrer_ref = db.collection('users').document(str(referrer_id))
                referrer_data = referrer_ref.get()
                
                if referrer_data.exists:
                    referrer_dict = referrer_data.to_dict()
                    referrer_dict['points'] = referrer_dict.get('points', 0) + 1
                    referrer_ref.set(referrer_dict)
                    logger.info(f"✅ تمت إحالة من {referrer_id} إلى {user_id}")
            except Exception as e:
                logger.error(f"❌ خطأ في معالجة الإحالة: {e}")
        
        keyboard = [[InlineKeyboardButton("📊 القائمة الرئيسية", callback_data="main_menu")]]
        reply_markup = InlineKeyboardMarkup(keyboard)
        
        await update.message.reply_text(
            "👋 أهلاً بك في وظيفتي بوت\n\n"
            "أنا بوت ذكي يراقب الوظائف اليومية ويرسل لك اللي تناسبك حسب:\n"
            "📍 مدينتك\n🎓 تخصصك\n📜 شهادتك\n\n"
            "📧 كيف يشتغل:\n"
            "يبحث يومياً عن وظائف تقبل التقديم بالإيميل ويرسلها لك\n"
            "ويمكنه التقديم تلقائياً باستخدام بريدك\n\n"
            "🎁 جرّب مجاناً الآن\n\n"
            f"💎 نقاطك المجانية اليومية: {config.FREE_POINTS_DAILY} نقطة",
            reply_markup=reply_markup
        )
    except Exception as e:
        logger.error(f"❌ خطأ في دالة start: {e}")
        await update.message.reply_text("⚠️ حدث خطأ، يرجى المحاولة لاحقاً")

async def main_menu(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """القائمة الرئيسية"""
    try:
        query = update.callback_query
        await query.answer()
        
        keyboard = [
            [InlineKeyboardButton("📋 آخر الوظائف", callback_data="jobs_menu")],
            [InlineKeyboardButton("📝 تقديماتي", callback_data="my_applications")],
            [InlineKeyboardButton("➕ رفع وظيفة (باحث عن موظف)", callback_data="post_job")],
            [InlineKeyboardButton("🎯 التقديم على وظيفة (باحث عن عمل)", callback_data="apply_job")],
            [InlineKeyboardButton("📤 أرسل سيرتي للشركات", callback_data="blast_cv")],
            [InlineKeyboardButton("⚙️ الإعدادات", callback_data="settings")],
            [InlineKeyboardButton("🔔 تذكيراتي", callback_data="my_reminders")],
            [InlineKeyboardButton("💳 الاشتراكات", callback_data="subscriptions")],
            [InlineKeyboardButton("🆘 الدعم", callback_data="support")],
        ]
        
        if is_admin(update.effective_user.id):
            keyboard.append([InlineKeyboardButton("🔐 لوحة الأدمن", callback_data="admin_login")])
        
        keyboard.append([InlineKeyboardButton("❌ إلغاء الاشتراك", callback_data="unsubscribe")])
        
        reply_markup = InlineKeyboardMarkup(keyboard)
        
        await query.edit_message_text(
            "📊 القائمة الرئيسية\n\nاختر الخدمة التي تريدها:",
            reply_markup=reply_markup
        )
    except Exception as e:
        logger.error(f"❌ خطأ في القائمة الرئيسية: {e}")

async def jobs_menu(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """قائمة الوظائف"""
    try:
        query = update.callback_query
        await query.answer()
        
        keyboard = [
            [InlineKeyboardButton("📋 كل الوظائف", callback_data="all_jobs")],
            [InlineKeyboardButton("🎯 الوظائف المناسبة لي", callback_data="matched_jobs")],
            [InlineKeyboardButton("🔙 رجوع", callback_data="main_menu")]
        ]
        reply_markup = InlineKeyboardMarkup(keyboard)
        
        await query.edit_message_text(
            "🔟 آخر الوظائف\n\nاختر نوع العرض:",
            reply_markup=reply_markup
        )
    except Exception as e:
        logger.error(f"❌ خطأ في قائمة الوظائف: {e}")

async def show_jobs(update: Update, context: ContextTypes.DEFAULT_TYPE, matched_only=False):
    """عرض الوظائف"""
    try:
        query = update.callback_query
        user_id = update.effective_user.id
        
        jobs_ref = db.collection('jobs').where('status', '==', 'active').limit(20)
        jobs = list(jobs_ref.stream())
        
        user_data = None
        if matched_only:
            user_ref = db.collection('users').document(str(user_id))
            user_doc = user_ref.get()
            if user_doc.exists:
                user_data = user_doc.to_dict()
        
        keyboard = [[InlineKeyboardButton("🔙 رجوع", callback_data="jobs_menu")]]
        
        if not jobs:
            await query.message.reply_text("❌ لا توجد وظائف حالياً", reply_markup=InlineKeyboardMarkup(keyboard))
            return
        
        count = 0
        for job_doc in jobs:
            job = job_doc.to_dict()
            
            if matched_only and user_data:
                match_score = match_job_with_user(job, user_data)
                if match_score < 50:
                    continue
            
            count += 1
            job_text = (
                f"📢 {job.get('title', 'وظيفة شاغرة')}\n\n"
                f"🏢 الشركة: {job.get('company', 'غير محدد')}\n"
                f"📍 الموقع: {job.get('city', 'غير محدد')}\n"
                f"📌 التخصص: {job.get('category', 'غير محدد')}\n\n"
                f"✅ الشروط والمتطلبات:\n{job.get('description', 'غير محددة')[:500]}\n\n"
                f"📩 التقديم:\n{job.get('email', 'غير محدد')}"
            )
            
            job_keyboard = [
                [InlineKeyboardButton("🔔 ذكرني", callback_data=f"remind_{job_doc.id}")],
                [InlineKeyboardButton("🚀 قدم تلقائي", callback_data=f"auto_apply_{job_doc.id}")],
                [InlineKeyboardButton("🔙 رجوع", callback_data="jobs_menu")]
            ]
            
            await query.message.reply_text(job_text, reply_markup=InlineKeyboardMarkup(job_keyboard), parse_mode=ParseMode.HTML)
        
        if count == 0:
            await query.message.reply_text("❌ لا توجد وظائف مناسبة", reply_markup=InlineKeyboardMarkup(keyboard))
        else:
            await query.message.reply_text(f"✅ تم عرض {count} وظيفة\n🔙 نهاية القائمة", reply_markup=InlineKeyboardMarkup(keyboard))
    except Exception as e:
        logger.error(f"❌ خطأ في عرض الوظائف: {e}")

async def auto_apply(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """التقديم التلقائي على وظيفة"""
    try:
        query = update.callback_query
        await query.answer()
        
        user_id = update.effective_user.id
        job_id = query.data.split('_')[2]
        
        is_complete, result = check_user_complete(user_id)
        if not is_complete:
            await query.message.reply_text(result)
            return
        
        user_dict = result
        
        if user_dict.get('points', 0) <= 0:
            keyboard = [
                [InlineKeyboardButton("💳 باقات الاشتراك", callback_data="subscriptions")],
                [InlineKeyboardButton("🆘 الدعم", callback_data="support")]
            ]
            await query.message.reply_text(
                "⚠️ انتهت نقاطك!\n\nيمكنك الاشتراك في إحدى الباقات للحصول على تقديمات غير محدودة",
                reply_markup=InlineKeyboardMarkup(keyboard)
            )
            return
        
        job_ref = db.collection('jobs').document(job_id)
        job = job_ref.get()
        
        if not job.exists:
            await query.message.reply_text("❌ الوظيفة غير موجودة")
            return
        
        job_dict = job.to_dict()
        
        await query.message.reply_text("📤 جاري التقديم...")
        
        if update_user_points(user_id):
            application_ref = db.collection('applications').document()
            application_ref.set({
                'user_id': user_id,
                'job_id': job_id,
                'job_title': job_dict.get('title'),
                'company': job_dict.get('company'),
                'applied_at': datetime.now().isoformat(),
                'status': 'pending',
                'user_email': user_dict.get('email'),
                'user_name': user_dict.get('username')
            })
            
            await query.message.reply_text(
                f"✅ تم التقديم بنجاح!\n\n"
                f"تم إرسال سيرتك الذاتية إلى:\n{job_dict.get('email')}\n\n"
                f"💎 النقاط المتبقية: {user_dict.get('points', 0) - 1}"
            )
        else:
            await query.message.reply_text("❌ فشل التقديم، يرجى المحاولة لاحقاً")
            
    except Exception as e:
        logger.error(f"❌ خطأ في التقديم التلقائي: {e}")
        await query.message.reply_text("⚠️ حدث خطأ في التقديم")

async def remind_job(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """إضافة تذكير للوظيفة"""
    try:
        query = update.callback_query
        await query.answer()
        
        user_id = update.effective_user.id
        job_id = query.data.split('_')[1]
        
        reminder_ref = db.collection('reminders').document()
        reminder_ref.set({
            'user_id': user_id,
            'job_id': job_id,
            'remind_at': (datetime.now() + timedelta(hours=24)).isoformat(),
            'created_at': datetime.now().isoformat(),
            'status': 'pending'
        })
        
        await query.message.reply_text("🔔 تم إضافة تذكير! راح نذكرك بعد 24 ساعة")
    except Exception as e:
        logger.error(f"❌ خطأ في إضافة التذكير: {e}")

async def my_applications(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """عرض تقديمات المستخدم"""
    try:
        query = update.callback_query
        await query.answer()
        
        user_id = update.effective_user.id
        
        applications_ref = db.collection('applications').where('user_id', '==', user_id).limit(10)
        applications = list(applications_ref.stream())
        
        keyboard = [[InlineKeyboardButton("🔙 رجوع", callback_data="main_menu")]]
        
        if not applications:
            await query.message.reply_text("❌ لم تقدم على أي وظيفة بعد", reply_markup=InlineKeyboardMarkup(keyboard))
            return
        
        for app_doc in applications:
            app = app_doc.to_dict()
            status_emoji = "✅" if app.get('status') == 'accepted' else "⏳" if app.get('status') == 'pending' else "❌"
            app_text = (
                f"{status_emoji} {app.get('job_title', 'وظيفة')}\n"
                f"🏢 {app.get('company', 'غير محدد')}\n"
                f"📅 التاريخ: {app.get('applied_at', 'غير محدد')[:10]}\n"
                f"📊 الحالة: {app.get('status', 'pending')}"
            )
            await query.message.reply_text(app_text)
        
        await query.message.reply_text("🔙 نهاية القائمة", reply_markup=InlineKeyboardMarkup(keyboard))
    except Exception as e:
        logger.error(f"❌ خطأ في عرض التقديمات: {e}")
        # ==================== دوال رفع الوظيفة (باحث عن موظف) ====================

async def post_job_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """بدء عملية رفع وظيفة"""
    try:
        query = update.callback_query
        await query.answer()
        
        categories = get_job_categories()
        
        keyboard = []
        for cat in categories:
            keyboard.append([InlineKeyboardButton(f"{cat.get('icon', '📌')} {cat.get('name')}", callback_data=f"category_{cat.get('id')}")])
        keyboard.append([InlineKeyboardButton("🔙 رجوع", callback_data="main_menu")])
        
        await query.edit_message_text("➕ رفع وظيفة جديدة (باحث عن موظف)\n\nاختر تخصص الوظيفة:", reply_markup=InlineKeyboardMarkup(keyboard))
        return JOB_TITLE
    except Exception as e:
        logger.error(f"❌ خطأ في بدء رفع الوظيفة: {e}")
        return ConversationHandler.END

async def select_category(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """اختيار تخصص الوظيفة"""
    try:
        query = update.callback_query
        await query.answer()
        
        category_id = int(query.data.split('_')[1])
        categories = get_job_categories()
        selected_cat = next((c for c in categories if c['id'] == category_id), None)
        
        if selected_cat:
            context.user_data['job_category'] = selected_cat['name']
            context.user_data['job_category_id'] = category_id
            
            await query.edit_message_text(f"✅ التخصص: {selected_cat.get('icon')} {selected_cat['name']}\n\n📝 أرسل مسمى الوظيفة:")
            return JOB_TITLE
        
        await query.edit_message_text("❌ التخصص غير موجود")
        return ConversationHandler.END
    except Exception as e:
        logger.error(f"❌ خطأ في اختيار التخصص: {e}")
        return ConversationHandler.END

async def get_job_title(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """استلام مسمى الوظيفة"""
    context.user_data['job_title'] = update.message.text
    await update.message.reply_text("🏢 أرسل اسم الشركة أو جهة العمل:")
    return COMPANY_NAME

async def get_company_name(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """استلام اسم الشركة"""
    context.user_data['company_name'] = update.message.text
    await update.message.reply_text("🌍 أرسل الدولة:")
    return COUNTRY

async def get_country(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """استلام الدولة"""
    context.user_data['country'] = update.message.text
    await update.message.reply_text("📍 أرسل المدينة:")
    return CITY

async def get_city(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """استلام المدينة"""
    context.user_data['city'] = update.message.text
    await update.message.reply_text("📝 أرسل وصف الوظيفة ومتطلباتها:\n\nمثال:\n- درجة البكالوريوس في التخصص\n- خبرة 3 سنوات\n- إجادة اللغة الإنجليزية")
    return JOB_DESCRIPTION

async def get_job_description(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """استلام وصف الوظيفة"""
    context.user_data['job_description'] = update.message.text
    await update.message.reply_text("📧 أرسل إيميل التواصل لاستقبال الطلبات:")
    return JOB_EMAIL

async def get_job_email(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """استلام إيميل الوظيفة"""
    context.user_data['job_email'] = update.message.text
    
    job_data = {
        'title': context.user_data.get('job_title'),
        'company': context.user_data.get('company_name'),
        'country': context.user_data.get('country'),
        'city': context.user_data.get('city'),
        'description': context.user_data.get('job_description'),
        'email': context.user_data.get('job_email'),
        'category': context.user_data.get('job_category'),
        'category_id': context.user_data.get('job_category_id'),
        'posted_by': update.effective_user.id,
        'posted_at': datetime.now().isoformat(),
        'status': 'active',
        'applications_count': 0
    }
    
    job_ref = db.collection('jobs').document()
    job_ref.set(job_data)
    
    await update.message.reply_text(
        f"✅ تم نشر الوظيفة بنجاح!\n\n"
        f"📌 المسمى: {job_data['title']}\n"
        f"🏢 الشركة: {job_data['company']}\n"
        f"🌍 الدولة: {job_data['country']}\n"
        f"📍 المدينة: {job_data['city']}\n"
        f"🎯 التخصص: {job_data['category']}\n"
        f"📧 الإيميل: {job_data['email']}\n\n"
        f"🔍 سيتم إرسال إشعارات للباحثين المناسبين"
    )
    
    return ConversationHandler.END
    # ==================== دوال التقديم على وظيفة (باحث عن عمل) ====================

async def apply_job_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """بدء عملية التقديم على وظيفة (باحث عن عمل)"""
    try:
        query = update.callback_query
        await query.answer()
        
        user_id = update.effective_user.id
        
        is_complete, result = check_user_complete(user_id)
        if not is_complete:
            await query.message.reply_text(result)
            return ConversationHandler.END
        
        user_dict = result
        if user_dict.get('points', 0) <= 0:
            keyboard = [
                [InlineKeyboardButton("💳 باقات الاشتراك", callback_data="subscriptions")],
                [InlineKeyboardButton("🆘 الدعم", callback_data="support")]
            ]
            await query.message.reply_text(
                "⚠️ انتهت نقاطك!\n\nيمكنك الاشتراك في إحدى الباقات للحصول على تقديمات غير محدودة",
                reply_markup=InlineKeyboardMarkup(keyboard)
            )
            return ConversationHandler.END
        
        categories = get_job_categories()
        
        keyboard = []
        for cat in categories:
            keyboard.append([InlineKeyboardButton(f"{cat.get('icon', '📌')} {cat.get('name')}", callback_data=f"apply_category_{cat.get('id')}")])
        keyboard.append([InlineKeyboardButton("🔙 رجوع", callback_data="main_menu")])
        
        await query.edit_message_text("🎯 التقديم على وظيفة (باحث عن عمل)\n\nاختر التخصص الذي تبحث عنه:", reply_markup=InlineKeyboardMarkup(keyboard))
        return APPLY_JOB_TITLE
    except Exception as e:
        logger.error(f"❌ خطأ في بدء التقديم: {e}")
        return ConversationHandler.END

async def select_apply_category(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """اختيار تخصص للتقديم"""
    try:
        query = update.callback_query
        await query.answer()
        
        category_id = int(query.data.split('_')[2])
        categories = get_job_categories()
        selected_cat = next((c for c in categories if c['id'] == category_id), None)
        
        if selected_cat:
            context.user_data['apply_category'] = selected_cat['name']
            
            await query.edit_message_text(f"✅ التخصص: {selected_cat.get('icon')} {selected_cat['name']}\n\n📝 أرسل مسمى الوظيفة التي تبحث عنها:")
            return APPLY_JOB_TITLE
        
        await query.edit_message_text("❌ التخصص غير موجود")
        return ConversationHandler.END
    except Exception as e:
        logger.error(f"❌ خطأ في اختيار تخصص التقديم: {e}")
        return ConversationHandler.END

async def get_apply_title(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """استلام مسمى الوظيفة المطلوبة"""
    context.user_data['apply_title'] = update.message.text
    await update.message.reply_text("🏢 أرسل اسم الشركة التي ترغب في العمل بها (اختياري، أو اكتب 'لا'):")
    return APPLY_COMPANY

async def get_apply_company(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """استلام اسم الشركة"""
    context.user_data['apply_company'] = update.message.text if update.message.text != 'لا' else ''
    await update.message.reply_text("🌍 أرسل الدولة المفضلة:")
    return APPLY_COUNTRY

async def get_apply_country(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """استلام الدولة"""
    context.user_data['apply_country'] = update.message.text
    await update.message.reply_text("📍 أرسل المدينة المفضلة:")
    return APPLY_CITY

async def get_apply_city(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """استلام المدينة"""
    context.user_data['apply_city'] = update.message.text
    await update.message.reply_text("📝 أرسل مهاراتك وخبراتك (اختياري):\n\nمثال:\n- خبرة 3 سنوات في التطوير\n- إجادة Python و JavaScript")
    return APPLY_SKILLS

async def get_apply_skills(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """استلام المهارات والخبرات"""
    context.user_data['apply_skills'] = update.message.text
    
    user_id = update.effective_user.id
    user_ref = db.collection('users').document(str(user_id))
    user_doc = user_ref.get()
    user_data = user_doc.to_dict() if user_doc.exists else {}
    
    user_ref.update({
        'category': context.user_data.get('apply_category'),
        'city': context.user_data.get('apply_city'),
        'country': context.user_data.get('apply_country'),
        'skills': context.user_data.get('apply_skills'),
        'job_title': context.user_data.get('apply_title')
    })
    
    job_request = {
        'user_id': user_id,
        'title': context.user_data.get('apply_title'),
        'company': context.user_data.get('apply_company'),
        'country': context.user_data.get('apply_country'),
        'city': context.user_data.get('apply_city'),
        'skills': context.user_data.get('apply_skills'),
        'category': context.user_data.get('apply_category'),
        'status': 'active',
        'created_at': datetime.now().isoformat()
    }
    
    db.collection('job_seekers').add(job_request)
    
    jobs_ref = db.collection('jobs').where('status', '==', 'active')
    jobs = list(jobs_ref.stream())
    
    matched_jobs = []
    for job_doc in jobs:
        job = job_doc.to_dict()
        match_score = match_job_with_user(job, user_data)
        if match_score >= 50:
            matched_jobs.append((job_doc.id, job, match_score))
    
    if matched_jobs:
        await update.message.reply_text(f"✅ تم حفظ طلبك بنجاح!\n\n🔍 تم العثور على {len(matched_jobs)} وظيفة مناسبة لك:")
        
        for job_id, job, score in matched_jobs[:5]:
            job_text = (
                f"📢 {job.get('title', 'وظيفة شاغرة')}\n"
                f"🏢 {job.get('company', 'غير محدد')}\n"
                f"📍 {job.get('city', 'غير محدد')}\n"
                f"🎯 نسبة المطابقة: {score}%\n"
                f"📩 {job.get('email', 'غير محدد')}"
            )
            
            keyboard = [[InlineKeyboardButton("🚀 قدم تلقائي", callback_data=f"auto_apply_{job_id}")]]
            await update.message.reply_text(job_text, reply_markup=InlineKeyboardMarkup(keyboard))
    else:
        await update.message.reply_text(f"✅ تم حفظ طلبك بنجاح!\n\nسيتم إعلامك عند توفر وظائف مناسبة في تخصص {context.user_data.get('apply_category')}")
    
    return ConversationHandler.END
    # ==================== دوال الإعدادات والسيرة الذاتية ====================

async def blast_cv(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """إرسال السيرة الذاتية للشركات"""
    query = update.callback_query
    await query.answer()
    
    user_id = update.effective_user.id
    
    is_complete, result = check_user_complete(user_id)
    if not is_complete:
        await query.message.reply_text(result)
        return ConversationHandler.END
    
    await query.message.reply_text("📤 أرسل سيرتك الذاتية (PDF)\n\nالحد الأقصى للحجم: 5 ميجابايت")
    return CV

async def receive_cv(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """استلام السيرة الذاتية"""
    try:
        user_id = update.effective_user.id
        
        if not update.message.document:
            await update.message.reply_text("❌ يرجى رفع ملف PDF")
            return CV
        
        document = update.message.document
        
        if not document.mime_type == 'application/pdf':
            await update.message.reply_text("❌ يرجى رفع ملف PDF فقط")
            return CV
        
        if document.file_size > 5 * 1024 * 1024:
            await update.message.reply_text("❌ حجم الملف كبير جداً. الحد الأقصى 5 ميجابايت")
            return CV
        
        await update.message.reply_text("📤 جاري رفع السيرة الذاتية...")
        
        blob = bucket.blob(f"cvs/{user_id}/{document.file_name}")
        file = await update.message.effective_attachment.get_file()
        file_path = f"/tmp/{document.file_name}"
        await file.download_to_drive(file_path)
        blob.upload_from_filename(file_path)
        blob.make_public()
        
        user_ref = db.collection('users').document(str(user_id))
        user_ref.update({
            'cv_url': blob.public_url,
            'cv_name': document.file_name,
            'cv_updated_at': datetime.now().isoformat()
        })
        
        os.remove(file_path)
        
        await update.message.reply_text(f"✅ تم حفظ السيرة الذاتية بنجاح!\n\n📎 الملف: {document.file_name}")
        return ConversationHandler.END
        
    except Exception as e:
        logger.error(f"❌ خطأ في رفع السيرة الذاتية: {e}")
        await update.message.reply_text("❌ حدث خطأ في رفع الملف")
        return ConversationHandler.END

async def settings_menu(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """قائمة الإعدادات"""
    try:
        query = update.callback_query
        await query.answer()
        
        user_id = update.effective_user.id
        user_ref = db.collection('users').document(str(user_id))
        user_data = user_ref.get()
        
        if not user_data.exists:
            await query.message.reply_text("⚠️ يرجى استخدام /start أولاً")
            return
        
        user_dict = user_data.to_dict()
        
        keyboard = [
            [InlineKeyboardButton("📧 الإيميل", callback_data="set_email")],
            [InlineKeyboardButton("🔑 كلمة المرور", callback_data="set_password")],
            [InlineKeyboardButton("👤 اسم المستخدم", callback_data="set_username")],
            [InlineKeyboardButton("📎 السيرة الذاتية", callback_data="set_cv")],
            [InlineKeyboardButton("💳 الاشتراك", callback_data="subscriptions")],
            [InlineKeyboardButton("🔗 رابط المشاركة", callback_data="share_link")],
            [InlineKeyboardButton("🔙 رجوع", callback_data="main_menu")]
        ]
        
        info_text = (
            f"⚙️ الإعدادات\n\n"
            f"📧 الإيميل: {user_dict.get('email', 'غير محدد')}\n"
            f"👤 اسم المستخدم: {user_dict.get('username', 'غير محدد')}\n"
            f"📍 المدينة: {user_dict.get('city', 'غير محدد')}\n"
            f"📌 التخصص: {user_dict.get('category', 'غير محدد')}\n"
            f"📎 السيرة الذاتية: {'✅ موجودة' if user_dict.get('cv_url') else '❌ غير مرفوعة'}\n"
            f"💳 الاشتراك: {config.SUBSCRIPTIONS.get(user_dict.get('subscription', 'free'), {}).get('name', 'مجاني')}\n"
            f"💎 النقاط المتبقية: {user_dict.get('points', 0)}\n"
        )
        
        await query.edit_message_text(info_text, reply_markup=InlineKeyboardMarkup(keyboard))
    except Exception as e:
        logger.error(f"❌ خطأ في قائمة الإعدادات: {e}")

async def set_email(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """تعيين الإيميل"""
    query = update.callback_query
    await query.answer()
    await query.edit_message_text("📧 أرسل إيميلك:")
    return EMAIL

async def receive_email(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """استلام الإيميل"""
    try:
        user_id = update.effective_user.id
        email = update.message.text
        
        if '@' not in email or '.' not in email:
            await update.message.reply_text("❌ يرجى إدخال إيميل صحيح")
            return EMAIL
        
        user_ref = db.collection('users').document(str(user_id))
        user_ref.update({'email': email})
        
        await update.message.reply_text("✅ تم حفظ الإيميل بنجاح!")
        return ConversationHandler.END
    except Exception as e:
        logger.error(f"❌ خطأ في حفظ الإيميل: {e}")
        return ConversationHandler.END

async def set_password(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """تعيين كلمة المرور"""
    query = update.callback_query
    await query.answer()
    await query.edit_message_text("🔑 أرسل كلمة المرور:")
    return PASSWORD

async def receive_password(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """استلام كلمة المرور"""
    try:
        user_id = update.effective_user.id
        password = update.message.text
        
        if len(password) < 4:
            await update.message.reply_text("❌ كلمة المرور قصيرة جداً (الحد الأدنى 4 أحرف)")
            return PASSWORD
        
        user_ref = db.collection('users').document(str(user_id))
        user_ref.update({'password': password})
        
        await update.message.reply_text("✅ تم حفظ كلمة المرور بنجاح!")
        return ConversationHandler.END
    except Exception as e:
        logger.error(f"❌ خطأ في حفظ كلمة المرور: {e}")
        return ConversationHandler.END

async def set_username(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """تعيين اسم المستخدم"""
    query = update.callback_query
    await query.answer()
    await query.edit_message_text("👤 أرسل اسم المستخدم:")
    return USERNAME

async def receive_username(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """استلام اسم المستخدم"""
    try:
        user_id = update.effective_user.id
        username = update.message.text
        
        if len(username) < 3:
            await update.message.reply_text("❌ اسم المستخدم قصير جداً (الحد الأدنى 3 أحرف)")
            return USERNAME
        
        user_ref = db.collection('users').document(str(user_id))
        user_ref.update({'username': username})
        
        await update.message.reply_text("✅ تم حفظ اسم المستخدم بنجاح!")
        return ConversationHandler.END
    except Exception as e:
        logger.error(f"❌ خطأ في حفظ اسم المستخدم: {e}")
        return ConversationHandler.END

async def share_link(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """مشاركة رابط الإحالة"""
    query = update.callback_query
    await query.answer()
    
    user_id = update.effective_user.id
    bot_username = context.bot.username
    invite_link = f"https://t.me/{bot_username}?start=ref{user_id}"
    
    await query.edit_message_text(
        f"🔗 رابط المشاركة الخاص بك:\n\n`{invite_link}`\n\nعندما يسجل شخص عن طريق هذا الرابط، ستحصل على نقطة إضافية!",
        parse_mode=ParseMode.MARKDOWN,
        reply_markup=InlineKeyboardMarkup([[
            InlineKeyboardButton("📋 نسخ الرابط", callback_data="copy_link"),
            InlineKeyboardButton("🔙 رجوع", callback_data="settings")
        ]])
    )

async def subscriptions(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """عرض باقات الاشتراك"""
    query = update.callback_query
    await query.answer()
    
    keyboard = [
        [InlineKeyboardButton("🆓 تجريبي — مجاني ← 3 تقديمات يومياً", callback_data="subscribe_free")],
        [InlineKeyboardButton("📅 شهرية — 9 ر.س ← تقديمات بلا حدود", callback_data="subscribe_monthly")],
        [InlineKeyboardButton("📆 نصف سنوية — 49 ر.س ← تقديمات بلا حدود", callback_data="subscribe_half_year")],
        [InlineKeyboardButton("🗓 سنوية — 99 ر.س ← تقديمات بلا حدود", callback_data="subscribe_yearly")],
        [InlineKeyboardButton("🔙 رجوع", callback_data="settings")]
    ]
    
    await query.edit_message_text(
        "💳 باقات الاشتراك\n\n"
        "💡 هذه الباقات خاصة بالتقديم التلقائي على الوظائف اليومية\n\n"
        "━━━━━━━━━━━━━━━\n"
        "🆓 تجريبي — مجاني ← 3 تقديمات يومياً\n\n"
        "📅 شهرية — 9 ر.س ← تقديمات بلا حدود\n\n"
        "📆 نصف سنوية — 49 ر.س ← تقديمات بلا حدود\n\n"
        "🗓 سنوية — 99 ر.س ← تقديمات بلا حدود",
        reply_markup=InlineKeyboardMarkup(keyboard)
    )

async def subscribe(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """الاشتراك في باقة"""
    query = update.callback_query
    await query.answer()
    
    user_id = update.effective_user.id
    subscription_type = query.data.split('_')[1]
    
    user_ref = db.collection('users').document(str(user_id))
    user_data = user_ref.get()
    
    if not user_data.exists:
        await query.message.reply_text("⚠️ يرجى استخدام /start أولاً")
        return
    
    user_dict = user_data.to_dict()
    subscription_info = config.SUBSCRIPTIONS.get(subscription_type, {})
    
    message = (
        f"أنا {user_dict.get('username', user_dict.get('first_name', 'المستخدم'))}\n"
        f"أريد الاشتراك في الباقة {subscription_info.get('name', subscription_type)}\n\n"
        f"السعر: {subscription_info.get('price', 0)} ر.س\n"
        f"المعرف: {user_id}"
    )
    
    await context.bot.send_message(
        config.ADMIN_USER_ID,
        f"🔔 طلب اشتراك جديد!\n\n👤 المستخدم: {user_dict.get('username', 'غير محدد')}\n🆔 المعرف: {user_id}\n💳 الباقة: {subscription_info.get('name', subscription_type)}\n💰 السعر: {subscription_info.get('price', 0)} ر.س"
    )
    
    keyboard = [
        [InlineKeyboardButton("🆘 تواصل مع الدعم للدفع", url=config.SUPPORT_LINK)],
        [InlineKeyboardButton("🔙 رجوع", callback_data="subscriptions")]
    ]
    
    await query.edit_message_text(
        f"✅ تم استلام طلب الاشتراك!\n\n{message}\n\n📌 خطوات إتمام الاشتراك:\n1️⃣ اضغط على زر الدعم\n2️⃣ أرسل هذه الرسالة للدعم\n3️⃣ قم بعملية الدفع",
        reply_markup=InlineKeyboardMarkup(keyboard)
    )

async def support(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """الدعم الفني"""
    query = update.callback_query
    await query.answer()
    
    keyboard = [
        [InlineKeyboardButton("📞 تواصل مع الدعم", url=config.SUPPORT_LINK)],
        [InlineKeyboardButton("🔙 رجوع", callback_data="main_menu")]
    ]
    
    await query.edit_message_text(
        "🆘 الدعم الفني\n\nإذا واجهت أي مشكلة، يمكنك التواصل مع فريق الدعم:\n\n📞 واتساب: [اضغط هنا](https://wa.me/966569127524)",
        parse_mode=ParseMode.MARKDOWN,
        reply_markup=InlineKeyboardMarkup(keyboard)
    )

async def unsubscribe(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """إلغاء الاشتراك"""
    query = update.callback_query
    await query.answer()
    
    user_id = update.effective_user.id
    
    user_ref = db.collection('users').document(str(user_id))
    user_ref.update({
        'notifications': False,
        'unsubscribed_at': datetime.now().isoformat()
    })
    
    await query.edit_message_text(
        "❌ تم إلغاء الاشتراك بنجاح!\n\nلإعادة الاشتراك، استخدم /start مرة أخرى",
        reply_markup=InlineKeyboardMarkup([[
            InlineKeyboardButton("📊 القائمة الرئيسية", callback_data="main_menu")
        ]])
    )
    # ==================== دوال الأدمن ====================

async def admin_login(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """تسجيل دخول الأدمن"""
    query = update.callback_query
    await query.answer()
    await query.edit_message_text("🔐 أرسل كلمة المرور لدخول قائمة الأدمن:")
    return ADMIN_PASSWORD

async def check_admin_password(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """التحقق من كلمة مرور الأدمن"""
    try:
        password = update.message.text
        
        admin_password_ref = db.collection('config').document('admin_password')
        admin_password = admin_password_ref.get()
        
        if admin_password.exists and admin_password.to_dict().get('password') == password:
            await show_admin_panel(update, context)
        else:
            await update.message.reply_text("❌ كلمة المرور غير صحيحة")
        
        return ConversationHandler.END
    except Exception as e:
        logger.error(f"❌ خطأ في التحقق من كلمة المرور: {e}")
        return ConversationHandler.END

async def show_admin_panel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """عرض لوحة تحكم الأدمن"""
    try:
        keyboard = [
            [InlineKeyboardButton("👤 المستخدمين", callback_data="admin_users")],
            [InlineKeyboardButton("💼 الباحثين عن وظائف", callback_data="admin_job_seekers")],
            [InlineKeyboardButton("🏢 الوظائف المنشورة", callback_data="admin_jobs")],
            [InlineKeyboardButton("📊 الإحصائيات", callback_data="admin_stats")],
            [InlineKeyboardButton("🔑 تغيير كلمة المرور", callback_data="admin_change_password")],
            [InlineKeyboardButton("🤖 نموذج الذكاء الاصطناعي", callback_data="admin_ai_model")],
            [InlineKeyboardButton("❌ إغلاق", callback_data="close_admin")]
        ]
        
        users_count = len(list(db.collection('users').stream()))
        jobs_count = len(list(db.collection('jobs').where('status', '==', 'active').stream()))
        seekers_count = len(list(db.collection('job_seekers').where('status', '==', 'active').stream()))
        
        stats_text = (
            f"📊 لوحة تحكم الأدمن\n\n"
            f"👤 عدد المستخدمين: {users_count}\n"
            f"🏢 عدد الوظائف النشطة: {jobs_count}\n"
            f"💼 عدد الباحثين: {seekers_count}\n\n"
            f"اختر القسم الذي تريد إدارته:"
        )
        
        if isinstance(update, Update) and update.callback_query:
            await update.callback_query.edit_message_text(stats_text, reply_markup=InlineKeyboardMarkup(keyboard))
        else:
            await update.message.reply_text(stats_text, reply_markup=InlineKeyboardMarkup(keyboard))
    except Exception as e:
        logger.error(f"❌ خطأ في عرض لوحة الأدمن: {e}")

async def admin_users(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """عرض المستخدمين"""
    try:
        query = update.callback_query
        await query.answer()
        
        users_ref = db.collection('users').limit(20)
        users = list(users_ref.stream())
        
        keyboard = [[InlineKeyboardButton("🔙 رجوع", callback_data="admin_panel")]]
        
        if not users:
            await query.message.reply_text("❌ لا يوجد مستخدمين", reply_markup=InlineKeyboardMarkup(keyboard))
            return
        
        for user_doc in users:
            user = user_doc.to_dict()
            user_text = (
                f"👤 {user.get('username', user.get('first_name', 'غير محدد'))}\n"
                f"🆔 {user_doc.id}\n"
                f"📧 {user.get('email', 'غير محدد')}\n"
                f"💎 نقاط: {user.get('points', 0)}\n"
                f"💳 الاشتراك: {user.get('subscription', 'free')}\n"
                f"📍 المدينة: {user.get('city', 'غير محدد')}\n"
                f"📅 التسجيل: {user.get('created_at', 'غير محدد')[:10] if user.get('created_at') else 'غير محدد'}\n"
            )
            
            user_keyboard = [
                [InlineKeyboardButton("➕ إضافة نقاط", callback_data=f"add_points_{user_doc.id}")],
                [InlineKeyboardButton("❌ حذف", callback_data=f"delete_user_{user_doc.id}")]
            ]
            
            await query.message.reply_text(user_text, reply_markup=InlineKeyboardMarkup(user_keyboard))
        
        await query.message.reply_text("🔙 نهاية قائمة المستخدمين", reply_markup=InlineKeyboardMarkup(keyboard))
    except Exception as e:
        logger.error(f"❌ خطأ في عرض المستخدمين: {e}")

async def admin_jobs(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """عرض وظائف الشركات"""
    try:
        query = update.callback_query
        await query.answer()
        
        jobs_ref = db.collection('jobs').where('status', '==', 'active').limit(20)
        jobs = list(jobs_ref.stream())
        
        keyboard = [[InlineKeyboardButton("🔙 رجوع", callback_data="admin_panel")]]
        
        if not jobs:
            await query.message.reply_text("❌ لا توجد وظائف", reply_markup=InlineKeyboardMarkup(keyboard))
            return
        
        for job_doc in jobs:
            job = job_doc.to_dict()
            job_text = (
                f"📢 {job.get('title', 'وظيفة شاغرة')}\n"
                f"🏢 {job.get('company', 'غير محدد')}\n"
                f"📍 {job.get('city', 'غير محدد')}\n"
                f"📧 {job.get('email', 'غير محدد')}\n"
                f"👤 بواسطة: {job.get('posted_by', 'غير محدد')}\n"
                f"📅 النشر: {job.get('posted_at', 'غير محدد')[:10] if job.get('posted_at') else 'غير محدد'}\n"
            )
            
            job_keyboard = [
                [InlineKeyboardButton("✏️ تعديل", callback_data=f"edit_job_{job_doc.id}")],
                [InlineKeyboardButton("❌ حذف", callback_data=f"delete_job_{job_doc.id}")]
            ]
            
            await query.message.reply_text(job_text, reply_markup=InlineKeyboardMarkup(job_keyboard))
        
        await query.message.reply_text("🔙 نهاية قائمة الوظائف", reply_markup=InlineKeyboardMarkup(keyboard))
    except Exception as e:
        logger.error(f"❌ خطأ في عرض وظائف الأدمن: {e}")

async def admin_job_seekers(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """عرض الباحثين عن وظائف"""
    try:
        query = update.callback_query
        await query.answer()
        
        seekers_ref = db.collection('job_seekers').where('status', '==', 'active').limit(20)
        seekers = list(seekers_ref.stream())
        
        keyboard = [[InlineKeyboardButton("🔙 رجوع", callback_data="admin_panel")]]
        
        if not seekers:
            await query.message.reply_text("❌ لا يوجد باحثين", reply_markup=InlineKeyboardMarkup(keyboard))
            return
        
        for seeker_doc in seekers:
            seeker = seeker_doc.to_dict()
            seeker_text = (
                f"👤 الباحث: {seeker.get('user_id', 'غير محدد')}\n"
                f"📌 التخصص: {seeker.get('category', 'غير محدد')}\n"
                f"📍 الموقع: {seeker.get('city', 'غير محدد')}\n"
                f"🎯 المسمى: {seeker.get('title', 'غير محدد')}\n"
                f"📅 التاريخ: {seeker.get('created_at', 'غير محدد')[:10] if seeker.get('created_at') else 'غير محدد'}\n"
            )
            
            seeker_keyboard = [
                [InlineKeyboardButton("✏️ تعديل", callback_data=f"edit_seeker_{seeker_doc.id}")],
                [InlineKeyboardButton("❌ حذف", callback_data=f"delete_seeker_{seeker_doc.id}")]
            ]
            
            await query.message.reply_text(seeker_text, reply_markup=InlineKeyboardMarkup(seeker_keyboard))
        
        await query.message.reply_text("🔙 نهاية قائمة الباحثين", reply_markup=InlineKeyboardMarkup(keyboard))
    except Exception as e:
        logger.error(f"❌ خطأ في عرض الباحثين: {e}")

async def admin_stats(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """عرض إحصائيات البوت"""
    try:
        query = update.callback_query
        await query.answer()
        
        users = list(db.collection('users').stream())
        total_users = len(users)
        active_subscribers = sum(1 for u in users if u.to_dict().get('subscription') != 'free')
        
        jobs = list(db.collection('jobs').where('status', '==', 'active').stream())
        total_jobs = len(jobs)
        
        applications = list(db.collection('applications').stream())
        total_applications = len(applications)
        
        today = datetime.now().date().isoformat()
        today_apps = sum(1 for a in applications if a.to_dict().get('applied_at', '').startswith(today))
        
        stats_text = (
            f"📊 إحصائيات البوت\n\n"
            f"👤 إجمالي المستخدمين: {total_users}\n"
            f"💎 المشتركين المدفوعين: {active_subscribers}\n"
            f"🏢 الوظائف النشطة: {total_jobs}\n"
            f"📝 إجمالي التقديمات: {total_applications}\n"
            f"📅 تقديمات اليوم: {today_apps}\n\n"
            f"━━━━━━━━━━━━━━━\n"
            f"📈 معدل التقديمات اليومي: {today_apps/30 if today_apps > 0 else 0:.1f} تقديم/يوم"
        )
        
        keyboard = [[InlineKeyboardButton("🔙 رجوع", callback_data="admin_panel")]]
        await query.message.reply_text(stats_text, reply_markup=InlineKeyboardMarkup(keyboard))
    except Exception as e:
        logger.error(f"❌ خطأ في عرض الإحصائيات: {e}")

async def admin_change_password(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """تغيير كلمة مرور الأدمن"""
    query = update.callback_query
    await query.answer()
    await query.edit_message_text("🔑 أرسل كلمة المرور الجديدة:")
    return EDIT_FIELD

async def receive_new_password(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """استلام كلمة المرور الجديدة"""
    try:
        new_password = update.message.text
        
        if len(new_password) < 6:
            await update.message.reply_text("❌ كلمة المرور قصيرة جداً (الحد الأدنى 6 أحرف)")
            return EDIT_FIELD
        
        admin_password_ref = db.collection('config').document('admin_password')
        admin_password_ref.set({'password': new_password})
        
        await update.message.reply_text("✅ تم تغيير كلمة المرور بنجاح!")
        await show_admin_panel(update, context)
        return ConversationHandler.END
    except Exception as e:
        logger.error(f"❌ خطأ في تغيير كلمة المرور: {e}")
        return ConversationHandler.END

async def admin_ai_model(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """إدارة نموذج الذكاء الاصطناعي"""
    query = update.callback_query
    await query.answer()
    
    keyboard = [
        [InlineKeyboardButton("📊 عرض بيانات النموذج", callback_data="view_ai_model")],
        [InlineKeyboardButton("➕ إضافة بيانات", callback_data="add_ai_data")],
        [InlineKeyboardButton("🔄 إعادة تعيين", callback_data="reset_ai_model")],
        [InlineKeyboardButton("🔙 رجوع", callback_data="admin_panel")]
    ]
    
    await query.edit_message_text(
        "🤖 إعدادات نموذج الذكاء الاصطناعي\n\n"
        "يمكنك عرض بيانات النموذج الحالية أو إضافة بيانات جديدة.",
        reply_markup=InlineKeyboardMarkup(keyboard)
    )

async def view_ai_model(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """عرض بيانات نموذج الذكاء الاصطناعي"""
    try:
        query = update.callback_query
        await query.answer()
        
        ai_model_ref = db.collection('config').document('ai_model')
        ai_model = ai_model_ref.get()
        
        if not ai_model.exists:
            ai_model = load_json_file('artificial_intelligence_model.json')
            if not ai_model:
                await query.message.reply_text("❌ لا توجد بيانات للنموذج")
                return
        else:
            ai_model = ai_model.to_dict()
        
        ai_text = json.dumps(ai_model, indent=2, ensure_ascii=False)
        
        if len(ai_text) > 4000:
            parts = [ai_text[i:i+4000] for i in range(0, len(ai_text), 4000)]
            for part in parts:
                await query.message.reply_text(f"```json\n{part}\n```", parse_mode=ParseMode.MARKDOWN)
        else:
            await query.message.reply_text(f"```json\n{ai_text}\n```", parse_mode=ParseMode.MARKDOWN)
        
        keyboard = [[InlineKeyboardButton("🔙 رجوع", callback_data="admin_ai_model")]]
        await query.message.reply_text("🔙 نهاية بيانات النموذج", reply_markup=InlineKeyboardMarkup(keyboard))
    except Exception as e:
        logger.error(f"❌ خطأ في عرض نموذج AI: {e}")

async def add_ai_data_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """بدء إضافة بيانات لنموذج الذكاء الاصطناعي"""
    query = update.callback_query
    await query.answer()
    
    await query.edit_message_text(
        "➕ إضافة بيانات إلى نموذج الذكاء الاصطناعي\n\n"
        "يرجى إرسال البيانات بتنسيق JSON.\n\n"
        "📝 أمثلة:\n"
        "```json\n"
        "{\n"
        '  "city_synonyms": {\n'
        '    "جدة": ["جدة", "Jeddah"]\n'
        "  }\n"
        "}\n"
        "```",
        parse_mode=ParseMode.MARKDOWN
    )
    return ADD_MODEL_DATA

async def receive_ai_data(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """استلام بيانات نموذج الذكاء الاصطناعي"""
    try:
        data_text = update.message.text
        new_data = json.loads(data_text)
        
        ai_model_ref = db.collection('config').document('ai_model')
        current_data = ai_model_ref.get()
        
        if current_data.exists:
            current_dict = current_data.to_dict()
            for key, value in new_data.items():
                if key in current_dict:
                    if isinstance(current_dict[key], dict):
                        current_dict[key].update(value)
                    elif isinstance(current_dict[key], list):
                        current_dict[key].extend(value)
                    else:
                        current_dict[key] = value
                else:
                    current_dict[key] = value
        else:
            current_dict = new_data
        
        ai_model_ref.set(current_dict)
        await update.message.reply_text("✅ تم إضافة البيانات بنجاح!")
        
    except json.JSONDecodeError as e:
        await update.message.reply_text(f"❌ خطأ في تنسيق JSON: {str(e)}")
    except Exception as e:
        logger.error(f"❌ خطأ في إضافة بيانات AI: {e}")
        await update.message.reply_text("❌ حدث خطأ")
    
    return ConversationHandler.END

async def reset_ai_model(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """إعادة تعيين نموذج الذكاء الاصطناعي"""
    try:
        query = update.callback_query
        await query.answer()
        
        default_data = load_json_file('artificial_intelligence_model.json')
        
        if default_data:
            ai_model_ref = db.collection('config').document('ai_model')
            ai_model_ref.set(default_data)
            await query.message.reply_text("✅ تم إعادة تعيين نموذج الذكاء الاصطناعي!")
        else:
            await query.message.reply_text("❌ لم يتم العثور على ملف النموذج الافتراضي")
            
    except Exception as e:
        logger.error(f"❌ خطأ في إعادة تعيين نموذج AI: {e}")

async def add_points(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """إضافة نقاط للمستخدم"""
    query = update.callback_query
    await query.answer()
    
    user_id = query.data.split('_')[2]
    await query.edit_message_text("➕ أرسل عدد النقاط لإضافتها:")
    context.user_data['editing_user'] = user_id
    return EDIT_VALUE

async def receive_points(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """استلام عدد النقاط وإضافتها"""
    try:
        points = int(update.message.text)
        user_id = context.user_data.get('editing_user')
        
        if points <= 0:
            await update.message.reply_text("❌ يجب أن يكون العدد أكبر من صفر")
            return ConversationHandler.END
        
        user_ref = db.collection('users').document(user_id)
        user_data = user_ref.get()
        
        if user_data.exists:
            user_dict = user_data.to_dict()
            current_points = user_dict.get('points', 0)
            new_points = current_points + points
            
            user_ref.update({'points': new_points})
            await update.message.reply_text(f"✅ تم إضافة {points} نقاط\n🎯 الرصيد الجديد: {new_points}")
        else:
            await update.message.reply_text("❌ المستخدم غير موجود")
        
    except ValueError:
        await update.message.reply_text("❌ يرجى إرسال رقم صحيح")
    except Exception as e:
        logger.error(f"❌ خطأ في إضافة النقاط: {e}")
    
    return ConversationHandler.END

async def edit_job(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """تعديل وظيفة"""
    query = update.callback_query
    await query.answer()
    
    job_id = query.data.split('_')[2]
    await query.edit_message_text(
        "✏️ تعديل الوظيفة\n\n"
        "أرسل البيانات الجديدة بتنسيق:\n"
        "الحقل: القيمة الجديدة\n\n"
        "مثال:\n"
        "email: newemail@example.com"
    )
    context.user_data['editing_job'] = job_id
    return EDIT_VALUE

async def update_job(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """تحديث وظيفة"""
    try:
        update_text = update.message.text
        
        if ':' not in update_text:
            await update.message.reply_text("❌ تنسيق غير صحيح. استخدم: الحقل: القيمة")
            return EDIT_VALUE
        
        field, value = update_text.split(':', 1)
        field = field.strip()
        value = value.strip()
        
        job_id = context.user_data.get('editing_job')
        allowed_fields = ['title', 'company', 'city', 'country', 'description', 'email', 'category']
        
        if field not in allowed_fields:
            await update.message.reply_text(f"❌ الحقل '{field}' غير مسموح بتعديله")
            return EDIT_VALUE
        
        job_ref = db.collection('jobs').document(job_id)
        job_ref.update({field: value})
        
        await update.message.reply_text(f"✅ تم تحديث {field} بنجاح!")
        return ConversationHandler.END
        
    except Exception as e:
        logger.error(f"❌ خطأ في تحديث الوظيفة: {e}")
        return ConversationHandler.END

async def delete_job(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """حذف وظيفة"""
    try:
        query = update.callback_query
        await query.answer()
        
        job_id = query.data.split('_')[2]
        job_ref = db.collection('jobs').document(job_id)
        job_ref.update({'status': 'deleted'})
        
        await query.edit_message_text("✅ تم حذف الوظيفة بنجاح!")
    except Exception as e:
        logger.error(f"❌ خطأ في حذف الوظيفة: {e}")

async def close_admin(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """إغلاق قائمة الأدمن"""
    query = update.callback_query
    await query.answer()
    await query.edit_message_text("🔐 تم إغلاق قائمة الأدمن")
    await main_menu(update, context)

async def my_reminders(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """عرض تذكيرات المستخدم"""
    try:
        query = update.callback_query
        await query.answer()
        
        user_id = update.effective_user.id
        
        reminders_ref = db.collection('reminders').where('user_id', '==', user_id).where('status', '==', 'pending').limit(10)
        reminders = list(reminders_ref.stream())
        
        keyboard = [[InlineKeyboardButton("🔙 رجوع", callback_data="main_menu")]]
        
        if not reminders:
            await query.message.reply_text("🔔 لا توجد تذكيرات حالياً", reply_markup=InlineKeyboardMarkup(keyboard))
            return
        
        for reminder_doc in reminders:
            reminder = reminder_doc.to_dict()
            job_id = reminder.get('job_id', '')
            
            job_ref = db.collection('jobs').document(job_id)
            job = job_ref.get()
            job_title = job.to_dict().get('title', 'وظيفة') if job.exists else 'وظيفة محذوفة'
            
            await query.message.reply_text(f"🔔 تذكير: {job_title}\n📅 التاريخ: {reminder.get('remind_at', 'غير محدد')[:16]}")
        
        await query.message.reply_text("🔙 نهاية القائمة", reply_markup=InlineKeyboardMarkup(keyboard))
    except Exception as e:
        logger.error(f"❌ خطأ في عرض التذكيرات: {e}")

async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """معالجة الرسائل العادية"""
    await update.message.reply_text(
        "❓ عذراً، لم أفهم طلبك.\nيرجى استخدام الأزرار في القائمة الرئيسية.",
        reply_markup=InlineKeyboardMarkup([[
            InlineKeyboardButton("📊 القائمة الرئيسية", callback_data="main_menu")
        ]])
    )

async def error_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """معالجة الأخطاء"""
    logger.error(f"❌ خطأ: {context.error}")
    
    if update and update.effective_message:
        await update.effective_message.reply_text(
            "⚠️ حدث خطأ ما. يرجى المحاولة لاحقاً.\nإذا استمرت المشكلة، تواصل مع الدعم.",
            reply_markup=InlineKeyboardMarkup([[
                InlineKeyboardButton("🆘 الدعم", url=config.SUPPORT_LINK)
            ]])
        )

async def check_reminders(context: ContextTypes.DEFAULT_TYPE):
    """فحص التذكيرات وإرسالها"""
    try:
        now = datetime.now().isoformat()
        reminders_ref = db.collection('reminders').where('status', '==', 'pending').where('remind_at', '<=', now)
        reminders = list(reminders_ref.stream())
        
        for reminder_doc in reminders:
            reminder = reminder_doc.to_dict()
            user_id = reminder.get('user_id')
            job_id = reminder.get('job_id')
            
            job_ref = db.collection('jobs').document(job_id)
            job = job_ref.get()
            
            if job.exists:
                job_dict = job.to_dict()
                job_text = (
                    f"🔔 تذكير بالوظيفة:\n\n"
                    f"📢 {job_dict.get('title')}\n"
                    f"🏢 {job_dict.get('company')}\n"
                    f"📍 {job_dict.get('city')}\n"
                    f"📩 {job_dict.get('email')}"
                )
                
                keyboard = [[InlineKeyboardButton("🚀 قدم تلقائي", callback_data=f"auto_apply_{job_id}")]]
                await context.bot.send_message(user_id, job_text, reply_markup=InlineKeyboardMarkup(keyboard))
            
            reminder_doc.reference.update({'status': 'sent'})
            
    except Exception as e:
        logger.error(f"❌ خطأ في فحص التذكيرات: {e}")
        # ==================== الدالة الرئيسية ====================

def main():
    """تشغيل البوت"""
    # تهيئة قاعدة البيانات
    init_firebase_data()
    
    # إنشاء التطبيق
    application = Application.builder().token(config.TOKEN).build()
    
    # إضافة مهمة دورية لفحص التذكيرات
    job_queue = application.job_queue
    if job_queue:
        job_queue.run_repeating(check_reminders, interval=3600, first=10)
    
    # ==================== إنشاء محادثات ====================
    
    # محادثة رفع الوظيفة (باحث عن موظف)
    post_job_conv = ConversationHandler(
        entry_points=[CallbackQueryHandler(post_job_start, pattern='^post_job$')],
        states={
            JOB_TITLE: [MessageHandler(filters.TEXT & ~filters.COMMAND, get_job_title)],
            COMPANY_NAME: [MessageHandler(filters.TEXT & ~filters.COMMAND, get_company_name)],
            COUNTRY: [MessageHandler(filters.TEXT & ~filters.COMMAND, get_country)],
            CITY: [MessageHandler(filters.TEXT & ~filters.COMMAND, get_city)],
            JOB_DESCRIPTION: [MessageHandler(filters.TEXT & ~filters.COMMAND, get_job_description)],
            JOB_EMAIL: [MessageHandler(filters.TEXT & ~filters.COMMAND, get_job_email)],
        },
        fallbacks=[CallbackQueryHandler(main_menu, pattern='^main_menu$')],
    )
    
    # محادثة التقديم على وظيفة (باحث عن عمل)
    apply_job_conv = ConversationHandler(
        entry_points=[CallbackQueryHandler(apply_job_start, pattern='^apply_job$')],
        states={
            APPLY_JOB_TITLE: [CallbackQueryHandler(select_apply_category, pattern='^apply_category_')],
            APPLY_COMPANY: [MessageHandler(filters.TEXT & ~filters.COMMAND, get_apply_company)],
            APPLY_COUNTRY: [MessageHandler(filters.TEXT & ~filters.COMMAND, get_apply_country)],
            APPLY_CITY: [MessageHandler(filters.TEXT & ~filters.COMMAND, get_apply_city)],
            APPLY_SKILLS: [MessageHandler(filters.TEXT & ~filters.COMMAND, get_apply_skills)],
        },
        fallbacks=[CallbackQueryHandler(main_menu, pattern='^main_menu$')],
    )
    
    # محادثة رفع السيرة الذاتية
    blast_cv_conv = ConversationHandler(
        entry_points=[CallbackQueryHandler(blast_cv, pattern='^blast_cv$')],
        states={CV: [MessageHandler(filters.Document.ALL, receive_cv)]},
        fallbacks=[CallbackQueryHandler(main_menu, pattern='^main_menu$')],
    )
    
    # محادثة تعيين الإيميل
    email_conv = ConversationHandler(
        entry_points=[CallbackQueryHandler(set_email, pattern='^set_email$')],
        states={EMAIL: [MessageHandler(filters.TEXT & ~filters.COMMAND, receive_email)]},
        fallbacks=[CallbackQueryHandler(settings_menu, pattern='^settings$')],
    )
    
    # محادثة تعيين كلمة المرور
    password_conv = ConversationHandler(
        entry_points=[CallbackQueryHandler(set_password, pattern='^set_password$')],
        states={PASSWORD: [MessageHandler(filters.TEXT & ~filters.COMMAND, receive_password)]},
        fallbacks=[CallbackQueryHandler(settings_menu, pattern='^settings$')],
    )
    
    # محادثة تعيين اسم المستخدم
    username_conv = ConversationHandler(
        entry_points=[CallbackQueryHandler(set_username, pattern='^set_username$')],
        states={USERNAME: [MessageHandler(filters.TEXT & ~filters.COMMAND, receive_username)]},
        fallbacks=[CallbackQueryHandler(settings_menu, pattern='^settings$')],
    )
    
    # محادثة تسجيل دخول الأدمن
    admin_login_conv = ConversationHandler(
        entry_points=[CallbackQueryHandler(admin_login, pattern='^admin_login$')],
        states={ADMIN_PASSWORD: [MessageHandler(filters.TEXT & ~filters.COMMAND, check_admin_password)]},
        fallbacks=[CallbackQueryHandler(main_menu, pattern='^main_menu$')],
    )
    
    # محادثة تغيير كلمة مرور الأدمن
    admin_password_conv = ConversationHandler(
        entry_points=[CallbackQueryHandler(admin_change_password, pattern='^admin_change_password$')],
        states={EDIT_FIELD: [MessageHandler(filters.TEXT & ~filters.COMMAND, receive_new_password)]},
        fallbacks=[CallbackQueryHandler(show_admin_panel, pattern='^admin_panel$')],
    )
    
    # محادثة إضافة نقاط
    add_points_conv = ConversationHandler(
        entry_points=[CallbackQueryHandler(add_points, pattern='^add_points_')],
        states={EDIT_VALUE: [MessageHandler(filters.TEXT & ~filters.COMMAND, receive_points)]},
        fallbacks=[CallbackQueryHandler(admin_users, pattern='^admin_users$')],
    )
    
    # محادثة تعديل وظيفة
    edit_job_conv = ConversationHandler(
        entry_points=[CallbackQueryHandler(edit_job, pattern='^edit_job_')],
        states={EDIT_VALUE: [MessageHandler(filters.TEXT & ~filters.COMMAND, update_job)]},
        fallbacks=[CallbackQueryHandler(admin_jobs, pattern='^admin_jobs$')],
    )
    
    # محادثة إضافة بيانات AI
    ai_model_conv = ConversationHandler(
        entry_points=[CallbackQueryHandler(add_ai_data_start, pattern='^add_ai_data$')],
        states={ADD_MODEL_DATA: [MessageHandler(filters.TEXT & ~filters.COMMAND, receive_ai_data)]},
        fallbacks=[CallbackQueryHandler(admin_ai_model, pattern='^admin_ai_model$')],
    )
    
    # ==================== إضافة معالجات الأوامر ====================
    application.add_handler(CommandHandler("start", start))
    application.add_handler(CommandHandler("jobs", jobs_menu))
    application.add_handler(CommandHandler("applications", my_applications))
    application.add_handler(CommandHandler("settings", settings_menu))
    application.add_handler(CommandHandler("subscriptions", subscriptions))
    application.add_handler(CommandHandler("support", support))
    application.add_handler(CommandHandler("unsubscribe", unsubscribe))
    
    # إضافة المحادثات
    application.add_handler(post_job_conv)
    application.add_handler(apply_job_conv)
    application.add_handler(blast_cv_conv)
    application.add_handler(email_conv)
    application.add_handler(password_conv)
    application.add_handler(username_conv)
    application.add_handler(admin_login_conv)
    application.add_handler(admin_password_conv)
    application.add_handler(add_points_conv)
    application.add_handler(edit_job_conv)
    application.add_handler(ai_model_conv)
    
    # ==================== إضافة معالجات الأزرار ====================
    application.add_handler(CallbackQueryHandler(main_menu, pattern='^main_menu$'))
    application.add_handler(CallbackQueryHandler(jobs_menu, pattern='^jobs_menu$'))
    application.add_handler(CallbackQueryHandler(show_jobs, pattern='^all_jobs$'))
    application.add_handler(CallbackQueryHandler(show_jobs, pattern='^matched_jobs$'))
    application.add_handler(CallbackQueryHandler(auto_apply, pattern='^auto_apply_'))
    application.add_handler(CallbackQueryHandler(remind_job, pattern='^remind_'))
    application.add_handler(CallbackQueryHandler(my_applications, pattern='^my_applications$'))
    application.add_handler(CallbackQueryHandler(my_reminders, pattern='^my_reminders$'))
    application.add_handler(CallbackQueryHandler(settings_menu, pattern='^settings$'))
    application.add_handler(CallbackQueryHandler(share_link, pattern='^share_link$'))
    application.add_handler(CallbackQueryHandler(subscriptions, pattern='^subscriptions$'))
    application.add_handler(CallbackQueryHandler(subscribe, pattern='^subscribe_'))
    application.add_handler(CallbackQueryHandler(support, pattern='^support$'))
    application.add_handler(CallbackQueryHandler(unsubscribe, pattern='^unsubscribe$'))
    
    # معالجات الأدمن
    application.add_handler(CallbackQueryHandler(show_admin_panel, pattern='^admin_panel$'))
    application.add_handler(CallbackQueryHandler(admin_users, pattern='^admin_users$'))
    application.add_handler(CallbackQueryHandler(admin_jobs, pattern='^admin_jobs$'))
    application.add_handler(CallbackQueryHandler(admin_job_seekers, pattern='^admin_job_seekers$'))
    application.add_handler(CallbackQueryHandler(admin_stats, pattern='^admin_stats$'))
    application.add_handler(CallbackQueryHandler(admin_ai_model, pattern='^admin_ai_model$'))
    application.add_handler(CallbackQueryHandler(view_ai_model, pattern='^view_ai_model$'))
    application.add_handler(CallbackQueryHandler(close_admin, pattern='^close_admin$'))
    application.add_handler(CallbackQueryHandler(delete_job, pattern='^delete_job_'))
    application.add_handler(CallbackQueryHandler(reset_ai_model, pattern='^reset_ai_model$'))
    
    # معالجات عامة
    application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))
    application.add_error_handler(error_handler)
    
    # تشغيل البوت
    logger.info("✅ تم تشغيل البوت بنجاح!")
    application.run_polling()

if __name__ == '__main__':
    main()