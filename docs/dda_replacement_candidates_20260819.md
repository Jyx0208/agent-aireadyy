# DDA 替换候选（2026-08-19）

本清单用于替换当前 benchmark 中不支持的 DIA 项目 PXD080696。候选均来自 PRIDE Archive API；只有项目元数据明确标注 `Data-dependent acquisition` 的项目才进入表格。

## 首选替换

| 项目 | 建议文件 | 格式/大小 | 项目元数据证据 | 文件来源 |
|---|---|---:|---|---|
| [PXD079900](https://www.ebi.ac.uk/pride/archive/projects/PXD079900) | `20241218_Exploris_TS25_LouridoLab_DV_Sample_7_DDA.raw` | RAW / 0.65 GiB | Bottom-up proteomics；Data-dependent acquisition；Orbitrap Exploris 480；*Toxoplasma gondii*；PI Sebastian Lourido；COMPLETE；另有 `Glycan_bead.sdrf.tsv` | `ftp://ftp.pride.ebi.ac.uk/pride/data/archive/2026/08/PXD079900/20241218_Exploris_TS25_LouridoLab_DV_Sample_7_DDA.raw` |
| [PXD079900](https://www.ebi.ac.uk/pride/archive/projects/PXD079900) | `20241218_Exploris_TS25_LouridoLab_DV_Sample_5_DDA.raw` | RAW / 0.60 GiB | 同上 | `ftp://ftp.pride.ebi.ac.uk/pride/data/archive/2026/08/PXD079900/20241218_Exploris_TS25_LouridoLab_DV_Sample_5_DDA.raw` |

这两个文件来自同一项目，适合直接替换 PXD080696 的两份 DIA 文件：仍保持 8 个项目、16 个文件；总新增下载量约 1.25 GiB。

## 备用候选

| 项目 | 建议文件 | 格式/大小 | 关键元数据证据 | 备注 |
|---|---|---:|---|---|
| [PXD079334](https://www.ebi.ac.uk/pride/archive/projects/PXD079334) | `20240430_AS_LC4_GF_Sante_exp2_400ng_F17.raw`、`..._F13.raw` | RAW / 0.57、0.55 GiB | Bottom-up；DDA；Orbitrap Ascend；人；PI Enzo Tramontano；COMPLETE；有 SDRF | 适合增加仪器/实验室差异 |
| [PXD081816](https://www.ebi.ac.uk/pride/archive/projects/PXD081816) | `BimczokD_022323_02.raw`、`CherneM_20260415_01_DDA_02.raw` | RAW / 0.80、1.13 GiB | Bottom-up；DDA；Orbitrap Eclipse；人；PI Diane Bimczok；COMPLETE；有 SampleList | 文件较大，但证据完整 |
| [PXD080314](https://www.ebi.ac.uk/pride/archive/projects/PXD080314) | `241211_S4_Flour-c.raw`、`250324_S8_Crust-c.raw` | RAW / 0.42、0.46 GiB | Bottom-up；DDA；Q Exactive；小麦/黑麦/大麦；PI Katharina Anne Scherf；COMPLETE | 项目级有多物种，文件级物种需再由 SDRF/样本表确认 |
| [PXD079061](https://www.ebi.ac.uk/pride/archive/projects/PXD079061) | `2024-10-22-204A.raw`、`2024-10-22-116B.raw` | RAW / 0.53、0.53 GiB | Bottom-up；DDA；Orbitrap Fusion Lumos；*Lepidochelys kempii*；COMPLETE | 可增加跨物种泛化，但实验室/样本背景较特殊 |

## 明确排除

- PXD080696 的两份 DIA 文件不进入新的处理计划。
- PXD061973、PXD064530 虽然明确是 DDA，但主要文件是 WIFF/WIFF.SCAN；当前 Tower3 的批处理链路没有把 WIFF sidecar 作为可处理输入，因此暂不作为本轮替换。

## 建议

先用 PXD079900 的两份 RAW 替换 PXD080696。确认后再更新最终 manifest、生成下载任务，并只把这两份 DDA 文件送入 Tower3 批处理。
