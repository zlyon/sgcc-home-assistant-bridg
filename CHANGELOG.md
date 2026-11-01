# Changelog

## [Unreleased]

### Changed

- 默认镜像地址改指本仓库的 GitHub Container Registry 命名空间：`docker-compose.yml` 里 app/browser 两个 `image` 的默认值、`config.yaml` 的 add-on `image`，以及 DOCS / Add-on 教程 / 发布说明里可复制执行的拉取示例，全部由 `ghcr.io/maribelhearm/...` 改为 `ghcr.io/zlyon/...`。此前 fork 之后重新拷贝 `docker-compose.yml` 会把镜像地址覆盖回上游，装出来的容器跑的是不含本仓库修改的镜像。
- 阿里云 ACR 章节增加说明：那套地址由上游账号推送，本仓库未配置 `ALIYUN_*` secrets 时 CI 会跳过推送，照抄会拉到不含本仓库修改的镜像。

### Fixed

- 修复**短信验证码登录拿不到验证码**：发码状态机原来只在 8 秒窗口里读前端状态，读到 `pending` 就换下一种点击方式、读到 `error` 就判定失败。实测（2026-11-01 11:22 现场截图 + 网络记录）国网从点击「获取验证码」到腾讯点选浮层渲染出来要 10 秒以上，浮层被稳定错过，于是流程掉头就走，短信从未发出，随后被 `RK001` 收尾。现在点击后进入 `_await_phone_code_sent` 统一循环：每轮先探测腾讯/自研浮层，探到就地解算，解完继续等前端确认发码；只有「没有浮层 + 前端确认已发送」才算成功，窗口默认放宽到 25 秒，并加了最多 3 轮解算的硬上限防死循环。
- 修复**二维码兜底登录不给 Telegram 发码**：发码失败后点选浮层还挂在页面上，`_qr_login` 直接 `ActionChains` 点 `.qr_code` 被遮罩吃掉（`ElementClickInterceptedException`），但旧代码不看结果就往下走，接着在短信面板上找二维码，超时抛异常，`send_qr_code` 一次都没执行。现在改用 JS 点击绕过遮罩，并以 `.sweepCodePic` 可见作为切换成功判据，切不过去会明确报 `qrcode_page_failed`。
- 短信表单定位去掉两处国网前端一改版就失效的绝对路径 XPath：切「短信验证码登录」标签改按 `.code_login` 定位并以「手机号输入框可见」判成功；登录按钮改按所在表单内的「登录」按钮点击。输入框也从 `el-input__inner` 下标改为按 `placeholder`（手机号码 / 请输入验证码）取，解验证码后重新取句柄，避免 DOM 重渲染导致 `StaleElementReferenceException`。

- 修复**参考目标是图片时仍走坐标直读**的问题（这才是 2026-11-01 01:22 那次仍然 `captcha_failed` 的原因）：上一版的「切符号 + 选编号」只在 `reference_text` 非空时生效，而国网这套腾讯点选把「请依次点击：」后面的三个目标字符**渲染成灰底图片条**（`header_answer_img`），DOM 里取不到文字，`targets` 恒为空，于是每次都退回让模型直接给坐标。现在把标题条当作第二张图送进模型，它只做「形状对照 + 报第几号」，坐标依旧由连通域算出，`targets` 为空也能走这条路。真实失败截图验证：`请依次点击 [宫形 0 1]` → 分割出 5 个候选 → 模型选出 `#1 #2 #0` → 坐标 `(60,62) (216,61) (295,24)`，连续多轮一致。
- 模型调用加节流与更长退避：免费档是账户级限流，1 秒级退避压不住（日志里一分钟能连撞三次 429）。现在每次请求前至少间隔 2 秒，429 后退避 5 秒再试；投票轮次由 3 降到 2、单轮重试由 3 次降到 2 次，避免把整轮验证时间耗在限流上。
- 修复**点选验证码点不中**导致的登录/发码全流程失败（`captcha_failed`）：腾讯点选题直接问模型要坐标时，它对这种又细又小的线描字形定位不准——实测一张 338×247 的图，三个目标里有一个偏了 167px，另一次把「1」指到了画面最右侧的书本干扰符号上。「确定」要三次都点中才会点亮，点空就等于整轮验证从未提交过，于是刷新重来、三次耗尽后报失败。现在改为**图像分割定位 + 模型只选编号**：连通域把画面里的黑色线描符号切出来，坐标由算法算出（精确到像素），模型只需要回答「第几个格子」，完全不输出坐标，误差源消失；整轮也从原来的 3 次模型调用降到 1~2 次。
- 单次采样的形近字符顺序错（`1` 与 `8` 对调）增加投票：连问两轮，两轮一致才采用，不一致再问一轮取多数，仍无多数则放弃本轮、换一张题图重来，绝不带着错误坐标去点。
- 修正验证码类型判定顺序：腾讯点选组件的标题同样是「请依次点击：…」，会先命中通用浮层探测，被错交给通用解算器。现在腾讯组件优先判定，通用浮层只兜非腾讯结构的情况。

