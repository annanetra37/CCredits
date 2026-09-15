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
    # local | azure | s3. Azure is the deployment target; local is for a laptop.
    storage_backend: str = Field(default="local", alias="STORAGE_BACKEND")
    storage_dir: str = Field(default="./var/uploads", alias="STORAGE_DIR")
    upload_max_mb: int = Field(default=50, alias="UPLOAD_MAX_MB")
    s3_bucket: str = Field(default="", alias="S3_BUCKET")
    s3_prefix: str = Field(default="uploads", alias="S3_PREFIX")
    s3_endpoint_url: str = Field(default="", alias="S3_ENDPOINT_URL")
    s3_region: str = Field(default="eu-west-1", alias="S3_REGION")
    s3_access_key_id: str = Field(default="", alias="S3_ACCESS_KEY_ID")
    s3_secret_access_key: str = Field(default="", alias="S3_SECRET_ACCESS_KEY")

    # --- Azure Blob Storage -----------------------------------------------
    # Either a connection string, or an account name with a managed identity
    # (no secret in configuration at all, the preferred arrangement on Azure).
    azure_storage_connection_string: str = Field(
        default="", alias="AZURE_STORAGE_CONNECTION_STRING"
    )
    azure_storage_account: str = Field(default="", alias="AZURE_STORAGE_ACCOUNT")
    azure_storage_container: str = Field(
        default="uploads", alias="AZURE_STORAGE_CONTAINER"
    )

    # --- Access -----------------------------------------------------------
    # Empty means the portal is open. Set it to require a token on any write.
    admin_token: str = Field(default="", alias="ADMIN_TOKEN")

    # --- Stated assumptions (task list section 8) -------------------------
    implausible_kwh_per_kwp: float = Field(default=8.0, alias="IMPLAUSIBLE_KWH_PER_KWP")
    zero_day_policy: str = Field(default="suspect", alias="ZERO_DAY_POLICY")  # suspect | ok
    missing_day_policy: str = Field(default="exclude", alias="MISSING_DAY_POLICY")  # exclude | zero
    trust_grid_connection_date: bool = Field(default=True, alias="TRUST_GRID_CONNECTION_DATE")
    # There is no agreed Armenian grid factor in this project yet. The seed
    # below is a placeholder that identifies itself as one; it must not be
    # dressed in a citation nobody has checked.
    default_emission_factor: float = Field(default=0.0, alias="DEFAULT_EMISSION_FACTOR")
    emission_factor_verified_by: str = Field(default="", alias="EMISSION_FACTOR_VERIFIED_BY")
    default_emission_factor_source: str = Field(
        default="UNVERIFIED, no source document supplied",
        alias="DEFAULT_EMISSION_FACTOR_SOURCE",
    )
    default_emission_factor_url: str = Field(default="", alias="DEFAULT_EMISSION_FACTOR_URL")
    default_emission_factor_vintage: str = Field(
        default="unset", alias="DEFAULT_EMISSION_FACTOR_VINTAGE"
    )
    emission_factor_valid_from: str = Field(default="", alias="EMISSION_FACTOR_VALID_FROM")
    # Empty means the factor is applied open-endedly. Its own published validity
    # is recorded separately, so the portal can show where the two differ.
    default_vcu_price: float = Field(default=3.0, alias="DEFAULT_VCU_PRICE_PER_TCO2E")
    price_currency: str = Field(default="USD", alias="PRICE_CURRENCY")
    vcu_price_source: str = Field(
        default="Indicative pilot pricing, conservative end of the VCU range",
        alias="VCU_PRICE_SOURCE",
    )

    # --- Presentation -----------------------------------------------------
    banner_text: str = Field(default="Pilot data. Not verified.", alias="BANNER_TEXT")
    fleet_name: str = Field(default="SANNOVA", alias="FLEET_NAME")
    # Sites are shown by pseudonym by default; client names are not disclosed.
    show_real_site_names: bool = Field(default=False, alias="SHOW_REAL_SITE_NAMES")
    # Record who opens the portal. First-party only: no third-party script and
    # nothing leaves this database. Set false to record nothing at all.
    track_visits: bool = Field(default=True, alias="TRACK_VISITS")

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
                "value": (
                    f"{self.default_emission_factor:g} tCO2e/MWh"
                    if self.default_emission_factor > 0
                    else "not set"
                ),
                "note": (
                    self.default_emission_factor_source
                    if self.default_emission_factor > 0
                    else "Still outstanding. Carbon and carbon revenue read zero until a "
                         "sourced factor is entered, no placeholder stands in for it."
                ),
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
