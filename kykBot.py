import json
import sqlite3
import datetime
import logging
import os
import asyncio
import tempfile
import urllib.request
from pathlib import Path
from urllib.parse import urlsplit
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.error import Forbidden, TelegramError
from telegram.ext import (
    Application,
    CommandHandler,
    CallbackQueryHandler,
    ContextTypes
)

logging.basicConfig(format='%(asctime)s - %(levelname)s - %(message)s', level=logging.WARNING)
TZ_TR = datetime.timezone(datetime.timedelta(hours=3))

def ayar_getir(anahtar):
    deger = os.environ.get(anahtar)
    if deger:
        return deger

    env_dosyasi = Path(__file__).with_name('.env')
    if env_dosyasi.exists():
        for satir in env_dosyasi.read_text(encoding='utf-8').splitlines():
            satir_anahtari, ayirac, deger = satir.partition('=')
            if ayirac and satir_anahtari.strip() == anahtar and deger.strip():
                return deger.strip()
    return None

TOKEN = ayar_getir('KYK_BOT_TOKEN')
if not TOKEN:
    raise RuntimeError('Bot anahtarı bulunamadı. .env dosyasına KYK_BOT_TOKEN=... satırını ekleyin.')

try:
    ADMIN_ID = int(ayar_getir('ADMIN_ID'))
    if ADMIN_ID <= 0:
        raise ValueError
except (TypeError, ValueError):
    raise RuntimeError('.env dosyasına geçerli bir ADMIN_ID=... satırı ekleyin.') from None

MENU_URL = ayar_getir('KYK_MENU_URL')
MENU_DOSYASI = Path(__file__).with_name('kyk_yemek.json')

# Menünün her gün 14:00'da atılacağı Telegram kanalının ID'si veya kullanıcı adı (Örn: "@kanal_kullanici_adi" veya -1001234567890)
KANAL_ID = "@kykyemek"

# --- VERİTABANI İŞLEMLERİ ---