### Added

- 新增 `SGCC_ERROR_HTML`（默认 `false`）：错误现场额外落 `login_box.html` + `ui-state.json`，记下登录框结构、当前激活的 Tab、输入框占位符、可见的腾讯浮层与近似模态框、`.errmsg-tip` 文案、短信/扫码面板是否可见。只写结构不写内容——`<input value>` 与图片 base64 一律抹掉，手机号密码不会落盘。排查「浮层到底弹没弹、选择器还对不对」不必再靠猜。
- 发码状态机增加逐次状态日志：状态、`auth_time`、错误文案发生变化时打一行 `发码状态=… auth_time=… error=…`，并在探到浮层、解算完成、超时各记一条 timeline 事件（`login_phone_code_request_captcha_started/finished`）。
- 新增 3 个单测：发码前浮层就地解算、浮层常驻时不把「前端倒计时」当已发送（防重复提交错答案）、扫码面板切换失败时明确报错。

- 启动日志改为打印真实的构建来源：`当前仓库版本为 <commit>，构建于 <构建时间>，代码仓库为 <仓库地址>`。此前仓库地址是写死的上游字符串，fork 用户重建镜像后日志里仍显示原作者地址，看起来像「还在跑别人的镜像」。现在三项分别来自镜像内的 `VERSION`、`SGCC_BUILD_TIME`、`SGCC_REPO_URL`，由 CI 通过 build-arg 注入，同时写入 `org.opencontainers.image.{revision,created,source}` 三个 OCI label，可以直接用 `docker inspect` 核对。
- `click_captcha_solver.py` 新增 `_solve_by_reference_strip` / `_ask_strip_picks`：参考目标是图片时，把标题条放大后与候选拼图一起送模型做形状对照；`_vote_picks` / `_request_picks` 抽出多图投票与带节流的单轮请求，文字题与图形题共用。新增 `_image_uri`、`_scale_to_height` 两个小工具。
- 新增 `tests/test_click_captcha_solver.py::ReferenceStripTestCase`（6 个用例）：编号映射、缺少参考图、候选不足、编号非法、`solve()` 在 `targets` 为空时确实走新路径且不再调用坐标直读、标题条放大。

- 两个 Dockerfile 都接受 `SGCC_REPO_URL` / `SGCC_BUILD_TIME` 两个 build arg；本地构建不传时日志显示 `unknown`，可据此判断镜像不是 CI 构建的。
- `click_captcha_solver.py` 新增符号分割三件套：`_symbol_boxes`（阈值分割 + 4 邻域连通域，纯 PIL 实现，不引入 numpy）、`_build_symbol_montage`（把每个符号裁出来拼成带 `#编号` 的对照图）、`_parse_picks`（编号校验：数量、重复、越界）。分割失败（底图过暗、候选数量不合法）时自动回退原有坐标直读路径。
- 新增 `tests/test_click_captcha_solver.py` 里的 `SymbolSegmentationTestCase` 与 `SymbolPickParsingTestCase`，共 14 个用例，覆盖分割、编号顺序、暗底拒绝、拼图尺寸、编号校验与非法返回，不依赖浏览器和模型。

