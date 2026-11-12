"""测试 dmSQLAlchemy 补丁模块"""

from unittest.mock import MagicMock, patch

import dmSQLAlchemy.dmasync as dm_async
from sqlalchemy.dialects import registry

import dm_mcp.infra.persistence.dmsqlalchemy_patch as patch_module


class TestDmSQLAlchemyPatch:
    """测试 dmSQLAlchemy slots 描述符与非 DBA 权限反射补丁"""

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

    def test_get_columns_fallback_on_syscolumns_privilege_error(self):
        """测试当遇到 SYSCOLUMNS 权限错误 (-5504) 时，get_columns 降级使用 ALL_TAB_COLS"""
        patch_module._DMSQLALCHEMY_PATCHED = False
        patch_module.apply_dmsqlalchemy_patch()

        dialect_cls = registry.load("dm.dmAsync")
        dialect = dialect_cls()
        dialect.default_schema_name = "XINHUA_APP"

        mock_conn = MagicMock()

        def mock_exec(stmt, *args, **kwargs):
            s = str(stmt)
            if "SYSCOLUMNS syscol" in s:
                raise RuntimeError("(dmPython.DatabaseError) [CODE:-5504]No select privilege on object [SYS.SYSCOLUMNS]")
            # 模拟 fallback 查询结果
            res = MagicMock()
            res.__iter__.return_value = [
                ("id", "id", "BIGINT", 8, 19, 0, "N", None, "主键ID", "NO"),
                ("username", "username", "VARCHAR", 64, None, None, "N", None, "用户名", "NO"),
            ]
            return res

        mock_conn.execute.side_effect = mock_exec

        cols = dialect.get_columns(mock_conn, "ADMIN_USERS")
        assert len(cols) == 2
        col_names = [c["name"] for c in cols]
        assert "id" in col_names
        assert "username" in col_names

    def test_get_identity_info_fallback_on_syscolumns_privilege_error(self):
        """测试当遇到 SYSCOLUMNS 权限错误时，get_identity_info 安全返回空列表"""
        patch_module._DMSQLALCHEMY_PATCHED = False
        patch_module.apply_dmsqlalchemy_patch()

        dialect_cls = registry.load("dm.dmAsync")
        dialect = dialect_cls()

        mock_conn = MagicMock()
        mock_conn.execute.side_effect = RuntimeError("[CODE:-5504]No select privilege on object [SYS.SYSCOLUMNS]")

        result = dialect.get_identity_info(mock_conn, "XINHUA_APP", ["ADMIN_USERS"], "")
        assert result == []

    def test_graceful_when_import_fails(self):
        """测试导入失败时静默跳过不抛异常"""
        patch_module._DMSQLALCHEMY_PATCHED = False
        with patch.dict("sys.modules", {"dmSQLAlchemy.dmasync": None, "dmSQLAlchemy.base": None}):
            patch_module.apply_dmsqlalchemy_patch()

    def test_executemany_returning_disabled_on_async_dialect(self):
        """测试补丁禁用异步方言的 insert_executemany_returning"""
        patch_module._DMSQLALCHEMY_PATCHED = False
        patch_module.apply_dmsqlalchemy_patch()

        assert dm_async.DMDialectAsync_dmasync.insert_executemany_returning is False
        assert dm_async.DMDialectAsync_dmasync.insert_executemany_returning_sort_by_parameter_order is False
        assert dm_async.DMDialect_dmAsync.insert_executemany_returning is False

    def test_safe_executemany_return_on_none_result(self):
        """测试当 executemany 返回 None 时，安全给 out_parameters 赋初值而不抛 TypeError"""
        import dmSQLAlchemy.extensions as dm_ext

        patch_module._DMSQLALCHEMY_PATCHED = False
        patch_module.apply_dmsqlalchemy_patch()

        adapter = dm_ext.DMDialect_Adapter()

        class DummyContext:
            def __init__(self):
                self.out_parameters = {"ret_0": None, "ret_1": None}
                self.invoked_statement = None

        class DummyDialect:
            def check_position(self, poslist, params):
                return poslist, params

        class DummyCursor:
            def executemany(self, stmt, params):
                return None

        # 测试异步版本
        ctx_async = DummyContext()
        adapter.async_do_executemany_return(
            2, 3, DummyDialect(), DummyCursor(), "INSERT INTO t VALUES (?, ?) RETURNING a, b INTO ?, ?", [[None, None]], ctx_async
        )
        assert ctx_async.out_parameters["ret_0"] == [None, None, None]
        assert ctx_async.out_parameters["ret_1"] == [None, None, None]

        # 测试同步版本
        ctx_sync = DummyContext()
        adapter.do_executemany_return(
            2, 3, DummyDialect(), DummyCursor(), "INSERT INTO t VALUES (?, ?) RETURNING a, b INTO ?, ?", [[None, None]], ctx_sync
        )
        assert ctx_sync.out_parameters["ret_0"] == [None, None, None]
        assert ctx_sync.out_parameters["ret_1"] == [None, None, None]

