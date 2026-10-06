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
| **3 — Nəticələr və backtest** | Seçimlərin avtomatik hesablaşması, CLV, statistika, `/neticeler`; 12 aylıq walk-forward backtest (26 liqa, 8,000+ oyun), parametr kalibrləməsi, piramida simulyasiyası | ✅ Hazır |
| **4 — AI rəyi** | Günün seçimlərinə Gemini (pulsuz) və Claude ayrıca baxır, son xəbərləri axtarır (mənbə linkləri ilə); AI yalnız əyləcdir | ✅ Hazır (açarlar lazımdır) |
| 4b — Canlı izləmə | Heyət və əmsal bildirişləri | ⏳ |
| **5 — Dashboard** | Statik veb panel (GitHub Pages): bu gün, nəticələr, piramida, backtest, sistem — [https://patchgroupdevelopment.github.io/football-betting-ai/](https://patchgroupdevelopment.github.io/football-betting-ai/) | ✅ Hazır |
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
| `GEMINI_API_KEY` | AI rəyi üçün (pulsuz) | [aistudio.google.com/apikey](https://aistudio.google.com/apikey) |
| `CLAUDE_API_KEY` | AI rəyi üçün (pulludur) | [console.anthropic.com/settings/keys](https://console.anthropic.com/settings/keys) |

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
əmsal 1.20–1.70 aralığındadır, ən yaxşı əmsal bazarın ədalətli (marjasız) qiymətindən aşağı deyil (EV ≥ 0), əminlik ən azı 55-dir,
risk yüksək deyil, məlumat kifayətdir, zədə vəziyyəti kritik deyil və model ilə bazar arasında 15 faiz bəndindən böyük ziddiyyət yoxdur.
Yekun ehtimalın 90%-i bazarın marjasız ehtimalına, 10%-i modelə əsaslanır. Bu qaydalar backtestlə seçilib (aşağıya bax).

**Backtest nə göstərdi (06.10.2025 – 05.10.2026, 26 liqa, 8,246 oyun):**
- Köhnə qaydalarla (EV ≥ 3%, əminlik ≥ 75) il ərzində **bir dənə də mərc** seçilməzdi.
- Model bazardan dəqiq deyil (Brier: model 0.615, bazar 0.599). Model bazardan "daha çox" ehtimal verəndə nəticə bazarın dediyindən pis çıxır.
  Ona görə ehtimalın əsası bazardır, model isə izah və "ziddiyyət" əyləci kimi işləyir.
- Bazarın marjası **power** üsulu ilə çıxılır: proporsional üsul favoritləri sistematik aşağı qiymətləndirirdi (80%-lik favorit 88% qazanırdı).
- Yeni qaydalarla: 127 mərc, 79.5% qazanma, orta əmsal 1.41, ROI +11.4%, bağlanış əmsalına qarşı üstünlük (CLV) +1.1%.
  Real gözlənti ROI deyil, CLV-dir (~+1%): ROI eyni dövrdə seçildiyi üçün nikbindir.
- ⚠️ Üstünlük **yalnız ən yaxşı əmsalı verən bukmekerdə** var. Adi bir bukmekerin əmsalı ilə nəticə zərərlidir.
  Hər seçimdə bukmeker göstərilir — başqa yerdə daha aşağı əmsalla mərc etmək üstünlüyü aradan qaldırır.
- Piramida (2 AZN-dən, gündə 1 mərhələ): klassik rejimdə ən yüksək nəticə 100.88 AZN olub (13 ardıcıl qələbə), sonra qırılıb; 17 cəhdə qoyulan
  34 AZN-in hamısı itib. Qazanc kilidi rejimində 60.85 AZN kilidlənib (xalis +26.85 AZN). 10,000 AZN-ə heç bir variantda çatılmayıb.
  Ona görə standart rejim `milestone_lock`-dur.

**AI rəyi** (`config.yaml` → `llm`): günün ən yaxşı seçimlərinə (5-ə qədər) Gemini və Claude ayrıca baxır, son 7 günün xəbərlərini axtarır
(zədə, rotasiya, məşqçi, motivasiya). Qaydalar: AI ehtimalı yalnız **azalda** bilər (iki rəyin ortalaması, ən çox 4 f.b.),
mənbəli xəbərlə **veto** edə bilər (o biri AI dəstəkləmirsə); EV və qərarı yenə sistem hesablayır. Mənbəsiz xəbər göstərilmir.
Hər seçim üçün "yalnız model" qərarı da saxlanılır — `/neticeler` AI-ın blokladığı seçimlərin real nəticəsini göstərir.
Açarı olmayan AI işləmir; hər ikisi yoxdursa sistem AI-sız davam edir.

Yenidən kalibrləmə: `python -m backend.cli backtest --sweep` (yalnız göstərir) və ya `--apply` (`config.yaml`-a yazır).

**Misli.az ilə mərc** (`config.yaml` → `selection.user_bookmaker`): hər seçimdə "Misli.az-da əmsal ən azı X olmalıdır" yazılır
(X = sistemin yekun ehtimalına görə ədalətli əmsal). Misli-dəki əmsalı Telegram-da yoxlayın: `/misli_12 1.45` — sistem həmin əmsalla
dəyəri hesablayıb ✅ və ya ⛔ deyir. Backtestdə adi bukmekerin əmsalı 127 seçimin yalnız 2-sində ədalətli qiymətə çatıb, yəni
Misli-də ✅ nadir olacaq. ⛔ olan mərclər uzun müddətdə bukmekerin marjası qədər zərər gətirir.
Hər yoxlama bazada saxlanılır: `/misli` (və veb paneldə "Nəticələr") Misli əmsallarının ədalətli qiymətdən orta fərqini
(təxminən Misli-nin marjası) və ✅ keçən seçimlərin Misli əmsalı ilə real nəticəsini göstərir.

VS Code-da: **Run and Debug** → "Sistemi işə sal".

## Kompüter sönülü olanda: GitHub Actions

Sistem GitHub-ın pulsuz serverlərində işləyir, kompüterin açıq olması lazım deyil:

| İş | Vaxt | Nə edir |
|---|---|---|
| **Gündəlik analiz** (`.github/workflows/daily.yml`) | 08:00 və 15:00 (Bakı) | Pulsuz mənbələr + API-Football → dünənki seçimlərin hesablaşması (Telegram-a "NƏTİCƏLƏR") → analiz. Günün ilk işi "GÜNÜN FUTBOL ANALİZİ" göndərir, ikincisi yalnız yeniləyir. |
| **Telegram əmrləri** (`.github/workflows/bot.yml`) | hər 15 dəqiqə | Gözləyən mesajlara cavab verir (`/bugun`, `/secimler`, `/oyun_12`…). Mesaj yoxdursa, bir neçə saniyəyə bitir. |
| **Həftəlik backtest** (`.github/workflows/backtest.yml`) | bazar ertəsi 06:30 (Bakı) | Son 12 ayın backtesti; hesabat `/backtest` və veb paneldə görünür. Qaydaları dəyişmir. |
| **Veb panel** (GitHub Pages) | hər gündəlik və həftəlik işdən sonra | [https://patchgroupdevelopment.github.io/football-betting-ai/](https://patchgroupdevelopment.github.io/football-betting-ai/) |

- Bot əmrlərə **dərhal yox, 15–20 dəqiqə ərzində** cavab verir. GitHub-ın planlı işləri bəzən bir neçə dəqiqə gecikir.
- Baza `state` budağında saxlanılır və hər işdən sonra yenilənir.
- Açarlar repo **Settings → Secrets and variables → Actions** bölməsində saxlanılır:
  `FOOTBALL_API_KEY`, `FOOTBALL_DATA_ORG_KEY`, `TELEGRAM_BOT_TOKEN`, `TELEGRAM_CHAT_ID`, `GEMINI_API_KEY`, `CLAUDE_API_KEY`.
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
| `/neticeler` | Seçimlərin canlı nəticələri: qazanma faizi, ROI, CLV, son 30 gün, piramida |
| `/backtest` | Son backtestin qısa hesabatı və piramida simulyasiyası |
| `/misli_ID 1.45` | Misli.az-dakı əmsalı yoxla: bu əmsalla mərc dəyərlidirmi? (✅ / ⛔) |
| `/misli` | Misli.az yoxlamalarının statistikası: əmsallar ədalətli qiymətdən neçə % fərqlənir, ✅ olanların nəticəsi |
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
python -m backend.cli backtest               # son 12 ayda yoxlama (hesabat bazaya yazılır)
python -m backend.cli backtest --sweep       # + parametrlərin kalibrlənməsi (yalnız göstərir)
python -m backend.cli backtest --apply       # + etibarlı qaydaları config.yaml-a yaz
python -m backend.cli dashboard              # veb paneli site/index.html faylına yarat
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
  services/        API klienti, cache, yükləmə, piramida, icmal, status, nəticələrin hesablaşması
  services/external/  pulsuz mənbələr: football-data.co.uk, football-data.org, ad uyğunlaşdırma
  analyzers/       forma, ev/səfər, yorğunluq, motivasiya, zədə təsiri, H2H, məlumat tamlığı
  predictors/      Dixon-Coles, Elo, market ehtimalları, bazar konsensusu
  selection/       namizədlər, əminlik, risk, qərar, izahlar
  presenters/      Azərbaycanca hesabat formatları
  telegram/        bot, əmrlər, bildirişlər
  scheduler/       cədvəl və pipeline
  api/             veb API
  i18n/az.py       istifadəçiyə görünən BÜTÜN mətnlər
  backtest/        walk-forward backtest, metrikalar, kalibrləmə, piramida simulyasiyası
  dashboard/       statik veb panel (GitHub Pages)
  llm/             AI rəyi: Gemini və Claude provayderləri, prompt, əyləc qaydası
alembic/           verilənlər bazası miqrasiyaları
tests/             testlər
```

## Təhlükəsizlik

- API açarları və Telegram tokeni yalnız `.env`-də saxlanılır; frontendə və loglara yazılmır.
- Bot yalnız `TELEGRAM_CHAT_ID`-də göstərilən istifadəçiyə cavab verir.
- Veb interfeys default olaraq yalnız bu kompüterdən açılır (`127.0.0.1`). Serverdə işlədəndə
  `DASHBOARD_PASSWORD` doldurun.
