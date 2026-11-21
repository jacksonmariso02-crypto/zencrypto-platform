"""User authentication for isolated paper-trading accounts.

Access tokens are short-lived HS256 JWTs. Passwords are stored as salted
scrypt hashes. This service does not authorize external exchange trading.
"""
import base64
import hashlib
import hmac
import json
import re
import secrets
import sqlite3
import time
import uuid
from contextlib import closing
from pathlib import Path


class AuthenticationError(ValueError):
    pass


class UsernameTaken(ValueError):
    pass


def _b64encode(value):
    return base64.urlsafe_b64encode(value).rstrip(b"=").decode("ascii")


def _b64decode(value):
    return base64.urlsafe_b64decode(value + "=" * (-len(value) % 4))


class AuthService:
    def __init__(self, db_path, token_secret, token_ttl_seconds=900):
        self.db_path = str(db_path)
        self.token_secret = str(token_secret or "")
        self.token_ttl_seconds = int(token_ttl_seconds)
        if self.db_path != ":memory:":
            Path(self.db_path).parent.mkdir(parents=True, exist_ok=True)
        if self.token_ttl_seconds < 60 or self.token_ttl_seconds > 86400:
            raise ValueError("Token lifetime must be between 60 and 86400 seconds.")
        self._initialize()

    def _connect(self):
        db = sqlite3.connect(self.db_path, timeout=15, isolation_level=None)
        db.row_factory = sqlite3.Row
        db.execute("PRAGMA busy_timeout = 15000")
        return db

    def _initialize(self):
        with closing(self._connect()) as db:
            db.execute("PRAGMA journal_mode = WAL")
            db.execute("""
                CREATE TABLE IF NOT EXISTS paper_accounts (
                    account_id TEXT PRIMARY KEY,
                    created_at INTEGER NOT NULL
                )
            """)
            db.execute("""
                CREATE TABLE IF NOT EXISTS paper_users (
                    user_id TEXT PRIMARY KEY,
                    username TEXT NOT NULL UNIQUE COLLATE NOCASE,
                    account_id TEXT NOT NULL UNIQUE,
                    password_hash TEXT NOT NULL,
                    created_at INTEGER NOT NULL,
                    disabled INTEGER NOT NULL DEFAULT 0 CHECK(disabled IN (0,1)),
                    FOREIGN KEY(account_id) REFERENCES paper_accounts(account_id)
                )
            """)
            db.execute("CREATE INDEX IF NOT EXISTS idx_paper_users_account ON paper_users(account_id)")

    @staticmethod
    def _username(value):
        username = str(value).strip().lower()
        if not re.fullmatch(r"[a-z0-9_]{3,32}", username):
            raise ValueError("Username must be 3-32 characters using letters, numbers, or underscores.")
        return username

    @staticmethod
    def _password(value):
        password = str(value)
        if len(password) < 12 or len(password) > 128:
            raise ValueError("Password must be between 12 and 128 characters.")
        if not any(ch.isalpha() for ch in password) or not any(ch.isdigit() for ch in password):
            raise ValueError("Password must contain at least one letter and one number.")
        return password

    @staticmethod
    def _hash_password(password):
        salt = secrets.token_bytes(16)
        derived = hashlib.scrypt(password.encode("utf-8"), salt=salt, n=2**14, r=8, p=1, dklen=32)
        return "scrypt$16384$8$1$" + _b64encode(salt) + "$" + _b64encode(derived)

    @staticmethod
    def _verify_password(password, encoded):
        try:
            scheme, n, r, p, salt, expected = encoded.split("$")
            if scheme != "scrypt":
                return False
            actual = hashlib.scrypt(
                password.encode("utf-8"), salt=_b64decode(salt),
                n=int(n), r=int(r), p=int(p), dklen=len(_b64decode(expected)),
            )
            return hmac.compare_digest(actual, _b64decode(expected))
        except (ValueError, TypeError, MemoryError):
            return False

    def _sign(self, payload):
        if (len(self.token_secret.encode("utf-8")) < 32
                or self.token_secret.startswith(("REPLACE_", "CHANGE_ME", "YOUR_"))):
            raise RuntimeError("Set AUTH_TOKEN_SECRET to a unique random secret of at least 32 characters.")
        header = _b64encode(json.dumps(
            {"alg": "HS256", "typ": "JWT"}, separators=(",", ":")
        ).encode())
        body = _b64encode(json.dumps(payload, separators=(",", ":")).encode())
        message = f"{header}.{body}".encode("ascii")
        signature = hmac.new(self.token_secret.encode(), message, hashlib.sha256).digest()
        return f"{header}.{body}.{_b64encode(signature)}"

    def _issue_token(self, user):
        now = int(time.time())
        return self._sign({
            "sub": user["user_id"],
            "account_id": user["account_id"],
            "iat": now,
            "exp": now + self.token_ttl_seconds,
        })

    def register(self, username, password):
        if (len(self.token_secret.encode("utf-8")) < 32
                or self.token_secret.startswith(("REPLACE_", "CHANGE_ME", "YOUR_"))):
            raise RuntimeError("Set AUTH_TOKEN_SECRET to a unique random secret of at least 32 characters.")
        username = self._username(username)
        password = self._password(password)
        user_id = uuid.uuid4().hex
        account_id = "u_" + uuid.uuid4().hex
        password_hash = self._hash_password(password)
        now = int(time.time())
        with closing(self._connect()) as db:
            db.execute("BEGIN IMMEDIATE")
            try:
                db.execute(
                    "INSERT INTO paper_accounts(account_id, created_at) VALUES (?, ?)",
                    (account_id, time.time_ns()),
                )
                db.execute(
                    """INSERT INTO paper_users
                       (user_id, username, account_id, password_hash, created_at)
                       VALUES (?, ?, ?, ?, ?)""",
                    (user_id, username, account_id, password_hash, now),
                )
                db.commit()
            except sqlite3.IntegrityError as exc:
                db.rollback()
                if "username" in str(exc).lower():
                    raise UsernameTaken("Username is already registered.") from exc
                raise
            except Exception:
                db.rollback()
                raise
        user = {"user_id": user_id, "username": username, "account_id": account_id}
        return {**user, "access_token": self._issue_token(user), "token_type": "bearer",
                "expires_in": self.token_ttl_seconds}

    def login(self, username, password):
        username = self._username(username)
        with closing(self._connect()) as db:
            row = db.execute(
                "SELECT * FROM paper_users WHERE username=? AND disabled=0",
                (username,),
            ).fetchone()
        # Use a dummy scrypt verification for unknown usernames to reduce
        # the obvious timing difference between unknown and known accounts.
        if row is None:
            self._verify_password(str(password), self._dummy_hash)
            raise AuthenticationError("Invalid username or password.")
        if not self._verify_password(str(password), row["password_hash"]):
            raise AuthenticationError("Invalid username or password.")
        user = {"user_id": row["user_id"], "username": row["username"],
                "account_id": row["account_id"]}
        return {**user, "access_token": self._issue_token(user), "token_type": "bearer",
                "expires_in": self.token_ttl_seconds}

    @property
    def _dummy_hash(self):
        # Fixed valid-format hash; its digest need not match any real password.
        return "scrypt$16384$8$1$" + _b64encode(b"zencrypto-dummy-salt") + "$" + _b64encode(b"0" * 32)

    def authenticate(self, token):
        if not token or len(token) > 4096 or len(self.token_secret.encode("utf-8")) < 32:
            raise AuthenticationError("Invalid or expired access token.")
        try:
            header, body, signature = token.split(".")
            message = f"{header}.{body}".encode("ascii")
            expected = hmac.new(self.token_secret.encode(), message, hashlib.sha256).digest()
            if not hmac.compare_digest(expected, _b64decode(signature)):
                raise AuthenticationError("Invalid or expired access token.")
            token_header = json.loads(_b64decode(header))
            payload = json.loads(_b64decode(body))
            now = int(time.time())
            if token_header != {"alg": "HS256", "typ": "JWT"}:
                raise AuthenticationError("Invalid or expired access token.")
            if not isinstance(payload.get("exp"), int) or payload["exp"] <= now:
                raise AuthenticationError("Invalid or expired access token.")
            if not isinstance(payload.get("iat"), int) or payload["iat"] > now + 60:
                raise AuthenticationError("Invalid or expired access token.")
            user_id = payload.get("sub")
            account_id = payload.get("account_id")
            if not isinstance(user_id, str) or not isinstance(account_id, str):
                raise AuthenticationError("Invalid or expired access token.")
        except (ValueError, TypeError, KeyError, json.JSONDecodeError):
            raise AuthenticationError("Invalid or expired access token.")
        with closing(self._connect()) as db:
            row = db.execute(
                """SELECT user_id, username, account_id FROM paper_users
                   WHERE user_id=? AND account_id=? AND disabled=0""",
                (user_id, account_id),
            ).fetchone()
        if row is None:
            raise AuthenticationError("Invalid or expired access token.")
        return dict(row)
