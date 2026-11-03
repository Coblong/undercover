"""Start again from the seed data: npm run seed

Drops our tables, and on Supabase the agent's saved progress with them, then
creates and seeds them. Stop the backend and the agent first.
"""

from contextlib import closing

from sqlalchemy import func, select

from backend import db
from backend import tables as t


def main() -> None:
    """Reset the database named by the environment and say what it holds."""
    db.reset_db()
    with closing(db.connect()) as conn:
        counts = {
            table.name: conn.execute(
                select(func.count()).select_from(table)
            ).scalar_one()
            for table in (t.claims, t.policies, t.customers, t.handlers)
        }
    print(f"Reset {db.engine().url.render_as_string(hide_password=True)}")
    print(", ".join(f"{count} {name}" for name, count in counts.items()))


if __name__ == "__main__":
    main()
