"""
schemas.py
Response schemas for the federation API. Mirrors what the frontend's
src/api/federation.js already expects, since that was built first.
"""
from datetime import datetime
from typing import Optional, List, Literal

from pydantic import BaseModel


class AdapterStatus(BaseModel):
    id: str
    name: str
    sourceModel: str
    endpoint: str
    status: Literal["connected", "error"]
    lastSync: datetime
    recordsSynced: int
    errorDetail: Optional[str] = None


class FederatedEvent(BaseModel):
    id: str
    plateNumber: str
    cameraId: str
    department: Optional[str] = None
    cameraHealthStatus: Optional[str] = None
    reliability: Literal["high", "medium", "low"]
    timestamp: datetime
    sourceModel: str = "Model 2"


class AnalyticsSummary(BaseModel):
    totalEvents: int
    lowReliabilityCount: int
    departmentBreakdown: dict
    headline: str
