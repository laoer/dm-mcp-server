"""dmSQLAlchemy 补丁模块

修复 dmSQLAlchemy 2.0.17 中 AsyncAdapt_dmasync_connection 和 AsyncAdapt_dmasync_cursor
定义类属性遮蔽 SQLAlchemy 父类 __slots__ 描述符的问题。
"""

import logging

logger = logging.getLogger(__name__)

_DMSQLALCHEMY_PATCHED = False


def apply_dmsqlalchemy_patch() -> None:
    """修复 dmSQLAlchemy 2.0.17 异步适配器中 slots 属性被类变量覆盖的问题。

    在 dmSQLAlchemy 2.0.17 中，AsyncAdapt_dmasync_connection 和 AsyncAdapt_dmasync_cursor
    定义了类属性：
        _connection = None
        _cursor = None
    而它们的父类（SQLAlchemy 的 AdaptedConnection 和 AsyncAdapt_dbapi_cursor）
    通过 __slots__ 定义了 _connection 和 _cursor 成员描述符（member_descriptor），
    且子类声明了 __slots__ = ()。

    根据 Python 描述符机制，子类字典中的常规类属性（None）会遮蔽父类的
    member_descriptor，导致实例无法通过 slots 赋值，抛出：
        AttributeError: 'AsyncAdapt_dmasync_connection' object attribute '_connection' is read-only
        AttributeError: 'AsyncAdapt_dmasync_cursor' object attribute '_cursor' is read-only

    此补丁在模块加载 / 引擎初始化前检测并删除这两个多余的类属性，恢复父类 slots 的正常行为。
    本操作为幂等操作，无论是否已打补丁或版本是否受影响均可安全调用。
    """
    global _DMSQLALCHEMY_PATCHED
    if _DMSQLALCHEMY_PATCHED:
        return

    try:
        import dmSQLAlchemy.dmasync as dm_async

        patched = False
        conn_cls = getattr(dm_async, "AsyncAdapt_dmasync_connection", None)
        if conn_cls is not None and "_connection" in conn_cls.__dict__:
            delattr(conn_cls, "_connection")
            patched = True

        cursor_cls = getattr(dm_async, "AsyncAdapt_dmasync_cursor", None)
        if cursor_cls is not None and "_cursor" in cursor_cls.__dict__:
            delattr(cursor_cls, "_cursor")
            patched = True

        if patched:
            logger.info("已成功应用 dmSQLAlchemy 异步适配器 slots 修复补丁")
        _DMSQLALCHEMY_PATCHED = True
    except Exception as e:  # noqa: BLE001
        logger.debug(f"应用 dmSQLAlchemy 补丁跳过或失败（非达梦异步环境）: {e}")


# 导入该模块时即执行一次补丁
apply_dmsqlalchemy_patch()
