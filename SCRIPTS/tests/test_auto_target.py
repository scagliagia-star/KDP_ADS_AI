"""Target report AUTO (campagna di tipo OTHER): header 'Gruppi di annunci con targeting automatico'. Fixture equivalente al formato
reale (UTF-8 con BOM, CRLF, virgola decimale nei rapporti, due colonne 'Stato'), con numeri inventati. Solo workspace temporanei."""
import json
import sys
import unittest
from datetime import date
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import test_dashboard_v2 as td2  # noqa: E402
from ops import catalog, importing, pipeline_ops  # noqa: E402

HEADER = ("Stato,Gruppi di annunci con targeting automatico,Tipo di corrispondenza target,Stato,Offerta (USD),Impressioni,Clic,CTR,"
          "Costo totale (USD),CPC (USD),Acquisti,Vendite (USD),ACOS")
ROWS = [  # stato, gruppo, bid, impr, clic, ctr, spend, cpc, ordini, vendite, acos
    ("ABILITATO", "loose-match", "0.40", 20000, 400, "0,0200", 120.0, "0.30", 30, 500.0, "0,2400"),
    ("IN PAUSA", "close-match", "0.12", 30000, 150, "0,0050", 45.0, "0.30", 5, 100.0, "0,4500"),
    ("IN PAUSA", "complements", "0.35", 40000, 80, "0,0020", 30.0, "0.38", 2, 34.0, "0,8824"),
    ("IN PAUSA", "substitutes", "0.30", 35000, 60, "0,0017", 20.0, "0.33", 2, 34.0, "0,5882"),
]
EXPECTED = {"loose-match": ("ENABLED", 0.40, 20000, 400, 120.0, 30, 500.0, 24.0),
            "close-match": ("PAUSED", 0.12, 30000, 150, 45.0, 5, 100.0, 45.0),
            "complements": ("PAUSED", 0.35, 40000, 80, 30.0, 2, 34.0, 88.24),
            "substitutes": ("PAUSED", 0.30, 35000, 60, 20.0, 2, 34.0, 58.82)}


def auto_csv(scale=1):
    lines = [HEADER]
    for st, g, bid, im, cl, ctr, sp, cpc, o, v, acos in ROWS:
        status2 = "Attivo" if st == "ABILITATO" else "In pausa"
        lines.append(f'{st},{g},—,{status2},{bid},{im * scale},{cl * scale},"{ctr}",{sp * scale},{cpc},{o * scale},{v * scale},"{acos}"')
    return ("\r\n".join(lines)).encode("utf-8-sig")


class AutoBase(td2.WsBase):
    BOOK = "B0AUTO0001"

    def setUp(self):
        super().setUp()
        self.new_book(book_id=self.BOOK)
        r = catalog.add_campaign(self.ws, {"book_id": self.BOOK, "campaign_id": "AUTO", "campaign_name": "AUTO", "campaign_type": "OTHER"})
        self.assertTrue(r.ok, r.errors)
        self._closers = []

    def tearDown(self):
        for f in self._closers:
            f()
        super().tearDown()

    def load(self, kind, scale=1, name="auto.csv"):
        return self.report("AUTO", "TARGETING", kind, auto_csv(scale), name, book=self.BOOK)

    def rows(self):
        c = self.db()
        try:
            return {r["target"]: r for r in c.execute("SELECT * FROM target_rows ORDER BY id")}
        finally:
            c.close()


