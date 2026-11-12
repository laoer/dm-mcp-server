"""
达梦 MCP 服务

本服务包提供功能包括:
- 为达梦数据库提供 MCP 协议实现
- 支持多种通信协议 (stdio, SSE, streamable HTTP)
- 综合数据库工具和资源访问
- 企业级安全和监控
"""

from dm_mcp.infra.persistence.dmsqlalchemy_patch import apply_dmsqlalchemy_patch

# 自动应用 dmSQLAlchemy 异步方言类属性遮蔽描述符的补丁
apply_dmsqlalchemy_patch()

__version__ = "1.0.0"
__author__ = "DM MCP Team"
__description__ = "达梦数据库 MCP 服务"

