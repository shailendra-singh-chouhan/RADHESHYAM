import os
from datetime import datetime, timedelta, timezone
from typing import Optional

from jose import JWTError, jwt
from passlib.context import CryptContext
from fastapi import Depends, HTTPException, status
from fastapi.security import OAuth2PasswordBearer
from logzero import logger

# ────────────────────────────────────────────
# Configuration
# ────────────────────────────────────────────
SECRET_KEY = os.getenv("JWT_SECRET_KEY", "super-secret-key-that-should-be-changed")
ALGORITHM = "HS256"
ACCESS_TOKEN_EXPIRE_MINUTES = 60 * 24 * 7 # 7 days

# Password hashing context
pwd_context = CryptContext(schemes=["bcrypt"], deprecated="auto")

# OAuth2 scheme for FastAPI
oauth2_scheme = OAuth2PasswordBearer(tokenUrl="token")

# ────────────────────────────────────────────
# Password Hashing
# ────────────────────────────────────────────
def get_password_hash(password: str) -> str:
    return pwd_context.hash(password)

def verify_password(plain_password: str, hashed_password: str) -> bool:
    return pwd_context.verify(plain_password, hashed_password)

# ────────────────────────────────────────────
# JWT Token Operations
# ────────────────────────────────────────────
def create_access_token(data: dict, expires_delta: Optional[timedelta] = None) -> str:
    to_encode = data.copy()
    if expires_delta:
        expire = datetime.now(timezone.utc) + expires_delta
    else:
        expire = datetime.now(timezone.utc) + timedelta(minutes=ACCESS_TOKEN_EXPIRE_MINUTES)
    to_encode.update({"exp": expire})
    encoded_jwt = jwt.encode(to_encode, SECRET_KEY, algorithm=ALGORITHM)
    return encoded_jwt

def verify_token(token: str, credentials_exception) -> str:
    try:
        payload = jwt.decode(token, SECRET_KEY, algorithms=[ALGORITHM])
        username: str = payload.get("sub")
        if username is None:
            raise credentials_exception
        return username
    except JWTError:
        raise credentials_exception

# ────────────────────────────────────────────
# FastAPI Dependency for Current User
# ────────────────────────────────────────────
def get_current_user(token: str = Depends(oauth2_scheme)) -> str:
    credentials_exception = HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail="Could not validate credentials",
        headers={"WWW-Authenticate": "Bearer"},
    )
    return verify_token(token, credentials_exception)

# ────────────────────────────────────────────
# User Management (Example - replace with actual DB lookup)
# ────────────────────────────────────────────
# In a real application, you would fetch user from a database.
# For this example, we'll use a simple hardcoded user.

class UserInDB:
    def __init__(self, username: str, hashed_password: str):
        self.username = username
        self.hashed_password = hashed_password

# This should ideally come from a database
TEST_USER_DB = {
    "admin": UserInDB(username="admin", hashed_password=get_password_hash("adminpass"))
}

def get_user(username: str) -> Optional[UserInDB]:
    return TEST_USER_DB.get(username)


# Example of how to use get_password_hash to generate a hash for a new user
# print(f"Hashed password for 'adminpass': {get_password_hash('adminpass')}")
