
from sqlalchemy import inspect, text

from app.database import SessionLocal, engine


if __name__ == "__main__":
    inspector = inspect(engine)

    if not inspector.has_table("sources"):
        raise RuntimeError("The sources table was not found.")

    with SessionLocal() as session:
        result = session.execute(
            text("SELECT COUNT(*) FROM sources")
        ).scalar_one()

    print("SUCCESS: Database session works!")
    print(f"Sources currently stored: {result}")
