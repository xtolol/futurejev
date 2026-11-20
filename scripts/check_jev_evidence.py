"""Print one live Jev evidence analysis for a synthetic Project Atlas passage."""

import json
import os
import sys
from contextlib import closing
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from futuresim_agents.minimalHarnessAgent.evidence_analysis import EvidenceAnalysisError, TypeSafeEvidenceClient
from pathing import load_repo_env


def main() -> None:
    """Load host credentials, analyze a synthetic passage, and print the normalized reply."""
    load_repo_env()
    if not os.environ.get("TYPESAFE_API_KEY", "").strip():
        raise SystemExit("Set TYPESAFE_API_KEY in the environment or project .env to run this check.")

    question = {
        "qid": "atlas_001",
        "title": "Will fictional Project Atlas launch by December 31, 2025?",
        "background": "Project Atlas is a synthetic software project.",
        "resolution_criteria": "A public launch by December 31 counts.",
    }
    try:
        with closing(TypeSafeEvidenceClient()) as client:
            result = client.analyze(
                question=question,
                evidence_text="Project Atlas has completed internal testing, but its final launch date remains unconfirmed.",
                sim_date="2025-12-30",
                source_id="standalone-atlas-note",
            )
    except EvidenceAnalysisError as exc:
        raise SystemExit(str(exc)) from None
    print(json.dumps(result, indent=2))


if __name__ == "__main__":
    main()
