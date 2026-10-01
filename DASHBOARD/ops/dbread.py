"""Letture SQLite in sola lettura (mode=ro) per le pagine V2. Tollera DB o tabelle assenti."""
import sqlite3


def mp_clause(ws, book_id, marketplace, column="marketplace"):
    """Filtro per marketplace; i run storici (NULL) appartengono al marketplace legacy del libro."""
    if not marketplace:
        return "", ()
    from kdp_ads import registry
    mp = str(marketplace).strip().upper()
    book = registry.load_books(ws.books_path).get(str(book_id))
    legacy = registry.run_marketplace(book, None) if book else None
    return f" AND (UPPER({column})=? OR ({column} IS NULL AND ?=?))", (mp, mp, legacy)


def query(ws, sql, params=()):
    if not ws.db_path.is_file():
        return []
    conn = sqlite3.connect(f"{ws.db_path.resolve().as_uri()}?mode=ro", uri=True)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA query_only=ON")
    try:
        return [dict(r) for r in conn.execute(sql, params)]
    except sqlite3.OperationalError:
        return []
    finally:
        conn.close()
