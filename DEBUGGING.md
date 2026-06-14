# Faber 沙箱服务 - 本地调试指南

> 最终方案：单一开发容器 + PyCharm 远程调试 + RealVNC 查看浏览器 + CDP 控制浏览器。

---

## 方案一：本地调试

适合：只调试 FastAPI 业务逻辑，不依赖 Chrome/VNC/Supervisor。

```bash
cd /Users/lyn/Desktop/faber/sandbox
uv run uvicorn app.main:app --host 0.0.0.0 --port 8080 --reload
```

PyCharm 运行配置：
- **类型**: `Python`
- **Module name**: `uvicorn`
- **Parameters**: `app.main:app --host 0.0.0.0 --port 8080 --reload`
- **Working directory**: `/Users/lyn/Desktop/faber/sandbox`
- **Interpreter**: 本地 `.venv/bin/python`

访问：`http://localhost:8080/docs`

> 本地运行没有 Supervisor，`/api/supervisor/*` 会报错是正常的。

---

## 方案二：PyCharm 远程调试容器内的服务（推荐）

适合：需要完整 Ubuntu 环境 + Chrome + VNC + Supervisor + SSH 调试。

开发容器已经集成了所有服务，只需要启动这一个容器。

### 1. 启动开发容器

```bash
cd /Users/lyn/Desktop/faber/sandbox/.devops
docker compose down
docker compose up -d --build
```

等待 10-15 秒，让 Chrome/Xvfb/VNC/SSH 全部启动。

端口对照：

| 端口 | 用途 |
|------|------|
| 2222 | SSH（PyCharm 远程调试） |
| 8080 | FastAPI（手动启动 uvicorn 后） |
| 5902 | VNC 原生协议（避开 macOS 自带 5900） |
| 9222 | Chrome DevTools Protocol (CDP) |

### 2. 验证服务

```bash
docker exec faber_sandbox_dev supervisorctl status
```

应该看到 sshd、xvfb、chrome、socat、x11vnc、websockify 都是 RUNNING。

### 3. 配置 PyCharm SSH 远程解释器

1. `Settings → Project: sandbox → Python Interpreter`
2. 齿轮 → `Add Interpreter → On SSH`
3. 新建连接：
   - **Host**: `localhost`
   - **Port**: `2222`
   - **Username**: `root`
   - **Authentication**: `Password`
   - **Password**: `root`
4. 下一页选择 **"选择现有"**（不要选 "生成新的"）
5. **类型**: `Python`
6. **Python 路径**: `/venv/bin/python`
7. **同步文件夹**: `<Project root>->/sandbox`
   - ⚠️ 必须是 `/sandbox`，不能是 `/tmp/pycharm_project_...`
8. **取消勾选** "自动上传项目文件到服务器"
   - 因为 `.devops/docker-compose.yml` 已经通过 `volumes` 把代码挂载到 `/sandbox`
9. **取消勾选** "通过 sudo 以 root 权限执行代码"
   - 开发容器里没有安装 `sudo`，而且登录的就是 root
10. 点击 **创建**

### 4. 配置运行配置

> ⚠️ **重要：不要用 PyCharm 的 `FastAPI` 运行配置模板**。它不支持远程 SSH 解释器的自定义参数，会导致服务跑到 `127.0.0.1:8000` 且断点不生效。必须用普通的 `Python` 配置。

1. `Edit Configurations...` → `+` → **`Python`**（不是 FastAPI）
2. 填写：
   - **Name**: `沙箱容器调试`
   - **Module name**: `uvicorn`
   - **Parameters**: `app.main:app --host 0.0.0.0 --port 8080 --reload`
   - **Working directory**: `/sandbox`
   - **Interpreter**: 选择刚才创建的 SSH 解释器（`/venv/bin/python`）
3. 点击 **OK**

### 5. 启动 Debug

点击 **Debug 按钮**（绿色甲壳虫）。

确认控制台输出包含：

```text
Will watch for changes in these directories: ['/sandbox']
INFO:     Uvicorn running on http://0.0.0.0:8080
```

如果显示的是 `http://127.0.0.1:8000`，说明你用的是 FastAPI 模板，返回第 4 步改成普通 `Python` 配置。

### 6. 打断点测试

1. 在 `app/services/file.py` 的 `read_file` 方法里打断点
2. 访问 `http://localhost:8080/docs`
3. 调用 `POST /api/file/read-file`
4. PyCharm 应该停在断点处

---

## 方案三：查看和调试容器内的浏览器

