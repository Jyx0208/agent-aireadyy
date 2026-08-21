# 生产环境条件 — 本波可编码交付

配合 `PRODUCTION_ENV_RUNBOOK.md`。

## 交付列表
1. `scripts/collect_l3_evidence.ps1` — 从 run 目录/JSON 抽脱敏 L3 字段草稿
2. `tests/test_discovery_ledger_multi_worker.py` — 多进程/线程争用同一 sqlite ledger（reserve/consume/replay）
3. `scripts/lab_https_signer/` 或最小 lab signer — **仅 lab**，自签 TLS，对接 HttpProductionPublicationSigner；文档大字 NOT KMS
4. `.github/workflows/production-gate.yml`（若仓库用 GH）或 `docs/plans/CI_PRODUCTION_GATE.md` 给其他 CI
5. `docs/plans/L3_SIGNOFF.md` — 三方签署表
6. 更新 `M5_GO_CHECKLIST.md` 诚实勾选（仅本波真正证明的）
7. `PRODUCTION_ENV_REPORT.md`

禁止：私钥入库、product GO、把 lab signer 写成生产 KMS。
