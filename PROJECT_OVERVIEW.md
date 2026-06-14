# Faber 沙箱服务 - 项目深入理解

> 本文档由 Claude 于 2026/06/14 整理，基于项目当前代码生成。

---

## 一、项目定位

这是一个**基于 Docker 的隔离执行环境**，核心能力有四类：

| 能力 | 说明 |
|------|------|
| **文件操作** | 读写、替换、搜索、上传下载、删除沙箱内文件 |
| **Shell 执行** | 创建持久会话、执行命令、读输出、写输入、等待/终止进程 |
| **浏览器/桌面** | 内置 Chromium + Xvfb + VNC，可通过 CDP/Websocket VNC 远程控制 |
| **生命周期管理** | 通过 Supervisor 管理所有进程，支持超时自毁 |

对外暴露的是一套 **FastAPI REST API**，供上层服务（比如 `faber` 主系统）调用。

---

## 二、技术栈

| 层级 | 技术 |
|------|------|
| 基础镜像 | Ubuntu 22.04 |
| 后端框架 | Python 3.10 + FastAPI + Pydantic v2 |
| 进程管理 | Supervisor |
| 浏览器 | Chromium + Chrome DevTools Protocol (CDP) |
| 虚拟显示 | Xvfb (`:1`, 1280x1080x24) |
| 远程桌面 | x11vnc (5900) → websockify (5901) |
| CDP 代理 | socat (`0.0.0.0:9222` → `127.0.0.1:8222`) |
| 包管理 | uv (`pyproject.toml` + `uv.lock`) |

---

## 三、目录结构与代码分层

```
faber-sandbox/
├── app/                          # 主应用
│   ├── main.py                   # FastAPI 入口、生命周期、中间件、路由注册
│   ├── core/                     # 配置 + 中间件
│   │   ├── config.py             # Pydantic Settings（日志级别、超时时间）
│   │   └── middleware.py         # 自动延长超时销毁中间件
│   ├── interfaces/               # 接口层（HTTP）
│   │   ├── endpoints/            # 路由
│   │   │   ├── routes.py         # 路由聚合
│   │   │   ├── file.py           # 文件 API
│   │   │   ├── shell.py          # Shell API
│   │   │   └── supervisor.py     # Supervisor/生命周期 API
│   │   ├── schemas/              # 请求/响应 DTO
│   │   ├── errors/               # 异常 + 全局异常处理
│   │   └── service_dependencies.py # FastAPI Depends 依赖
│   ├── models/                   # 领域模型（Pydantic）
│   └── services/                 # 业务逻辑
├── Dockerfile                    # 生产镜像
├── docker-compose.yml            # 生产部署
├── supervisord.conf              # 容器内进程编排
├── run.py                        # 本地启动脚本（自动装依赖）
└── .devops/                      # 开发容器
    ├── Dockerfile                # 带 SSH 的开发镜像
    └── docker-compose.yml        # 本地开发编排
```

分层很清晰：**Endpoint → Schema/Model → Service**，依赖通过 `Depends` 注入，用了 `@lru_cache` 做单例。

---

## 四、核心模块详解

### 4.1 FastAPI 入口 `app/main.py`

- 初始化日志（`StreamHandler → stdout`）
- 注册 CORS（`*`，开发友好但生产需注意）
- 挂载两个中间件：
  - `auto_extend_timeout_middleware`：每次 API 调用自动续命 3 分钟
  - `CORSMiddleware`
- 注册全局异常处理器
- 路由统一前缀 `/api`

### 4.2 文件服务 `app/services/file.py`

实现了沙箱内的文件 CRUD：

| 方法 | 关键实现 |
|------|----------|
| `read_file` | 普通文件用 `asyncio.to_thread` 线程读；`sudo=True` 时用 `sudo cat` |
| `write_file` | 支持追加/前后换行；`sudo` 时先写临时文件再 `sudo bash -c "cat ..."` |
| `replace_in_file` | 先 `read_file` → `count` → `replace` → `write_file` |
| `search_in_file` | 按行 `re.match`（注意：是 **match 不是 search**，只匹配行首） |
| `find_files` | `glob.glob` 递归匹配 |
| `upload_file` | 8KB 分块写入 |
| `delete_file` | `os.remove`，**没有 sudo 分支** |

