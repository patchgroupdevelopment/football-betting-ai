# Arxitektura

Bu sənəd sistemin necə qurulduğunu və əsas qərarların səbəbini izah edir.

## 1. Əsas prinsiplər

1. **Hesablamanı backend edir, LLM yox.** Ehtimal, əmsal, dəyər üstünlüyü və piramida riyaziyyatı kodda hesablanır.
   LLM yalnız kontekst, taktika və risk şərhi verir. Ehtimalı ən çox ±4 faiz bəndi dəyişə bilər (config) və
   "veto" qoya bilər.
2. **Bazar əmsalı yoxlayıcıdır.** Yekun ehtimal = w × model + (1 − w) × bazarın marjasız ehtimalı.
   Böyük liqalarda 1.20–1.70 aralığı səmərəli bazardır, ona görə "MƏRC YOXDUR" tez-tez çıxacaq və bu normaldır.
3. **İki ayrı göstərici:** EHTİMAL ÜSTÜNLÜYÜ (model − bazar, faiz bəndi) və DƏYƏR ÜSTÜNLÜYÜ (EV = ehtimal × əmsal − 1).
4. **Modelin keyfiyyəti CLV ilə ölçülür.** 50–100 mərcdə qazanma faizi təsadüfə çox bağlıdır. CLV (götürülən əmsal
   bağlanış əmsalından yüksəkdirmi) daha tez siqnal verir. Bağlanış əmsalları `odds` cədvəlində saxlanılır.
5. **Faktiki əmsal.** İstifadəçinin bukmekeri API-də yoxdur. Mənfəət və piramida istifadəçinin daxil etdiyi əmsalla
   hesablanır (`bets.odds_taken`).
6. **Dil qaydası.** İstifadəçiyə görünən bütün mətnlər `backend/i18n/az.py`-dadır. Testlər qadağan olunmuş ingilis
   terminlərini yoxlayır. Provayderin ingiliscə xəta mətni istifadəçiyə heç vaxt çatmır (yalnız loga yazılır).
7. **Çökməmək.** Hər addım ayrıca qorunur. API xətası xəbərdarlığa çevrilir və yükləmə davam edir.
   Limit bitəndə qalan oyunlar "ətraflı yüklənmədi" kimi göstərilir.

## 2. Proses modeli

Bir Python prosesi və bir asyncio loop-u: FastAPI (veb), APScheduler (cədvəl), Telegram botu (long polling).
Bloklayan işlər (HTTP, SQLite yazıları) `asyncio.to_thread` ilə ayrıca thread-də gedir. SQLite WAL rejimindədir:
bot oxuyarkən yükləmə yaza bilir.

```
Scheduler (Asia/Baku) ─┐
Telegram /yenile ──────┼─► PipelineRunner (eyni anda bir yükləmə)
CLI ingest ────────────┘        │
                                ▼
                     IngestionService ──► ApiFootballClient ──► HttpJsonClient
                                │              │  quota, xəta xəritəsi     │  retry, timeout,
                                │              ▼                           │  rate limit
                                │         ResponseCache (DB, TTL)          ▼
                                ▼                                    API-Football v3
                          SQLite (SQLAlchemy 2, Alembic)
                                │
             DataQuality ◄──────┤
                                ▼
          DailyOverview ─► presenters (az) ─► Telegram / CLI / veb API
```

## 3a. Məlumat mənbələri

| Məlumat | API-Football (pulsuz) | football-data.co.uk | football-data.org |
|---|---|---|---|
| Günün oyunları | ✅ (dünən–sabah pəncərəsi) | | |
| Əmsallar (11 market) | ✅ | | |
| Zədəli / cəzalılar | ✅ (oyun-oyun sorğu) | | |
| Nəticələr, tarixçə | ❌ | ✅ 20+ liqa | ✅ 12 turnir |
| xG, zərbə, künc, kart, hakim | ❌ | ✅ əsas liqalar | |
| Turnir cədvəli | ❌ | (nəticələrdən hesablanır) | ✅ |
| Bombardirlər | ❌ | | ✅ |

**Bir klub — bir sətir.** Hər klub `teams` cədvəlində bir dəfə saxlanılır. Pulsuz mənbədən ilk dəfə görünən klub
API-Football ID-si olmadan yaradılır. API-Football onu eyni liqada oxşar adla göstərəndə həmin sətir ID-ni alır.
Mənbələrdəki adlar `team_aliases` cədvəlində saxlanılır. Ad müqayisəsi (`services/external/teamnames.py`)
aksentləri, "FC/CF" kimi əlavələri və qısaltmaları ("Man United", "Nott'm Forest") nəzərə alır.
Fərqləndirici sözləri fərqli olan adlar ("Man City" və "Man United") birləşdirilmir. İki namizəd eyni dərəcədə uyğun gələrsə, heç biri seçilmir.

