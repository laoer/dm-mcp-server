"""测试 dmSQLAlchemy 补丁模块"""

from unittest.mock import MagicMock, patch

import dmSQLAlchemy.dmasync as dm_async

import dm_mcp.infra.persistence.dmsqlalchemy_patch as patch_module


class TestDmSQLAlchemyPatch:
    """测试 dmSQLAlchemy slots 描述符补丁"""

    def test_apply_patch_removes_shadowed_attributes(self):
        """测试补丁成功删除子类上遮蔽 slots 的 _connection 和 _cursor 类属性"""
        # 模拟 2.0.17 初始未打补丁的有 bug 状态
        dm_async.AsyncAdapt_dmasync_connection._connection = None
        dm_async.AsyncAdapt_dmasync_cursor._cursor = None

        assert "_connection" in dm_async.AsyncAdapt_dmasync_connection.__dict__
        assert "_cursor" in dm_async.AsyncAdapt_dmasync_cursor.__dict__

        # 重置补丁状态标记并重新执行
        patch_module._DMSQLALCHEMY_PATCHED = False
        patch_module.apply_dmsqlalchemy_patch()

        # 验证类属性已被成功删除
        assert "_connection" not in dm_async.AsyncAdapt_dmasync_connection.__dict__
        assert "_cursor" not in dm_async.AsyncAdapt_dmasync_cursor.__dict__

    def test_apply_patch_idempotent(self):
        """测试多次调用补丁幂等安全"""
        patch_module.apply_dmsqlalchemy_patch()
        patch_module.apply_dmsqlalchemy_patch()

    def test_connection_instantiation_without_readonly_error(self):
        """测试打补丁后实例化 AsyncAdapt_dmasync_connection 不再报 read-only 错误"""
        patch_module._DMSQLALCHEMY_PATCHED = False
        patch_module.apply_dmsqlalchemy_patch()

        mock_dbapi = MagicMock()
        mock_raw_conn = MagicMock()

        conn = dm_async.AsyncAdapt_dmasync_connection(mock_dbapi, mock_raw_conn)
        assert conn._connection is mock_raw_conn
        assert conn.dbapi is mock_dbapi

    def test_cursor_attribute_assignment_without_readonly_error(self):
        """测试打补丁后给 AsyncAdapt_dmasync_cursor 赋值 _cursor 不再报 read-only 错误"""
        patch_module._DMSQLALCHEMY_PATCHED = False
        patch_module.apply_dmsqlalchemy_patch()

        mock_cursor_raw = MagicMock()
        cur = dm_async.AsyncAdapt_dmasync_cursor.__new__(dm_async.AsyncAdapt_dmasync_cursor)
        cur._cursor = mock_cursor_raw
        assert cur._cursor is mock_cursor_raw

    def test_graceful_when_import_fails(self):
        """测试导入失败时静默跳过不抛异常"""
        patch_module._DMSQLALCHEMY_PATCHED = False
        with patch.dict("sys.modules", {"dmSQLAlchemy.dmasync": None}):
            patch_module.apply_dmsqlalchemy_patch()
