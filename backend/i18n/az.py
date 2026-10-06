"""All user-facing Azerbaijani texts.

Rule: no user-visible string is hard-coded elsewhere. Telegram messages, CLI
output, API messages and error texts are built with ``t()`` or the lookup
tables below, so the language rule can be enforced (and tested) in one place.
"""

from __future__ import annotations

from typing import Any

MONTHS: tuple[str, ...] = (
    "yanvar", "fevral", "mart", "aprel", "may", "iyun",
    "iyul", "avqust", "sentyabr", "oktyabr", "noyabr", "dekabr",
)

WEEKDAYS: tuple[str, ...] = (
    "bazar ertəsi", "çərşənbə axşamı", "çərşənbə", "cümə axşamı", "cümə", "şənbə", "bazar",
)

# API-Football country names -> Azerbaijani.
COUNTRIES: dict[str, str] = {
    "World": "Dünya",
    "Europe": "Avropa",
    "England": "İngiltərə",
    "Spain": "İspaniya",
    "Italy": "İtaliya",
    "Germany": "Almaniya",
    "France": "Fransa",
    "Netherlands": "Niderland",
    "Portugal": "Portuqaliya",
    "Turkey": "Türkiyə",
    "Türkiye": "Türkiyə",
    "Brazil": "Braziliya",
    "USA": "ABŞ",
    "Belgium": "Belçika",
    "Scotland": "Şotlandiya",
    "Wales": "Uels",
    "Ireland": "İrlandiya",
    "Northern-Ireland": "Şimali İrlandiya",
    "Saudi-Arabia": "Səudiyyə Ərəbistanı",
    "Mexico": "Meksika",
    "Argentina": "Argentina",
    "Austria": "Avstriya",
    "Switzerland": "İsveçrə",
    "Greece": "Yunanıstan",
    "Denmark": "Danimarka",
    "Sweden": "İsveç",
    "Norway": "Norveç",
    "Poland": "Polşa",
    "Azerbaijan": "Azərbaycan",
    "Russia": "Rusiya",
    "Ukraine": "Ukrayna",
    "Croatia": "Xorvatiya",
    "Serbia": "Serbiya",
    "Czech-Republic": "Çexiya",
    "Romania": "Rumıniya",
    "Hungary": "Macarıstan",
    "Bulgaria": "Bolqarıstan",
    "Slovakia": "Slovakiya",
    "Slovenia": "Sloveniya",
    "Finland": "Finlandiya",
    "Iceland": "İslandiya",
    "Georgia": "Gürcüstan",
    "Kazakhstan": "Qazaxıstan",
    "Israel": "İsrail",
    "Cyprus": "Kipr",
    "Japan": "Yaponiya",
    "South-Korea": "Cənubi Koreya",
    "China": "Çin",
    "Australia": "Avstraliya",
    "Egypt": "Misir",
    "Morocco": "Mərakeş",
    "Qatar": "Qətər",
    "United-Arab-Emirates": "BƏƏ",
    "Iran": "İran",
    "Colombia": "Kolumbiya",
    "Chile": "Çili",
    "Uruguay": "Uruqvay",
    "Paraguay": "Paraqvay",
    "Peru": "Peru",
    "Ecuador": "Ekvador",
    "Canada": "Kanada",
}

MATCH_STATUSES: dict[str, str] = {
    "TBD": "Vaxt dəqiqləşməyib",
    "NS": "Başlamayıb",
    "1H": "1-ci hissə",
    "HT": "Fasilə",
    "2H": "2-ci hissə",
    "ET": "Əlavə vaxt",
    "BT": "Əlavə vaxt fasiləsi",
    "P": "Penaltilər",
    "SUSP": "Dayandırılıb",
    "INT": "Fasiləyə verilib",
    "LIVE": "Canlı",
    "FT": "Bitib",
    "AET": "Əlavə vaxtda bitib",
    "PEN": "Penaltilərlə bitib",
    "PST": "Təxirə salınıb",
    "CANC": "Ləğv edilib",
    "ABD": "Yarımçıq dayandırılıb",
    "AWD": "Texniki nəticə",
    "WO": "Rəqib çıxmadı",
}

LEAGUE_TYPES: dict[str, str] = {"League": "Liqa", "Cup": "Kubok"}

MARKETS: dict[str, str] = {
    "1X2": "Oyunun nəticəsi (1X2)",
    "DC": "İkili şans",
    "DNB": "Heç-heçəyə mərcsiz",
    "OU": "Qol ÜST/ALT",
    "OU_1H": "İlk hissə qol ÜST/ALT",
    "BTTS": "Hər iki komanda qol vurar",
    "AH": "Asiya handikapı",
    "TEAM_TOTAL_HOME": "Ev sahibinin qol sayı",
    "TEAM_TOTAL_AWAY": "Qonağın qol sayı",
    "CORNERS_OU": "Künclər ÜST/ALT",
    "CARDS_OU": "Kartlar ÜST/ALT",
}

RISK_LEVELS: dict[str, str] = {"low": "AŞAĞI RİSK", "medium": "ORTA RİSK", "high": "YÜKSƏK RİSK"}

DECISIONS: dict[str, str] = {
    "bet": "MƏRC EDİLƏ BİLƏR",
    "watch": "İZLƏMƏDƏ SAXLA",
    "no_bet": "MƏRC YOXDUR",
}

