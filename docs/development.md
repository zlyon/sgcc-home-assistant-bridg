# 开发与仓库结构

这个仓库按“可发布开源项目”整理：运行代码、兼容入口、测试、示例和文档分开存放。

## 目录结构

```text
sgcc_ha_bridge/   核心 Python 包
scripts/          Docker/Add-on shell 入口和旧导入路径兼容 wrapper
tests/            单元测试
tools/            离线辅助脚本
examples/         Home Assistant / Lovelace 示例
docs/             专题文档
assets/           README/docs/examples 使用的图片素材
ha_addons_doc/    Home Assistant Add-on 图文说明
.github/          CI、issue 模板和 PR 模板
```

## 入口兼容

历史版本把业务代码直接放在 `scripts/`。现在真实源码在 `sgcc_ha_bridge/`，但 `scripts/main.py`、`scripts/browser_service.py` 和其他同名 wrapper 仍然保留。

这样旧用法仍然有效：

```bash
PYTHONPATH=scripts python -c "import main, model"
python scripts/main.py
```

Docker/Add-on 也继续使用旧入口脚本，不影响已有部署。

## 本地验证

标准单测：

```bash
python -m unittest discover -s tests -v
```

如果本机 `.venv` 已安装测试依赖：

```bash
.venv/bin/python -m pytest -q
```

Dockerfile 静态检查：

```bash
docker build --check -f Dockerfile-for-github-action .
docker build --check -f Dockerfile.browser .
```

旧导入路径兼容检查：

```bash
python - <<'PY'
import sys
sys.path.insert(0, 'scripts')
import main, model, browser_service
assert main.__name__ == 'sgcc_ha_bridge.main'
assert model.__name__ == 'sgcc_ha_bridge.model'
assert browser_service.__name__ == 'sgcc_ha_bridge.browser_service'
PY
```

## 文档分工

- `README.md`：项目门面、快速开始和文档索引。
- `DOCS.md`：完整配置、部署、实体和排障说明。
- `docs/`：专题文档。
- `examples/`：可复制示例，不承诺自动安装。
