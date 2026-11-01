from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
DEMO = ROOT / "demo" / "brief_ai_draft.txt"


@pytest.fixture
def demo_bytes() -> bytes:
    return DEMO.read_bytes()


@pytest.fixture
def demo_text() -> str:
    return DEMO.read_text(encoding="utf-8")


def cluster(name, date, full=""):
    return {"case_name": name, "case_name_full": full, "date_filed": date, "absolute_url": ""}


def entry(status, clusters=(), citation="1 U.S. 1", msg=""):
    return {"citation": citation, "normalized_citations": [citation], "start_index": 0,
            "end_index": len(citation), "status": status, "error_message": msg,
            "clusters": list(clusters)}
