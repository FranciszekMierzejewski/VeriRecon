"""
Shared Firestore client factory.
Centralised to avoid project ID naming issues.
"""

import os
from dotenv import load_dotenv
from google.cloud import firestore

load_dotenv()

PROJECT_ID: str = os.environ.get("GCP_PROJECT_ID", "verirecon-hackathon")
_client: firestore.Client | None = None


def get_client() -> firestore.Client:
    """
    Reuses same Firestore client instance than reconstructing per new call to avoid setup overhead.
    """

    global _client

    if _client is None:
        _client = firestore.Client(project = PROJECT_ID)
    return _client