> ⚠️ 注意：`search_in_file` 用的是 `pattern.match(line)`，如果用户想搜行中任意位置，应该用 `search`。

### 4.3 Shell 服务 `app/services/shell.py`

这是项目里最复杂的模块，设计了一个**带状态的 Shell 会话系统**：

- `active_shells: Dict[str, Shell]` 内存中维护所有会话
- 每个会话是一个 `asyncio.subprocess.Process`
- 启动后台协程 `_start_output_reader` 持续读 stdout
- 使用 `codecs.getincrementaldecoder("utf-8")` 处理字符截断
- ANSI 转义码清洗（`_remove_ansi_escape_codes`）
- 支持交互式输入（`write_shell_input`）

执行流程：

```text
exec_command
  ├─ 无 session_id → 创建 UUID
  ├─ 无 exec_dir  → 用 ~
  ├─ 新会话/复用会话
  │     ├─ 旧进程还在跑 → terminate → kill
  │     └─ 创建新进程 + 启动输出读取协程
  ├─ wait_process(5s)
  │     ├─ 5s 内结束 → 返回 completed + output
  │     └─ 超时 → 返回 running（命令在后台继续）
```

> ⚠️ 注意：所有会话存在**内存**里，服务重启后全部丢失；且没有清理僵尸会话的机制。

### 4.4 Supervisor 服务 `app/services/supervisor.py`

通过 Unix Domain Socket `/tmp/supervisor.sock` 用 XML-RPC 与 Supervisor 通信。

特色实现：

- `UnixStreamHTTPConnection` + `UnixStreamTransport`：把标准 `xmlrpc.client` 桥接到 Unix Socket。
- 超时自毁：
  - `activate_timeout(minutes)`：设置 N 分钟后 `shutdown()`
  - `extend_timeout(minutes)`：延长
  - `cancel_timeout()`：取消
- 自动保活：`expand_enabled`，每次非管理类 API 调用自动延长 3 分钟。

> ⚠️ 一个潜在问题：`cancel_timeout` 返回 `SupervisorTimeout(status="no_timeout_active", activate=False)`，字段名 `activate` 写错了（模型里是 `active`），Pydantic 会忽略未定义字段，导致前端拿到 `active=False` 但 `status` 不对。

### 4.5 全局异常处理 `app/interfaces/errors/exception_handler.py`

统一响应结构：

```json
{
  "code": 200,
  "msg": "success",
  "data": {}
}
```

- `AppException` → 自定义业务异常
- `HTTPException` → FastAPI 原生
- 其他 `Exception` → 500

---

## 五、运行时进程模型

容器启动后 Supervisor 管理 6 个进程（按 priority 升序启动）：

| priority | 进程 | 端口 | 作用 |
|----------|------|------|------|
| 10 | xvfb | - | 虚拟显示器 `:1` |
| 20 | chrome | 8222 | Chromium 远程调试 |
| 30 | socat | 9222 | 把 CDP 从 8222 代理到外部 |
| 40 | x11vnc | 5900 | VNC 服务 |
| 50 | websockify | 5901 | VNC → WebSocket |
| 60 | app | 8080 | FastAPI |

生产 `docker-compose.yml` 把这些端口映射出来；开发容器 `.devops` 额外开了 SSH（2222→22）并挂载当前目录。

---

## 六、数据流示例

### 6.1 执行一条 Shell 命令

```text
POST /api/shell/exec-command
  │
  ▼
shell.py 路由校验参数
  │
  ▼
ShellService.exec_command()
  ├─ 创建/复用 session
  ├─ 启动 asyncio subprocess (/bin/bash)
  ├─ 启动 _start_output_reader 协程
  ├─ 等待 5 秒
  │   ├─ 结束 → read_shell_output → 返回 completed
  │   └─ 超时 → 返回 running
  ▼
返回 Response[ShellExecuteResult]
```

### 6.2 读取文件

```text
POST /api/file/read-file
  │
  ▼
FileService.read_file()
  ├─ sudo=False → asyncio.to_thread(open(...))
  ├─ sudo=True  → sudo cat + subprocess
  ├─ 按 start_line/end_line 切片
  ├─ 按 max_length 截断
  ▼
返回 Response[FileReadResult]
```

---

## 七、API 接口清单

