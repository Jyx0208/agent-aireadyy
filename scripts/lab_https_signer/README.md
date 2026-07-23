# LAB-ONLY HTTPS signer — NOT production KMS/HSM

此目录只用于验证 `HttpProductionPublicationSigner` 的真实 TLS、Bearer、`key_id`、payload digest 与 Ed25519 signature 合约。

禁止：

- 将这里的文件部署为生产 signer；
- 把私钥、证书私钥或 bearer token 提交到仓库；
- 对非 loopback 地址监听；
- 把本服务通过视为真实 KMS/HSM 或 product GO 证据。

自动测试会在 pytest 临时目录生成自签证书、TLS 私钥和 Ed25519 signing key，进程结束后由测试临时目录清理。生产环境必须使用组织管理的 KMS/HSM/secret manager 与正式证书。

运行合约测试：

```powershell
.\.venv\Scripts\python.exe -m pytest -q tests/test_lab_https_signer.py
```
