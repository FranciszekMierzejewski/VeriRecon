"""
Shared Firestore client factory.
Centralised to avoid project ID naming issues.
"""

import os
from dotenv import load_dotenv
from google.cloud import firestore
from google.oauth2 import service_account

load_dotenv()

PROJECT_ID: str = os.environ.get("GCP_PROJECT_ID", "verirecon-hackathon")
_client: firestore.Client | None = None


def get_client() -> firestore.Client:
    """
    Reuses same Firestore client instance than reconstructing per new call to avoid setup overhead.

    Locally, credentials come from gcloud ADC automatically. On Streamlit Cloud, no ADC session exists, 
    so credentials are built explicitly from a service account dict stored in st.secrets
    """

    global _client

    if _client is None:
        try:
            import streamlit as st # wrap import around, since used by firestore_client, agent/tools.py, data/generate_data.py and API container, therefore 
            # no seperate versions needed
            if "gcp_service_account" in st.secrets:
                credentials = service_account.Credentials.from_service_account_info(
                    dict(st.secrets["gcp_service_account"])
                )
                _client = firestore.Client(project=PROJECT_ID, credentials=credentials)
                return _client
        except (ImportError, FileNotFoundError):
            # streamlit not installed or no secrets.toml present
            pass

        _client = firestore.Client(project=PROJECT_ID)
    return _client


def sanitise_document_id(name: str) -> str:
    """
    Convert supplier name into a document ID safe for Firestore, to prevent mismatches that actually should work.
    """

    return name.replace(" ", "_").replace("&", "and")