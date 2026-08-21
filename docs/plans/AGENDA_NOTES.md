# Internal Beta Agenda Notes

以下两条可直接复制到 `INTERNAL_BETA_RUNBOOK.md` 的“对话试跑”小节。它们验证
TaskProfile agenda 与 B 薄接线，不承诺一定找到 build-ready 数据。

## 示例 1：Browse-only

开场白：

> 先帮我浏览 PRIDE 里的海洋无脊椎动物蛋白质组，暂时不做训练；数量不限。

期望观察：

- 识别为 `browse_only`，不出现 DDA、训练标签、relabel 或 optional labeling 问题；
- 如果候选即停/证据复核的交付终点仍不明确，只围绕 `delivery_horizon` 提一个
  `next_decision`；
- agenda 清空后只进入“关键决策已齐，可确认策略”，不能显示业务完成；
- 后续找到候选但没有 issued build-ready completion 时，只显示进展或 blocker。

## 示例 2：Chimeric 训练任务

开场白：

> 我想做 DDA 嵌合谱解释训练表，先找大约 20 个项目，物种开放。

期望观察：

- task、scale、acquisition 与 species-open 被吸收，不重复询问已经开放的物种；
- 下一个任务专属决策应优先为 `chimeric_label_feasibility`：要求已有可信的
  multi-peptide label，还是允许扩大候选后再做 downstream relabel；
- 该问题必须先于 `labeling_compatibility` 等 optional labeling；
- 每轮只出现一个动态 `next_decision`，不出现 Q1–Q10；
- 回答完 agenda、确认策略或看到候选都不等于毕业，仍只认 Authority-issued
  build-ready completion。

AGENDA_BETA_NOTES_STATUS: READY
