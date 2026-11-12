FROM python:3.12-slim AS builder

WORKDIR /app

COPY pyproject.toml /app/pyproject.toml
COPY uv.lock /app/uv.lock
COPY README.md /app/README.md

# 构建 wheel 需要源代码；但最终镜像里不会保留 /app/src（只拷贝 wheel + resources）
COPY src /app/src
COPY resources /app/resources

RUN pip install --no-cache-dir --upgrade pip \
  && pip wheel --no-deps --wheel-dir /wheels .


FROM python:3.12-slim

WORKDIR /app

# 方便 healthcheck 与排障
RUN apt-get update \
  && LIBAIO_PKG="$(if apt-cache show libaio1 >/dev/null 2>&1; then echo libaio1; else echo libaio1t64; fi)" \
  && apt-get install -y --no-install-recommends \
    ca-certificates \
    curl \
    "${LIBAIO_PKG}" \
    openssl \
    libssl-dev \
  && rm -rf /var/lib/apt/lists/*

# 只拷贝运行期需要的内容：resources + wheel 安装产物
COPY --from=builder /app/resources /app/resources
COPY --from=builder /wheels /wheels

# pip 会按 pyproject.toml 中的版本约束解析依赖；sqlalchemy 已在
# pyproject.toml 中限制为 <2.1，避免装到移除了 await_fallback 的 2.1.x。
RUN pip install --no-cache-dir /wheels/*.whl \
  && rm -rf /wheels

# 构建期自检：greenlet 缺失、SQLAlchemy 2.1（无 await_fallback）、
# dmSQLAlchemy 异步方言加载及 slots 描述符补丁有效性，让 build 直接失败，而不是等到容器启动。
RUN python -c "import greenlet; import sqlalchemy.ext.asyncio; from sqlalchemy.util import await_fallback; from sqlalchemy.dialects import registry; registry.load('dm.dmAsync'); import dm_mcp; import dmSQLAlchemy.dmasync as m; assert '_connection' not in m.AsyncAdapt_dmasync_connection.__dict__, 'AsyncAdapt_dmasync_connection._connection must not shadow slot descriptor'; assert '_cursor' not in m.AsyncAdapt_dmasync_cursor.__dict__, 'AsyncAdapt_dmasync_cursor._cursor must not shadow slot descriptor'; import sqlalchemy; print('dependency check ok, sqlalchemy=' + sqlalchemy.__version__)"

# 运行时不需要 pip，卸载它以减少镜像体积（失败不影响构建）
RUN python -m pip uninstall -y pip || true

RUN rm -rf /usr/local/lib/python3.12/site-packages/pip* \
  && rm -rf /usr/local/bin/pip*

# 默认启用 HTTP 传输（使容器对外提供 Web/API）
ENV SERVER__TRANSPORT=http \
  SERVER__HOST=0.0.0.0 \
  SERVER__PORT=18081 \
  SERVER__BASE_URL=/dm-mcp

# 由 pyproject.toml 的 entrypoint 脚本启动
CMD ["dm-mcp-server"]
