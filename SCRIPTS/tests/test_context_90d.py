"""FINESTRA DI CONTESTO 90D (al posto del 65D): 7D = SHORT_TREND, 30D = PRIMARY_DECISION, 90D = MEDIUM_CONTEXT, Lifetime = LONG_HISTORY. Il 90D non decide mai da solo; gli snapshot 65D gia'
importati restano nel DB (LEGACY: leggibili, mai sommati al 90D, mai preferiti quando esiste un 90D). Schema v9 (il CHECK su window_label ammette RECENT_90D e conserva RECENT_65D).
Fixture sintetiche, workspace temporanei: mai il DB reale, mai books.json reale."""
import json
import sqlite3
import sys
import tempfile
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]
sys.path.insert(0, str(ROOT / "DASHBOARD"))
sys.path.insert(0, str(ROOT / "SCRIPTS"))
sys.path.insert(0, str(HERE))

import test_global_reports as tg
import test_recent_escalation as tre
import ui_labels as U
from kdp_ads import escalation as ESC
from kdp_ads import globalreport as G
from kdp_ads import globalstore as GS
from kdp_ads import registry
from kdp_ads.db import SCHEMA_VERSION, connect
from kdp_ads.snapshots import window_label, window_role
from ops import global_import as GI

RANGE65 = "10 ago 2026 - 13 ott 2026"                  # 65 giorni: finestra LEGACY
RULES = json.loads((ROOT / "CONFIG" / "rules.json").read_text(encoding="utf-8"))


# ------------------------------------------------------------------ gerarchia e riconoscimento
class HierarchyTests(unittest.TestCase):
    def test_roles_are_short_trend_primary_medium_context_long_history(self):
        self.assertEqual(G.WINDOW_ROLES["RECENT_7D"], "SHORT_TREND")
        self.assertEqual(G.WINDOW_ROLES["RECENT_30D"], "PRIMARY_DECISION")
        self.assertEqual(G.WINDOW_ROLES["RECENT_90D"], "MEDIUM_CONTEXT")
        self.assertEqual(G.WINDOW_ROLES["LIFETIME"], "LONG_HISTORY")
        self.assertEqual(G.WINDOW_ROLES["RECENT_65D"], "LEGACY_CONTEXT")                  # il 65D non e' piu' una finestra del workflow: solo legacy
        cfg = RULES["snapshots"]
        self.assertEqual([window_role("DATE_RANGE", d1, d2, cfg) for d1, d2 in (("2026-10-07", "2026-10-13"), ("2026-09-14", "2026-10-13"), ("2026-07-16", "2026-10-13"))],
                         ["SHORT_TREND", "TACTICAL", "RECENT_CONTEXT"])
        self.assertEqual(window_role("LIFETIME", None, "2026-10-13", cfg), "HISTORICAL_CONTEXT")

    def test_a_standard_90_day_window_is_recognised_as_90d_not_65d_or_unknown(self):
        self.assertEqual(window_label("DATE_RANGE", "2026-07-16", "2026-10-13"), "RECENT_90D")
        self.assertEqual(G.WINDOWS["RECENT_90D"], 90)

    def test_search_term_90d_replaces_65d_in_the_slots_and_in_the_weekly_monthly_split(self):
        self.assertEqual(GS.SLOTS["SEARCH_TERM_90D"], ("SEARCH_TERM", "RECENT_90D"))
        self.assertNotIn("SEARCH_TERM_65D", GS.SLOTS)                                       # nessuna finestra parallela: il 65D non si importa piu'
        self.assertEqual(set(GS.SLOTS), {"TARGETING_7D", "TARGETING_30D", "TARGETING_LIFETIME", "SEARCH_TERM_7D", "SEARCH_TERM_30D", "SEARCH_TERM_90D"})
        self.assertEqual(set(GS.WEEKLY_SLOTS), {"SEARCH_TERM_7D", "SEARCH_TERM_30D", "SEARCH_TERM_90D", "TARGETING_7D", "TARGETING_30D"})
        self.assertEqual(GS.MONTHLY_SLOTS, ("TARGETING_LIFETIME",))
        self.assertEqual(GS.SLOT_LABELS["SEARCH_TERM_90D"], "Search Term 90D")

    def test_ui_texts_say_90d_and_the_legacy_label_is_marked(self):
        self.assertIn("Search Term 90D", U.GLOBAL_FREQ_NOTE)
        self.assertNotIn("65", U.GLOBAL_FREQ_NOTE + U.GLOBAL_INTRO)                        # nessun 65D nel workflow attuale
        self.assertEqual(U.label_window("RECENT_90D"), "Ultimi 90 giorni")
        self.assertEqual(U.label_window("RECENT_65D"), "Ultimi 65 giorni (legacy)")        # solo per gli snapshot storici
        self.assertEqual(json.loads((ROOT / "CONFIG" / "settings.json").read_text(encoding="utf-8"))["search_term_max_days"], 90)


