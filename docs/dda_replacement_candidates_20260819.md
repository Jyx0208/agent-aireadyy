# DDA Replacement Candidates (2026-08-19)

This shortlist replaces PXD080696 because the current processing lane does not support DIA. Only projects whose PRIDE metadata explicitly contains `Data-dependent acquisition` are included.

## Selected replacement

| Project | Files | Format / size | Evidence | Source |
|---|---|---:|---|---|
| [PXD079900](https://www.ebi.ac.uk/pride/archive/projects/PXD079900) | `20241218_Exploris_TS25_LouridoLab_DV_Sample_7_DDA.raw` | RAW / 701,006,363 bytes | Bottom-up proteomics; DDA; Orbitrap Exploris 480; *Toxoplasma gondii rh*; COMPLETE; SDRF available | [FTP file](ftp://ftp.pride.ebi.ac.uk/pride/data/archive/2026/08/PXD079900/20241218_Exploris_TS25_LouridoLab_DV_Sample_7_DDA.raw) |
| [PXD079900](https://www.ebi.ac.uk/pride/archive/projects/PXD079900) | `20241218_Exploris_TS25_LouridoLab_DV_Sample_5_DDA.raw` | RAW / 646,817,627 bytes | Same project-level evidence | [FTP file](ftp://ftp.pride.ebi.ac.uk/pride/data/archive/2026/08/PXD079900/20241218_Exploris_TS25_LouridoLab_DV_Sample_5_DDA.raw) |

These two files preserve the benchmark shape: 8 projects and 16 files. They add approximately 1.25 GiB and are both directly supported by the current RAW conversion lane.

## Backup candidates

| Project | Instrument / organism | File format and approximate size | Evidence notes |
|---|---|---:|---|
| [PXD079334](https://www.ebi.ac.uk/pride/archive/projects/PXD079334) | Orbitrap Ascend / human | two RAW files, 0.57 and 0.55 GiB | Bottom-up; DDA; COMPLETE; SDRF available |
| [PXD081816](https://www.ebi.ac.uk/pride/archive/projects/PXD081816) | Orbitrap Eclipse / human | two RAW files, 0.80 and 1.13 GiB | Bottom-up; DDA; COMPLETE; SampleList available |
| [PXD080314](https://www.ebi.ac.uk/pride/archive/projects/PXD080314) | Q Exactive / wheat, rye, barley | two RAW files, 0.42 and 0.46 GiB | Bottom-up; DDA; file-level organism mapping still needs confirmation |
| [PXD079061](https://www.ebi.ac.uk/pride/archive/projects/PXD079061) | Orbitrap Fusion Lumos / *Lepidochelys kempii* | two RAW files, 0.53 GiB each | Bottom-up; DDA; COMPLETE |

## Explicit exclusions

- The two PXD080696 DIA files are excluded from the active manifest and processing plan. They are retained under `excluded_dia_20260819/PXD080696` for traceability.
- PXD061973 and PXD064530 have explicit DDA evidence but their primary files are WIFF/WIFF.SCAN, which the current Tower3 preparation lane does not accept as input.

The final manifest is now 8 projects, 16 files, and 0 DIA rows. The download task processes only the two PXD079900 RAW files before any Tower3 preparation is started.
