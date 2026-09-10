"""
Pydantic response models for the VeriRecon API.
"""

from typing import Any
from pydantic import BaseModel


class ReconciliationResponse(BaseModel):
    invoice: dict[str, Any]
    classification: dict[str, Any]