**Bir oyun — bir sətir.** Eyni komandalar və ±36 saat daxilində olan oyun iki mənbədən gəlsə, birləşdirilir.
API-Football-un nəticəsi heç vaxt üzərindən yazılmır, pulsuz mənbə yalnız çatışmayan statistikanı tamamlayır.

**Plan məhdudiyyəti.** API-Football tarixçə, H2H və ya cədvəli rədd edəndə bu bir dəfə qeyd olunur və həmin gün
o addım üçün yenidən sorğu göndərilmir.

## 3. Gündəlik yükləmə (Mərhələ 1)

1. Günün və növbəti günün bütün oyunları — **iki sorğu** (`/fixtures?date=…&timezone=Asia/Baku`). Saxlanılan oyunlar:
   `config.yaml`-dakı liqalardan, günün 00:00-ından növbəti səhər 08:00-a qədər başlayanlar (analiz pəncərəsi).
   Oyunçu statistikası (dəqiqə, qol, ötürmə) bitmiş oyunların detallarından alınır və zədə təsiri üçün istifadə olunur.
2. Turnir cədvəli — hər liqa üçün bir sorğu (cache 6 saat).
3. Zədəli və cəzalılar — 20 oyunluq paketlərlə (`ids`). Bu parametr dəstəklənməsə, oyun-oyun sorğuya keçir.
4. Hər oyun üçün, liqa prioriteti sırası ilə:
   - hər iki komandanın son 20 oyunu (cache 12 saat). Bitmiş oyunların statistikası 20-lik paketlərlə yüklənir
     və bazada **daimi** saxlanılır, yəni yenidən sorğulanmır;
   - son qarşılaşmalar (H2H, cache 24 saat);
   - əmsallar (cache 10 dəqiqə). Yalnız qiymət dəyişəndə yeni qeyd yazılır, bu da əmsal tarixçəsi yaradır.
5. Nəticə `model_runs` cədvəlinə yazılır. Gündəlik hesabat həmin gün üçün bir dəfə göndərilir (təkrar olmur).

Liqa ID-si səhv olarsa (API başqa ölkə qaytarırsa), yükləmə xəbərdarlıq verir.

## 4. Məlumat tamlığı skoru

| Komponent | Bal |
|---|---|
| Ev sahibinin ≥5 son oyunu (statistika ilə) | 20 |
| Qonağın ≥5 son oyunu | 20 |
| Əmsallar (1X2 və ya ÜST/ALT) | 20 |
| Zədə/cəza siyahısı yoxlanılıb | 10 |
| Turnir cədvəli | 10 |
| Son oyunların ≥50%-də xG | 10 |
| H2H | 5 |
| Təsdiqlənmiş heyət | 5 |

≥80 — 🟢 Tam, 60–79 — 🟡 Qismən, <60 — 🔴 Natamam. Əminlik səviyyəsi bu skordan ən çox 10 bal yuxarı ola bilər,
"Natamam" məlumatla isə ümumiyyətlə seçim verilmir.

## 5. Verilənlər bazası

20 cədvəl. Tarixlər UTC-də saxlanılır, göstəriləndə Bakı vaxtına çevrilir. PostgreSQL-ə keçmək üçün yalnız
`DATABASE_URL` dəyişir.

| Qrup | Cədvəllər |
|---|---|
| Futbol | `leagues`, `teams`, `players`, `matches`, `match_stats`, `player_match_stats`, `injuries`, `lineups`, `standings`, `team_ratings`, `results` |
| Bazar | `odds` (zaman sırası, `is_closing`) |
| Mərc | `predictions`, `bets`, `pyramid_stages` (`attempt_no` — cəhdlər) |
| Sistem | `users`, `analysis_logs` (LLM xərci), `notifications`, `model_runs`, `api_cache` |

Sxem dəyişiklikləri: `alembic revision --autogenerate -m "..."`, sonra `python -m backend.cli init-db`.

## 6. Piramida

`növbəti balans = balans × əmsal`, sentə qədər yuxarı yuvarlaqlaşdırılır. Bir məğlubiyyət cəhdi bitirir, yeni cəhd
başlanğıc məbləğdən başlayır. "Bütün cəhdlərə qoyulan" məbləğ strategiyanın real xərcini göstərir.
`milestone_lock` rejimində mərhələ keçiləndə balansın bir hissəsi kilidlənir.

