from enum import IntEnum
from typing import Annotated, Literal
from uuid import UUID, uuid4

import pycountry
from pydantic import BaseModel, ConfigDict, Field, PositiveInt, field_validator


class Input(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)


class CreateCampaign(Input):
    name: str = Field(min_length=1, max_length=100)
    country: str = Field(min_length=2, max_length=2)
    offer_id: PositiveInt
    domain_id: PositiveInt | None = None
    group_id: PositiveInt | None = None
    traffic_source_id: PositiveInt | None = None
    request_id: UUID = Field(default_factory=uuid4)

    @field_validator("country")
    @classmethod
    def country_iso(cls, value: str) -> str:
        value = value.upper()
        if not pycountry.countries.get(alpha_2=value):
            raise ValueError("Укажите код страны ISO 3166-1 alpha-2, например AU")
        return value


class OfferRow(BaseModel):
    offer_id: PositiveInt
    share: int = Field(ge=0, le=100)
    state: Literal["active", "disabled"] = "active"
    pinned: bool = False
    removed: bool = False


class OfferChange(Input):
    action: Literal["add", "remove", "restore", "pin", "unpin", "share"]
    offer_id: PositiveInt
    share: int | None = Field(default=None, ge=0, le=100)
    version: str


class Revision(Input):
    version: str


class Period(IntEnum):
    TODAY = 1
    WEEK = 7
    MONTH = 30


Id = Annotated[int, Field(gt=0)]
