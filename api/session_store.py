"""session_store.py -- Production session state abstraction with TTL eviction.

Provides session and conversation history management for stateless multi-turn dialogue.
Supports:
  1. Thread-safe in-memory store with sliding TTL eviction (default)
  2. Distributed Redis backend (when REDIS_URL environment variable is provided)
"""
import json
import logging
import os
import threading
import time
from typing import Any, Dict, List, Optional

logger = logging.getLogger("loanassist.session")

DEFAULT_SESSION_TTL = int(os.getenv("SESSION_TTL_SECONDS", "3600"))  # 1 hour default


class SessionStore:
    """Unified session store interface with sliding TTL expiration."""

    def __init__(self, redis_url: Optional[str] = None, default_ttl: int = DEFAULT_SESSION_TTL):
        self.default_ttl = default_ttl
        self.redis_client = None
        self._lock = threading.Lock()

        redis_url = redis_url or os.getenv("REDIS_URL")
        if redis_url:
            try:
                import redis
                self.redis_client = redis.from_url(redis_url, decode_responses=True)
                self.redis_client.ping()
                logger.info("SessionStore: Connected to Redis backend at %s", redis_url)
            except Exception as exc:
                logger.warning("SessionStore: Redis unavailable (%s) — falling back to memory store", exc)
                self.redis_client = None

        if not self.redis_client:
            # Memory store: session_id -> { "history": [...], "profile": {...}, "expires_at": float }
            self._store: Dict[str, Dict[str, Any]] = {}

    def _cleanup_expired(self):
        """Prune expired sessions from memory."""
        now = time.time()
        with self._lock:
            expired_keys = [sid for sid, data in self._store.items() if data.get("expires_at", 0) < now]
            for sid in expired_keys:
                del self._store[sid]

    def get_history(self, session_id: str) -> List[Dict[str, str]]:
        if self.redis_client:
            try:
                raw = self.redis_client.get(f"session:{session_id}:history")
                return json.loads(raw) if raw else []
            except Exception as e:
                logger.error("Redis get_history error: %s", e)
                return []

        self._cleanup_expired()
        with self._lock:
            data = self._store.get(session_id)
            if not data or data.get("expires_at", 0) < time.time():
                return []
            return list(data.get("history", []))

    def save_message(self, session_id: str, role: str, content: str, ttl: Optional[int] = None):
        expiry = ttl or self.default_ttl
        new_msg = {"role": role, "content": content}

        if self.redis_client:
            try:
                history = self.get_history(session_id)
                history.append(new_msg)
                self.redis_client.setex(f"session:{session_id}:history", expiry, json.dumps(history))
                return
            except Exception as e:
                logger.error("Redis save_message error: %s", e)

        self._cleanup_expired()
        with self._lock:
            if session_id not in self._store:
                self._store[session_id] = {"history": [], "profile": {}, "expires_at": 0.0}
            self._store[session_id]["history"].append(new_msg)
            self._store[session_id]["expires_at"] = time.time() + expiry

    def set_history(self, session_id: str, messages: List[Dict[str, str]], ttl: Optional[int] = None):
        expiry = ttl or self.default_ttl
        if self.redis_client:
            try:
                self.redis_client.setex(f"session:{session_id}:history", expiry, json.dumps(messages))
                return
            except Exception as e:
                logger.error("Redis set_history error: %s", e)

        self._cleanup_expired()
        with self._lock:
            if session_id not in self._store:
                self._store[session_id] = {"history": [], "profile": {}, "expires_at": 0.0}
            self._store[session_id]["history"] = list(messages)
            self._store[session_id]["expires_at"] = time.time() + expiry

    def get_profile(self, session_id: str) -> Optional[dict]:
        if self.redis_client:
            try:
                raw = self.redis_client.get(f"session:{session_id}:profile")
                return json.loads(raw) if raw else None
            except Exception as e:
                logger.error("Redis get_profile error: %s", e)
                return None

        self._cleanup_expired()
        with self._lock:
            data = self._store.get(session_id)
            if not data or data.get("expires_at", 0) < time.time():
                return None
            return data.get("profile")

    def save_profile(self, session_id: str, profile_dict: dict, ttl: Optional[int] = None):
        expiry = ttl or self.default_ttl
        if self.redis_client:
            try:
                self.redis_client.setex(f"session:{session_id}:profile", expiry, json.dumps(profile_dict))
                return
            except Exception as e:
                logger.error("Redis save_profile error: %s", e)

        self._cleanup_expired()
        with self._lock:
            if session_id not in self._store:
                self._store[session_id] = {"history": [], "profile": {}, "expires_at": 0.0}
            self._store[session_id]["profile"] = profile_dict
            self._store[session_id]["expires_at"] = time.time() + expiry

    def clear(self, session_id: str):
        if self.redis_client:
            try:
                self.redis_client.delete(f"session:{session_id}:history", f"session:{session_id}:profile")
            except Exception as e:
                logger.error("Redis clear error: %s", e)

        with self._lock:
            self._store.pop(session_id, None)


# Default global instance
global_session_store = SessionStore()