CONFIDENCE_BANDS: tuple[tuple[int, str], ...] = (
    (90, "ÇOX GÜCLÜ"),
    (80, "GÜCLÜ"),
    (70, "MƏQBUL"),
    (60, "ORTA"),
    (50, "ZƏİF"),
    (0, "QAÇIN"),
)

RUN_STATUSES: dict[str, str] = {
    "running": "⏳ Gedir",
    "success": "✅ Uğurlu",
    "partial": "🟡 Qismən uğurlu",
    "failed": "❌ Uğursuz",
}

DATA_QUALITY_LEVELS: dict[str, str] = {"full": "Tam", "partial": "Qismən", "low": "Natamam"}
DATA_QUALITY_ICONS: dict[str, str] = {"full": "🟢", "partial": "🟡", "low": "🔴"}

PYRAMID_MODES: dict[str, str] = {"classic": "Klassik", "milestone_lock": "Qazanc kilidi"}

FORM_LETTERS: dict[str, str] = {"W": "Q", "D": "H", "L": "M"}  # qələbə / heç-heçə / məğlubiyyət

DIRECTIONS: dict[str, str] = {"OVER": "ÜST", "UNDER": "ALT", "YES": "BƏLİ", "NO": "XEYR"}

MOTIVATION_TAGS: dict[str, str] = {
    "title_race": "çempionluq mübarizəsi",
    "relegation_battle": "aşağı liqaya düşmə təhlükəsi",
    "europe_race": "avrokubok uğrunda mübarizə",
    "safe_midtable": "turnirdə vəziyyəti sabitdir, motivasiya aşağı ola bilər",
    "regular": "adi turnir motivasiyası",
    "cup_knockout": "həlledici (pley-off) oyun",
    "early_season": "mövsümün əvvəli",
    "unknown": "turnir cədvəli məlumatı yoxdur",
}

PLAYER_ROLES: dict[str, str] = {
    "key_goalkeeper": "əsas qapıçı",
    "top_scorer": "bombardir",
    "top_assister": "əsas assistçi",
    "key_defender": "əsas müdafiəçi",
    "key_midfielder": "əsas yarımmüdafiəçi",
    "key_attacker": "əsas hücumçu",
}

POSITIONS: dict[str, str] = {"G": "qapıçı", "D": "müdafiəçi", "M": "yarımmüdafiəçi", "F": "hücumçu"}

ABSENCE_KINDS: dict[str, str] = {"injury": "zədə", "suspension": "cəza", "doubtful": "şübhəli"}

FACTOR_NAMES: dict[str, str] = {
    "form": "forma",
    "home_away": "ev/səfər göstəricisi",
    "xg": "xG",
    "injuries": "zədə/cəza vəziyyəti",
    "strength": "komandanın gücü",
    "h2h": "son qarşılaşmalar",
    "motivation": "motivasiya",
    "fatigue": "yorğunluq",
    "market": "bazarla uyğunluq",
}

NO_BET_REASONS: dict[str, str] = {
    "low_data": "kifayət qədər məlumat yoxdur",
    "no_odds": "əmsal yoxdur",
    "no_market_in_range": "əmsal aralığında uyğun market yoxdur",
    "model_market_conflict": "model ilə bazar arasında böyük ziddiyyət var",
    "conflicting_signals": "göstəricilər bir-biri ilə ziddiyyət təşkil edir",
    "critical_absence": "zədə vəziyyəti kritikdir",
    "high_risk": "risk çox yüksəkdir",
    "low_value": "statistik üstünlük kifayət qədər yüksək deyil",
    "low_confidence": "əminlik səviyyəsi tələb olunan həddən aşağıdır",
}

DECISION_ICONS: dict[str, str] = {"bet": "✅", "watch": "👀", "no_bet": "⛔"}
RISK_ICONS: dict[str, str] = {"low": "🟢", "medium": "🟡", "high": "🔴"}