def veritabani_kur():
    conn = sqlite3.connect('kullanicilar.db')
    cursor = conn.cursor()
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS users (
            chat_id INTEGER,
            hedef_saat TEXT,
            menu_tipi TEXT,
            PRIMARY KEY (chat_id, menu_tipi)
        )
    ''')
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS tum_kullanicilar (
            chat_id INTEGER PRIMARY KEY,
            kayit_tarihi TEXT
        )
    ''')
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS gunluk_istatistik (
            tarih TEXT,
            komut TEXT,
            sayi INTEGER DEFAULT 0,
            PRIMARY KEY (tarih, komut)
        )
    ''')
    cursor.execute('''
        INSERT OR IGNORE INTO tum_kullanicilar (chat_id, kayit_tarihi)
        SELECT DISTINCT chat_id, 'gecmis_kayit' FROM users
    ''')
    conn.commit()
    conn.close()

def kullanici_kaydet(chat_id: int):
    conn = sqlite3.connect('kullanicilar.db')
    cursor = conn.cursor()
    su_an = datetime.datetime.now(TZ_TR).strftime('%Y-%m-%d %H:%M:%S')
    cursor.execute('''
        INSERT OR IGNORE INTO tum_kullanicilar (chat_id, kayit_tarihi)
        VALUES (?, ?)
    ''', (chat_id, su_an))
    conn.commit()
    conn.close()

def istatistik_artir(komut: str):
    conn = sqlite3.connect('kullanicilar.db')
    cursor = conn.cursor()
    bugun_tarih = datetime.datetime.now(TZ_TR).strftime('%Y-%m-%d')
    
    cursor.execute('''
        INSERT INTO gunluk_istatistik (tarih, komut, sayi)
        VALUES (?, ?, 1)
        ON CONFLICT(tarih, komut) DO UPDATE SET sayi = sayi + 1
    ''', (bugun_tarih, komut))
    conn.commit()
    conn.close()

def kullanici_sil_engelli(chat_id: int):
    conn = sqlite3.connect('kullanicilar.db')
    cursor = conn.cursor()
    cursor.execute("DELETE FROM users WHERE chat_id=?", (chat_id,))
    cursor.execute("DELETE FROM tum_kullanicilar WHERE chat_id=?", (chat_id,))
    conn.commit()
    conn.close()

# --- MENÜ YARDIMCISI ---

def menu_getir(gun_farki=0):
    try:
        with MENU_DOSYASI.open('r', encoding='utf-8') as dosya:
            yemekler = json.load(dosya)
        
        hedef_tarih = (datetime.datetime.now(TZ_TR).date() + datetime.timedelta(days=gun_farki)).strftime('%m-%d')
        return yemekler.get(hedef_tarih, None)
    except FileNotFoundError:
        logging.error("kyk_yemek.json dosyası bulunamadı.")
        return None
    except json.JSONDecodeError:
        logging.error("JSON dosyası hatalı formata sahip.")
        return None

def menu_dosyasi_guncelle():
    if not MENU_URL:
        raise ValueError('KYK_MENU_URL ayarlanmamış.')

    adres = urlsplit(MENU_URL)
    if adres.scheme != 'https' or adres.hostname != 'raw.githubusercontent.com':
        raise ValueError('KYK_MENU_URL, GitHub dosyasının raw.githubusercontent.com adresi olmalı.')

    istek = urllib.request.Request(MENU_URL, headers={
        'User-Agent': 'KYKMenuBot/1.0',
        'Cache-Control': 'no-cache',
    })
    with urllib.request.urlopen(istek, timeout=15) as yanit:
        icerik = yanit.read(1_000_001)
    if len(icerik) > 1_000_000:
        raise ValueError('GitHub menü dosyası 1 MB sınırını aşıyor.')

    icerik = icerik.decode('utf-8-sig').encode('utf-8')
    yemekler = json.loads(icerik)
    if not isinstance(yemekler, dict) or not yemekler:
        raise ValueError('GitHub menü dosyası boş veya beklenen JSON biçiminde değil.')
    for gun, menu in yemekler.items():
        try:
            ay, gun_numarasi = map(int, gun.split('-'))
            datetime.date(2000, ay, gun_numarasi)
        except (ValueError, AttributeError):
            raise ValueError(f'Geçersiz menü tarihi: {gun!r}') from None
        if gun != f'{ay:02d}-{gun_numarasi:02d}' or not isinstance(menu, str) or not menu.strip():
            raise ValueError(f'Geçersiz menü kaydı: {gun!r}')

    bu_ay = datetime.datetime.now(TZ_TR).strftime('%m-')
    if not any(gun.startswith(bu_ay) for gun in yemekler):
        raise ValueError('GitHub dosyasında bu aya ait menü yok; mevcut dosya korundu.')

    gecici_dosya = None
    try:
        with tempfile.NamedTemporaryFile(dir=MENU_DOSYASI.parent, prefix='kyk_yemek_',
                                         suffix='.tmp', delete=False) as dosya:
            gecici_dosya = Path(dosya.name)
            dosya.write(icerik)
        os.replace(gecici_dosya, MENU_DOSYASI)
    finally:
        if gecici_dosya and gecici_dosya.exists():
            gecici_dosya.unlink()
    return len(yemekler)

async def gunluk_menu_guncelle(context: ContextTypes.DEFAULT_TYPE):
    try:
        adet = await asyncio.to_thread(menu_dosyasi_guncelle)
        logging.warning('GitHub menüsü güncellendi: %s gün.', adet)
    except Exception as hata:
        logging.error('GitHub menüsü güncellenemedi: %s', hata)

# --- KULLANICI KOMUTLARI ---

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.effective_chat.id
    kullanici_kaydet(chat_id)

    mesaj = (
        "KYK Yemek Botu komutları:\n\n"
        "Anlık Menü:\n"
        "/bugun - Bugünün menüsünü gösterir.\n"
        "/yarin - Yarının menüsünü gösterir.\n\n"
        "Otomatik Bildirim:\n"
        "/saat [saat] - Her gün belirlediğin saatte BUGÜNÜN menüsünü gönderir.\n"
        "/yarinsaat [saat] - Her gün belirlediğin saatte YARININ menüsünü gönderir.\n"
        "/ayar - Hazır saat butonları ile seçim yapın.\n"
        "/durum - Aktif bildirim ayarlarınızı listeler.\n\n"
        "İptal İşlemi:\n"
        "/iptal - Otomatik mesaj gönderimini tamamen durdurur.\n\n"
    )
    await update.message.reply_text(mesaj, parse_mode='Markdown', disable_web_page_preview=True)

async def iptal(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.effective_chat.id
    kullanici_kaydet(chat_id)
    conn = sqlite3.connect('kullanicilar.db')
    cursor = conn.cursor()
    cursor.execute("DELETE FROM users WHERE chat_id=?", (chat_id,))
    conn.commit()
    conn.close()
    await update.message.reply_text("🛑 Tüm bildirimler iptal edildi.")

async def bugun_goster(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.effective_chat.id
    kullanici_kaydet(chat_id)
    istatistik_artir("bugun")

    menu = menu_getir(0)
    if not menu:
        await update.message.reply_text("⚠️ Bugün için henüz menü kaydı girilmemiştir.")
        return

    su_an_saat = datetime.datetime.now(TZ_TR).strftime('%H:%M')
    keyboard = [
        [InlineKeyboardButton(f"🔔 Her Gün Bu Saatte Bildir ({su_an_saat})", callback_data=f'set_bugun_{su_an_saat}')],
        [InlineKeyboardButton("⚙️ Başka Saat Seç", callback_data='panel_ayar_ac')]
    ]
    reply_markup = InlineKeyboardMarkup(keyboard)

    await update.message.reply_text(
        f"🍽️ Bugünün Menüsü:\n{menu}",
        reply_markup=reply_markup
    )

async def yarin_goster(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.effective_chat.id
    kullanici_kaydet(chat_id)
    istatistik_artir("yarin")

    menu = menu_getir(1)
    if not menu:
        await update.message.reply_text("⚠️ Yarın için henüz menü kaydı girilmemiştir.")
        return

    keyboard = [
        [
            InlineKeyboardButton("Yarın 16:00", callback_data='set_yarin_16:00'),
            InlineKeyboardButton("Yarın 18:00", callback_data='set_yarin_18:00'),
        ],
        [
            InlineKeyboardButton("Yarın 20:00", callback_data='set_yarin_20:00'),
            InlineKeyboardButton("Yarın 22:00", callback_data='set_yarin_22:00'),
        ],
    ]
    reply_markup = InlineKeyboardMarkup(keyboard)

    await update.message.reply_text(
        f"🍽️ Yarının Menüsü:\n{menu}",
        reply_markup=reply_markup
    )

async def saat_kaydet(update: Update, context: ContextTypes.DEFAULT_TYPE, menu_tipi: str):
    chat_id = update.effective_chat.id
    kullanici_kaydet(chat_id)
    try:
        if not context.args:
            raise ValueError
            
        ham_girdi = " ".join(context.args)
        formatli_girdi = ham_girdi.replace('.', ':').replace(' ', ':')
        standart_saat = datetime.datetime.strptime(formatli_girdi, '%H:%M').strftime('%H:%M')
        
        conn = sqlite3.connect('kullanicilar.db')
        cursor = conn.cursor()
        cursor.execute('''
            REPLACE INTO users (chat_id, hedef_saat, menu_tipi)
            VALUES (?, ?, ?)
        ''', (chat_id, standart_saat, menu_tipi))
        conn.commit()
        conn.close()

        tip_metni = "Günün" if menu_tipi == 'bugun' else "Sonraki günün"
        await update.message.reply_text(f"✅ Tamamdır! {tip_metni} menüsü her gün saat {standart_saat}'da sana gönderilecek.")
    except ValueError:
        await update.message.reply_text("❌ Lütfen geçerli bir saat girin. Örnek: 17:00, 17.00 veya 17 00")

async def saat_bugun(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await saat_kaydet(update, context, 'bugun')

async def saat_yarin(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await saat_kaydet(update, context, 'yarin')

async def durum_goster(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.effective_chat.id
    kullanici_kaydet(chat_id)

    conn = sqlite3.connect('kullanicilar.db')
    cursor = conn.cursor()
    cursor.execute("SELECT hedef_saat, menu_tipi FROM users WHERE chat_id=?", (chat_id,))
    kayitlar = cursor.fetchall()
    conn.close()

    if not kayitlar:
        await update.message.reply_text(
            "ℹ️ Şu anda aktif bir bildirim saatiniz bulunmuyor.\n"
            "Ayarlamak için `/saat 17:00` veya `/ayar` komutunu kullanabilirsiniz.",
            parse_mode='Markdown'
        )
        return

    metin = "🔔 **Aktif Bildirim Ayarlarınız:**\n\n"
    for saat, menu_tipi in kayitlar:
        tip_adi = "Bugünün Menüsü" if menu_tipi == 'bugun' else "Yarının Menüsü"
        metin += f"• Her gün saat **{saat}** -> {tip_adi}\n"

    metin += "\nİptal etmek isterseniz `/iptal` yazabilirsiniz."
    await update.message.reply_text(metin, parse_mode='Markdown')

async def menu_sec(update: Update, context: ContextTypes.DEFAULT_TYPE):
    kullanici_kaydet(update.effective_chat.id)
    keyboard = [
        [
            InlineKeyboardButton("Bugün 10:00", callback_data='set_bugun_10:00'),
            InlineKeyboardButton("Bugün 12:00", callback_data='set_bugun_12:00'),
            InlineKeyboardButton("Bugün 14:00", callback_data='set_bugun_14:00'),
        ],
        [
            InlineKeyboardButton("Bugün 16:00", callback_data='set_bugun_16:00'),
            InlineKeyboardButton("Bugün 18:00", callback_data='set_bugun_18:00'),
        ],
    ]
    reply_markup = InlineKeyboardMarkup(keyboard)
    await update.message.reply_text('Lütfen bir bildirim saati seçin:', reply_markup=reply_markup)

# --- ADMİN PANELİ VE YÖNETİM ---

def ascii_tablo_olustur(satirlar):
    col1_w = max(len("Metrik"), max((len(str(s[0])) for s in satirlar), default=0)) + 2
    col2_w = max(len("Adet"), max((len(str(s[1])) for s in satirlar), default=0)) + 2

    cizgi = f"+{'-' * col1_w}+{'-' * col2_w}+"
    baslik = f"| {'Metrik':<{col1_w-2}} | {'Adet':>{col2_w-2}} |"

    cikti = [cizgi, baslik, cizgi]
    for m, d in satirlar:
        cikti.append(f"| {str(m):<{col1_w-2}} | {str(d):>{col2_w-2}} |")
    cikti.append(cizgi)
    return "\n".join(cikti)

async def admin_panel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id != ADMIN_ID:
        await update.message.reply_text("⛔ Bu komutu kullanma yetkiniz yok.")
        return

    conn = sqlite3.connect('kullanicilar.db')
    cursor = conn.cursor()

    cursor.execute("SELECT COUNT(*) FROM tum_kullanicilar")
    toplam_tekil = cursor.fetchone()[0]

    cursor.execute("SELECT COUNT(*) FROM users")
    toplam_alarm = cursor.fetchone()[0]

    cursor.execute("SELECT SUM(sayi) FROM gunluk_istatistik WHERE komut='bugun'")
    row_b = cursor.fetchone()
    bugun_sayi = row_b[0] if (row_b and row_b[0] is not None) else 0

    cursor.execute("SELECT SUM(sayi) FROM gunluk_istatistik WHERE komut='yarin'")
    row_y = cursor.fetchone()
    yarin_sayi = row_y[0] if (row_y and row_y[0] is not None) else 0

    cursor.execute("SELECT hedef_saat, COUNT(*) FROM users GROUP BY hedef_saat ORDER BY COUNT(*) DESC")
    saat_dagilimi = cursor.fetchall()
    conn.close()

    tablo_verisi = [
        ("Toplam Tekil Kisi", toplam_tekil),
        ("Aktif Alarm Kaydi", toplam_alarm),
        ("/bugun Sayaci", bugun_sayi),
        ("/yarin Sayaci", yarin_sayi),
    ]
    ascii_tablo = ascii_tablo_olustur(tablo_verisi)

    metin = f"📊 **Sistem İstatistikleri:**\n\n```text\n{ascii_tablo}\n```\n"
    if saat_dagilimi:
        metin += "**Saat Bazlı Dağılım:**\n"
        for s, kisi in saat_dagilimi:
            metin += f"• `{s}` -> {kisi} kişi\n"

    if update.message:
        await update.message.reply_text(metin, parse_mode='Markdown')
    elif update.callback_query:
        await update.callback_query.edit_message_text(metin, parse_mode='Markdown')

async def duyuru(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id != ADMIN_ID:
        await update.message.reply_text("⛔ Bu komutu kullanma yetkiniz yok.")
        return

    if not context.args:
        await update.message.reply_text("❌ Lütfen duyuru metni girin.\nÖrnek: `/duyuru Menü sistemi güncellenmiştir.`", parse_mode='Markdown')
        return

    duyuru_metni = " ".join(context.args)

    conn = sqlite3.connect('kullanicilar.db')
    cursor = conn.cursor()
    cursor.execute("SELECT chat_id FROM tum_kullanicilar")
    kullanicilar = cursor.fetchall()
    conn.close()

    basarili, engelli = 0, 0
    for (cid,) in kullanicilar:
        try:
            await context.bot.send_message(chat_id=cid, text=f"📢 **Duyuru:**\n\n{duyuru_metni}", parse_mode='Markdown')
            basarili += 1
        except Forbidden:
            kullanici_sil_engelli(cid)
            engelli += 1
        except TelegramError:
            pass

    await update.message.reply_text(
        f"📢 **Duyuru Sonucu:**\n\n"
        f"✅ Başarılı: {basarili}\n"
        f"🚫 Botu Engellemiş (Temizlendi): {engelli}"
    )

async def menu_guncelle_komutu(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id != ADMIN_ID:
        await update.message.reply_text('⛔ Bu komutu kullanma yetkiniz yok.')
        return
    try:
        adet = await asyncio.to_thread(menu_dosyasi_guncelle)
        await update.message.reply_text(f'✅ Menü GitHub’dan güncellendi: {adet} gün.')
    except Exception as hata:
        logging.error('Elle menü güncellemesi başarısız: %s', hata)
        await update.message.reply_text(f'⚠️ Menü güncellenemedi: {hata}\nEski menü dosyası korundu.')

# --- CALLBACK BUTON YÖNETİMİ ---

async def button_click(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    data = query.data
    chat_id = query.message.chat_id

    # 1. Hazır saat seçimi veya doğrudan saat kurulumu
    if data.startswith('set_'):
        _, tip, saat = data.split('_')
        conn = sqlite3.connect('kullanicilar.db')
        cursor = conn.cursor()
        cursor.execute('''
            REPLACE INTO users (chat_id, hedef_saat, menu_tipi)
            VALUES (?, ?, ?)
        ''', (chat_id, saat, tip))
        conn.commit()
        conn.close()

        tip_adi = "Bugünün" if tip == 'bugun' else "Yarının"
        await query.edit_message_text(text=f"✅ Harika! {tip_adi} menüsü her gün saat {saat}'da iletilecektir.")

    # 2. /bugun altındaki ayar aç butonu
    elif data == 'panel_ayar_ac':
        keyboard = [
            [
                InlineKeyboardButton("Bugün 10:00", callback_data='set_bugun_10:00'),
                InlineKeyboardButton("Bugün 12:00", callback_data='set_bugun_12:00'),
                InlineKeyboardButton("Bugün 14:00", callback_data='set_bugun_14:00'),
            ],
            [
                InlineKeyboardButton("Bugün 16:00", callback_data='set_bugun_16:00'),
                InlineKeyboardButton("Bugün 18:00", callback_data='set_bugun_18:00'),
            ],
        ]
        await query.edit_message_text('Lütfen bir bildirim saati seçin:', reply_markup=InlineKeyboardMarkup(keyboard))

# --- ZAMANLAYICI KONTROLÜ ---

async def zamanlayici_kontrol(context: ContextTypes.DEFAULT_TYPE):
    su_an = datetime.datetime.now(TZ_TR).strftime('%H:%M')
    
    menu_bugun = menu_getir(0)
    menu_yarin = menu_getir(1)

    # 1. Sabit Telegram Kanalına Saat 14:00'da Günün Menüsünü Gönder
    if su_an == "14:00" and KANAL_ID:
        if menu_bugun:
            try:
                await context.bot.send_message(chat_id=KANAL_ID, text=f"🍽️ Günün Menüsü:\n{menu_bugun}")
            except Exception as e:
                logging.error(f"Kanal mesajı gönderilemedi: {e}")

    # 2. Bireysel Kullanıcı Bildirimleri
    conn = sqlite3.connect('kullanicilar.db')
    cursor = conn.cursor()
    cursor.execute("SELECT chat_id, menu_tipi FROM users WHERE hedef_saat=?", (su_an,))
    kullanicilar = cursor.fetchall()
    conn.close()

    if kullanicilar:
        for chat_id, menu_tipi in kullanicilar:
            gonderilecek_menu = menu_yarin if menu_tipi == 'yarin' else menu_bugun
            
            # Eğer ilgili günün menüsü yoksa kullanıcıya mesaj atma
            if not gonderilecek_menu:
                continue

            try:
                baslik = "🍽️ Yarının Menüsü:" if menu_tipi == 'yarin' else "🍽️ Günün Menüsü:"
                await context.bot.send_message(chat_id=chat_id, text=f"{baslik}\n{gonderilecek_menu}")
            except Forbidden:
                logging.warning(f"{chat_id} ID'li kullanıcı botu engellediği için veritabanından silindi.")
                kullanici_sil_engelli(chat_id)
            except Exception as e:
                logging.error(f"{chat_id} ID'li kullanıcıya mesaj gönderilemedi: {e}")

# --- ANA PROGRAM ---

def main():
    veritabani_kur()
    app = Application.builder().token(TOKEN).build()

    # Kullanıcı komutları
    app.add_handler(CommandHandler(["start", "help"], start))
    app.add_handler(CommandHandler("bugun", bugun_goster))
    app.add_handler(CommandHandler("yarin", yarin_goster))
    app.add_handler(CommandHandler("saat", saat_bugun))
    app.add_handler(CommandHandler("yarinsaat", saat_yarin))
    app.add_handler(CommandHandler("durum", durum_goster))
    app.add_handler(CommandHandler("ayar", menu_sec))
    app.add_handler(CommandHandler("iptal", iptal))

    # Admin komutları
    app.add_handler(CommandHandler("admin", admin_panel))
    app.add_handler(CommandHandler("duyuru", duyuru))
    app.add_handler(CommandHandler("guncelle", menu_guncelle_komutu))

    # Buton dinleyicisi
    app.add_handler(CallbackQueryHandler(button_click))

    # Zamanlayıcı (Dakikada 1 kez kontrol eder)
    job_queue = app.job_queue
    job_queue.run_repeating(zamanlayici_kontrol, interval=60, first=5)
    if MENU_URL:
        job_queue.run_daily(gunluk_menu_guncelle,
                            time=datetime.time(hour=6, minute=0, tzinfo=TZ_TR))
        job_queue.run_once(gunluk_menu_guncelle, when=10)

    print("Bot başarıyla çalıştırıldı ve mesaj bekliyor...")
    app.run_polling()

if __name__ == '__main__':
    main()
