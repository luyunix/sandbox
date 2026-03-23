#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
Faber 沙箱服务启动脚本
"""

import subprocess
import sys
import os


def check_dependencies():
    """检查依赖是否已安装"""
    try:
        import fastapi
        import uvicorn
        import pydantic
        return True
    except ImportError:
        return False


def install_dependencies():
    """安装依赖"""
    print("正在安装依赖...")
    deps = ["fastapi", "uvicorn[standard]", "pydantic", "pydantic-settings", "python-multipart"]

    # 尝试使用阿里云镜像
    mirrors = [
        "https://mirrors.aliyun.com/pypi/simple/",
        "https://pypi.tuna.tsinghua.edu.cn/simple/",
    ]

    for mirror in mirrors:
        try:
            cmd = [sys.executable, "-m", "pip", "install", "-i", mirror, "--trusted-host",
                   mirror.split("//")[1].split("/")[0]] + deps
            result = subprocess.run(cmd, capture_output=True, text=True, timeout=120)
            if result.returncode == 0:
                print(f"✓ 依赖安装成功 (使用镜像: {mirror})")
                return True
        except Exception:
            continue

    # 如果镜像都失败，尝试默认源
    try:
        cmd = [sys.executable, "-m", "pip", "install"] + deps
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=120)
        if result.returncode == 0:
            print("✓ 依赖安装成功")
            return True
    except Exception as e:
        print(f"✗ 依赖安装失败: {e}")
        return False

    return False


def main():
    """主入口"""
    # 检查并安装依赖
    if not check_dependencies():
        if not install_dependencies():
            print("错误：无法安装依赖，请检查网络连接")
            sys.exit(1)

    # 启动服务
    import uvicorn

    host = os.getenv("HOST", "0.0.0.0")
    port = int(os.getenv("PORT", "8080"))
    reload = os.getenv("RELOAD", "true").lower() == "true"

    print(f"\n🚀 启动 Faber 沙箱服务...")
    print(f"   地址: http://{host}:{port}")
    print(f"   热重载: {'开启' if reload else '关闭'}")
    print(f"   按 Ctrl+C 停止服务\n")

    uvicorn.run(
        "app.main:app",
        host=host,
        port=port,
        reload=reload,
        log_level="info"
    )


if __name__ == "__main__":
    main()