MESSAGES: dict[str, str] = {
    # --- Common ---
    "common.warnings_title": "⚠️ XƏBƏRDARLIQLAR",
    "common.more_warnings": "… və daha {count} xəbərdarlıq (ətraflı: logs/app.log)",
    "common.yes": "var",
    "common.no": "yoxdur",
    "common.unknown": "məlum deyil",
    "disclaimer.title": "⚠️ QEYD",
    "disclaimer.body": (
        "Bu sistem statistik analiz aparır və heç bir nəticəyə zəmanət vermir.\n"
        "Mərc yüksək maliyyə riski daşıya bilər."
    ),

    # --- Errors ---
    "error.provider_unavailable": "⚠️ Məlumat mənbəyindən cavab alınmadı.",
    "error.provider_auth": "⚠️ API açarı yanlışdır və ya aktiv deyil. .env faylında {key_name} dəyərini yoxlayın.",
    "error.missing_api_key": "⚠️ {key_name} təyin edilməyib. Açarı .env faylına əlavə edin.",
    "error.rate_limited": "⚠️ Məlumat mənbəyi sorğu sürətini məhdudlaşdırdı. Bir az sonra yenidən cəhd ediləcək.",
    "error.quota_exhausted": "⚠️ Gündəlik API sorğu limiti bitib. Bəzi oyunlar üçün məlumat natamam qala bilər.",
    "error.plan_restricted": "⚠️ Cari API planı bu məlumata giriş vermir (plan məhdudiyyəti).",
    "error.bad_response": "⚠️ Məlumat mənbəyi gözlənilməz cavab qaytardı.",
    "error.not_enough_data": "⚠️ Bu oyun üçün kifayət qədər statistik məlumat tapılmadı.",
    "error.unexpected": "⚠️ Gözlənilməz xəta baş verdi. Ətraflı məlumat logs/app.log faylındadır.",
    "error.config": "⚠️ Konfiqurasiya xətası: {detail}",
    "error.config_missing": "⚠️ Konfiqurasiya faylı tapılmadı: {path}",
    "error.config_yaml": "⚠️ config.yaml faylında sintaksis xətası var (sətir {line}).",
    "error.database": "⚠️ Verilənlər bazası hazırlanmadı. Ətraflı məlumat logs/app.log faylındadır.",
    "error.telegram_send": "⚠️ Telegram mesajı göndərilmədi.",
    "error.telegram_start": "⚠️ Telegram botu işə düşmədi. TELEGRAM_BOT_TOKEN dəyərini və internet bağlantısını yoxlayın.",
    "error.telegram_not_configured": (
        "⚠️ Telegram deaktivdir və ya TELEGRAM_BOT_TOKEN / TELEGRAM_CHAT_ID təyin edilməyib."
    ),
    "error.invalid_date": "Tarix İİİİ-AA-GG formatında olmalıdır (məs. 2026-10-05).",

    # --- Config validation ---
    "config.target_below_start": "hədəf balans başlanğıc balansdan böyük olmalıdır",
    "config.odds_range": "min_odds dəyəri max_odds dəyərindən kiçik olmalıdır",
    "config.weights_sum": "əminlik çəkilərinin cəmi 1.00 olmalıdır (indi {total})",
    "config.bad_time": "vaxt SS:DD formatında olmalıdır (məs. 08:00)",
    "config.bad_timezone": "naməlum vaxt zonası",
    "config.bad_chat_id": "TELEGRAM_CHAT_ID rəqəm olmalıdır (bir neçə ID vergüllə ayrılır)",
    "config.duplicate_league": "liqa ID {api_id} siyahıda təkrarlanır",
    "config.unknown_market": "naməlum market: {markets} (mümkün olanlar: {allowed})",
    "config.err.greater_than": "dəyər {gt} rəqəmindən böyük olmalıdır",
    "config.err.greater_than_equal": "dəyər {ge} rəqəmindən kiçik olmamalıdır",
    "config.err.less_than": "dəyər {lt} rəqəmindən kiçik olmalıdır",
    "config.err.less_than_equal": "dəyər {le} rəqəmindən böyük olmamalıdır",
    "config.err.missing": "bu sahə tələb olunur",
    "config.err.number": "rəqəm olmalıdır",
    "config.err.integer": "tam ədəd olmalıdır",
    "config.err.boolean": "true və ya false olmalıdır",
    "config.err.literal": "icazə verilən dəyərlər: {expected}",
    "config.err.invalid": "dəyər yanlışdır",

    # --- Ingestion steps ---
    "step.fixtures": "Oyunların yüklənməsi",
    "step.fixtures_next": "Növbəti günün gecə oyunları",
    "step.fixtures_prev": "Dünənki nəticələr",
    "step.external_couk": "football-data.co.uk nəticələri",
    "step.external_org_matches": "football-data.org nəticələri",
    "step.external_org_linking": "football-data.org komandalarının uyğunlaşdırılması",
    "step.external_org_standings": "football-data.org turnir cədvəli",
    "step.external_org_scorers": "football-data.org bombardirləri",
    "step.standings": "Turnir cədvəli",
    "step.injuries": "Zədəli və cəzalılar",
    "step.history": "Son oyunlar",
    "step.h2h": "Son qarşılaşmalar",
    "step.odds": "Əmsallar",

    # --- Ingestion report ---
    "ingest.title": "📥 MƏLUMAT YÜKLƏNMƏSİ",
    "ingest.date": "📅 Tarix: {date}",
    "ingest.status": "Status: {status}",
    "ingest.fixtures": "Tapılan oyunlar: {total} (prioritet liqalarda: {priority})",
    "ingest.deep": "Ətraflı yüklənən oyunlar: {done} / {planned}",
    "ingest.requests": "İstifadə olunan API sorğuları: {used}",
    "ingest.remaining": "Bu gün qalan sorğu: {remaining}",
    "ingest.duration": "Müddət: {seconds} san.",
    "ingest.step_error": "{step}: {message}",
    "ingest.step_error_context": "{step} ({context}): {message}",
    "ingest.skipped_quota": "⚠️ {count} oyun API limiti səbəbindən ətraflı yüklənmədi.",
    "ingest.plan_skip": "ℹ️ {step}: cari API-Football planında yoxdur — pulsuz mənbələrdən istifadə olunur.",
    "ingest.external": (
        "🆓 Pulsuz mənbələr: {added} yeni, {updated} yoxlanılmış oyun (xG olan: {xg}) · "
        "turnir cədvəli: {standings} liqa · bombardirlər: {scorers} liqa"
    ),
    "ingest.external_teams": "Komandalar: {linked} uyğunlaşdırıldı, {created} yeni əlavə olundu",
    "ingest.league_mismatch": (
        "⚠️ Liqa ID {api_id}: gözlənilən ölkə «{expected}», API-də «{actual}». "
        "config.yaml-da ID-ni yoxlayın."
    ),

    # --- Daily overview ---
    "daily.title": "⚽ GÜNÜN OYUNLARI",
    "daily.found": "Prioritet liqalarda {count} oyun tapıldı.",
    "daily.readiness": "Analizə hazır: {ready} · Natamam məlumat: {incomplete}",
    "daily.other_leagues": "Digər liqalarda daha {count} oyun var (ətraflı analizə daxil edilməyib).",
    "daily.none": "Bu gün prioritet liqalarda oyun tapılmadı.",
    "daily.not_loaded": "Bu tarix üçün məlumat hələ yüklənməyib.",
    "daily.hint_telegram": "Yükləmək üçün /yenile yazın.",
    "daily.hint_cli": "Yükləmək üçün: python -m backend.cli ingest",
    "daily.data_line": "📊 Məlumat: {score}% {icon} {level} · 💰 Əmsal: {odds}",
    "daily.model_inactive": (
        "ℹ️ Statistik model hələ aktiv deyil. Bu hesabat yalnız oyunları və "
        "məlumatların vəziyyətini göstərir."
    ),

    "daily.decision_line": "{icon} {decision} · /oyun_{match_id}",

    # --- Analysis bullets ---
    "an.record": "{w}Q {d}H {l}M, {pts} xal",
    "an.standing": "(yer: {rank}, {points} xal)",
    "an.goals_count": "{goals} qol",
    "an.none": "yoxdur",
    "an.form5": "Son 5 oyun forması: {home} {home_seq} ({home_record}) · {away} {away_seq} ({away_record})",
    "an.form10": (
        "Son 10 oyun: {home} — {home_pts} xal, qollar {home_gf}:{home_ga} · "
        "{away} — {away_pts} xal, qollar {away_gf}:{away_ga}"
    ),
    "an.venue": (
        "Ev/səfər göstəricisi: {home} evdə son {home_n} oyunda {home_record} · "
        "{away} səfərdə son {away_n} oyunda {away_record}"
    ),
    "an.h2h": (
        "Son qarşılaşmalar ({n} oyun): {home} {home_wins} qələbə, {draws} heç-heçə, "
        "{away} {away_wins} qələbə; orta {avg} qol"
    ),
    "an.h2h_none": "Son qarşılaşmalar: məlumat yoxdur",
    "an.injuries": "Zədəlilər: {home} — {home_list} · {away} — {away_list}",
    "an.suspensions": "Cəzalılar: {home} — {home_list} · {away} — {away_list}",
    "an.doubtful": "Şübhəlilər: {home} — {home_list} · {away} — {away_list}",
    "an.xg": (
        "xG göstəricisi (son 10, oyun başına): {home} {home_xg} xG / {home_xga} xGA · "
        "{away} {away_xg} xG / {away_xga} xGA"
    ),
    "an.xg_none": "xG göstəricisi: bu oyun üçün kifayət qədər xG məlumatı yoxdur",
    "an.attack": (
        "Hücum performansı: {home} orta {home_gf} qol vurur (qolsuz oyunlar {home_fts}%) · "
        "{away} orta {away_gf} qol (qolsuz oyunlar {away_fts}%)"
    ),
    "an.defence": (
        "Müdafiə performansı: {home} orta {home_ga} qol buraxır (quru oyunlar {home_cs}%) · "
        "{away} orta {away_ga} qol buraxır (quru oyunlar {away_cs}%)"
    ),
    "an.motivation": "Motivasiya: {home} — {home_label} · {away} — {away_label}",
    "an.fatigue": "Yorğunluq: {home} son oyundan {home_rest} gün, {away} {away_rest} gün sonra meydana çıxır",
    "an.note.key_attacker_out": "{team}: əsas hücumçunun olmaması hücum gücünü əhəmiyyətli dərəcədə aşağı salır.",
    "an.note.key_goalkeeper_out": "{team}: əsas qapıçının olmaması müdafiəni zəiflədir.",
    "an.note.key_defender_out": "{team}: əsas müdafiəçinin olmaması qol buraxma riskini artırır.",
    "an.model": "Model: gözlənilən qollar {home} {lh} – {la} {away}",

    # --- Facts (why / against) ---
    "fact.market": "Bazarın marjasız ehtimalı {market}%, statistik model {model}% hesablayır.",
    "fact.market_disagrees": (
        "Bazar bu nəticəni modeldən aşağı qiymətləndirir ({market}% və {model}%) — "
        "model hansısa amili nəzərə almaya bilər."
    ),
    "fact.elo": "Elo reytinqi: {home} {home_rating}, {away} {away_rating}.",
    "fact.motivation": "Motivasiya: {home} — {home_reason}; {away} — {away_reason}.",
    "fact.rest": "İstirahət: {home} — {home_rest} gün, {away} — {away_rest} gün.",
    "fact.form_points": (
        "{team} son 5 oyunda {points} xal toplayıb ({sequence}), {opponent} isə {opp_points} xal ({opp_sequence})."
    ),
    "fact.venue_points": (
        "{home} evdə son {n} oyunda {points} xal, {away} səfərdə son {opp_n} oyunda {opp_points} xal toplayıb."
    ),
    "fact.no_xg": "xG məlumatı tam deyil — hücum keyfiyyəti əsasən qollarla qiymətləndirilib.",
    "fact.xg_diff": "Oyun başına xG fərqi (son 10): {home} {home_xgd}, {away} {away_xgd}.",
    "fact.absences": "{team} heyətində itkilər: {players}.",
    "fact.absences_both": "Heyət itkiləri: {players}.",
    "fact.h2h_record": "Son {n} qarşılaşma: {home} {home_wins} qələbə, {draws} heç-heçə, {away} {away_wins} qələbə.",
    "fact.total_rates": "Son 5 oyunda {line} {dir} nisbəti: {home} — {home_rate}%, {away} — {away_rate}%.",
    "fact.total_venue_rates": (
        "{home} ev oyunlarında {home_rate}%, {away} səfər oyunlarında {away_rate}% hallarda {line} {dir} olub."
    ),
    "fact.xg_expected_total": "xG əsasında gözlənilən qol sayı: {expected} (xətt {line}).",
    "fact.model_goals": "Modelin qol gözləntisi: {home} {lh}, {away} {la} (cəmi {total}).",
    "fact.h2h_total": "Son {n} qarşılaşmada {rate}% hallarda {line} {dir} olub, orta {avg} qol.",
    "fact.motivation_high_both": "Hər iki komanda üçün oyun çox vacibdir — ehtiyatlı oyun ehtimalı var.",
    "fact.motivation_low": "Komandalardan birinin motivasiyası aşağıdır — oyun açıq keçə bilər.",
    "fact.btts_rates": (
        "Son 5 oyunda hər iki komandanın qol vurduğu oyunlar: {home} — {home_rate}%, {away} — {away_rate}%."
    ),
    "fact.btts_venue_rates": (
        "{home} ev, {away} səfər oyunlarında hər iki komandanın qol vurduğu oyunlar: {home_rate}% və {away_rate}%."
    ),
    "fact.xg_expected_teams": "xG əsasında qol gözləntisi: {home} {eh}, {away} {ea}.",
    "fact.model_btts": "Model hər iki komandanın qol vurma ehtimalını {p}% hesablayır.",
    "fact.h2h_btts": "Son {n} qarşılaşmada {rate}% hallarda hər iki komanda qol vurub.",
    "fact.team_scoring": "{team} son 5 oyunda orta {gf} qol vurub, {opponent} isə orta {ga} qol buraxıb.",
    "fact.team_venue_scoring": "Ev/səfər bölgüsündə: {team} orta {gf} qol vurur, {opponent} orta {ga} qol buraxır.",
    "fact.xg_expected_team": "{team} üçün xG əsasında qol gözləntisi: {expected} (xətt {line}).",
    "fact.model_team_goals": "Model {team} üçün {lam} qol gözləyir (xətt {line}).",
    "fact.h2h_team_goals": "Son {n} qarşılaşmada {rate}% hallarda {team} üçün {line} {dir} keçib.",
    "fact.corners_rates": "Son 10 oyunda künclər {line} {dir}: {home} — {home_rate}%, {away} — {away_rate}%.",
    "fact.corners_venue": "{home} ev, {away} səfər oyunlarında künclər {line} {dir}: {home_rate}% və {away_rate}%.",
    "fact.model_corners": "Gözlənilən künc sayı: {expected} (xətt {line}).",
    "fact.cards_rates": "Son 10 oyunda kartlar {line} {dir}: {home} — {home_rate}%, {away} — {away_rate}%.",
    "fact.cards_venue": "{home} ev, {away} səfər oyunlarında kartlar {line} {dir}: {home_rate}% və {away_rate}%.",
    "fact.model_cards": "Gözlənilən kart sayı: {expected} (xətt {line}).",
    "fact.referee_cards": "Hakim {referee} oyun başına orta {avg} kart göstərir.",
    "fact.loss_probability": (
        "Model düz olsa belə, bu mərc təxminən {loss}% ehtimalla uduzur — təqribən hər {n} mərcdən biri."
    ),
    "fact.thin_history": "{team} üçün bazada cəmi {n} son oyun var — statistika daha az etibarlıdır.",
    "fact.doubtful": "Oynayıb-oynamayacağı şübhəli olanlar: {players}.",
    "fact.lineups_unknown": (
        "Start heyətləri hələ açıqlanmayıb. Heyət açıqlanandan sonra seçim yenidən qiymətləndiriləcək."
    ),

    # --- Selection labels ---
    "sel.1X2.1": "{home} qalib gələr (1)",
    "sel.1X2.2": "{away} qalib gələr (2)",
    "sel.1X2.X": "Heç-heçə (X)",
    "sel.DC.1X": "İkili şans 1X — {home} uduzmaz",
    "sel.DC.X2": "İkili şans X2 — {away} uduzmaz",
    "sel.DC.12": "İkili şans 12 — heç-heçə olmaz",
    "sel.DNB.1": "{home} — heç-heçəyə mərcsiz",
    "sel.DNB.2": "{away} — heç-heçəyə mərcsiz",
    "sel.AH.1": "{home} — Asiya handikapı {line}",
    "sel.AH.2": "{away} — Asiya handikapı {line}",
    "sel.OU": "{line} {dir}",
    "sel.OU_1H": "İlk hissə {line} {dir}",
    "sel.BTTS": "Hər iki komanda qol vurar — {dir}",
    "sel.TEAM_TOTAL_HOME": "{home} {line} {dir} qol",
    "sel.TEAM_TOTAL_AWAY": "{away} {line} {dir} qol",
    "sel.CORNERS_OU": "Künclər {line} {dir}",
    "sel.CARDS_OU": "Kartlar {line} {dir}",

    # --- Pick card ---
    "pick.bet": "🎯 Mərc: {label}",
    "pick.odds": "💰 Əmsal: {odds} ({bookmaker})",
    "pick.model": "📊 Model ehtimalı: {final}% (statistik model: {model}%, bazar: {market}%)",
    "pick.implied": "📌 Əmsalın göstərdiyi ehtimal: {implied}%",
    "pick.edge": "📈 Ehtimal üstünlüyü: {edge} faiz bəndi",
    "pick.ev": "💎 Dəyər üstünlüyü: {ev}",
    "pick.confidence": "⭐ Əminlik səviyyəsi: {confidence}/100 ({band})",
    "pick.risk": "{icon} Risk səviyyəsi: {risk}",
    "pick.fair_odds": "⚖️ Ədalətli əmsal: {fair} — bundan aşağı əmsalla mərc dəyərsizdir",
    "pick.quality": "🧾 Məlumat tamlığı: {score}% {icon} {level}",

    # --- Daily analysis ---
    "analysis.title": "⚽ GÜNÜN FUTBOL ANALİZİ",
    "analysis.best_pick": "🔥 GÜNÜN ƏN YAXŞI SEÇİMİ",
    "analysis.match_line": "⚽ {home} – {away}",
    "analysis.league_time": "🏆 {league} · 🕐 {time}",
    "analysis.league_datetime": "🏆 {league} · 📅 {date} · 🕐 {time}",
    "analysis.section_analysis": "📋 ANALİZ",
    "analysis.verdict_title": "✅ YEKUN RƏY",
    "analysis.why_title": "👍 NİYƏ BU MƏRC?",
    "analysis.against_title": "⚠️ BU MƏRC NİYƏ UDUZA BİLƏR?",
    "analysis.other_picks": "🎯 DİGƏR SEÇİMLƏR",
    "analysis.other_line": "{rank}. {home} – {away} · {label} · {odds} · əminlik {confidence}/100 · {risk_icon} {risk}",
    "analysis.detail_link": "   Ətraflı analiz: /oyun_{match_id}",
    "analysis.counts": (
        "📊 Analiz edilən oyunlar: {total} · ✅ Mərc edilə bilər: {bet} · 👀 İzləmədə: {watch} · ⛔ Mərc yoxdur: {no_bet}"
    ),
    "analysis.no_bet_title": "⛔ MƏRC YOXDUR",
    "analysis.no_bet_body": "Bu gün uyğun seçim tapılmadı. Sistem bu gün mərc etməməyi tövsiyə edir.",
    "analysis.no_bet_reasons": "Əsas səbəblər:",
    "analysis.reason_count": "• {reason} — {count} oyun",
    "analysis.watch_title": "👀 İZLƏMƏDƏ SAXLA",
    "analysis.watch_line": "{n}. {home} – {away} · {label} · {odds} · əminlik {confidence}/100 · dəyər {ev}",
    "analysis.watch_reason": "   Səbəb: {reasons} · /oyun_{match_id}",
    "analysis.not_run": "Bu tarix üçün analiz hələ aparılmayıb.",
    "analysis.no_matches": (
        "Analiz ediləcək oyun yoxdur: prioritet liqalarda oyun tapılmayıb və ya oyunlar artıq başlayıb."
    ),
    "analysis.pyramid_title": "💰 PİRAMİDA",
    "analysis.pyramid_stage": "Cari mərhələ: {stage} (cəhd {attempt})",
    "analysis.pyramid_balance": "Cari balans: {amount}",
    "analysis.pyramid_odds": "Seçilmiş əmsal: {odds}",
    "analysis.pyramid_next": "Nəzəri növbəti balans: {amount}",
    "analysis.pyramid_target": "Hədəf: {amount}",
    "analysis.pyramid_idle": "Bu gün yeni mərhələ açılmır.",
    "analysis.paper_note": "📝 Simulyasiya rejimi: seçimlər paper mərc kimi qeyd olunur, real mərc açılmır.",

    # --- Verdicts ---
    "verdict.bet": "Statistik model, bazar və əsas göstəricilər bu seçimi dəstəkləyir. Əsas üstünlük: {factors}.",
    "verdict.bet_plain": "Statistik model və bazar bu seçimdə üstünlük göstərir.",
    "verdict.watch": "Seçim maraqlıdır, amma şərtlərə tam cavab vermir: {reasons}. İzləmədə saxlanılır.",
    "verdict.no_bet": "Bu oyunda mərc tövsiyə edilmir: {reasons}.",

    # --- Match detail ---
    "detail.title": "⚽ {home} – {away}",
    "detail.decision": "QƏRAR: {icon} {decision}",
    "detail.not_found": "Bu ID ilə analiz edilmiş oyun tapılmadı.",
    "detail.candidates_title": "📈 MARKETLƏR (əmsal · model · bazar · dəyər · əminlik)",
    "detail.candidate_line": "{icon} {label} — {odds} · {model}% · {market}% · {ev} · {confidence}/100",

    # --- Analysis run report ---
    "analysis_run.title": "🧮 ANALİZ",
    "analysis_run.summary": "Analiz edilən oyunlar: {analyzed} · ✅ {bets} · 👀 {watch} · ⛔ {no_bet}",
    "analysis_run.model": "Model {matches} bitmiş oyun əsasında qurulub.",
    "analysis_run.match_failed": "⚠️ {match}: analiz zamanı xəta baş verdi.",

    # --- Pyramid ---
    "pyramid.title": "💰 PİRAMİDA İRƏLİLƏYİŞİ",
    "pyramid.paper": "📝 Simulyasiya rejimi — real mərc açılmır.",
    "pyramid.mode": "Rejim: {mode}",
    "pyramid.attempt": "Cəhd: {attempt}",
    "pyramid.start": "Başlanğıc: {amount}",
    "pyramid.balance": "Cari balans: {amount}",
    "pyramid.stage": "Cari mərhələ: {stage}",
    "pyramid.target": "Hədəf: {amount}",
    "pyramid.remaining": "Hədəfə qalan: {amount}",
    "pyramid.avg_odds": "Orta istifadə olunan əmsal: {odds}",
    "pyramid.no_bets": "hələ mərc yoxdur",
    "pyramid.stages_left": "Təxmini qalan mərhələ: {count} (əmsal {odds} ilə)",
    "pyramid.locked": "Kilidlənmiş qazanc: {amount}",
    "pyramid.total_staked": "Bütün cəhdlərə qoyulan: {amount} ({attempts} cəhd)",
    "pyramid.pending": "⏳ Gözləyən mərhələ: əmsal {odds}, nəzəri növbəti balans {amount}",
    "pyramid.target_reached": "🏆 Hədəfə çatılıb!",
    "pyramid.path_title": "📈 NƏZƏRİ YOL (əmsal {odds} ilə)",
    "pyramid.path_step": "{stage}. {before} → {after}",
    "pyramid.path_gap": "…",
    "pyramid.probability": (
        "🎲 Hər mərhələdə +{ev}% dəyər üstünlüyü fərziyyəsi ilə hədəfə çatma ehtimalı: {probability}"
    ),
    "pyramid.probability_note": (
        "Bu, nəzəri hesabdır: bir məğlubiyyət cəhdi bitirir və yeni cəhd başlanğıc məbləğdən başlayır."
    ),
    "pyramid.error_pending_exists": "Gözləyən mərhələ artıq var. Əvvəlcə onun nəticəsini qeyd edin.",
    "pyramid.error_no_pending": "Nəticəsi gözlənilən mərhələ yoxdur.",
    "pyramid.error_bad_odds": "Əmsal 1.00 rəqəmindən böyük olmalıdır.",
    "pyramid.error_target_reached": "Hədəfə artıq çatılıb. Yeni cəhd üçün piramidanı sıfırlayın.",

    # --- System status ---
    "status.title": "🛠 SİSTEM VƏZİYYƏTİ",
    "status.mode_paper": "Rejim: 📝 Simulyasiya (real mərc açılmır)",
    "status.mode_live": "Rejim: 💵 Real",
    "status.last_run": "Son yükləmə: {value}",
    "status.last_run_value": "{date} — {status}",
    "status.never": "hələ olmayıb",
    "status.api_plan": "API planı: {plan}",
    "status.api_quota": "API sorğuları (bu gün): {used} istifadə edilib, {remaining} qalıb (limit {limit})",
    "status.api_unavailable": "API sorğuları: məlum deyil ({reason})",
    "status.db": "Bazada: {matches} oyun · {teams} komanda · {odds} əmsal qeydi",
    "status.telegram": "Telegram: {value}",
    "status.llm": "LLM analizi: {value}",
    "status.enabled": "aktiv",
    "status.disabled": "deaktiv",
    "status.llm_enabled_pending": "konfiqurasiyada aktivdir, analiz modulu hələ qoşulmayıb",
    "status.next_run": "Növbəti planlı yükləmə: {value}",
    "status.not_scheduled": "planlaşdırılmayıb",

    # --- Telegram bot ---
    "bot.welcome": "👋 Xoş gəlmisiniz!\nBu bot futbol oyunlarını statistik analiz edən şəxsi köməkçinizdir.",
    "bot.commands_title": "Əmrlər:",
    "bot.cmd.start": "Başlanğıc və əmrlər",
    "bot.cmd.bugun": "Günün oyunları",
    "bot.cmd.secimler": "Günün analizi və seçimlər",
    "bot.cmd.piramida": "Piramida irəliləyişi",
    "bot.cmd.status": "Sistem vəziyyəti",
    "bot.cmd.yenile": "Məlumatları indi yenilə",
    "bot.cmd.komek": "Kömək",
    "bot.help_footer": "Avtomatik hesabat: hər gün saat {time}.",
    "bot.unauthorized": "⛔ Bu bot şəxsi istifadə üçündür.",
    "bot.setup_mode_title": "🔧 Quraşdırma rejimi: TELEGRAM_CHAT_ID hələ təyin edilməyib.",
    "bot.your_chat_id": "Sizin chat ID: ",
    "bot.setup_mode_hint": (
        "Bu rəqəmi .env faylında TELEGRAM_CHAT_ID dəyərinə yazın və sistemi yenidən başladın."
    ),
    "bot.refresh_started": "🔄 Məlumatlar yüklənir… Bu bir neçə dəqiqə çəkə bilər. Bitəndə hesabat göndəriləcək.",
    "bot.refresh_busy": "⏳ Məlumat yüklənməsi artıq gedir. Bitəndə hesabat göndəriləcək.",
    "bot.unknown_command": "Bu əmri tanımıram. Əmrlərin siyahısı üçün /komek yazın.",
    "bot.test_message": "✅ Telegram bağlantısı işləyir.",

    # --- CLI ---
    "cli.description": "Futbol Analiz Sistemi — əmr sətri alətləri",
    "cli.command_metavar": "<əmr>",
    "cli.help.init_db": "Verilənlər bazasını yarat / yenilə",
    "cli.help.ingest": "Günün məlumatlarını API-dən yüklə",
    "cli.help.today": "Günün oyunlarını göstər",
    "cli.help.pyramid": "Piramida irəliləyişini göstər",
    "cli.help.status": "Sistem vəziyyətini göstər",
    "cli.help.leagues": "API-Football liqa ID-lərini axtar və ya yoxla",
    "cli.help.send_test": "Telegram-a sınaq mesajı göndər",
    "cli.help.send_today": "Günün analizini Telegram-a göndər",
    "cli.help.cache_purge": "Vaxtı keçmiş cache qeydlərini sil",
    "cli.help.analyze": "Yüklənmiş məlumatlar əsasında analizi apar",
    "cli.help.sync": "Yalnız pulsuz mənbələrdən tarixçəni yüklə (football-data.co.uk / .org)",
    "cli.help.run_daily": "Planlı gündəlik iş (GitHub Actions): yükləmə, analiz, günün ilk işində Telegram mesajı",
    "cli.help.bot_poll": "Telegram-da gözləyən mesajlara cavab ver (GitHub Actions)",
    "cli.bot_polled": "Cavablandırılan mesaj: {count}",
    "cli.sync_disabled": "Pulsuz mənbələr config.yaml-da söndürülüb (external_data.enabled: false).",
    "cli.help.picks": "Günün analizini və seçimləri göstər",
    "cli.help.match": "Bir oyunun ətraflı analizini göstər",
    "cli.help.match_id": "Oyunun ID-si (/bugun siyahısındakı /oyun_ID rəqəmi)",
    "cli.help.date": "Tarix (İİİİ-AA-GG). Göstərilməsə — bu gün",
    "cli.help.country": "API-dəki ölkə adı, ingiliscə (məs. Azerbaijan)",
    "cli.help.search": "Liqa adına görə axtarış (ən azı 3 hərf)",
    "cli.help.check": "config.yaml-dakı liqa ID-lərini API ilə yoxla",
    "cli.db_ready": "✅ Verilənlər bazası hazırdır.",
    "cli.sent": "✅ Mesaj göndərildi.",
    "cli.cache_purged": "🧹 {count} köhnə cache qeydi silindi.",
    "cli.leagues_none": "Heç bir liqa tapılmadı.",
    "cli.leagues_need_filter": "--country, --search və ya --check parametrlərindən birini göstərin.",
    "cli.leagues_header": "   ID | Liqa | Ölkə | Növ",
    "cli.leagues_row": "{api_id:>5} | {name} | {country} | {kind}",
    "cli.leagues_check_ok": "✅ {api_id:>5} | {name_az} → API: {name} ({country})",
    "cli.leagues_check_mismatch": "⚠️ {api_id:>5} | {name_az} → API: {name} ({country}) — ölkə uyğun gəlmir",
    "cli.leagues_check_missing": "❌ {api_id:>5} | {name_az} → API-də tapılmadı",
    "cli.interrupted": "Dayandırıldı.",

    # --- Web ---
    "web.title": "Futbol Analiz Sistemi",
    "web.running": "✅ Sistem işləyir.",
    "web.dashboard_soon": (
        "Tam idarə paneli növbəti mərhələdə əlavə olunacaq. Hazırda bu məlumat ünvanları mövcuddur:"
    ),
    "web.endpoint.status": "Sistem vəziyyəti",
    "web.endpoint.matches": "Günün oyunları",
    "web.endpoint.pyramid": "Piramida",
    "web.endpoint.picks": "Günün analizi və seçimlər",
    "web.unauthorized": "Giriş üçün parol tələb olunur.",
    "web.not_found": "Səhifə tapılmadı.",
    "web.invalid_request": "Sorğu parametrləri yanlışdır.",
    "web.error": "Sorğunu emal etmək mümkün olmadı.",
}


def t(key: str, **kwargs: Any) -> str:
    """Return the Azerbaijani text for ``key``.

    An unknown key raises ``KeyError`` on purpose: a missing text must fail
    loudly in tests rather than leak a key or an English fallback to users.
    """
    template = MESSAGES[key]
    return template.format(**kwargs) if kwargs else template
