from __future__ import annotations

import json
import subprocess
from pathlib import Path


SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "collect_l3_evidence.ps1"


def test_l3_evidence_script_whitelists_and_fingerprints_sensitive_fields(
    tmp_path: Path,
) -> None:
    source = tmp_path / "run.json"
    output = tmp_path / "l3-evidence.json"
    source.write_text(
        json.dumps(
            {
                "run_id": "run:l3",
                "workflow": "discovery",
                "status": "completed",
                "prompt": "SECRET USER PROMPT",
                "latest_discovery_audit": {
                    "status": "ready",
                    "counts": {"candidate_projects": 3, "qualified_projects": 1},
                    "limitations": [],
                },
                "publication_authority": {
                    "authority_mode": "production",
                    "key_id": "prod-key-1",
                    "authorized_package_digest": "sha256:package",
                    "issuance_token": "SECRET PUBLICATION TOKEN",
                },
                "business_completion": {
                    "succeeded": "SECRET_SUCCEEDED",
                    "status": "build_ready_succeeded",
                    "issuance_token": "SECRET COMPLETION TOKEN",
                    "repair_authority_id": "https://authority.invalid/?secret=SECRET_AUTHORITY",
                    "repair_attempt_id": "publication-attempt:SECRET/../../path",
                    "repair_attempt_nonce": "SECRET NONCE",
                    "progress": {
                        "candidate_projects": "SECRET_COUNT",
                        "reviewed_projects": {"secret": "SECRET_TYPED_OBJECT"},
                        "judgment_qualified_projects": 1,
                        "build_ready_projects": 1,
                        "build_ready_files": 2,
                    },
                    "build_ready_package": {
                        "package_id": "package:l3?token=SECRET_PACKAGE",
                        "audit_ref": "audit:l3?token=SECRET_AUDIT",
                        "manifest_ref": "https://example.invalid/SECRET_PATH/manifest?token=SECRET_QUERY",
                        "evidence_store_ref": "file:///SECRET_PATH/evidence.json",
                        "builder_entrypoint": "https://builder.invalid/SECRET_ENTRYPOINT",
                        "builder_preflight_ref": "https://builder.invalid/preflight?secret=SECRET_PREFLIGHT",
                        "project_ids": ["PROJECT_A"],
                        "files": [{"file_id": "FILE_SECRET", "project_id": "PROJECT_A"}],
                    },
                },
                "builder_dry_run_result": {
                    "accepted": {"secret": "SECRET_ACCEPTED"},
                    "status": "builder_dry_run_accepted",
                    "package_digest": "sha256:package",
                    "key_id": "prod-key-1",
                    "receipt_ref": "https://builder.invalid/receipt?secret=SECRET_RECEIPT",
                },
                "repair_execution_keys": ["SECRET IDEMPOTENCY KEY"],
            }
        ),
        encoding="utf-8",
    )

    completed = subprocess.run(
        [
            "powershell.exe",
            "-NoProfile",
            "-File",
            str(SCRIPT),
            "-RunJson",
            str(source),
            "-OutputPath",
            str(output),
            "-EnvironmentName",
            "lab",
            "-DeploymentId",
            "https://deploy.invalid/?secret=SECRET_DEPLOYMENT",
            "-BuildStamp",
            "build/SECRET_BUILD",
        ],
        capture_output=True,
        text=True,
        check=False,
    )

    assert completed.returncode == 0, completed.stderr
    evidence_text = output.read_text(encoding="utf-8-sig")
    evidence = json.loads(evidence_text)
    assert evidence["schema_version"] == "l3-evidence-draft/v1"
    assert evidence["run"]["run_id"] == "run:l3"
    assert evidence["environment"]["deployment_id"].startswith("sha256:")
    assert evidence["environment"]["build_stamp"].startswith("sha256:")
    assert evidence["publication"]["package_digest"] == "sha256:package"
    assert evidence["publication"]["manifest_ref_fingerprint"].startswith("sha256:")
    assert evidence["publication"]["evidence_store_ref_fingerprint"].startswith(
        "sha256:"
    )
    assert evidence["publication"]["succeeded"] is None
    assert evidence["progress"]["candidate_projects"] is None
    assert evidence["progress"]["reviewed_projects"] is None
    assert evidence["builder"]["accepted"] is None
    assert evidence["builder"]["receipt_ref_fingerprint"].startswith("sha256:")
    assert evidence["repair"]["authority_id"].startswith("sha256:")
    assert evidence["repair"]["attempt_id"].startswith("sha256:")
    assert evidence["fingerprints"]["completion_token"].startswith("sha256:")
    assert evidence["fingerprints"]["completion_nonce"].startswith("sha256:")
    assert "SECRET" not in evidence_text
    assert "FILE_SECRET" not in evidence_text
