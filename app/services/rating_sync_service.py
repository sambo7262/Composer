"""Manual 'Resync now' entry point (EVT-05).

Phase 5 backs Resync with the same backfill_service implementation — both auto
and manual paths share the singleton state machine and pagination logic. Per
CONTEXT D-11: "Auto handles initial deploy; manual handles future drift."

A future phase may diverge (e.g., resync only touches the rated set; backfill
scans all). This thin shim keeps that seam open without duplicating code now.
"""
from __future__ import annotations

from app.services.backfill_service import (
    BackfillStateEnum as ResyncStateEnum,
    get_backfill_status as get_resync_status,
    run_backfill as run_resync,
)

__all__ = ["run_resync", "get_resync_status", "ResyncStateEnum"]
