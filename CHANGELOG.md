# Changelog

## [Unreleased]

### Added

- 日用电抓取会尽量切换到国网页面近 30 天范围，并把更多日历史发布到 HA/MQTT。
- 新增无人值守登录风控熔断：RK001、操作频繁、验证码通过后仍失败等情况会进入冷却，避免立即重试反复打账号。
- 新增 `SGCC_DAILY_RUNS`、`RISK_COOLDOWN_MINUTES`、`SGCC_LOGIN_COOLDOWN_ENABLED`、`SGCC_QRCODE_FALLBACK_UNATTENDED` 等运行参数。

### Changed

- 无人值守默认每日一次真实登录；二维码兜底默认不用于定时无人值守任务。
- 浏览器启动时补充语言、timezone 与 webdriver 显性特征一致性设置，减少误判风险。

## [arc-v0.1.0] - 2026-06-18

第一版 SGCC Home Assistant Bridge 二开发布。

### Added

- 真实浏览器账号密码登录，支持多模态 LLM 点选验证码。
- Path B 抓取 SGCC Vue2/Vuex store 与组件数据。
- 规范化 SQLite 本地事实源：账户、余额、日/月/年用电、运行记录、会话检查、发布状态。
- Home Assistant MQTT Discovery 自动创建设备和实体，并保留 REST states API 兼容发布。
- 日用电、月度、年度、峰/平/谷/尖分时数据的缓存恢复与重发布。
- 错误现场保存与日志脱敏。

### Changed

- 保留上游 Home Assistant / Docker 部署外壳，重写核心抓取、解析、存储和发布链路。
- 项目元数据、README、Add-on repository 信息改为 SGCC Home Assistant Bridge。

[arc-v0.1.0]: https://github.com/MaribelHearm/sgcc-home-assistant-bridg/releases/tag/arc-v0.1.0
