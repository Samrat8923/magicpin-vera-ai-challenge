import os
from typing import List


class Settings:
    HOST: str = os.getenv("HOST", "0.0.0.0")
    PORT: int = int(os.getenv("PORT", "8080"))
    TEAM_NAME: str = os.getenv("TEAM_NAME", "Team VERA")
    
    @property
    def TEAM_MEMBERS(self) -> List[str]:
        raw = os.getenv("TEAM_MEMBERS", "Antigravity Developer")
        return [m.strip() for m in raw.split(",") if m.strip()]

    MODEL: str = os.getenv("MODEL", "deterministic-v1")
    APPROACH: str = os.getenv(
        "APPROACH",
        "4-context deterministic compositional engine with rule-based routing & post-synthesis verification",
    )
    CONTACT_EMAIL: str = os.getenv("CONTACT_EMAIL", "team@example.com")
    VERSION: str = os.getenv("VERSION", "1.0.0")
    SUBMITTED_AT: str = os.getenv("SUBMITTED_AT", "2026-04-26T08:00:00Z")


settings = Settings()