2 → 10,000 AZN üçün lazım olan ardıcıl qələbə sayı: 1.20 ilə 47, 1.30 ilə 33, 1.50 ilə 22, 1.70 ilə 17.
Hər mərhələdə +5% dəyər üstünlüyü olsa belə, bir cəhdin hədəfə çatma ehtimalı ~0.03–0.2%-dir. Sistem bunu açıq göstərir.

## 7. Analiz (Mərhələ 2)

```
bitmiş oyunlar ──► Dixon-Coles (xG 60% + qol 40%, yarımömür 180 gün, 3 virtual oyunluq prior)
               └─► Elo (K=20, ev üstünlüyü 65, qol fərqi çarpanı)
                                   │
hər oyun üçün kontekst ────────────┤  forma 5/10, ev/səfər 5/10, xG, H2H, cədvəl,
(analyzers/context.py)             │  motivasiya, istirahət, zədələr (oyunçu payı + qol/ötürmə)
                                   ▼
        λ(ev), λ(səfər) × düzəlişlər (zədə ≤20%, yorğunluq ≤6%, motivasiya ±3%)
                                   ▼
        hesab matrisi (Dixon-Coles ρ) ──► 11 marketin ehtimalı
                                   ▼
        bazar: hər bukmeker üçün marja çıxılır → orta; ən yaxşı əmsal seçilir
                                   ▼
        namizəd: əmsal 1.20–1.70, bazar ehtimalı var
        yekun ehtimal = 0.4 × model + 0.6 × bazar;  EV = yekun × əmsal − 1
                                   ▼
        əminlik (9 çəki, faktor seçimi nə qədər dəstəkləyir) ≤ məlumat tamlığı + 10
        risk (əmsal, tamlıq, əminlik, market növü, ziddiyyət, kritik zədə)
        bloklar: az məlumat · model–bazar fərqi > 15 f.b. · ≥3 zidd faktor · kritik zədə · yüksək risk
                                   ▼
        BET (EV ≥ 3% və əminlik ≥ 75) · WATCH (müsbət EV, əminlik ≥ 65) · NO_BET
                                   ▼
        hər oyundan bir seçim; gündə ən çox 3 (EV × əminlik sırası ilə) → paper mərc (1 vahid)
```

**Marketlər:** 1X2, ikili şans, heç-heçəyə mərcsiz, qol ÜST/ALT, ilk hissə ÜST/ALT, BTTS, komandaların qol sayı,
Asiya handikapı, künclər və kartlar. Künclər və kartlar mənfi binomial paylanma ilə hesablanır, kartlarda hakim əmsalı da nəzərə alınır.
Bütün xətlər yarımdır (x.5), yəni "push" olmur. Yalnız "heç-heçəyə mərcsiz" marketində pul qaytarılması EV-də nəzərə alınır.

**İzahlar** yalnız hesablanmış faktlardan qurulur (`selection/narrative.py`). "BU MƏRC NİYƏ UDUZA BİLƏR?" heç vaxt boş qalmır:
orada həmişə uduzma ehtimalı, ən zəif faktorlar, məlumat boşluqları və heyətin hələ açıqlanmadığı yazılır.

**Hələ olmayanlar (sonrakı mərhələlər):** rotasiya və növbəti vacib oyun (komandaların növbəti oyunları yüklənmir),
heyət açıqlananda yenidən qiymətləndirmə, LLM kontekst analizi.

## 8. Sonrakı mərhələlər

| Mərhələ | Əsas işlər |
|---|---|
| 3 | Nəticələrin hesablanması, statistika (marketlər üzrə), CLV; football-data.co.uk ilə walk-forward backtest; kalibrləmə; Monte Carlo piramida |
| 4 | LLM provayder interfeysi (Anthropic / OpenRouter), ciddi JSON sxemi, "BU MƏRC NİYƏ UDUZA BİLƏR?", tənqidçi keçidi, faktların yoxlanması; heyət və əmsal monitorinqi |
| 5 | Tam dashboard (FastAPI + Jinja2 + HTMX + Chart.js) |
| 6 | VPS-ə deploy, həftəlik hesabat, backup |

LLM backtest edilmir: model keçmiş nəticələri "bilə" bilər, keçmiş zədə snapshot-ları da yoxdur. LLM-in faydası
paper rejimdə "yalnız model" ilə "model + LLM" nəticələri müqayisə edilərək ölçülür.
