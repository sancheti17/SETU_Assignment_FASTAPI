"""Explicit environment settings; no .env auto-loading or hidden defaults."""
import os
from dataclasses import dataclass


@dataclass(frozen=True)
class Settings:
    database_path: str = 'data/payments.db'
    api_key: str = ''
    require_api_key: bool = False
    settlement_grace_hours: int = 24
    db_timeout_ms: int = 5000
    max_body_bytes: int = 16384

    def __post_init__(self):
        if self.require_api_key and len(self.api_key) < 24:
            raise ValueError('REQUIRE_API_KEY=true requires API_KEY with at least 24 characters')
        if not 0 <= self.settlement_grace_hours <= 8760:
            raise ValueError('SETTLEMENT_GRACE_HOURS must be between 0 and 8760')
        if not 1 <= self.db_timeout_ms <= 60000:
            raise ValueError('DB_TIMEOUT_MS must be between 1 and 60000')
        if self.max_body_bytes < 1:
            raise ValueError('max_body_bytes must be positive')
        if self.database_path == ':memory:':
            raise ValueError('Use a file-backed SQLite database')

    @classmethod
    def from_env(cls):
        required = os.environ.get('REQUIRE_API_KEY', 'false').lower()
        if required not in {'true', 'false'}:
            raise ValueError('REQUIRE_API_KEY must be true or false')
        return cls(
            database_path=os.environ.get('DATABASE_PATH', 'data/payments.db'),
            api_key=os.environ.get('API_KEY', ''),
            require_api_key=required == 'true',
            settlement_grace_hours=int(os.environ.get('SETTLEMENT_GRACE_HOURS', '24')),
            db_timeout_ms=int(os.environ.get('DB_TIMEOUT_MS', '5000')),
        )
