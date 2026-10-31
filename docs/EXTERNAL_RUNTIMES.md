# Headless Chromium（前端视觉校验，可选）

**最近更新：** 2026-10-31

本机无系统 Chrome、无 sudo。`design-taste-frontend` 的 `inspect.mjs` / `scenes.mjs` 与 `.taste-skill/drive.mjs` 使用 Playwright 缓存中的 Chromium 二进制，配合 `~/.local/chromium-libs` 下的共享库与 fontconfig：

```bash
export CHROME_PATH=$HOME/.cache/ms-playwright/chromium-1248/chrome-linux64/chrome
export LD_LIBRARY_PATH=$HOME/.local/chromium-libs/usr/lib
export FONTCONFIG_PATH=$HOME/.local/chromium-libs/etc/fonts
export FONTCONFIG_FILE=$HOME/.local/chromium-libs/etc/fonts/fonts.conf
export XDG_DATA_HOME=$HOME/.local/share   # Noto Sans SC / Noto Serif SC / Adwaita Mono 在 ~/.local/share/fonts
```

缺少 `LD_LIBRARY_PATH` 时 Chrome 以 127 退出（`libnspr4.so`）；缺少 fontconfig 时中文渲染为方块。用法：

```bash
AGR_PROFILE=offline AGR_API_KEYS=default:design-admin:admin .venv/bin/agr-api        # 另一终端
node .taste-skill/drive.mjs .taste-skill/states [welcome,answer,...] --theme dark|light   # 走交互状态，留截图 + 静态快照
node .claude/skills/design-taste-frontend/scripts/inspect.mjs --app http://localhost:8000/web   # 实时首屏
node .claude/skills/design-taste-frontend/scripts/inspect.mjs --app .taste-skill/states/answer-1440.html  # 任一快照
```

---

其它外部运行时（JDK / Neo4j / 镜像 / Vue 与 Lucide 的 vendor 记录）见 [`docs/ops/EXTERNAL_RUNTIMES.md`](./ops/EXTERNAL_RUNTIMES.md)。
