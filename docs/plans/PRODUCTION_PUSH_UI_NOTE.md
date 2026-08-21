# Production Push UI 短记

前端已对 run record 的可选 `publication_authority.authority_mode/key_id`、`publication_builder_preflight_status/ref` 做诚实展示：字段存在时仅在“文件与兼容指标”展开区显示，并明确 Builder preflight 只是兼容预检、**不等于 dry-run 接受或业务成功**；字段缺失时 fail-soft、不影响既有页面。

成功门禁未改变：只有完整 v2 Authority issued build-ready decision、有效 package/token 且 build-ready projects/files 非零才显示绿色“已完成”。新增负例覆盖 `production + preflight ready + succeeded=false + build_ready=0`，结果仍为 blocked、无成功绿态。

验证：

```text
npm test                    10 files / 193 tests passed
npx tsc -b --pretty false   passed
```

结论：无 UI 阻塞；本说明不构成 product GO。
