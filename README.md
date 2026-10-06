# ⚽ Futbol Analiz Sistemi

Futbol oyunlarını avtomatik tapan, məlumat toplayan və statistik analiz edən sistem:
Python backend, Telegram bot və veb interfeys.

> Sistem heç bir mərcin qazanacağına zəmanət vermir. Hər seçim üçün ehtimal, risk və
> əsaslandırma göstərilir; uyğun seçim yoxdursa, nəticə **MƏRC YOXDUR** olur.
> `PAPER_MODE=true` rejimində real mərc açılmır — nəticələr yalnız simulyasiya kimi saxlanılır.

## Hazırkı vəziyyət

| Mərhələ | Məzmun | Vəziyyət |
|---|---|---|
| **1 — Təməl** | Konfiqurasiya, verilənlər bazası, API-Football klienti (retry, limit, cache), gündəlik yükləmə, məlumat tamlığı skoru, piramida, Telegram botu, CLI, veb API | ✅ Hazır |
| **2 — Model və seçim** | Forma, ev/səfər, yorğunluq, motivasiya, zədə təsiri; Elo + Dixon-Coles; 11 market; marjasız bazar ehtimalı; dəyər üstünlüyü; əminlik; risk; TOP 3 / MƏRC YOXDUR; gündəlik analiz mesajı; paper mərclər | ✅ Hazır |
| 3 — Nəticələr və backtest | Hesablaşma, statistika, CLV, 3/6/12 aylıq backtest, kalibrləmə | ⏳ |
| 4 — LLM və canlı izləmə | Claude/OpenRouter analizi, "BU MƏRC NİYƏ UDUZA BİLƏR?", heyət və əmsal bildirişləri | ⏳ |
| 5 — Dashboard | Tam veb idarə paneli | ⏳ |
| 6 — Canlıya çıxış | VPS, həftəlik hesabat, backup | ⏳ |

Arxitektura və qərarlar: [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md).

## Quraşdırma (Windows, VS Code)

```powershell
cd C:\Fuad\football-betting-ai
python -m venv .venv
.venv\Scripts\activate
pip install -r requirements-dev.txt
copy .env.example .env      # sonra .env faylını doldurun
python -m backend.cli init-db
```

### API açarları (`.env`)

