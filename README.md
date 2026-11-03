# KYK Yemek Botu

KYK yemek menüsünü Telegram üzerinden görüntülemeyi ve günlük bildirim almayı sağlayan bir bottur.

Kullanıcılar bugünün veya yarının menüsünü görüntüleyebilir, istedikleri saate otomatik bildirim kurabilir ve mevcut bildirimlerini yönetebilir. Menü listesi GitHub üzerinde tutulan JSON dosyasından otomatik olarak güncellenir.

> Bu proje bağımsız olarak geliştirilmiştir. KYK veya T.C. Gençlik ve Spor Bakanlığı ile resmî bir bağlantısı yoktur.

## Özellikler

- Bugünün yemek menüsünü gösterme
- Yarının yemek menüsünü gösterme
- Kullanıcıya özel bildirim saati belirleme
- Bugünün ve yarının menüsü için ayrı bildirim oluşturma
- Hazır saat butonlarıyla kolay bildirim ayarlama
- Kullanıcı tercihlerini SQLite veritabanında saklama
- Menü listesini GitHub üzerinden otomatik güncelleme
- Admin komutuyla menüyü anında yenileme
- Hatalı veya eksik JSON indirildiğinde mevcut listeyi koruma
- Botu engelleyen kullanıcıları veritabanından temizleme
- Yönetici için temel kullanım istatistikleri
- Belirlenen Telegram kanalına günlük menü gönderme

## Kullanılan Teknolojiler

- Python 3.11
- [python-telegram-bot](https://python-telegram-bot.org/)
- Telegram Bot API
- SQLite
- GitHub Raw Content

## Bot Komutları

| Komut | Açıklama |
|---|---|
| `/start` | Botun komutlarını gösterir |
| `/help` | Yardım mesajını gösterir |
| `/bugun` | Bugünün yemek menüsünü gösterir |
| `/yarin` | Yarının yemek menüsünü gösterir |
| `/saat 17:00` | Bugünün menüsü için günlük bildirim kurar |
| `/yarinsaat 17:00` | Yarının menüsü için günlük bildirim kurar |
| `/ayar` | Hazır bildirim saatlerini gösterir |
| `/durum` | Aktif bildirim ayarlarını listeler |
| `/iptal` | Kullanıcının bütün bildirimlerini iptal eder |
| `/guncelle` | Menü listesini GitHub’dan hemen günceller |
| `/admin` | Botun kullanım istatistiklerini gösterir |
| `/duyuru mesaj` | Kayıtlı kullanıcılara duyuru gönderir |

`/guncelle`, `/admin` ve `/duyuru` komutları yalnızca bot yöneticisi tarafından kullanılabilir.

## Kurulum

### 1. Projeyi indirin

```bash
git clone https://github.com/Syrblo/telegram-kyk-menu-botu.git
cd telegram-kyk-menu-botu
```

### 2. Sanal ortam oluşturun

```bash
python -m venv .venv
```

Linux üzerinde sanal ortamı etkinleştirin:

```bash
source .venv/bin/activate
```

Windows üzerinde sanal ortamı etkinleştirin:

```powershell
.venv\Scripts\activate
```

### 3. Gerekli kütüphaneyi kurun

```bash
pip install -r requirements.txt
```

## Yapılandırma

Proje klasöründe `.env` adında bir dosya oluşturun:

```env
KYK_BOT_TOKEN=telegram_bot_tokeniniz
KYK_MENU_URL=https://raw.githubusercontent.com/Syrblo/kyk-yemek-botu/main/kyk_yemek.json
```

Telegram bot tokenını Telegram’daki [BotFather](https://t.me/BotFather) üzerinden oluşturabilirsiniz.

Gerçek `.env` dosyanızı ve bot tokenınızı GitHub’a yüklemeyin. Depoda bulunan `.env.example` dosyasını örnek olarak kullanabilirsiniz.

Yönetici Telegram kullanıcı kimliği, `kykBot.py` içerisindeki `ADMIN_ID` alanından değiştirilir:

```python
ADMIN_ID = 123456789
```

Menünün her gün gönderileceği Telegram kanalı ise `KANAL_ID` alanından ayarlanır:

```python
KANAL_ID = "@kanal_kullanici_adi"
```

Botun kanala mesaj gönderebilmesi için ilgili kanalda yönetici olması gerekir.

## Menü Listesi

Menüler GitHub deposundaki `kyk_yemek.json` dosyasında tutulur. Tarihler `AA-GG` biçiminde yazılır.

Örnek:

```json
{
  "11-01": "Pazar\nMercimek Çorbası\nTavuk Sote\nPirinç Pilavı\nAyran",
  "11-02": "Pazartesi\nEzogelin Çorbası\nKuru Fasulye\nBulgur Pilavı\nTatlı"
}
```

Her tarih bir günlük menüyü temsil eder. Menüde yeni satıra geçmek için `\n` kullanılır.

Bot, menü listesini şu zamanlarda GitHub’dan kontrol eder:

- Bot başlatıldıktan kısa süre sonra
- Her gün Türkiye saatiyle 06.00’da
- Yönetici `/guncelle` komutunu kullandığında

İndirilen dosya bozuksa, boşsa veya içinde mevcut aya ait menü bulunmuyorsa eski menü dosyası korunur.

## Botu Çalıştırma

```bash
python kykBot.py
```

Bot başarıyla başladığında terminalde aşağıdaki mesaj görüntülenir:

```text
Bot başarıyla çalıştırıldı ve mesaj bekliyor...
```

Botun sunucu yeniden başlatıldığında otomatik olarak çalışması için `systemd`, Docker veya benzer bir servis yöneticisi kullanılabilir.

## Proje Dosyaları

```text
kyk-yemek-botu/
├── kykBot.py          # Botun ana Python kodu
├── kyk_yemek.json         # GitHub üzerinden yayınlanan menü listesi
├── requirements.txt   # Python bağımlılıkları
├── .env.example       # Örnek ortam değişkenleri
├── .gitignore         # GitHub'a yüklenmeyecek dosyalar
└── README.md          # Proje açıklaması
```

Bot çalışırken ayrıca şu yerel dosyalar oluşabilir:

```text
kullanicilar.db
kyk_yemek.json
```

`kullanicilar.db`, kullanıcıların bildirim ayarlarını ve bot istatistiklerini saklar. `kyk_yemek.json` ise GitHub’dan indirilen aktif menü listesidir. Bu dosyalar GitHub’a yüklenmemelidir.

## Güvenlik

Aşağıdaki dosya ve bilgileri public depoya yüklemeyin:

- `.env`
- Telegram bot tokenı
- `kullanicilar.db`
- `.venv` klasörü
- Gerçek kullanıcı bilgileri
- Eski ve güvensiz kod kopyaları

Bir bot tokenı yanlışlıkla GitHub’a yüklenirse yalnızca dosyadan silmek yeterli değildir. Token Git geçmişinde kalabileceği için BotFather üzerinden iptal edilmeli ve yeni bir token oluşturulmalıdır.

## Katkıda Bulunma

Hata bildirimleri, geliştirme önerileri ve pull request’ler açıktır.

