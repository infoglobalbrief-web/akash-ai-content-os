from app.database import test_database_connection

if __name__ == "__main__":
    result = test_database_connection()

    print("Database connection successful!")
    print(f"Database: {result['database']}")
    print(f"Username: {result['username']}")
    print(f"Connected: {result['connected']}")