# ------------------------------------------------------------------ regole: 90D = contesto, mai decisione
class ContextRulesTests(unittest.TestCase):
    CTX = tre.EscalationUnitTests.CTX
    CFG = tre.EscalationUnitTests.CFG

    def m(self, *a):
        return tre.EscalationUnitTests.m(self, *a)

    def ev(self, T, L=None, C=None, m7=None):
        return ESC.evaluate(T, L, C, m7, self.CTX, self.CFG)

    def test_90d_alone_never_generates_a_decision(self):
        bad90 = self.m(60, 0, 55.0, 0.0)
        self.assertIsNone(self.ev(None, C=bad90, m7=self.m(9, 0, 8.0)))
        self.assertIsNone(self.ev(None, C=bad90))

    def test_negative_30d_7d_and_90d_strengthens_the_escalation(self):
        T, m7 = self.m(18, 0, 17.59), self.m(9, 0, 8.59)
        bad90 = self.m(40, 0, 40.0, 0.0)
        self.assertEqual(self.ev(T, C=None, m7=m7)["step"], 10)                         # senza 90D: conservativo
        strong = self.ev(T, C=bad90, m7=m7)
        self.assertEqual(strong["step"], 100)                                           # 90D conferma un problema persistente: la pausa si sblocca
        self.assertIn("90D conferma un problema persistente", strong["logic"])

    def test_good_90d_limits_the_escalation(self):
        T, m7 = self.m(18, 0, 17.59), self.m(9, 0, 8.59)
        good90 = self.m(60, 6, 50.0, 220.0)
        lim = self.ev(T, C=good90, m7=m7)
        self.assertEqual(lim["step"], 5)                                                # calo temporaneo: massimo -5%
        self.assertIn("90D", lim["logic"])

    def test_a_recovering_7d_means_the_90d_cannot_force_a_cut(self):
        T, bad90 = self.m(18, 0, 17.59), self.m(40, 0, 40.0, 0.0)
        recovering = self.m(9, 3, 7.0)                                                  # 7D con acquisti: smentisce
        self.assertIsNone(self.ev(T, C=bad90, m7=recovering))
        self.assertIsNone(self.ev(T, C=bad90, m7=None))                                 # senza 7D nessuna escalation, qualunque sia il 90D

    def test_strong_lifetime_moderates_but_does_not_block_a_confirmed_deterioration(self):
        T, m7, bad90 = self.m(24, 0, 30.0), self.m(9, 0, 8.59), self.m(40, 0, 40.0, 0.0)
        L = self.m(80, 8, 50.0, 183.92)
        out = self.ev(T, L=L, C=bad90, m7=m7)
        self.assertEqual(out["step"], 10)                                               # Lifetime forte: modera (mai pausa)...
        self.assertIsNotNone(out)                                                       # ...ma il deterioramento confermato resta un'azione
        self.assertEqual(window_role("LIFETIME", None, "2026-10-13", RULES["snapshots"]), "HISTORICAL_CONTEXT")

    def test_context_state_uses_the_90d_wording(self):
        self.assertEqual(ESC.context_state(self.m(40, 0, 40.0, 0.0), self.CTX, self.CFG), "PERSISTENT")
        self.assertEqual(ESC.context_state(None, self.CTX, self.CFG), "UNKNOWN")
        self.assertIn("90D", ESC.__doc__)
        self.assertNotIn("65D", ESC.__doc__)


