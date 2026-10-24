"""Terac human studies (spec 5.4). Mock mode prices instantly and pretends to launch a study.

Quote statuses are normalised to: "pending", "priced", "failed".
"""
import secrets

from app import config

MOCK_CPI_USD = 4.0


def request_quote(spec: dict) -> str:
    if config.MOCK_TERAC:
        return "mock_req_" + secrets.token_hex(6)
    raise NotImplementedError("Real Terac adapter is built in Milestone 6 (spec 5.4 VERIFY). Set MOCK_TERAC=1.")


def get_quote(request_id: str) -> dict:
    if config.MOCK_TERAC:
        return {"status": "priced", "cpi_usd": MOCK_CPI_USD}
    raise NotImplementedError("Real Terac adapter is built in Milestone 6 (spec 5.4 VERIFY). Set MOCK_TERAC=1.")


def launch(request_id: str, participant_url: str) -> str:
    if config.MOCK_TERAC:
        return "mock_study_" + secrets.token_hex(6)
    raise NotImplementedError("Real Terac adapter is built in Milestone 6 (spec 5.4 VERIFY). Set MOCK_TERAC=1.")


def get_study(study_id: str) -> dict:
    """Mock reports no Terac-side submissions; the agent counts responses from our own DB (spec 5.6)."""
    if config.MOCK_TERAC:
        return {"status": "active", "submissions_received": 0, "target": config.TERAC_PARTICIPANTS}
    raise NotImplementedError("Real Terac adapter is built in Milestone 6 (spec 5.4 VERIFY). Set MOCK_TERAC=1.")
