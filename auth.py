import os
import bcrypt
from datetime import datetime, timedelta, timezone
from typing import Optional

from jose import JWTError, jwt
from fastapi import Depends, HTTPException, status
from fastapi.security import OAuth2PasswordBearer
from logzero import logger

# ────────────────────────────────────────────
# Configuration
# ────────────────────────────────────────────
SECRET_KEY = os.getenv("JWT_SECRET_KEY", "super-secret-key-that-should-be-changed")
ALGORITHM = "HS256"
ACCESS_TOKEN_EXPIRE_MINUTES = 60 * 24 * 7 # 7 days

# OAuth2 scheme for FastAPI
oauth2_scheme = OAuth2PasswordBearer(tokenUrl="token")

# ────────────────────────────────────────────
# Password Hashing (Using direct bcrypt)
# ────────────────────────────────────────────
def get_password_hash(password: str) -> str:
    # bcrypt requires bytes, so we encode the password
    pwd_bytes = password.encode('utf-8')
    salt = bcrypt.gensalt()
    hashed = bcrypt.hashpw(pwd_bytes, salt)
    return hashed.decode('utf-8')

def verify_password(plain_password: str, hashed_password: str) -> bool:
    # bcrypt requires bytes for both plain and hashed password
    password_byte_enc = plain_password.encode('utf-8')
    hashed_password_byte_enc = hashed_password.encode('utf-8')
    return bcrypt.checkpw(password_byte_enc, hashed_password_byte_enc)

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
class UserInDB:
    def __init__(self, username: str, hashed_password: str):
        self.username = username
        self.hashed_password = hashed_password

# This should ideally come from a database
# Pre-hashing 'adminpass' for the test user
ADMIN_HASHED_PASSWORD = get_password_hash("adminpass")
TEST_USER_DB = {
    "admin": UserInDB(username="admin", hashed_password=ADMIN_HASHED_PASSWORD)
}

def get_user(username: str) -> Optional[UserInDB]:
    return TEST_USER_DB.get(username)