# ------------------------------------------------------------------ import 90D + snapshot 65D legacy nel motore
class Scenario90(tre.Scenario):
    """Rare Coins / Route 66 / Twenty Women con il Search Term 90D importato (C90 = importa il Search Term 90D di contesto)."""
    LIFE, C90 = False, True


class Import90dTests(Scenario90):
    def test_the_90d_global_report_imports_as_recent_90d_with_its_dates(self):
        g = self.q("SELECT * FROM global_report_imports WHERE window_label='RECENT_90D'")[0]
        self.assertEqual((g["report_type"], g["window_days"], g["date_from"], g["date_to"], g["period_id"], g["status"]),
                         ("SEARCH_TERM", 90, "2026-07-16", "2026-10-13", "2026-07-16__2026-10-13", "ACTIVE"))
        self.assertEqual(self.q("SELECT COUNT(*) n FROM global_report_imports WHERE window_label='RECENT_65D'")[0]["n"], 0)      # nessun nuovo 65D

    def test_the_engine_uses_the_90d_as_the_current_context_and_30d_stays_primary(self):
        d = self.decision(tre.BOOK_TW, tre.T_TW1)
        self.assertEqual(d["recent_window"], "RECENT_90D")
        self.assertEqual(d["tactical_window"], "RECENT_30D")                              # il 30D decide
        self.assertEqual((d["recent_clicks"], d["recent_orders"]), (100.0, 8.0))          # il 90D da solo, mai sommato ad altre finestre
        self.assertEqual((d["tactical_clicks"], d["tactical_orders"]), (40.0, 4.0))


