"""Presentazione: nessuna colonna di tabella mescola numeri e "n/d" (errore Streamlit/pyarrow "Expected bytes, got a 'float'").
Solo UI: motore, DB e decisioni non cambiano."""
import hashlib
import os
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

import pandas as pd
import pyarrow as pa

import test_overview as tov
import ui_labels as U
from components import cards

try:
    from streamlit.testing.v1 import AppTest
except Exception:                                    # pragma: no cover
    AppTest = None

COLS = ["lifetime_acos", "recent_acos", "current_bid", "lifetime_clicks", "target"]


def decisions():
    return [{"lifetime_acos": 35.7, "recent_acos": None, "current_bid": 0.42, "lifetime_clicks": 20.0, "target": "a"},
            {"lifetime_acos": None, "recent_acos": 48.25, "current_bid": None, "lifetime_clicks": 35.0, "target": "b"},
            {"lifetime_acos": 0.0, "recent_acos": 100.0, "current_bid": 1.0, "lifetime_clicks": None, "target": "c"}]


def mixed_columns(rows):
    out = []
    for k in rows[0]:
        kinds = {isinstance(r[k], str) for r in rows if r[k] is not None}
        if kinds == {True, False}:
            out.append(k)
    return out


class UiColumnTests(unittest.TestCase):
    def test_acos_values_are_formatted_percent_strings_and_missing_is_nd(self):
        self.assertEqual(U.display_value("lifetime_acos", 35.7), "35.7%")
        self.assertEqual(U.display_value("recent_acos", 48.25), "48.2%")
        self.assertEqual(U.display_value("lifetime_acos", 0.0), "0.0%")
        self.assertEqual(U.display_value("lifetime_acos", None), "n/d")
        self.assertEqual(U.display_value("lifetime_acos", "abc"), "abc")

    def test_display_rows_has_no_mixed_columns_and_is_arrow_serializable(self):
        rows = cards.display_rows(decisions(), COLS)
        self.assertEqual(mixed_columns(rows), [])
        pa.Table.from_pandas(pd.DataFrame(rows))                                   # prima del fix: ArrowTypeError "Expected bytes, got a 'float'"
        col = [r["ACOS storico completo"] for r in rows]
        self.assertEqual(col, ["35.7%", "n/d", "0.0%"])                           # "n/d" resta "n/d", i numeri sono formattati
        self.assertEqual([r["ACOS recente"] for r in rows], ["n/d", "48.2%", "100.0%"])

    def test_other_numeric_columns_with_missing_values_are_homogeneous_too(self):
        rows = cards.display_rows(decisions(), COLS)
        self.assertEqual([r["Offerta attuale"] for r in rows], ["0.42", "n/d", "1"])
        self.assertEqual([r["Clic storico completo"] for r in rows], ["20", "35", "n/d"])

    def test_table_rows_is_homogeneous_and_columns_without_mix_keep_their_type(self):
        rows = cards.table_rows(decisions(), COLS)
        self.assertEqual(mixed_columns(rows), [])
        pa.Table.from_pandas(pd.DataFrame(rows))
        full = cards.table_rows([{"lifetime_acos": 1.5, "target": "x"}, {"lifetime_acos": 2.5, "target": "y"}], ["lifetime_acos", "target"])
        self.assertEqual([r["lifetime_acos"] for r in full], [1.5, 2.5])           # nessun cambio dove non serve
        self.assertEqual(cards.homogenize_columns([]), [])

    def test_source_data_is_not_modified(self):
        d = decisions()
        before = [dict(x) for x in d]
        cards.display_rows(d, COLS)
        cards.table_rows(d, COLS)
        self.assertEqual(d, before)                                                # tipi del motore/DB invariati


REAL_DB = ROOT / "DATABASE" / "kdp_ads.db"


@unittest.skipIf(AppTest is None, "streamlit non installato")
@unittest.skipUnless(REAL_DB.is_file(), "DB reale assente")
class RealDecisionsPageTests(unittest.TestCase):
    """Pagina Tutte le decisioni sui libri reali (COPIA del DB): nessuna tabella con colonne miste, DB reale e books.json invariati."""

    def test_page_tables_are_arrow_serializable_on_real_books(self):
        real_hash = hashlib.sha256(REAL_DB.read_bytes()).hexdigest()
        books_hash = hashlib.sha256((ROOT / "CONFIG" / "books.json").read_bytes()).hexdigest()
        src = sqlite3.connect(f"{REAL_DB.resolve().as_uri()}?mode=ro", uri=True)
        with tempfile.TemporaryDirectory() as tmp:
            db = Path(tmp) / "k.db"
            dst = sqlite3.connect(db)
            src.backup(dst)
            src.close()
            books = [r[0] for r in dst.execute("SELECT DISTINCT book_id FROM decision_runs")]
            dst.close()
            old = os.environ.get("KDP_ADS_DB")
            os.environ["KDP_ADS_DB"] = str(db)
            try:
                checked = 0
                for book in books:
                    at = AppTest.from_file(str(ROOT / "DASHBOARD" / "pages" / "2_Tutte_le_decisioni.py"), default_timeout=120).run()
                    tov.select(at, "Libro", book)
                    self.assertFalse(at.exception, book)
                    for df in at.dataframe:
                        pa.Table.from_pandas(df.value)                              # nessun ArrowTypeError
                        checked += 1
                self.assertGreater(checked, 0)
            finally:
                if old is None:
                    os.environ.pop("KDP_ADS_DB", None)
                else:
                    os.environ["KDP_ADS_DB"] = old
        self.assertEqual(hashlib.sha256(REAL_DB.read_bytes()).hexdigest(), real_hash)
        self.assertEqual(hashlib.sha256((ROOT / "CONFIG" / "books.json").read_bytes()).hexdigest(), books_hash)


if __name__ == "__main__":
    unittest.main()
