from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import Optional

from sqlmodel import Session, select

from app.models.settings import ServiceConfig, ServiceConfigResponse
from app.services.encryption import get_encryptor


def save_setting(
    session: Session,
    service_name: str,
    url: str,
    credential: str,
    extra_config: Optional[dict] = None,
) -> ServiceConfig:
    """Save or update a service configuration. Encrypts the credential before storage."""
    encryptor = get_encryptor()
    encrypted = encryptor.encrypt(credential)
    extra_config_str = json.dumps(extra_config) if extra_config else None

    # Upsert: check for existing record by service_name
    statement = select(ServiceConfig).where(ServiceConfig.service_name == service_name)
    existing = session.exec(statement).first()

    if existing:
        existing.url = url
        existing.encrypted_credential = encrypted
        existing.extra_config = extra_config_str
        existing.is_configured = True
        existing.updated_at = datetime.now(timezone.utc).isoformat()
        session.add(existing)
        session.commit()
        session.refresh(existing)
        return existing
    else:
        config = ServiceConfig(
            service_name=service_name,
            url=url,
            encrypted_credential=encrypted,
            extra_config=extra_config_str,
            is_configured=True,
            updated_at=datetime.now(timezone.utc).isoformat(),
        )
        session.add(config)
        session.commit()
        session.refresh(config)
        return config


def get_setting(session: Session, service_name: str) -> Optional[ServiceConfigResponse]:
    """Fetch a service config and return a masked response. Never decrypts credentials."""
    statement = select(ServiceConfig).where(ServiceConfig.service_name == service_name)
    row = session.exec(statement).first()
    if row is None:
        return None

    extra = json.loads(row.extra_config) if row.extra_config else None

    return ServiceConfigResponse(
        service_name=row.service_name,
        url=row.url,
        is_configured=row.is_configured,
        credential_set=bool(row.encrypted_credential),
        extra_config=extra,
    )


def get_decrypted_credential(session: Session, service_name: str) -> Optional[str]:
    """Internal use only: decrypt and return the raw credential for service client calls."""
    statement = select(ServiceConfig).where(ServiceConfig.service_name == service_name)
    row = session.exec(statement).first()
    if row is None or not row.encrypted_credential:
        return None

    encryptor = get_encryptor()
    return encryptor.decrypt(row.encrypted_credential)


def get_all_settings(session: Session) -> list[ServiceConfigResponse]:
    """Return masked config status for all configured services."""
    statement = select(ServiceConfig)
    rows = session.exec(statement).all()
    results = []
    for row in rows:
        extra = json.loads(row.extra_config) if row.extra_config else None
        results.append(
            ServiceConfigResponse(
                service_name=row.service_name,
                url=row.url,
                is_configured=row.is_configured,
                credential_set=bool(row.encrypted_credential),
                extra_config=extra,
            )
        )
    return results


def is_service_configured(session: Session, service_name: str) -> bool:
    """Check if a service has been configured."""
    statement = select(ServiceConfig).where(ServiceConfig.service_name == service_name)
    row = session.exec(statement).first()
    return row is not None and row.is_configured