class Legacy65dTests(tre.Scenario):
    """Un Search Term 65D LEGACY gia' importato (staged direttamente: la pagina non lo importa piu') accanto al 90D."""
    LIFE, C90 = False, True
    WITH_LEGACY = True

    @classmethod
    def setUpClass(cls):
        cls.base = tg.WsBase()
        cls.base.setUp()
        cls.ws, cls.root = cls.base.ws, cls.base.root
        (cls.root / "CONFIG" / "books.json").write_text(json.dumps(tre.BOOKS), encoding="utf-8")
        files = tre.files(False, cls.C90)
        if not cls.C90:
            files.pop("SEARCH_TERM_90D", None)
        assert GI.run_import(cls.ws, files, None, recalc=False).ok
        c = connect(cls.ws.db_path)
        try:
            books = registry.load_books(cls.ws.books_path)
            parsed = G.parse_global_report("legacy65.csv", tg.csv_text(tg.ST_HEAD, [tre.strow(tre.C_RC, tre.T_COLLECT, tre.T_COLLECT, "PHRASE", "2101", 41, 0, 38.0, 0.0, RANGE65)]))
            cls.legacy = GS.stage_file(c, parsed, "RECENT_65D", None, books)
            c.commit()
        finally:
            c.close()
        for cid, book in (("555000111222333", tre.BOOK_RC), ("555000111222555", tre.BOOK_RC), ("555000111222444", tre.BOOK_RC), ("12345678901234567890", tre.BOOK_R66), ("777000111222333", tre.BOOK_TW)):
            assert GI.associate(cls.ws, cid, book, recalc=False).ok
        assert all(x["ok"] for x in GI.recalculate(cls.ws, {(tre.BOOK_RC, "US"), (tre.BOOK_R66, "US"), (tre.BOOK_TW, "US")}))

    def test_the_legacy_65d_snapshot_is_stored_and_readable_and_never_deleted(self):
        self.assertIn(self.legacy["status"], ("IMPORTED",))
        g = self.q("SELECT * FROM global_report_imports WHERE window_label='RECENT_65D'")
        self.assertEqual(len(g), 1)
        self.assertEqual((g[0]["window_days"], g[0]["status"], g[0]["date_from"], g[0]["date_to"]), (65, "ACTIVE", "2026-08-10", "2026-10-13"))
        self.assertTrue(self.q("SELECT 1 FROM search_term_rows WHERE period_id='2026-08-10__2026-10-13' LIMIT 1"))     # materializzato e leggibile
        self.assertEqual(U.label_window("RECENT_65D"), "Ultimi 65 giorni (legacy)")
        c = connect(self.ws.db_path)
        try:
            self.assertTrue(any(i["window_label"] == "RECENT_65D" for i in GS.list_imports(c)))                       # visibile nello storico/audit
        finally:
            c.close()

    def test_the_legacy_65d_does_not_block_the_90d_import_nor_duplicate_it(self):
        self.assertEqual(self.q("SELECT COUNT(*) n FROM global_report_imports WHERE window_label='RECENT_90D'")[0]["n"], 1)
        self.assertEqual(self.q("SELECT COUNT(*) n FROM global_report_imports WHERE window_label='RECENT_65D'")[0]["n"], 1)
        keys = self.q("SELECT report_type, window_label, date_from, date_to, COUNT(*) n FROM global_report_imports WHERE status='ACTIVE' GROUP BY 1,2,3,4 HAVING n>1")
        self.assertEqual(keys, [])

    def test_the_90d_is_preferred_to_the_legacy_65d_and_they_are_never_summed(self):
        d = self.decision(tre.BOOK_RC, tre.T_COLLECT)
        self.assertEqual(d["recent_window"], "RECENT_90D")                                # esiste un 90D: il 65D legacy non e' la finestra corrente
        self.assertEqual(d["recent_clicks"], 30.0)                                        # i clic del 90D (30), non quelli del 65D (41) ne' la somma (71)
        self.assertEqual(d["recent_spend"], 28.0)
        rows = self.q("SELECT COUNT(DISTINCT period_id) n FROM search_term_rows WHERE book_id=? AND campaign_id='FRASE_RARE_COINS'", tre.BOOK_RC)
        self.assertGreaterEqual(rows[0]["n"], 3)                                          # 30D + 65D legacy + 90D coesistono come snapshot distinti

    def test_the_legacy_65d_is_used_as_context_only_when_no_90d_exists(self):
        d = self.decision(tre.BOOK_RC, tre.T_COLLECT)
        # stato senza 90D: stessa fixture ma solo 65D -> contesto 65D (compatibilita' transitoria)
        from kdp_ads.opportunities import campaign_windows
        recs = [{"campaign_id": "C", "period_id": "2026-08-10__2026-10-13", "report_scope": "DATE_RANGE", "date_from": "2026-08-10", "date_to": "2026-10-13"},
                {"campaign_id": "C", "period_id": "2026-09-14__2026-10-13", "report_scope": "DATE_RANGE", "date_from": "2026-09-14", "date_to": "2026-10-13"}]
        w = campaign_windows(recs, RULES["snapshots"])
        self.assertEqual((w["C"]["tactical"], w["C"]["confirm"]), ("2026-09-14__2026-10-13", "2026-08-10__2026-10-13"))
        recs.append({"campaign_id": "C", "period_id": "2026-07-16__2026-10-13", "report_scope": "DATE_RANGE", "date_from": "2026-07-16", "date_to": "2026-10-13"})
        self.assertEqual(campaign_windows(recs, RULES["snapshots"])["C"]["confirm"], "2026-07-16__2026-10-13")      # con il 90D presente vince il 90D
        self.assertTrue(d)


