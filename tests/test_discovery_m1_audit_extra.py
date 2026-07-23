from __future__ import annotations

from pathlib import Path

import agent.web.app as web_app
from agent.discovery.models import DatasetManifest, DatasetRequest


def test_public_discovery_record_without_authority_completion_is_not_completed(
    tmp_path: Path,
) -> None:
    """A finished transport record must not self-certify business completion."""

    manifest = DatasetManifest(
        run_id="m1-audit-no-authority-completion",
        request=DatasetRequest(repository="pride", max_projects=1, max_files=1),
        projects=[],
        files=[],
    )

    record = web_app._public_discovery_record(
        discovery_id=manifest.run_id,
        output_dir=tmp_path,
        manifest=manifest,
    )

    assert record["business_completion"] is None
    assert record["status"] != "completed"

