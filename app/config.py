"""Application configuration.

Every tunable lives here and is read from the environment exactly once.
The assumption values (thresholds, policies) are deliberately configuration
rather than constants in code: section 8 of the task list requires them to be
visible on screen and changeable without a code change.
"""
from __future__ import annotations

import os
from functools import lru_cache

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


def _normalise_db_url(url: str) -> str:
    """Railway hands out postgres:// URLs; psycopg wants postgresql://."""
    if url.startswith("postgres://"):
        return "postgresql://" + url[len("postgres://") :]
    return url


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    # --- Core -------------------------------------------------------------
    app_env: str = Field(default="local", alias="APP_ENV")
    port: int = Field(default=8000, alias="PORT")
    database_url: str = Field(
        default="postgresql://postgres:postgres@127.0.0.1:5432/ccredits",
        alias="DATABASE_URL",
    )
    auto_migrate: bool = Field(default=True, alias="AUTO_MIGRATE")
    log_level: str = Field(default="info", alias="LOG_LEVEL")

    # --- Upload / storage -------------------------------------------------
    storage_backend: str = Field(default="local", alias="STORAGE_BACKEND")  # local | s3
    storage_dir: str = Field(default="./var/uploads", alias="STORAGE_DIR")
    upload_max_mb: int = Field(default=50, alias="UPLOAD_MAX_MB")
    s3_bucket: str = Field(default="", alias="S3_BUCKET")
    s3_prefix: str = Field(default="uploads", alias="S3_PREFIX")
    s3_endpoint_url: str = Field(default="", alias="S3_ENDPOINT_URL")
    s3_region: str = Field(default="eu-west-1", alias="S3_REGION")
    s3_access_key_id: str = Field(default="", alias="S3_ACCESS_KEY_ID")
    s3_secret_access_key: str = Field(default="", alias="S3_SECRET_ACCESS_KEY")

    # --- Access -----------------------------------------------------------
    # Empty means the portal is open. Set it to require a token on any write.
    admin_token: str = Field(default="", alias="ADMIN_TOKEN")

    # --- Stated assumptions (task list section 8) -------------------------
    implausible_kwh_per_kwp: float = Field(default=8.0, alias="IMPLAUSIBLE_KWH_PER_KWP")
    zero_day_policy: str = Field(default="suspect", alias="ZERO_DAY_POLICY")  # suspect | ok
    missing_day_policy: str = Field(default="exclude", alias="MISSING_DAY_POLICY")  # exclude | zero
    trust_grid_connection_date: bool = Field(default=True, alias="TRUST_GRID_CONNECTION_DATE")
    default_emission_factor: float = Field(default=0.3550, alias="DEFAULT_EMISSION_FACTOR")
    default_emission_factor_source: str = Field(
        default="IFI Default Grid Factor 2021 v3.2 — Armenia combined margin",
        alias="DEFAULT_EMISSION_FACTOR_SOURCE",
    )
    default_irec_price: float = Field(default=1.50, alias="DEFAULT_IREC_PRICE_PER_MWH")
    default_vcu_price: float = Field(default=8.00, alias="DEFAULT_VCU_PRICE_PER_TCO2E")
    price_currency: str = Field(default="USD", alias="PRICE_CURRENCY")

    # --- Presentation -----------------------------------------------------
    banner_text: str = Field(default="Pilot data. Not verified.", alias="BANNER_TEXT")
    fleet_name: str = Field(default="SANNOVA", alias="FLEET_NAME")

    @property
    def dsn(self) -> str:
        return _normalise_db_url(self.database_url)

    @property
    def upload_max_bytes(self) -> int:
        return self.upload_max_mb * 1024 * 1024

    def assumptions(self) -> list[dict]:
        """The stated assumptions, shaped for display on every screen."""
        return [
            {
                "key": "implausible_kwh_per_kwp",
                "label": "Implausibility threshold",
                "value": f"{self.implausible_kwh_per_kwp:g} kWh/kWp/day",
                "note": "Daily specific yield above this is flagged suspect, not deleted.",
                "env": "IMPLAUSIBLE_KWH_PER_KWP",
            },
            {
                "key": "zero_day_policy",
                "label": "Zero-generation days",
                "value": "flagged suspect" if self.zero_day_policy == "suspect" else "treated as ok",
                "note": "Winter zero and broken inverter look identical in this dataset.",
                "env": "ZERO_DAY_POLICY",
            },
            {
                "key": "missing_day_policy",
                "label": "Missing days",
                "value": "excluded" if self.missing_day_policy == "exclude" else "counted as zero",
                "note": "Never interpolated. Excluded is the honest answer and it lowers the headline.",
                "env": "MISSING_DAY_POLICY",
            },
            {
                "key": "trust_grid_connection_date",
                "label": "Grid connection date",
                "value": "trusted as supplied" if self.trust_grid_connection_date else "verification required",
                "note": "Decides when each site starts earning.",
                "env": "TRUST_GRID_CONNECTION_DATE",
            },
            {
                "key": "emission_factor",
                "label": "Emission factor",
                "value": f"{self.default_emission_factor:g} tCO2e/MWh",
                "note": self.default_emission_factor_source,
                "env": "DEFAULT_EMISSION_FACTOR",
            },
        ]


@lru_cache
def get_settings() -> Settings:
    return Settings()


settings = get_settings()

# Railway injects PORT at runtime; honour it even if the model cached a default.
if os.getenv("PORT"):
    settings.port = int(os.environ["PORT"])