# ------------------------------------------------------------------ tolleranza 85-95 giorni e freschezza 90D vs 65D legacy
class WindowToleranceTests(unittest.TestCase):
    def label(self, days):
        from datetime import date, timedelta
        end = date(2026, 10, 13)
        return window_label("DATE_RANGE", (end - timedelta(days=days - 1)).isoformat(), end.isoformat())

    def test_89_90_91_95_days_are_recent_90d_and_84_is_not(self):
        for n in (85, 89, 90, 91, 95):
            self.assertEqual(self.label(n), "RECENT_90D", n)
        for n in (84, 96):
            self.assertNotEqual(self.label(n), "RECENT_90D", n)
        self.assertEqual(self.label(84), "RECENT_84D")

    def test_7d_30d_and_the_legacy_65d_are_never_classified_as_90d(self):
        self.assertEqual([self.label(n) for n in (7, 30, 65, 28)], ["RECENT_7D", "RECENT_30D", "RECENT_65D", "RECENT_28D"])

    def test_91_and_95_day_windows_keep_the_medium_context_role(self):
        from datetime import date, timedelta
        cfg = RULES["snapshots"]
        end = date(2026, 10, 13)
        for n in (89, 90, 91, 95):
            self.assertEqual(window_role("DATE_RANGE", (end - timedelta(days=n - 1)).isoformat(), end.isoformat(), cfg), "RECENT_CONTEXT", n)
        self.assertEqual(window_role("DATE_RANGE", (end - timedelta(days=64)).isoformat(), end.isoformat(), cfg), "RECENT_CONTEXT")          # il 65D legacy resta contesto


class PickContextUnitTests(unittest.TestCase):
    def pick(self, items, ref="2026-11-10", tol=7):
        from kdp_ads.windows import pick_context
        return pick_context(items, ref, tol)

    def test_fresh_90d_wins_over_a_fresh_65d(self):
        self.assertEqual(self.pick([("65", 65, "2026-11-10"), ("90", 90, "2026-11-10")]), "90")

    def test_stale_90d_loses_to_a_fresh_65d_legacy(self):
        self.assertEqual(self.pick([("65", 65, "2026-11-10"), ("90", 90, "2026-10-15")]), "65")        # 90D di 26 giorni prima: stale

    def test_tolerance_is_configurable_and_a_new_fresh_90d_wins_again(self):
        items = [("65", 65, "2026-11-10"), ("90", 90, "2026-11-05")]
        self.assertEqual(self.pick(items, tol=7), "90")                                                   # 5 giorni di scarto: dentro la tolleranza
        self.assertEqual(self.pick(items, tol=3), "65")                                                   # tolleranza piu' stretta
        self.assertEqual(self.pick(items + [("90b", 90, "2026-11-10")], tol=7), "90b")                    # arriva un 90D fresco: si torna al 90D

    def test_single_candidates_and_nothing(self):
        self.assertEqual(self.pick([("65", 65, "2026-11-10")]), "65")
        self.assertEqual(self.pick([("90", 90, "2026-11-10")]), "90")
        self.assertIsNone(self.pick([]))
        self.assertEqual(self.pick([("90", 90, "2026-08-01"), ("65", 65, "2026-09-01")]), "65")           # nessuno fresco: il piu' recente
        self.assertEqual(self.pick([("65", 65, "2026-09-01"), ("90", 90, "2026-09-01")], ref=None), "90")


RANGE90_FRESH = "16 lug 2026 - 13 ott 2026"            # 90 giorni, date_to = date_to del 30D corrente
RANGE90_STALE = "1 lug 2026 - 28 set 2026"             # 90 giorni, ma finisce 15 giorni prima del 30D corrente


