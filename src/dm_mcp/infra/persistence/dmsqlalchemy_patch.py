"""dmSQLAlchemy 补丁模块

修复 dmSQLAlchemy 中的已知兼容性问题：
1. 2.0.17 中 AsyncAdapt_dmasync_connection 和 AsyncAdapt_dmasync_cursor
   定义类属性遮蔽 SQLAlchemy 父类 __slots__ 描述符的问题。
2. DMDialect.get_columns 和 get_identity_info 在 SQL 中硬编码对 SYS.SYSCOLUMNS /
   SYS.SYSOBJECTS 的关联查询，导致普通非 DBA 用户（如 RESOURCE 角色）反射表结构时
   报 [CODE:-5504]No select privilege on object [SYS.SYSCOLUMNS] 的问题。
3. 达梦异步方言 (dm+dmAsync) 批量插入 (executemany) 时，因 dmAsync 驱动的 executemany
   返回 None，导致 async_do_executemany_return 报 TypeError: 'NoneType' object is not subscriptable 的问题。
"""

import logging
import re

logger = logging.getLogger(__name__)

_DMSQLALCHEMY_PATCHED = False


def _fallback_get_columns(self, connection, table_name, schema=None, **kw):
    """当用户缺少 SYS.SYSCOLUMNS 权限时，使用 ALL_TAB_COLS 与 ALL_COL_COMMENTS 降级查询。"""
    from dmSQLAlchemy.types import BYTE, NUMBER, TIMESTAMP, VECTOR
    from sqlalchemy import sql, util
    from sqlalchemy import types as sqltypes

    resolve_synonyms = kw.get("dm_resolve_synonyms", False)
    dblink = kw.get("dblink", "")
    info_cache = kw.get("info_cache")

    (table_name, schema, dblink, _synonym) = self._prepare_reflection_args(
        connection, table_name, schema, resolve_synonyms, dblink, info_cache=info_cache
    )
    owner = self.denormalize_name(schema or self.default_schema_name)

    char_length_col = (
        "char_length" if getattr(self, "_supports_char_length", True) else "data_length"
    )

    fallback_sql = (
        f"SELECT a_tab_cols.column_name, a_tab_cols.data_type, "
        f"a_tab_cols.{char_length_col}, a_tab_cols.data_precision, "
        f"a_tab_cols.data_scale, a_tab_cols.nullable, a_tab_cols.data_default, "
        f"a_col_comments.comments, a_tab_cols.virtual_column "
        f"FROM ALL_TAB_COLS{dblink} a_tab_cols "
        f"LEFT JOIN ALL_COL_COMMENTS{dblink} a_col_comments "
        f"ON a_tab_cols.table_name = a_col_comments.table_name "
        f"AND a_tab_cols.column_name = a_col_comments.column_name "
        f"AND a_tab_cols.owner = a_col_comments.schema_name "
        f"WHERE a_tab_cols.table_name = :table_name "
        f"AND a_tab_cols.owner = :schema_name "
        f"AND a_tab_cols.hidden_column = 'NO' "
        f"ORDER BY a_tab_cols.column_id"
    )

    c = connection.execute(
        sql.text(fallback_sql).bindparams(table_name=table_name, schema_name=owner)
    )

    try:
        identity = self.get_identity_info(connection, owner, [table_name], dblink)
    except Exception:  # noqa: BLE001
        identity = []

    columns = []
    for row in c:
        (
            colname,
            orig_colname,
            coltype,
            length,
            precision,
            scale,
            nullable,
            default,
            comments,
            virtual_column,
        ) = (
            self.normalize_name(row[0]),
            row[0],
            row[1],
            row[2],
            row[3],
            row[4],
            row[5] == "Y",
            row[6],
            row[7],
            row[8],
        )

        if coltype in ("DEC", "NUMERIC", "DECIMAL", "NUMBER"):
            if precision is None:
                coltype = NUMBER
            else:
                coltype = NUMBER(precision, scale)
        elif coltype in (
            "VARCHAR",
            "VARCHAR2",
            "NVARCHAR2",
            "CHAR",
            "CHARACTER",
            "LONGVARCHAR",
            "LONGVARBINARY",
        ):
            ischema = self.ischema_names.get(coltype)
            coltype = ischema(length) if ischema else sqltypes.String(length)
        elif coltype in ("INTEGER", "INT"):
            coltype = sqltypes.Integer
        elif "WITH TIME ZONE" in coltype:
            coltype = TIMESTAMP(timezone=True)
        elif "VECTOR(" in coltype:
            parts = coltype.split(",", 2)
            dim = int(parts[0][7:])
            if dim == 0:
                dim = "*"
            format = parts[1][1:]
            storage_format = parts[2][1:-1]
            coltype = VECTOR(dim=dim, format=format, storage_format=storage_format)
        elif coltype == "BYTE":
            coltype = BYTE
        else:
            coltype = re.sub(r"\(\d+\)", "", coltype)
            try:
                coltype = self.ischema_names[coltype]
            except KeyError:
                util.warn(f"Did not recognize type '{coltype}' of column '{colname}'")
                coltype = sqltypes.NULLTYPE

        computed = {"sqltext": default} if virtual_column == "YES" else None

        cdict = {
            "name": colname,
            "type": coltype,
            "nullable": nullable,
            "default": default,
            "autoincrement": None,
            "comment": comments,
        }
        if computed is not None:
            cdict["computed"] = computed
        if len(identity) > 0:
            for identity_dict in identity:
                if self.normalize_name(
                    identity_dict.get("col_name", "")
                ) == colname and self.normalize_name(
                    identity_dict.get("tab_name", "")
                ) == self.normalize_name(table_name):
                    identity_dict_copy = dict(identity_dict)
                    identity_dict_copy.pop("col_name", None)
                    identity_dict_copy.pop("tab_name", None)
                    cdict["identity"] = identity_dict_copy
                    cdict["autoincrement"] = True
                    identity.remove(identity_dict)
        if orig_colname.lower() == orig_colname:
            cdict["quote"] = True

        columns.append(cdict)

    return columns