- 修复**密码登录**在提交后不点验证、30 秒后被服务端判成 `RK001` 的问题：`login()` 的密码分支只调用腾讯 `solve_captcha_in_browser`，而国网点完「登录」弹出的是自研图形点选浮层（「请依次点击：4 0 5」），不在腾讯选择器覆盖范围内，识别恒为 False，于是浮层一直挂着没人点，服务端等到超时返回「网络连接超时（RK001）」，又被 `classify_login_failure` 归到 `risk_blocked` 触发熔断。上一版（`fd02b3e`）只修了短信发码前的浮层，密码登录这条路没跟上。现在提交后先判定浮层类型再分派解算器（`_detect_pending_captcha` / `_solve_post_submit_captcha`），密码与短信两条路径共用。
- 修复发码状态机里「前端报错」压过「浮层还在」的问题：原逻辑一读到 `status=error` 就直接返回，而国网有时在弹出点选浮层的同一刻把发码标成失败（或残留上一轮的失败信息），于是流程掉头就走，永远不去点那三个符号，表现正是「不点验证、也不发验证码」。现在只有 `sent` 是终局判据，`error` 让位给浮层检测。
- 密码登录提交后的固定等待由 20 秒缩短为 2 秒：浮层摆着不动的时间越长越容易踩 `RK001`，必须尽快接手。
- 浮层探测逻辑加固：改以「请依次点击 / 请点击图片 / 拖动滑块」等强文案为主判据，命中即认定，不再要求 `z-index >= 60`（国网改版会动层级和 class）；候选容器按「同时含强文案与确定按钮且面积最小」选取，避免选中继承同一段文案的外层容器导致裁剪多一圈边距、坐标整体偏移；登录表单改用「是否含可见输入框」判定，避免隐藏 input 把浮层一起排掉。
- 修复短信验证码登录在「获取验证码」阶段永久卡死的问题：国网在发码前弹出的是自研图形点选浮层（「请依次点击：1 7 □」+ 立体底图 + 若干黑色描边符号），DOM 结构与腾讯 `tcaptcha` 完全不同，`has_captcha_in_browser` 对它恒为 False，发码状态机只能停在 `pending`，三种点击方式耗尽后以 `phone_code_request_failed` 结束。这条路径此前从未交给多模态模型处理。
- 发码状态等待时长由固定 3 秒改为可配置（`SGCC_CAPTCHA_RENDER_WAIT`，默认 8 秒）。国网是先渲染浮层、点对图形并确认后前端才真正请求短信，3 秒覆盖不到；一旦探到浮层立即返回，正常发码路径不受影响。
- 探到该浮层后不再重复点击「获取验证码」。原逻辑在状态仍为 `pending` 时会换下一种点击方式再点一次按钮，而浮层还挂在那里，重复请求只会加重风控。
- 新增环境变量 `SGCC_CAPTCHA_RENDER_WAIT`（发码等待秒数，默认 8）与 `SGCC_OVERLAY_CAPTCHA_ATTEMPTS`（解算重试轮次，默认 2）。

### Added

- 新增 `sgcc_ha_bridge/overlay_captcha.py`：DOM 定位非腾讯结构浮层 → 截屏后**把中间的底图单独裁出来放大 4.5 倍**作为模型主图（坐标基准）、标题行单独放大 6 倍作为第二张图 → 多模态模型给出比例坐标 → 按底图在浮层内的位置换算回页面坐标 → CDP 真实鼠标事件点击 → 点「确定」→ 复查浮层是否消失。
- 坐标基准定为底图而不是整张浮层：整张浮层一起放大时，标题行那几个字符会变得又大又抢眼，实测模型会把它们当成待点符号，三个 `picks` 全落在标题行上；只送底图后同一张图连续三次都能正常枚举出「右上 4、左中 5、右下 0」这类分布。
- 「确定」按钮不再问模型，按版式取浮层底部居中（`_CONFIRM_RATIO`）。按钮不在送进模型的底图里，模型只会回 `[0, 0]`，而 0/0 是合法比例坐标，照着点等于点标题行。
- 模型调用增加 429 退避：免费档视觉模型（如 `glm-4.6v-flash`）经常限流，限流与参数无关，原来那套「换下一组参数」的重试只会连打四次都失败，现在改为等 2 秒、4 秒后重打同一组。
- 新增 `tests/test_overlay_captcha.py`：覆盖坐标换算、符号下标反查、方案解析容错与浮层探测异常处理，不依赖浏览器和模型。
- 新增环境变量 `SGCC_OVERLAY_DEBUG`：打开后把送进模型的底图和标题图保存到 `/data/debug/overlay-*.png`。浮层识别失败时，光看日志分不清是「没找到浮层」还是「裁歪了/模型看错」，留张原图能省一轮盲猜。
- 浮层定位不到但页面确实含验证文案时，日志会打印该片段并提示探测规则需要更新，不会再静默判成「没有浮层」。
- 新增环境变量 `SGCC_LLM_THINKING`：智谱 GLM 用 `thinking.type` 关闭深度思考，与火山方舟的 `reasoning_effort` 互斥，按厂商二选一即可。
- 模型请求改为逐级降级：`reasoning_effort` → `response_format=json_object` → 朴素请求 → 去掉 `image_url.detail`。不同厂商的 OpenAI 兼容层支持的字段不一致，任何一级成功即返回，不必再手动试参数。
- 新增环境变量 `SGCC_LLM_IMAGE_DETAIL`（默认 `high`，填 `off` 去掉 `image_url.detail`）。
- 图形点选的输出预算由 2048 提到 4096：强制思考的模型（如 GLM-5.3-Flash）把推理 token 算在输出里，2048 容易被推理吃光而返回空正文。按实际输出计费，对不思考的模型没有额外成本。

