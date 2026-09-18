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
    # These two were already present in every correlate_events() output
    # dict but never declared here — since no endpoint set
    # response_model= on its return type, nothing caught the mismatch;
    # once response_model is applied (see main.py) an undeclared field
    # would otherwise just be silently dropped.
    confidence: Optional[float] = None
    isFlagged: bool = False
    sourceModel: str = "Model 2"

    # Vehicle recognition, passed through from Model 2's vehicle_pipeline.py.
    # None means "not determined" on Model 2's side, not missing data here.
    vehicleType: Optional[str] = None
    vehicleColour: Optional[str] = None
    vehicleMake: Optional[str] = None
    vehicleModel: Optional[str] = None
    vehicleMakeModelSource: Optional[str] = None  # "vision" | "registry"

    # Multi-frame consensus, from Model 2's plate_consensus.py — lets the
    # frontend show WHY a reliability rating landed where it did.
    consensusReadCount: Optional[int] = None
    consensusAgreement: Optional[float] = None


class AnalyticsSummary(BaseModel):
    totalEvents: int
    lowReliabilityCount: int
    # Separate from lowReliabilityCount — see correlation.py's
    # build_analytics_summary for why a camera-health problem and a
    # weak-OCR-agreement problem are kept as distinct counts.
    lowAgreementCount: int = 0
    departmentBreakdown: dict
    headline: str