def _patch_reflection() -> None:
    """修复 DMDialect 中硬编码 SYSCOLUMNS / SYSOBJECTS 导致非 DBA 用户权限不足的问题。"""
    try:
        import dmSQLAlchemy.base as dm_base

        dialect_cls = getattr(dm_base, "DMDialect", None)
        if dialect_cls is None or getattr(
            dialect_cls, "_syscolumns_fallback_patched", False
        ):
            return

        orig_get_columns = dialect_cls.get_columns
        orig_get_identity_info = dialect_cls.get_identity_info

        def safe_get_identity_info(self, connection, owner, all_objects, dblink):
            try:
                return orig_get_identity_info(
                    self, connection, owner, all_objects, dblink
                )
            except Exception as e:
                err_upper = str(e).upper()
                if (
                    "SYSCOLUMNS" in err_upper
                    or "SYSOBJECTS" in err_upper
                    or "-5504" in err_upper
                ):
                    return []
                raise

        def safe_get_columns(self, connection, table_name, schema=None, **kw):
            try:
                return orig_get_columns(self, connection, table_name, schema=schema, **kw)
            except Exception as e:
                err_upper = str(e).upper()
                if (
                    "SYSCOLUMNS" in err_upper
                    or "SYSOBJECTS" in err_upper
                    or "-5504" in err_upper
                ):
                    logger.debug(
                        f"dmSQLAlchemy 反射表 {table_name} 缺少 SYSCOLUMNS 权限，降级使用 ALL_TAB_COLS 获取列信息"
                    )
                    return _fallback_get_columns(
                        self, connection, table_name, schema=schema, **kw
                    )
                raise

        dialect_cls.get_columns = safe_get_columns
        dialect_cls.get_identity_info = safe_get_identity_info
        dialect_cls._syscolumns_fallback_patched = True
        logger.info("已成功应用 dmSQLAlchemy 非 DBA 用户 SYSCOLUMNS 权限降级补丁")
    except Exception as e:  # noqa: BLE001
        logger.debug(f"应用 dmSQLAlchemy 反射权限降级补丁跳过或失败: {e}")


