# VeriRecon
 
**Autonomous invoice reconciliation agent for UK HGV/truck parts procurement.**
 
VeriRecon ingests a supplier invoice (PDF or image), extracts its structured data, and uses an autonomous agent to reconcile it against purchase order records — deciding whether to auto-approve, flag for review, or escalate, with a full audit trail of its reasoning. This is a submission to Google's ['All Things Agentic' Hackathon](https://allthingsagentichackathon.devpost.com/).
 
## Live links
 
- **Dashboard:** [verirecon.streamlit.app](https://verirecon.streamlit.app/)
- **API (Swagger UI):** [https://verirecon-api-849632669512.europe-west1.run.app/docs](https://verirecon-api-849632669512.europe-west1.run.app/docs)
- **Demo video:** see `demonstrations/` (Gemini extraction walkthrough)
## Why an agent, not a script
 
Reconciliation logic isn't fixed — the right checks depend on what earlier checks find. A missing PO makes a price-variance check meaningless. A valid PO doesn't rule out a duplicate submission. Rather than hardcode this branching, VeriRecon gives an LLM agent a set of tools and lets it decide which to call and in what order, based on what it learns from each result.
 
This makes the tool-call sequence itself observable proof that the system is reasoning, not following a script — a clean-match invoice and a missing-PO invoice take genuinely different paths through the same toolset.
 
## Architecture
 
```
Upload (PDF/image)
      │
      ▼
Gemini 3.5 Flash (multimodal extraction)
      │  → structured JSON: supplier, PO reference, amount, VAT, line items
      ▼
ADK Agent (Google Agent Development Kit)
      │  → calls tools autonomously, logs every call
      ▼
Reconciliation tools (Firestore-backed)
      │  → lookup_po, compute_variance, check_exact_duplicate,
      │     check_fuzzy_duplicates, verify_invoice_arithmetic,
      │     get_vendor_history
      ▼
Classification: Auto Approve / Flag For Review / Escalate
      │
      ├──► Firestore: processed_invoices (result + audit log)
      └──► Firestore: reasoning_chains (every tool call, in order)
```
 
**Backend:** FastAPI, containerized and deployed on Google Cloud Run.
**Frontend:** Streamlit dashboard (upload + review queue), deployed on Streamlit Community Cloud.
**Data:** Firestore (purchase orders, vendor history, processed invoices, reasoning chains).
 
## Key features
 
- **Multimodal extraction** — Gemini reads PDFs/images directly, extracting supplier, PO reference, amounts, VAT breakdown, and line items without OCR pre-processing.
- **Autonomous tool use** — the agent decides its own investigation path per invoice rather than following a fixed pipeline.
- **Six reconciliation tools:**
  - `lookup_po` — verifies a PO exists and matches its recorded amount/supplier
  - `compute_variance` — flags overbilling beyond a GBP tolerance
  - `check_exact_duplicate` / `check_fuzzy_duplicates` — catches resubmitted or near-identical invoices
  - `verify_invoice_arithmetic` — checks an invoice's own subtotal/VAT/shipping actually sum to its stated total, independent of PO matching
  - `get_vendor_history` — supplies supplier track record as context
- **Full audit trail** — every tool call, its arguments, and its result are logged to Firestore in order, giving a human reviewer a legible explanation for any decision without needing a separate ML explainer. The trail is cleared and rewritten per invoice on each processing run, so re-processing the same invoice never produces a mixed or misleading chain.
- **Three-tier classification** — Auto Approve (no action needed), Flag For Review (minor discrepancy, human should check), Escalate (missing PO, duplicate, or serious mismatch).
## Folder structure
 
```
VeriRecon/
├── agent/
│   ├── __init__.py
│   ├── main_agent.py        ADK agent, system prompt, tool registration,
│   │                        reasoning-chain logging, reconcile entry points
│   └── tools.py              Reconciliation tools (Firestore-backed)
│
├── api/
│   ├── __init__.py
│   ├── app.py                 FastAPI app: /health, /reconcile
│   └── schemas.py             Pydantic response models
│
├── data/
│   ├── __init__.py
│   ├── generate_data.py       Synthetic PO/vendor/invoice generator,
│   │                          seeds Firestore for local testing
│   ├── demo_invoices.json     Generated labeled test invoices
│   └── sample_pdfs/           Sample invoice PDFs used for manual/extraction testing
│
├── db/
│   ├── __init__.py
│   └── firestore_client.py    Shared Firestore client factory,
│                              handles local ADC, Cloud Run's built-in
│                              service identity, and Streamlit Cloud's
│                              service-account-secret auth
│
├── demonstrations/            Demo video(s)
│
├── extraction/
│   ├── __init__.py
│   └── gemini_extract.py      Multimodal invoice extraction via Gemini (Vertex AI)
│
├── tests/
│   ├── __init__.py
│   └── test_agent.py          End-to-end suite: runs demo_invoices.json
│                              through the agent, checks against expected outcomes
│
├── streamlit_app.py            Review dashboard: upload + reasoning-chain viewer
├── Dockerfile                  API container image definition
├── .dockerignore
├── .env / .env.example         Local environment config (GCP project, API URL)
├── .gitignore
├── requirements.txt
└── README.md
```
 
## Running locally
 
```bash
pip install -r requirements.txt
 
# Seed Firestore with synthetic PO and vendor data
python -m data.generate_data
 
# Run the API
uvicorn api.app:app --reload
 
# In a separate terminal, run the dashboard
streamlit run streamlit_app.py
```
 
Requires a `.env` file (see `.env.example`) with GCP project/location config, and `gcloud auth application-default login` for local Firestore/Vertex AI access.
 
## Deployment
 
- **API:** Dockerized, built and deployed via `gcloud builds submit` + `gcloud run deploy` to Google Cloud Run. Authenticates to GCP using Cloud Run's default compute service account (IAM-bound to Firestore and Vertex AI roles) — no credentials file needed in the image. Originally deployed on Azure Container Instances; migrated to Cloud Run after exhausting Azure trial credits, since Cloud Run scales to zero and incurs no cost while idle.
- **Dashboard:** Deployed on Streamlit Community Cloud, reading the API URL and GCP service account credentials from Streamlit secrets rather than a local `.env`.
## Testing
 
`tests/test_agent.py` runs a suite of synthetic invoices — covering clean matches, price variance, missing POs, and duplicate pairs — through the full agent pipeline and checks classifications against expected outcomes.
 
## Known constraints
 
- The first invoice in a duplicate pair is correctly Auto Approved, since nothing is wrong with it in isolation — only the second occurrence can be detected as a duplicate, once the first is on record.
- `check_exact_duplicate` and `check_fuzzy_duplicates` compare against `processed_invoices`, so duplicate detection only activates for invoices processed after the system went live — it has no visibility into paper/legacy records outside this system.