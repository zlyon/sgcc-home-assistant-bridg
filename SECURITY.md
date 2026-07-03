# Security Policy

## 支持范围

当前主要维护 GitHub 默认分支和最新发布版本。旧版本建议先升级到最新镜像或最新 tag 后再复现问题。

## 报告安全问题

如果你发现可能导致账号、Token、Cookie、Home Assistant 凭据、MQTT 凭据或 LLM API Key 泄露的问题，请不要在公开 issue 中贴出敏感信息。

推荐做法：

1. 使用 GitHub Security Advisory 私下报告；
2. 或在 issue 中只描述影响范围，不附带真实凭据、完整日志和截图中的敏感字段；
3. 等待确认后再公开细节。

## 日志和截图脱敏

提交日志、错误现场或截图前，请检查：

- 国网手机号、户号、姓名、地址；
- Home Assistant URL 和长期访问 Token；
- MQTT 用户名和密码；
- LLM API Key；
- 浏览器 Cookie、验证码会话和请求头。