class FreshnessScenarioBase(unittest.TestCase):
    """Un workspace per scenario (copia sintetica, mai il DB reale): 30D corrente (date_to 2026-10-13) + 90D e/o 65D legacy con numeri DIVERSI (90D: 30 clic; 65D: 41 clic)."""
    R90, R65 = None, None

    @classmethod
    def setUpClass(cls):
        if cls is FreshnessScenarioBase:
            raise unittest.SkipTest("base")
        cls.base = tg.WsBase()
        cls.base.setUp()
        cls.ws, cls.root = cls.base.ws, cls.base.root
        (cls.root / "CONFIG" / "books.json").write_text(json.dumps(tre.BOOKS), encoding="utf-8")
        files = tre.files(False, False)
        if cls.R90:
            files["SEARCH_TERM_90D"] = ("s90.csv", tg.csv_text(tg.ST_HEAD, [tre.strow(tre.C_RC, tre.T_COLLECT, tre.T_COLLECT, "PHRASE", "2101", 30, 0, 28.0, 0.0, cls.R90)]))
        assert GI.run_import(cls.ws, files, None, recalc=False).ok
        if cls.R65:
            c = connect(cls.ws.db_path)
            try:
                parsed = G.parse_global_report("legacy65.csv", tg.csv_text(tg.ST_HEAD, [tre.strow(tre.C_RC, tre.T_COLLECT, tre.T_COLLECT, "PHRASE", "2101", 41, 0, 38.0, 0.0, cls.R65)]))
                GS.stage_file(c, parsed, "RECENT_65D", None, registry.load_books(cls.ws.books_path))
                c.commit()
            finally:
                c.close()
        for cid, book in (("555000111222333", tre.BOOK_RC), ("555000111222555", tre.BOOK_RC), ("555000111222444", tre.BOOK_RC)):
            assert GI.associate(cls.ws, cid, book, recalc=False).ok
        assert all(x["ok"] for x in GI.recalculate(cls.ws, {(tre.BOOK_RC, "US")}))
        cls.d = cls.base.q("SELECT * FROM decisions WHERE book_id=? AND target=? ORDER BY run_id DESC LIMIT 1", tre.BOOK_RC, tre.T_COLLECT)[0]

    @classmethod
    def tearDownClass(cls):
        if cls is not FreshnessScenarioBase:
            cls.base.tearDown()


class ScenarioA_StaleNinetyFreshLegacy(FreshnessScenarioBase):
    R90, R65 = RANGE90_STALE, RANGE65

    def test_the_fresh_65d_legacy_is_used_temporarily(self):
        self.assertEqual(self.d["recent_window"], "RECENT_65D")
        self.assertEqual(self.d["recent_clicks"], 41.0)                                                   # solo il 65D: mai 41 + 30
        self.assertEqual(self.d["tactical_window"], "RECENT_30D")


class ScenarioB_BothFresh(FreshnessScenarioBase):
    R90, R65 = RANGE90_FRESH, RANGE65

    def test_the_90d_is_used(self):
        self.assertEqual(self.d["recent_window"], "RECENT_90D")
        self.assertEqual(self.d["recent_clicks"], 30.0)                                                   # non 41 e non la somma 71


class ScenarioC_OnlyLegacy(FreshnessScenarioBase):
    R90, R65 = None, RANGE65

    def test_the_legacy_65d_is_used(self):
        self.assertEqual((self.d["recent_window"], self.d["recent_clicks"]), ("RECENT_65D", 41.0))


class ScenarioD_OnlyNinety(FreshnessScenarioBase):
    R90, R65 = RANGE90_FRESH, None

    def test_the_90d_is_used(self):
        self.assertEqual((self.d["recent_window"], self.d["recent_clicks"]), ("RECENT_90D", 30.0))


class ScenarioE_Neither(FreshnessScenarioBase):
    R90, R65 = None, None

    def test_conservative_behaviour_without_a_medium_context(self):
        self.assertIsNone(self.d["recent_window"])
        self.assertIsNone(self.d["recent_clicks"])
        self.assertEqual(self.d["final_action"], "BID_DOWN_10_CANDIDATE")                                  # 30D + 7D (R1) invariati
        self.assertNotIn("conferma un problema persistente", self.d["reason"])                             # nessuna conferma da un contesto assente


class StaleNinetyAloneIsStillTheOnlyContext(FreshnessScenarioBase):
    """Un 90D stale senza legacy e' l'unico contesto disponibile: si usa (meglio di niente) ma il 30D resta la finestra decisionale."""
    R90, R65 = RANGE90_STALE, None

    def test_the_only_available_context_is_used_and_30d_stays_primary(self):
        self.assertEqual(self.d["recent_window"], "RECENT_90D")
        self.assertEqual(self.d["tactical_window"], "RECENT_30D")


