from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from pydantic import Field, HttpUrl, PositiveInt, SecretStr, field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_ignore_empty=True, extra="ignore")

    demo_mode: bool = True
    keitaro_base_url: HttpUrl | None = None
    keitaro_api_key: SecretStr | None = None
    database_url: str | None = None
    campaign_prefix: str = Field(default="test_pechenov_", pattern=r"^test_[a-z0-9_]{1,32}_$")
    report_timezone: str = "Europe/Moscow"
    domain_id: PositiveInt | None = None
    group_id: PositiveInt | None = None
    traffic_source_id: PositiveInt | None = None

    @field_validator("report_timezone")
    @classmethod
    def valid_timezone(cls, value: str) -> str:
        try:
            ZoneInfo(value)
        except ZoneInfoNotFoundError as error:
            raise ValueError("Use an IANA timezone, for example Europe/Moscow") from error
        return value

    @model_validator(mode="after")
    def check_live_settings(self):
        if not self.demo_mode and not (self.keitaro_base_url and self.keitaro_api_key):
            raise ValueError("Set KEITARO_BASE_URL and KEITARO_API_KEY for live mode")
        if self.keitaro_base_url and (
            self.keitaro_base_url.path not in (None, "/")
            or self.keitaro_base_url.query
            or self.keitaro_base_url.fragment
            or self.keitaro_base_url.username
            or self.keitaro_base_url.password
        ):
            raise ValueError("KEITARO_BASE_URL must be the tracker origin, without /admin")
        return self

    @property
    def db_url(self) -> str:
        default_file = "demo.sqlite3" if self.demo_mode else "workbench.sqlite3"
        return self.database_url or f"sqlite:///./{default_file}"