| Dəyişən | Lazımdır? | Haradan alınır |
|---|---|---|
| `FOOTBALL_API_KEY` | Mütləq | [dashboard.api-football.com](https://dashboard.api-football.com) — pulsuz qeydiyyat |
| `FOOTBALL_DATA_ORG_KEY` | Tövsiyə olunur | [football-data.org/client/register](https://www.football-data.org/client/register) — pulsuz |
| `TELEGRAM_BOT_TOKEN` | Telegram üçün | Telegram-da **@BotFather** → `/newbot` |
| `TELEGRAM_CHAT_ID` | Telegram üçün | Boş saxlayın, sistemi işə salın və bota `/start` yazın — bot chat ID-nizi göndərəcək |
| `ODDS_API_KEY` | Sonrakı mərhələ | [the-odds-api.com](https://the-odds-api.com) |
| `CLAUDE_API_KEY` / `OPENROUTER_API_KEY` | Sonrakı mərhələ | console.anthropic.com / openrouter.ai |

`.env` faylı git-ə göndərilmir (`.gitignore`-dadır). Açarlar yalnız backenddə istifadə olunur.

## İşə salma

```powershell
python -m backend.main
```

Bir proses üç işi görür:
- **Veb interfeys:** <http://127.0.0.1:8000> (`/api/status`, `/api/matches`, `/api/picks`, `/api/analysis/{id}`, `/api/pyramid`)
- **Telegram botu** (token verilibsə)
- **Cədvəl:** hər gün 08:00 yükləmə → analiz → Telegram-a "GÜNÜN FUTBOL ANALİZİ", 15:00 yeniləmə
  (təzə əmsal və zədələrlə analiz yenidən aparılır), 03:00 cache təmizliyi.
  Sistem 08:00-dan sonra işə düşübsə və bu gün yükləmə olmayıbsa, yükləməni dərhal başladır.
- **Analiz pəncərəsi:** bir günün analizi növbəti səhər 08:00-a qədər olan oyunları da əhatə edir
  (gecə saatlarındakı MLS və Braziliya oyunları buraxılmasın deyə).

### Seçimlər haqqında

Sistem gündə ən çox 3 seçim verir və bu seçimlər fərqli oyunlardan olur. Seçim yalnız bu şərtlərin hamısı ödəndikdə verilir:
əmsal 1.20–1.70 aralığındadır, dəyər üstünlüyü (EV) ən azı +3%-dir, əminlik ən azı 75-dir, risk yüksək deyil,
məlumat kifayətdir, zədə vəziyyəti kritik deyil və model ilə bazar arasında 15 faiz bəndindən böyük ziddiyyət yoxdur.
Yekun ehtimalın 40%-i modelə, 60%-i bazarın marjasız ehtimalına əsaslanır. Ona görə "MƏRC YOXDUR" tez-tez çıxacaq.
Bu, sistemin ehtiyatla işlədiyini göstərir. Bütün parametrlər `config.yaml`-dadır və Mərhələ 3-dəki backtestlə kalibrlənəcək.

VS Code-da: **Run and Debug** → "Sistemi işə sal".

## Kompüter sönülü olanda: GitHub Actions

Sistem GitHub-ın pulsuz serverlərində işləyir, kompüterin açıq olması lazım deyil:

| İş | Vaxt | Nə edir |
|---|---|---|
| **Gündəlik analiz** (`.github/workflows/daily.yml`) | 08:00 və 15:00 (Bakı) | Pulsuz mənbələr + API-Football + analiz. Günün ilk işi Telegram-a "GÜNÜN FUTBOL ANALİZİ" göndərir, ikincisi yalnız yeniləyir. |
| **Telegram əmrləri** (`.github/workflows/bot.yml`) | hər 15 dəqiqə | Gözləyən mesajlara cavab verir (`/bugun`, `/secimler`, `/oyun_12`…). Mesaj yoxdursa, bir neçə saniyəyə bitir. |

- Bot əmrlərə **dərhal yox, 15–20 dəqiqə ərzində** cavab verir. GitHub-ın planlı işləri bəzən bir neçə dəqiqə gecikir.
- Baza `state` budağında saxlanılır və hər işdən sonra yenilənir.
- Açarlar repo **Settings → Secrets and variables → Actions** bölməsində saxlanılır:
  `FOOTBALL_API_KEY`, `FOOTBALL_DATA_ORG_KEY`, `TELEGRAM_BOT_TOKEN`, `TELEGRAM_CHAT_ID`.
- İşi əl ilə başlatmaq: repo → **Actions** → iş → **Run workflow**.
- ⚠️ GitHub rejimi işləyərkən kompüterdə `python -m backend.main`-i **Telegram ilə işə salmayın**: eyni botun mesajlarını iki yer paylaşa bilməz.
  Lokal sınaq üçün `.env`-də `TELEGRAM_ENABLED=false` yazın.

## Telegram əmrləri

| Əmr | Nə edir |
|---|---|
| `/secimler` | Günün analizi: ən yaxşı seçim, digər seçimlər və ya MƏRC YOXDUR |
| `/bugun` | Günün oyunları: məlumat tamlığı, əmsal vəziyyəti, qərar və `/oyun_ID` keçidi |
| `/oyun_ID` | Bir oyunun ətraflı analizi (keçidə toxunmaq kifayətdir) |
| `/piramida` | Piramida irəliləyişi və nəzəri yol |
| `/status` | Sistem vəziyyəti, API limiti, növbəti yükləmə |
| `/yenile` | Məlumatları indi yüklə |
| `/komek` | Əmrlərin siyahısı |

## Terminal əmrləri

```powershell
python -m backend.cli ingest                 # pulsuz mənbələr + API-Football + analiz
python -m backend.cli ingest --date 2026-10-05
python -m backend.cli sync                   # yalnız pulsuz mənbələrdən tarixçə
python -m backend.cli analyze                # yalnız analiz (yükləmədən)
python -m backend.cli picks                  # günün analizi və seçimlər
python -m backend.cli match 12               # 12 nömrəli oyunun ətraflı analizi
python -m backend.cli today                  # günün oyunları
python -m backend.cli pyramid                # piramida
python -m backend.cli status                 # sistem vəziyyəti
python -m backend.cli leagues --check        # config.yaml-dakı liqa ID-lərini yoxla
python -m backend.cli leagues --country Azerbaijan
python -m backend.cli send-test              # Telegram-a sınaq mesajı
python -m backend.cli send-today             # günün oyunlarını Telegram-a göndər
python -m backend.cli cache-purge
```

## Konfiqurasiya

- `config.yaml` — balans, əmsal aralığı, əminlik çəkiləri, liqalar, cədvəl, cache müddətləri.
- `.env` — gizli açarlar və rejimlər. `.env`-də `MIN_ODDS`, `STARTING_BANKROLL` və s. doldurulubsa,
  `config.yaml`-dakı dəyərdən üstündür.

### Pulsuz məlumat mənbələri

API-Football-un pulsuz planı yalnız 3 günlük pəncərəni (dünən, bu gün, sabah) verir. Ondan **günün oyunları,
əmsallar və zədəlilər** alınır. Tarixçə, xG, turnir cədvəli və bombardirlər isə iki pulsuz mənbədən gəlir:

| Mənbə | Nə verir | Açar |
|---|---|---|
| football-data.co.uk | 20+ liqanın nəticələri, **xG**, zərbələr, künclər, kartlar, hakim (cari və keçən mövsüm) | lazım deyil |
| football-data.org | 12 turnirin cədvəli, bombardirləri, Çempionlar Liqasının nəticələri | `FOOTBALL_DATA_ORG_KEY` |

Hər liqanın hansı mənbədən oxunacağı `config.yaml`-da (`fd_couk`, `fd_org`) göstərilib.

Komanda adları mənbələrdə fərqlidir ("Man United", "Manchester United FC", "Manchester United"). Sistem onları
avtomatik uyğunlaşdırır. "Man City" və "Man United" kimi oxşar, amma fərqli klubları qarışdırmır,
əmin olmadıqda isə birləşdirmir. Eyni oyun iki mənbədən gəlsə, bir dəfə saxlanılır.

Tarixçəni ayrıca yeniləmək üçün: `python -m backend.cli sync` (API-Football limitini sərf etmir).

UEFA Avropa və Konfrans Liqası, Səudiyyə və Azərbaycan pulsuz mənbələrdə yoxdur. Bu liqaların tarixçəsi
API-Football-un "dünənki nəticələri" ilə gündən-günə yığılır.

Pro plana keçsəniz ($19/ay, gündə 7,500 sorğu), heç nəyi dəyişmək lazım deyil: sistem API-Football-dan
tarixçəni, oyunçu statistikasını və cədvəlləri özü götürəcək. Bu halda `config.yaml`-da `requests_per_minute: 300` yazın.

## Testlər

```powershell
pytest
```

## Struktur

```
backend/
  main.py          giriş nöqtəsi (veb + bot + cədvəl)
  cli.py           terminal əmrləri
  config.py        .env + config.yaml
  container.py     servislərin qurulması
  database/        engine, sessiya, miqrasiyalar
  models/          cədvəllər (22 cədvəl)
  schemas/         API cavablarının tipli modelləri
  services/        API klienti, cache, yükləmə, piramida, icmal, status
  services/external/  pulsuz mənbələr: football-data.co.uk, football-data.org, ad uyğunlaşdırma
  analyzers/       forma, ev/səfər, yorğunluq, motivasiya, zədə təsiri, H2H, məlumat tamlığı
  predictors/      Dixon-Coles, Elo, market ehtimalları, bazar konsensusu
  selection/       namizədlər, əminlik, risk, qərar, izahlar
  presenters/      Azərbaycanca hesabat formatları
  telegram/        bot, əmrlər, bildirişlər
  scheduler/       cədvəl və pipeline
  api/             veb API
  i18n/az.py       istifadəçiyə görünən BÜTÜN mətnlər
  llm/ backtest/   sonrakı mərhələlər
alembic/           verilənlər bazası miqrasiyaları
tests/             testlər
```

## Təhlükəsizlik

- API açarları və Telegram tokeni yalnız `.env`-də saxlanılır; frontendə və loglara yazılmır.
- Bot yalnız `TELEGRAM_CHAT_ID`-də göstərilən istifadəçiyə cavab verir.
- Veb interfeys default olaraq yalnız bu kompüterdən açılır (`127.0.0.1`). Serverdə işlədəndə
  `DASHBOARD_PASSWORD` doldurun.