开发容器启动后，可以通过 RealVNC 查看浏览器画面，通过 CDP 控制浏览器。

### 1. 用 RealVNC 查看浏览器画面

> macOS 自带屏幕共享占用 5900 端口，所以容器 VNC 映射到了 **5902**。

1. 下载 **RealVNC Connect Viewer**：https://www.realvnc.com/en/connect/download/viewer/
2. 打开后选择 **Direct connection**
3. 输入 `localhost:5902`
4. 密码留空（x11vnc 配置了 `-nopw`）
5. 连上后就能看到 Chromium 桌面

如果首次打开是黑屏，用 CDP 让 Chrome 打开一个网页即可显示。

### 2. 用 CDP 控制浏览器

Chrome DevTools Protocol 通过 socat 暴露在 `localhost:9222`。

#### 查看 Chrome 信息

```bash
# Chrome 版本
curl -s http://localhost:9222/json/version

# 可调试页面列表
curl -s http://localhost:9222/json/list
```

#### 打开一个网页

```bash
curl -s -X PUT "http://localhost:9222/json/new?https://www.baidu.com"
```

#### 用浏览器打开 DevTools

从 `/json/list` 返回的 JSON 里找到 `devtoolsFrontendUrl`，复制到浏览器打开，就能像平时调试网页一样操作容器里的 Chrome。

#### 用 Python 控制浏览器

SSH 进容器：

```bash
ssh root@localhost -p 2222
cd /sandbox
```

安装 websocket 客户端：

```bash
/venv/bin/python -m pip install websocket-client
```

示例脚本：

```python
import json
import urllib.request
import websocket

# 1. 获取页面列表
res = urllib.request.urlopen("http://localhost:9222/json/list").read()
pages = json.loads(res)
ws_url = pages[0]["webSocketDebuggerUrl"]

# 2. 连接 CDP
ws = websocket.create_connection(ws_url)

# 3. 导航到目标网页
ws.send(json.dumps({
    "id": 1,
    "method": "Page.navigate",
    "params": {"url": "https://www.baidu.com"}
}))
print(ws.recv())

# 4. 截图
import base64
ws.send(json.dumps({
    "id": 2,
    "method": "Page.captureScreenshot",
    "params": {"format": "png"}
}))
result = json.loads(ws.recv())
with open("/tmp/screenshot.png", "wb") as f:
    f.write(base64.b64decode(result["result"]["data"]))

ws.close()
```

### 3. 用 Playwright / Selenium 控制浏览器

开发容器里已经装了 Chromium，可以直接用 Playwright 或 Selenium 连接本地 CDP。

#### Playwright 示例

```python
from playwright.sync_api import sync_playwright

with sync_playwright() as p:
    browser = p.chromium.connect_over_cdp("http://localhost:9222")
    page = browser.contexts[0].pages[0]
    page.goto("https://www.baidu.com")
    page.screenshot(path="/tmp/baidu.png")
    browser.close()
```

### 4. 端口说明

| 端口 | 用途 |
|------|------|
| 9222 | Chrome DevTools Protocol (CDP) |
| 5902 | VNC 原生协议（RealVNC 查看浏览器） |
| 8080 | 开发容器的 FastAPI（手动启动 uvicorn 后） |

---

## 常见问题

### 端口 8000 无法访问

服务运行在 **8080**，不是 8000。访问：

```text
http://localhost:8080/docs
```

### 断点不生效

检查 Debug 控制台里的 `Will watch for changes in these directories:` 路径，必须是 `/sandbox`。

如果不是，去 `Settings → Python Interpreter → SSH 解释器设置` 修改同步文件夹为 `<Project root>->/sandbox`。

### `/usr/bin/env: 'sudo': No such file or directory`

取消勾选 "通过 sudo 以 root 权限执行代码"。开发容器没有 `sudo`。

### SSH 连不上

检查开发容器是否用了正确的 Dockerfile：

```bash
docker ps --format "table {{.Names}}\t{{.Command}}\t{{.Ports}}"
```

应该看到 `faber_sandbox_dev` 运行的是 `/usr/sbin/sshd -D`。如果不是，检查 `.devops/docker-compose.yml` 里的 `dockerfile` 是否为 `.devops/Dockerfile`。

### 8080 端口被占用

如果本地或其他容器已经占用了 8080，把开发容器的 FastAPI 映射到其他端口，比如 8081：

```yaml
ports:
  - "2222:22"
  - "8081:8080"
```