### Notes

- **订正前一版对验证码类型的判断**：截图与 DOM 选择器都表明，国网这套「请依次点击：1 8 9」的弹层是腾讯 `tencent-captcha-dy` 的点选模式（`#tCaptchaDyContent`、`.tencent-captcha-dy__header-text` 等都能命中，`has_captcha_in_browser` 返回 True），底图是 AI 生成照片、目标符号是黑色线描字形，另有猫脸/书本作为干扰项。之前误判成「国网自研浮层」，据此写的通用解算器路径保留下来只作兜底。
- 本改动只负责把浮层交给视觉模型并完成点击，不绕过国网服务端的 `RK001` 风控；`RK001` 仍按既有逻辑转短信登录。
- 判断这次 `RK001` 属于「验证未完成」而非「环境指纹被拒」的依据：`/data/errors` 里 `login_failed_captcha_failed_20261101_000701` 的截图显示浮层停在「请依次点击：4 0 5」原样未动，且 23:50:55 提交到 23:51:25 报错之间正好是腾讯解算器 5 次空转的时长。修复后若仍拿到 `RK001`，才需要回到环境指纹方向继续查。

## [v0.1.9] - 2026-08-08

### Fixed

- 修复多户号切换后余额页面尚未刷新便被读取的问题，避免不同户号复用同一余额或偶发漏抓。
- 兼容当前国网账号登录面板的前端切换方式，并完善 browser-service 环境和短信二次验证处理。
- 腾讯验证码只处理当前 viewport 内实际显示的组件，避免把页面预加载在屏幕外的隐藏 scaffold 误判为正在进行的点选或滑块验证。
- 点选验证码主图和参考图优先从渲染后的元素截图提取，兼容当前 95598 前端使用 `currentSrc`、CSS background 或运行时资源的方式。
- 短信验证码提交后的腾讯文字点选题不再依赖 `.tencent-captcha-dy__header-answer img`；目标文字直接从可见标题提取，显式元素查找也不再叠加全局 60 秒隐式等待。
- 文字点选增加三次视觉识别一致性裁决，并把粗坐标校正到当前腾讯黄/橙色字形中心，降低模型命中字形上方空白区域导致验证失败的概率。
- 短信登录只有在国网页面确认进入发码倒计时或返回“验证码已发送”后才开始 Telegram 等待；点击未生效时按多种方式回退，避免短信尚未发送却提前消耗人工输入窗口。
- 二维码登录会等待图片真正加载，并在失效时点击实际绑定刷新事件的遮罩；刷新后拒绝继续使用旧二维码。

### Diagnostics

- app/browser 镜像由同一次 CI 注入相同 Git revision；browser-service `/status` 暴露 revision，主程序启动 sidecar 时记录并检查两者是否一致。
- 登录调试包增加不含凭证的浏览器运行态、请求元数据和关键阶段时间线。

### Documentation

- README 新增常见问题，说明 ARM64 适配进度、RK001 处理方式、当前版本状态和 MQTT 兼容实体。

### Limitations

- 上述修改修复项目侧验证码与二维码兜底链路，不代表国网服务端 `RK001` 风控已被绕过或根治；真实账号仍需低频、受控验证。

## [v0.1.8] - 2026-07-21

### Fixed

- 修复 v0.1.5 及更早用户升级后旧 MQTT 实体被删除、Lovelace/自动化引用失效的问题：默认 `MQTT_LEGACY_DISCOVERY_MODE=compat` 会在 canonical v2 成功发布后，为末四位唯一的账户恢复原 v0.1.5 Discovery 身份。
- 同一账号存在多个末四位相同的户号时，自动撤销无法证明归属的旧别名，只保留各自防碰撞的 canonical v2 实体；非权威账户枚举不创建也不删除旧别名。
- REST 旧实体清理仅在全部 delete 成功或目标不存在后标记完成，部分失败会在后续发布重试。

### Added