def _patch_executemany() -> None:
    """修复 dmSQLAlchemy 中批量插入 (executemany) 相关兼容性问题。

    1. 达梦异步方言 (DMDialectAsync_dmasync / DMDialect_dmAsync) 默认
       insert_executemany_returning = True，但底层 dmAsync 驱动的 executemany
       不返回结果集，导致 SQLAlchemy 尝试批量获取 RETURNING 时失败。
       将其设置为 False，使 SQLAlchemy 采用单行 INSERT ... RETURNING 模式，
       保证各行主键和默认列（如 created_at/updated_at）正常正确返回。
    2. DMDialect_Adapter.async_do_executemany_return 在 result 为 None 时
       直接执行 result[poslist[k]] 抛出 TypeError: 'NoneType' object is not subscriptable。
       在此进行安全防护，并在 result 为 None 时为 out_parameters 赋初值 [None] * rows。
    3. 同理对 DMDialect_Adapter.do_executemany_return 进行安全防护。
    """
    try:
        import inspect

        import dmSQLAlchemy.dmasync as dm_async
        import dmSQLAlchemy.extensions as dm_ext
        from sqlalchemy.util import await_only

        # 1. 禁用异步方言的 executemany_returning
        for dialect_cls_name in ("DMDialectAsync_dmasync", "DMDialect_dmAsync"):
            dialect_cls = getattr(dm_async, dialect_cls_name, None)
            if dialect_cls is not None:
                dialect_cls.insert_executemany_returning = False
                dialect_cls.insert_executemany_returning_sort_by_parameter_order = False

        # 2. 修复 DMDialect_Adapter.async_do_executemany_return 与 do_executemany_return
        adapter_cls = getattr(dm_ext, "DMDialect_Adapter", None)
        if adapter_cls is not None and not getattr(
            adapter_cls, "_executemany_patch_applied", False
        ):
            orig_async_do_executemany_return = adapter_cls.async_do_executemany_return
            orig_do_executemany_return = adapter_cls.do_executemany_return

            def safe_async_do_executemany_return(
                self, columns, rows, dialect, cursor, statement, parameters, context=None
            ):
                if (
                    context is not None
                    and context.out_parameters is not None
                    and len(context.out_parameters) > 0
                ):
                    dict_len = len(context.out_parameters)
                    poslist = []
                    for k in range(dict_len):
                        key = f"ret_{k}"
                        target_param = context.out_parameters.get(key)
                        for j in range(columns):
                            if parameters and parameters[0][j] == target_param:
                                poslist.append(j)
                                break
                    if len(poslist) == dict_len:
                        poslist, parameters = dialect.check_position(poslist, parameters)

                    res = cursor.executemany(statement, parameters)
                    result = await_only(res) if inspect.isawaitable(res) else res

                    if result is not None and isinstance(result, (list, tuple, dict)):
                        for k in range(dict_len):
                            key = f"ret_{k}"
                            if k < len(poslist) and poslist[k] < len(result):
                                val = result[poslist[k]]
                                context.out_parameters[key] = (
                                    [] if val == [None] else val
                                )
                            else:
                                context.out_parameters[key] = [None] * rows
                    else:
                        for k in range(dict_len):
                            context.out_parameters[f"ret_{k}"] = [None] * rows
                else:
                    if (
                        context is None
                        or getattr(context, "invoked_statement", None) is None
                    ):
                        res = cursor.executemany(statement, parameters)
                        if inspect.isawaitable(res):
                            await_only(res)
                        return
                    orig_async_do_executemany_return(
                        self,
                        columns,
                        rows,
                        dialect,
                        cursor,
                        statement,
                        parameters,
                        context,
                    )

            def safe_do_executemany_return(
                self, columns, rows, dialect, cursor, statement, parameters, context=None
            ):
                if (
                    context is not None
                    and context.out_parameters is not None
                    and len(context.out_parameters) > 0
                ):
                    dict_len = len(context.out_parameters)
                    poslist = []
                    for k in range(dict_len):
                        key = f"ret_{k}"
                        target_param = context.out_parameters.get(key)
                        for j in range(columns):
                            if parameters and parameters[0][j] == target_param:
                                poslist.append(j)
                                break
                    if len(poslist) == dict_len:
                        poslist, parameters = dialect.check_position(poslist, parameters)

                    result = cursor.executemany(statement, parameters)

                    if result is not None and isinstance(result, (list, tuple, dict)):
                        for k in range(dict_len):
                            key = f"ret_{k}"
                            if k < len(poslist) and poslist[k] < len(result):
                                val = result[poslist[k]]
                                context.out_parameters[key] = (
                                    [] if val == [None] else val
                                )
                            else:
                                context.out_parameters[key] = [None] * rows
                    else:
                        for k in range(dict_len):
                            context.out_parameters[f"ret_{k}"] = [None] * rows
                else:
                    if (
                        context is None
                        or getattr(context, "invoked_statement", None) is None
                    ):
                        cursor.executemany(statement, parameters)
                        return
                    orig_do_executemany_return(
                        self,
                        columns,
                        rows,
                        dialect,
                        cursor,
                        statement,
                        parameters,
                        context,
                    )

            adapter_cls.async_do_executemany_return = safe_async_do_executemany_return
            adapter_cls.do_executemany_return = safe_do_executemany_return
            adapter_cls._executemany_patch_applied = True
            logger.info("已成功应用 dmSQLAlchemy executemany returning 修复补丁")
    except Exception as e:  # noqa: BLE001
        logger.debug(f"应用 dmSQLAlchemy executemany 补丁跳过或失败: {e}")


def apply_dmsqlalchemy_patch() -> None:
    """修复 dmSQLAlchemy 2.0.17 异步适配器 slots、反射权限及 executemany 问题。

    1. slots 描述符修复：
       AsyncAdapt_dmasync_connection 和 AsyncAdapt_dmasync_cursor 上定义的
       _connection = None 和 _cursor = None 会遮蔽父类的 member_descriptor，
       导致只读异常。删除这两个多余类变量。
    2. 反射权限修复：
       当数据库用户非 DBA 且未授权 SYSCOLUMNS 时，DMDialect.get_columns
       自动降级回退到 ALL_TAB_COLS。
    3. 批量插入 (executemany) 返回值修复：
       禁用异步方言的 insert_executemany_returning 并防御 async_do_executemany_return
       在 result 为 None 时的 TypeError 异常。
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

        _patch_reflection()
        _patch_executemany()
        _DMSQLALCHEMY_PATCHED = True
    except Exception as e:  # noqa: BLE001
        logger.debug(f"应用 dmSQLAlchemy 补丁跳过或失败（非达梦异步环境）: {e}")


# 导入该模块时即执行一次补丁
apply_dmsqlalchemy_patch()