# ------------------------------------------------------------------ schema v9: nessuna perdita, 65D conservato, 90D ammesso
def make_v8(d) -> Path:
    """DB come DISTRIBUITO alla v8: CHECK window_label con 7D/30D/65D/LIFETIME (NIENTE 90D), indici ux_gri_*_v8, manual_actions con una riga, uno snapshot 65D importato.
    Costruito regredendo un DB nuovo (DROP/RENAME solo nel TEST)."""
    p = Path(d) / "v8.db"
    connect(p).close()
    c = sqlite3.connect(p)
    c.execute("PRAGMA foreign_keys=OFF")
    c.execute("PRAGMA legacy_alter_table=ON")
    c.execute("ALTER TABLE global_report_imports RENAME TO tmp_v9")
    for ix in ("ux_gri_active_v9", "ux_gri_file_mp_v9"):
        c.execute(f"DROP INDEX IF EXISTS {ix}")
    ddl = GS._IMPORTS_DDL.format(name="global_report_imports").replace("'RECENT_7D','RECENT_30D','RECENT_90D','RECENT_65D','LIFETIME'", "'RECENT_7D','RECENT_30D','RECENT_65D','LIFETIME'")
    c.executescript(ddl + """
CREATE UNIQUE INDEX ux_gri_active_v8 ON global_report_imports(report_type, window_label, date_from, date_to, marketplace, COALESCE(account_id, '')) WHERE status='ACTIVE';
CREATE UNIQUE INDEX ux_gri_file_mp_v8 ON global_report_imports(file_sha256, marketplace);""")
    c.execute("DROP TABLE tmp_v9")
    c.execute("PRAGMA legacy_alter_table=OFF")
    for i, (rt, wl, days, d1, d2) in enumerate((("SEARCH_TERM", "RECENT_65D", 65, "2026-08-10", "2026-10-13"), ("TARGETING", "RECENT_30D", 30, "2026-09-14", "2026-10-13")), 1):
        c.execute("INSERT INTO global_report_imports (report_type,window_label,window_days,date_from,date_to,period_id,marketplace,currency,file_name,file_sha256,imported_at,row_count,"
                  "campaign_count,account_id,status,revision,source_gimport_id) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,'ACTIVE',1,?)",
                  (rt, wl, days, d1, d2, f"{d1}__{d2}", "US", "USD", f"f{i}.csv", f"sha{i}", "2026-10-14T10:00:00", 2, 1, "acc", i))
        for cid in ("1", "2"):
            c.execute("INSERT INTO global_report_rows (gimport_id,amazon_campaign_id,account_id,campaign_name,clicks,materialized,raw_json,marketplace) VALUES (?,?,?,?,?,?,?,?)",
                      (i, cid, "acc", "FRASE", 7, 0, "{}", "US"))
    c.execute("INSERT INTO manual_actions (created_at, executed_at, account_id, marketplace, book_id, campaign_id, target_identity, action_type, status) "
              "VALUES ('2026-11-05T09:21:03','2026-11-05T09:21:03','acc','US','B1','C1','coin books','PAUSE','EXECUTED')")
    c.execute("PRAGMA user_version=8")
    c.commit()
    c.close()
    return p