- canonical MQTT Discovery 显式发布 `default_entity_id=sensor.sgcc_<末四位_稳定摘要>_<key>`，冻结新安装的实体命名契约。
- 新增 `MQTT_LEGACY_DISCOVERY_MODE=compat|off|cleanup`；`off` 不触碰旧 retained Discovery，`cleanup` 仅供所有旧消费者迁移完成后显式清理。
- 新增独立实体迁移文档、canonical Lovelace 示例契约和 v0.1.5 → v0.1.8 回归测试。

### Compatibility

- `PUBLISHER=rest|mqtt|both` 全部保留；默认升级无需 Home Assistant API，也不要求改用 `both`。
- v0.1.6 引入 `末四位_稳定摘要` 身份时会删除旧 MQTT Discovery，属于需要消费者迁移的实体身份变更；v0.1.8 为该升级路径提供默认兼容和自愈。

## [v0.1.7] - 2026-07-21

### Fixed

- 短信验证码等待超时后停止本轮外层登录重试，避免无人响应时重复请求并发送短信验证码。
- 每日抓取偏移写入 `/data/daily_schedule.json`；容器在午夜后重启时会恢复仍待执行的上一逻辑日跨日任务，避免正偏移或每日两次任务被漏调度。

## [v0.1.6] - 2026-07-20

### Added

- 每日定时抓取在持续运行期间会为下一天重新抽取 `SGCC_DAILY_JITTER_MINUTES` 偏移；每天两次任务仍共用当天偏移并保持 12 小时间隔。
- 新增可配置的 `phone-code,qrcode` 人工登录兜底顺序，以及 Telegram Bot 二维码通知、短信验证码回复和结果通知。
- 新增统一的 `SGCC_LOGIN_FALLBACK_UNATTENDED` 安全开关；默认定时任务不等待人工响应，旧 `SGCC_QRCODE_FALLBACK_UNATTENDED` 保持兼容。
- 新增默认关闭的 `SGCC_RISK_FALLBACK_OVERRIDE`；只有本次任务原本允许 fallback 时，才可在 RK001 后尝试一次已配置的限时人工接管，失败仍进入冷却。

## [v0.1.5] - 2026-07-03

### Changed

- 整理仓库结构：移除 `scripts/` Python 兼容 wrapper，Docker/Add-on/镜像入口统一使用 `sgcc_ha_bridge` 包模块。
- 整理 examples、开发文档和发布文档，补充 Markdown 本地链接检查。

### CI

- 补充包入口导入检查、Markdown 本地链接检查和 Dockerfile 静态检查，文档与示例变更也会触发 CI。

## [v0.1.4] - 2026-06-30

### Fixed

- 修正阿里云 ACR browser-service 镜像发布方式：browser 镜像改为发布到已公开的 `sgcc_ha:browser-*` tag，避免新建 `sgcc_ha_browser` 仓库匿名拉取失败。
- Add-on 版本更新到 `v0.1.4`。

### Docs

- 更新 Docker Compose 国内镜像示例，app 使用 `sgcc_ha:*`，browser 使用 `sgcc_ha:browser-*`。

## [v0.1.3] - 2026-06-30

### Added

- Docker Compose 新增官方 Google Chrome browser-service sidecar 镜像，默认 `SGCC_BROWSER_MODE=browser-service`，主程序通过 CDP attach，Chrome 按需启动并在任务结束后关闭。
- Home Assistant Add-on 默认接入单容器内嵌 browser-service，镜像内安装官方 `google-chrome-stable` 和匹配 ChromeDriver；Add-on 用户不需要另装 Google Chrome。
- CI 同时发布 app 镜像和 browser-service 镜像到 GHCR / 阿里云 ACR。

### Changed

- Add-on 版本更新到 `v0.1.3`。
- `docker-compose.yml` 支持通过 `SGCC_APP_IMAGE` / `SGCC_BROWSER_IMAGE` 直接拉取预构建镜像，也保留本地 build。

### Docs

- 补充 `browser-service` / `local` / `host-cdp` 三种浏览器模式说明，以及 Docker Compose、Add-on 和 RK001 场景下的配置切换方式。

## [v0.1.2] - 2026-06-27

### Fixed

- 修复多户号账号只抓到部分户号的问题：Path B 现在会先抓当前户号，再遍历所有可切换下拉项，并按真实户号去重，避免当前户号被下拉组件隐藏/禁用或同名民用户号被文本去重折叠。

### Changed

- Add-on 版本更新到 `v0.1.2`。

## [v0.1.1] - 2026-06-19

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
