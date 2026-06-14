# 1.使用ubuntu基础镜像来源
FROM ubuntu:22.04

# 2.安装过程避免交互式提示并设置主机名、uv环境目录、uv连接模式
ENV DEBIAN_FRONTEND=noninteractive
ENV HOSTNAME=sandbox
ENV UV_PROJECT_ENVIRONMENT="/venv"
ENV UV_LINK_MODE=copy

# 3.安装基础工具+OpenSSH Server+浏览器/桌面环境
# mkdir /var/run/sshd是SSH服务启动必须的目录
RUN apt-get update && apt-get install -y \
    ca-certificates git curl wget gnupg software-properties-common openssh-server \
    supervisor xterm socat xvfb x11vnc websockify \
    fonts-noto-cjk fonts-noto-color-emoji language-pack-zh-hans locales \
    && locale-gen zh_CN.UTF-8 \
    && rm -rf /var/lib/apt/lists/* \
    && mkdir /var/run/sshd

# 4.安装Google Chrome浏览器
RUN add-apt-repository ppa:xtradeb/apps -y && \
    apt-get update && \
    apt-get install -y chromium --no-install-recommends && \
    apt-get clean && \
    rm -rf /var/lib/apt/lists/*

# 5.配置SSH的账户密码(开发环境设置root账号密码为root)
RUN echo 'root:root' | chpasswd && \
    sed -i 's/#PermitRootLogin prohibit-password/PermitRootLogin yes/' /etc/ssh/sshd_config

# 6.设置默认编码
ENV LANG=zh_CN.UTF-8 \
    LANGUAGE=zh_CN:zh \
    LC_ALL=zh_CN.UTF-8

# 7.安装uv
COPY --from=ghcr.io/astral-sh/uv:latest /uv /uvx /bin/

# 8.设置下工作环境
WORKDIR /sandbox

# 9.同步uv环境
COPY pyproject.toml uv.lock ./
RUN uv sync --frozen

# 10.拷贝开发环境supervisor配置
COPY .devops/supervisord.dev.conf /etc/supervisor/conf.d/sandbox.conf

# 11.开放端口: SSH(22) FastAPI(8080) VNC(5900) WebSocket VNC(5901) CDP(9222)
EXPOSE 22 8080 5900 5901 9222

# 12.使用supervisor管理所有进程(sshd/xvfb/chrome/socat/x11vnc/websockify)
CMD ["supervisord", "-n", "-c", "/etc/supervisor/conf.d/sandbox.conf"]
