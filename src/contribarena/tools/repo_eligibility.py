from __future__ import annotations

from contribarena.config.schema import RepoCandidate
from contribarena.models import EligibilityResult


def repo_check_eligibility(candidate: RepoCandidate) -> EligibilityResult:
    return EligibilityResult(
        eligible=True,
        reasons=["M0.0 stub: all candidates are eligible"],
        checks_performed=[],
    )
