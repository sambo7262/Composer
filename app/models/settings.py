from __future__ import annotations

from typing import Optional

from sqlmodel import Field, SQLModel


class ServiceConfig(SQLModel, table=True):
    """Database model for service configuration. Stores encrypted credentials."""

    id: Optional[int] = Field(default=None, primary_key=True)
    service_name: str = Field(unique=True, index=True)
    url: str
    encrypted_credential: str
    extra_config: Optional[str] = None  # JSON string for library_id, model_name, profile_id
    is_configured: bool = False
    updated_at: Optional[str] = None


class ServiceConfigResponse(SQLModel):
    """API response model -- NEVER includes the credential value (D-04 / CONF-04)."""

    service_name: str
    url: str
    is_configured: bool
    credential_set: bool
    extra_config: Optional[dict] = None