class AutoTargetImportTests(AutoBase):
    def test_lifetime_import_ok(self):
        _, _, dry, out = self.load("LIFETIME")
        self.assertEqual((dry.status, dry.report_type, dry.rows), ("DRY_RUN_OK", "TARGETING", 4))
        self.assertEqual((out.status, out.rows), ("IMPORTED", 4))
        self.assertEqual(self.rows()["loose-match"]["report_scope"], "LIFETIME")

    def test_30d_import_ok(self):
        _, _, dry, out = self.load("D30")
        self.assertEqual((dry.status, out.status), ("DRY_RUN_OK", "IMPORTED"))
        r = self.rows()["loose-match"]
        self.assertEqual((r["report_scope"], r["date_from"], r["date_to"]), ("DATE_RANGE", "2026-09-01", "2026-09-30"))

    def test_four_auto_targets_preserved_as_they_are(self):
        self.load("LIFETIME")
        self.assertEqual(set(self.rows()), {"loose-match", "close-match", "complements", "substitutes"})   # nessuna conversione in keyword
        c = self.db()
        self.assertEqual({r[0] for r in c.execute("SELECT report_type FROM imports")}, {"TARGETING"})
        self.assertEqual({r[0] for r in c.execute("SELECT campaign_type FROM target_rows")}, {"OTHER"})
        c.close()

    def test_status_bid_and_metrics(self):
        self.load("LIFETIME")
        from kdp_ads.decision import map_target_status
        for tgt, (status, bid, imp, clk, spend, orders, sales, acos) in EXPECTED.items():
            r = self.rows()[tgt]
            self.assertEqual(map_target_status(r["raw_json"]), status, tgt)               # prima colonna Stato
            self.assertEqual(r["bid"], bid, tgt)                                           # Offerta (USD)
            self.assertEqual((r["impressions"], r["clicks"], r["spend"], r["orders"], r["sales"]), (imp, clk, spend, orders, sales), tgt)
            self.assertAlmostEqual(r["acos_reported_pct"], acos, 2)
            self.assertAlmostEqual(r["spend"] / r["sales"] * 100, acos, 1)                 # ACOS coerente col ricalcolo

    def test_no_audit_warning_and_no_unmapped_columns(self):
        _, _, dry, _ = self.load("LIFETIME", name="a.csv")
        self.assertEqual(dry.unmapped, [])
        self.assertEqual(dry.problems, [])


class ManualReportsUnchangedTests(td2.WsBase):
    def test_manual_target_report_still_imports(self):
        self.new_book()
        self.new_campaign()
        _, _, dry, out = self.report("C_EXACT", "TARGETING", "LIFETIME", td2.tcsv(), "t.csv")
        self.assertEqual((dry.status, out.status, dry.rows), ("DRY_RUN_OK", "IMPORTED", 2))
        c = self.db()
        self.assertEqual({r[0] for r in c.execute("SELECT target FROM target_rows")}, {"kw uno", "kw due"})
        c.close()

    def test_unknown_target_header_is_still_rejected(self):
        self.new_book()
        self.new_campaign()
        bad = td2.tcsv().replace(b"Parola chiave", b"Colonna sconosciuta") if b"Parola chiave" in td2.tcsv() else None
        if bad is None:
            self.skipTest("fixture manuale senza header 'Parola chiave'")
        _, _, dry, _ = self.report("C_EXACT", "TARGETING", "LIFETIME", bad, "b.csv", commit=False)
        self.assertEqual(dry.status, "ERROR")


class AutoPipelineTests(AutoBase):
    def test_analysis_decisions_and_dashboard_with_other_campaign(self):
        self.load("LIFETIME")
        a = pipeline_ops.run_book_analysis(self.ws, self.BOOK, "US")
        self.assertTrue(a.ok, a.errors)
        d = pipeline_ops.run_book_decisions(self.ws, self.BOOK, a.data["run_id"], "US")
        self.assertTrue(d.ok, d.errors)
        self.assertEqual(d.data["paused"], 3)                                            # close-match, complements, substitutes in pausa
        c = self.db()
        decs = {r["target"]: r for r in c.execute("SELECT target, campaign_type, target_status, status, manual_approval_required FROM decisions")}
        c.close()
        self.assertEqual(set(decs), set(EXPECTED))
        self.assertEqual({v["campaign_type"] for v in decs.values()}, {"OTHER"})
        self.assertEqual({k: v["target_status"] for k, v in decs.items()},
                         {"loose-match": "ENABLED", "close-match": "PAUSED", "complements": "PAUSED", "substitutes": "PAUSED"})
        self.assertTrue(all(v["status"] == "PROPOSED" and v["manual_approval_required"] == 1 for v in decs.values()))
        from data_access.context import load_context
        from data_access.repository import Repo
        repo = Repo(self.ws.db_path, root=td2.ROOT)
        ctx = load_context(repo, self.BOOK, d.data["run_id"], "US")
        self.assertIsNone(ctx.error)
        self.assertEqual((ctx.summary["total"], ctx.summary["paused"]), (4, 3))
        self.assertEqual({x["campaign_type"] for x in ctx.decisions}, {"OTHER"})


if __name__ == "__main__":
    unittest.main()