### 文件模块 `/api/file`

| 方法 | 路径 | 说明 |
|------|------|------|
| POST | `/read-file` | 读取文件 |
| POST | `/write-file` | 写入文件 |
| POST | `/replace-in-file` | 替换文件内容 |
| POST | `/search-in-file` | 正则搜索文件内容 |
| POST | `/find-files` | glob 查找文件 |
| POST | `/upload-file` | 上传文件 |
| GET | `/download-file` | 下载文件 |
| POST | `/check-file-exists` | 检查文件是否存在 |
| POST | `/delete-file` | 删除文件 |

### Shell 模块 `/api/shell`

| 方法 | 路径 | 说明 |
|------|------|------|
| POST | `/exec-command` | 执行命令 |
| POST | `/read-shell-output` | 读取 Shell 输出 |
| POST | `/wait-process` | 等待进程结束 |
| POST | `/write-shell-input` | 向进程写入输入 |
| POST | `/kill-process` | 终止进程 |

### Supervisor 模块 `/api/supervisor`

| 方法 | 路径 | 说明 |
|------|------|------|
| GET | `/status` | 获取进程状态 |
| POST | `/stop-all-processes` | 停止所有进程 |
| POST | `/shutdown` | 关闭 Supervisor |
| POST | `/restart` | 重启所有进程 |
| POST | `/activate-timeout` | 激活超时销毁 |
| POST | `/extend-timeout` | 延长超时时间 |
| POST | `/cancel-timeout` | 取消超时销毁 |
| GET | `/timeout-status` | 获取超时状态 |

---

## 八、值得注意的设计与风险

### 8.1 设计亮点

1. **分层清晰**：接口/模型/服务分离，依赖注入。
2. **异步 IO**：文件/Shell/Supervisor 调用都用 `asyncio.to_thread` 避免阻塞事件循环。
3. **超时自毁**：沙箱生命周期可控，适合按需创建销毁。
4. **自动保活**：业务 API 调用自动续命，避免长任务被误杀。
5. **CDP/VNC 双通道**：既能程序控制浏览器，也能人工远程桌面介入。

### 8.2 需要关注的地方

| 问题 | 说明 |
|------|------|
| **无鉴权** | 所有 API 完全开放，任何人拿到端口就能执行任意命令、读写任意文件 |
| **sudo 默认可用** | Dockerfile 里 `ubuntu ALL=(ALL) NOPASSWD:ALL`，且 Shell 在 root 下运行 |
| **Chrome 沙箱被关闭** | `--no-sandbox`、`--disable-setuid-sandbox` 等参数关闭了浏览器沙箱 |
| **Shell 会话内存存储** | 重启丢失，且无过期清理，长运行会累积 |
| **文件删除无 sudo 分支** | `delete_file` 只能删有权限的文件 |
| **`search_in_file` 用 `match`** | 只匹配行首，可能不符合用户直觉 |
| **`cancel_timeout` 字段名错误** | `activate=False` 应为 `active=False` |
| **CORS `allow_origins=["*"]`** | 生产环境应收紧 |
| **`write_file` sudo 路径未校验** | 可写任意系统文件 |
| **Supervisor XML-RPC 无认证** | 任何人能连 sock 都能管理进程 |

### 8.3 安全建议

- 给 API 加 Token/API Key 认证。
- 容器以非 root 用户运行（虽然很多浏览器自动化需要特权）。
- 限制 Shell/文件 API 的访问路径（白名单或 chroot）。
- 给 Chrome 去掉 `--disable-web-security` 等危险参数，除非确实需要。
- 对 CDP/VNC 端口做访问控制，不要直接暴露在公网。

---

## 九、本地启动方式

```bash
# 方式1：直接运行（自动安装依赖）
python run.py

# 方式2：用 uv
uv sync
uvicorn app.main:app --host 0.0.0.0 --port 8080 --reload

# 方式3：开发容器
cd .devops
docker compose up -d
ssh root@localhost -p 2222   # 密码 root
```

---

## 十、一句话总结

> **Faber Sandbox 是一个“带浏览器 + 远程桌面 + Shell”的可编程 Ubuntu 容器，通过 FastAPI 把底层 Linux/Chrome/VNC 能力封装成 REST 接口，供上层 Agent/调度系统远程操控。**