class SchemaV9Tests(unittest.TestCase):
    def test_schema_version_is_9_and_a_fresh_db_is_born_v9_without_backup(self):
        self.assertEqual(SCHEMA_VERSION, 9)
        with tempfile.TemporaryDirectory() as d:
            c = connect(Path(d) / "k.db")
            self.assertEqual(c.execute("PRAGMA user_version").fetchone()[0], 9)
            sql = c.execute("SELECT sql FROM sqlite_master WHERE name='global_report_imports'").fetchone()[0]
            self.assertIn("RECENT_90D", sql)
            self.assertIn("RECENT_65D", sql)                                              # il legacy resta ammesso
            names = {r[0] for r in c.execute("SELECT name FROM sqlite_master WHERE type='index'")}
            self.assertTrue({"ux_gri_active_v9", "ux_gri_file_mp_v9"} <= names)
            c.close()
            self.assertFalse((Path(d) / "backups").exists())

    def test_v8_migrates_to_v9_with_one_backup_keeping_every_row_and_the_65d_snapshot(self):
        with tempfile.TemporaryDirectory() as d:
            p = make_v8(d)
            raw = sqlite3.connect(p)
            before = raw.execute("SELECT window_label, COUNT(*) FROM global_report_imports GROUP BY 1 ORDER BY 1").fetchall()
            rows_before = raw.execute("SELECT COUNT(*) FROM global_report_rows").fetchone()[0]
            ma_before = raw.execute("SELECT * FROM manual_actions").fetchall()
            raw.close()
            c = connect(p)                                                                # MIGRAZIONE v8 -> v9
            self.assertEqual(c.execute("PRAGMA user_version").fetchone()[0], 9)
            self.assertEqual([tuple(r) for r in c.execute("SELECT window_label, COUNT(*) FROM global_report_imports GROUP BY 1 ORDER BY 1")], before)     # 65D incluso: nessuna riga persa
            self.assertEqual(c.execute("SELECT COUNT(*) FROM global_report_rows").fetchone()[0], rows_before)
            self.assertEqual([tuple(r) for r in c.execute("SELECT * FROM manual_actions")], [tuple(r) for r in ma_before])                    # le azioni manuali non vengono toccate
            self.assertEqual(c.execute("SELECT COUNT(*) FROM global_report_imports_v8_old").fetchone()[0], 2)    # la vecchia tabella resta come audit
            self.assertEqual(c.execute("PRAGMA foreign_key_check").fetchall(), [])
            self.assertEqual(c.execute("PRAGMA integrity_check").fetchone()[0], "ok")
            c.execute("INSERT INTO global_report_imports (report_type,window_label,window_days,date_from,date_to,period_id,marketplace,file_name,file_sha256,imported_at) "
                      "VALUES ('SEARCH_TERM','RECENT_90D',90,'2026-07-16','2026-10-13','2026-07-16__2026-10-13','US','n.csv','shaN','2026-10-14T10:00:00')")   # ora il 90D e' ammesso
            with self.assertRaises(sqlite3.IntegrityError):
                c.execute("INSERT INTO global_report_imports (report_type,window_label,window_days,date_from,date_to,period_id,marketplace,file_name,file_sha256,imported_at) "
                          "VALUES ('SEARCH_TERM','RECENT_77D',77,'2026-07-30','2026-10-13','x','US','m.csv','shaM','2026-10-14T10:00:00')")                  # ...ma non una finestra qualsiasi
            c.close()
            backups = list((Path(d) / "backups").glob("kdp_ads_pre_schema_v9_*.db"))
            self.assertEqual(len(backups), 1)
            b = sqlite3.connect(backups[0])
            self.assertEqual(b.execute("PRAGMA user_version").fetchone()[0], 8)             # il backup e' la copia v8 intatta
            self.assertEqual(b.execute("SELECT COUNT(*) FROM global_report_imports").fetchone()[0], 2)
            b.close()

    def test_migration_is_idempotent_and_never_repeats_the_backup(self):
        with tempfile.TemporaryDirectory() as d:
            p = make_v8(d)
            connect(p).close()
            connect(p).close()
            connect(p).close()
            self.assertEqual(len(list((Path(d) / "backups").glob("kdp_ads_pre_schema_v9_*.db"))), 1)
            c = sqlite3.connect(p)
            self.assertEqual(c.execute("SELECT COUNT(*) FROM global_report_imports").fetchone()[0], 2)               # nessuna duplicazione
            self.assertEqual(c.execute("SELECT COUNT(*) FROM sqlite_master WHERE name='global_report_imports_v8_old'").fetchone()[0], 1)
            c.close()

    def test_no_destructive_statement_in_the_v9_migration(self):
        import re
        src = (ROOT / "SCRIPTS" / "kdp_ads" / "db.py").read_text(encoding="utf-8")
        body = src[src.index("def rebuild_global_imports_window_check"):src.index("def _upgrade")]
        self.assertIsNone(re.search(r"DROP (TABLE|COLUMN|INDEX)", body))


if __name__ == "__main__":
    unittest.main()
