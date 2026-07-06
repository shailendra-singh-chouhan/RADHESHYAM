# database.py

import os
import logging
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from models import Base  # Import Base from the models file

# Logger setup
logger = logging.getLogger(__name__)

# Get the DATABASE_URL from environment variables.
# Render automatically injects this for your PostgreSQL service.
DATABASE_URL = os.environ.get("DATABASE_URL")

if not DATABASE_URL:
    logger.error("DATABASE_URL environment variable not set or is empty. Cannot connect to the database.")
    db_engine = None
    SessionLocal = None
else:
    try:
        logger.info("Attempting to connect to the database using DATABASE_URL.")
        # Create the database engine.
        # 'pool_pre_ping=True' helps manage connections more effectively.
        db_engine = create_engine(
            DATABASE_URL,
            pool_pre_ping=True,
            # Add connect_args for SSL if needed, though Render often handles this.
            # Example: connect_args={"sslmode": "require"}
        )
        # Create a configured Session class
        SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=db_engine)
        logger.info("Database engine and session maker created successfully.")
    except Exception as e:
        logger.error(f"Error creating database engine or session: {e}")
        db_engine = None
        SessionLocal = None

def get_db():
    """
    Dependency function to provide a database session to API endpoints (e.g., in FastAPI).
    This yields a session and ensures it's closed afterwards.
    """
    if SessionLocal is None:
        logger.error("SessionLocal is not initialized. Cannot provide database session.")
        # Yield None or raise an HTTPException depending on your framework
        yield None
        return # Exit the generator cleanly

    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()
        logger.debug("Database session closed.")

def init_db(engine):
    """
    Initializes the database by creating all tables defined in models.
    This should be called once when the application starts up.
    """
    if engine:
        try:
            # Base.metadata.create_all will create tables if they don't exist
            Base.metadata.create_all(bind=engine)
            logger.info("Database tables created or verified successfully.")
        except Exception as e:
            logger.error(f"Error creating/verifying database tables: {e}")
    else:
        logger.warning("Database engine is not available, skipping table initialization.")
