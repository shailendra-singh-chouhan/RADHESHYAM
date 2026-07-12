"""
GOAT PRO — One-time DB migration (safe to run multiple times)
Adds missing columns to 'trades' table if they don't exist.
"""

import os
from sqlalchemy import create_engine, inspect, text

DATABASE_URL = os.getenv("DATABASE_URL")
if not DATABASE_URL:
    raise ValueError("DATABASE_URL environment variable not set")

engine = create_engine(DATABASE_URL)


def migrate():
    inspector = inspect(engine)
    try:
        existing = {c["name"] for c in inspector.get_columns("trades")}
    except Exception:
        print("Table 'trades' does not exist yet — Base.metadata.create_all() will handle it.")
        return

    with engine.begin() as conn:
        # direction
        if "direction" not in existing:
            conn.execute(text("ALTER TABLE trades ADD COLUMN direction VARCHAR(10)"))
            print("Added: direction")

        # entry_price — model says nullable=False, so add as nullable first,
        # fill old rows with 0, then try to set NOT NULL
        if "entry_price" not in existing:
            conn.execute(text("ALTER TABLE trades ADD COLUMN entry_price FLOAT"))
            conn.execute(text("UPDATE trades SET entry_price = 0 WHERE entry_price IS NULL"))
            try:
                conn.execute(text("ALTER TABLE trades ALTER COLUMN entry_price SET NOT NULL"))
            except Exception as e:
                print(f"Note: could not set NOT NULL on entry_price (probably no rows): {e}")
            print("Added: entry_price")

        if "exit_price" not in existing:
            conn.execute(text("ALTER TABLE trades ADD COLUMN exit_price FLOAT"))
            print("Added: exit_price")

        if "pnl" not in existing:
            conn.execute(text("ALTER TABLE trades ADD COLUMN pnl FLOAT"))
            print("Added: pnl")

        if "status" not in existing:
            conn.execute(text("ALTER TABLE trades ADD COLUMN status VARCHAR(10) DEFAULT 'OPEN'"))
            print("Added: status")

        if "signal_type" not in existing:
            conn.execute(text("ALTER TABLE trades ADD COLUMN signal_type VARCHAR(10) DEFAULT 'WAIT'"))
            print("Added: signal_type")

        if "trade_date" not in existing:
            conn.execute(text("ALTER TABLE trades ADD COLUMN trade_date TIMESTAMP WITH TIME ZONE DEFAULT NOW()"))
            print("Added: trade_date")

    print("Migration complete — DB schema is now in sync with models.py.")


if __name__ == "__main__":
    migrate()
