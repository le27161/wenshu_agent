"""
元数据库 MySQL 仓储

这一层对应文档里的 Meta Repository，负责接收业务实体并落到 Meta MySQL
Repository 自身只关心“如何写入”，而“哪些写操作要放在同一笔事务里”，由 Service 层统一决定

表 字段 指标和字段指标关系都会先以业务实体流转，再在这里统一转成 ORM 模型
问数链路运行时也会从这里读取元数据，用来把召回到的 id 补齐成完整实体
"""

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.entities.column_info import ColumnInfo
from app.entities.column_metric import ColumnMetric
from app.entities.metric_info import MetricInfo
from app.entities.query_audit import QueryAudit
from app.entities.table_info import TableInfo
from app.repositories.mysql.meta.mappers.column_info_mapper import ColumnInfoMapper
from app.repositories.mysql.meta.mappers.column_metric_mapper import ColumnMetricMapper
from app.repositories.mysql.meta.mappers.metric_info_mapper import MetricInfoMapper
from app.repositories.mysql.meta.mappers.query_audit_mapper import QueryAuditMapper
from app.repositories.mysql.meta.mappers.table_info_mapper import TableInfoMapper


class MetaMySQLRepository:
    """负责把元数据业务实体持久化到 Meta MySQL"""

    def __init__(self, session: AsyncSession):
        self.session = session

    async def ensure_enterprise_schema(self):
        """补齐企业版运行所需的元数据库结构。"""

        await self._add_column_if_missing(
            "table_info", "dataset", "VARCHAR(32) NOT NULL DEFAULT 'dw' COMMENT '数据集'"
        )
        await self._add_column_if_missing(
            "column_info", "dataset", "VARCHAR(32) NOT NULL DEFAULT 'dw' COMMENT '数据集'"
        )
        await self._add_column_if_missing(
            "metric_info", "dataset", "VARCHAR(32) NOT NULL DEFAULT 'dw' COMMENT '数据集'"
        )
        await self._add_column_if_missing(
            "column_metric", "dataset", "VARCHAR(32) NOT NULL DEFAULT 'dw' COMMENT '数据集'"
        )
        await self._ensure_primary_key("table_info", ("id", "dataset"))
        await self._ensure_primary_key("column_info", ("id", "dataset"))
        await self._ensure_primary_key("metric_info", ("id", "dataset"))
        await self._ensure_primary_key("column_metric", ("column_id", "metric_id", "dataset"))
        await self.session.execute(
            text(
                """
                CREATE TABLE IF NOT EXISTS query_audit (
                    request_id VARCHAR(64) PRIMARY KEY COMMENT '请求编号',
                    user_id VARCHAR(128) COMMENT '用户编号',
                    tenant_id VARCHAR(128) COMMENT '租户编号',
                    query TEXT COMMENT '自然语言问题',
                    dataset VARCHAR(32) NOT NULL COMMENT '数据集',
                    dsl JSON COMMENT '业务 DSL',
                    compiled_sql TEXT COMMENT '编译 SQL',
                    final_sql TEXT COMMENT '最终执行 SQL',
                    policy JSON COMMENT '权限策略',
                    ast_validation JSON COMMENT 'AST 校验结果',
                    row_count INT NOT NULL DEFAULT 0 COMMENT '返回行数',
                    duration_ms DOUBLE NULL COMMENT '耗时毫秒',
                    created_at DATETIME NOT NULL COMMENT '创建时间',
                    KEY idx_query_audit_dataset_created_at (dataset, created_at),
                    KEY idx_query_audit_user_created_at (user_id, created_at)
                )
                """
            )
        )

    async def _add_column_if_missing(
        self, table_name: str, column_name: str, column_definition: str
    ):
        result = await self.session.execute(
            text(
                """
                SELECT COUNT(*) AS n
                FROM information_schema.columns
                WHERE table_schema = DATABASE()
                  AND table_name = :table_name
                  AND column_name = :column_name
                """
            ),
            {"table_name": table_name, "column_name": column_name},
        )
        if int(result.scalar() or 0) == 0:
            await self.session.execute(
                text(f"ALTER TABLE {table_name} ADD COLUMN {column_name} {column_definition}")
            )

    async def _ensure_primary_key(self, table_name: str, columns: tuple[str, ...]):
        result = await self.session.execute(
            text(
                """
                SELECT column_name
                FROM information_schema.key_column_usage
                WHERE table_schema = DATABASE()
                  AND table_name = :table_name
                  AND constraint_name = 'PRIMARY'
                ORDER BY ordinal_position
                """
            ),
            {"table_name": table_name},
        )
        current = tuple(row[0] for row in result.fetchall())
        if current == columns:
            return
        if current:
            await self.session.execute(text(f"ALTER TABLE {table_name} DROP PRIMARY KEY"))
        quoted = ", ".join(columns)
        await self.session.execute(
            text(f"ALTER TABLE {table_name} ADD PRIMARY KEY ({quoted})")
        )

    async def clear_dataset(self, dataset: str):
        """清理某个数据集的元数据，避免重建时和其他数据集互相覆盖。"""

        await self.session.execute(
            text("delete from column_metric where dataset = :dataset"),
            {"dataset": dataset},
        )
        await self.session.execute(
            text("delete from metric_info where dataset = :dataset"),
            {"dataset": dataset},
        )
        await self.session.execute(
            text("delete from column_info where dataset = :dataset"),
            {"dataset": dataset},
        )
        await self.session.execute(
            text("delete from table_info where dataset = :dataset"),
            {"dataset": dataset},
        )

    def save_table_infos(self, table_infos: list[TableInfo]):
        """批量保存表元数据。输入仍然是业务实体，而不是 ORM 模型"""
        self.session.add_all(
            [TableInfoMapper.to_model(table_info) for table_info in table_infos]
        )

    def save_column_infos(self, column_infos: list[ColumnInfo]):
        """批量保存字段元数据。实体到模型的转换统一通过 Mapper 完成"""
        self.session.add_all(
            [ColumnInfoMapper.to_model(column_info) for column_info in column_infos]
        )

    def save_metric_infos(self, metric_infos: list[MetricInfo]):
        """批量保存指标元数据。指标本身和字段关联关系分开写入"""
        self.session.add_all(
            [MetricInfoMapper.to_model(metric_info) for metric_info in metric_infos]
        )

    def save_column_metrics(self, column_metrics: list[ColumnMetric]):
        """批量保存字段与指标的关联关系"""
        self.session.add_all(
            [
                ColumnMetricMapper.to_model(column_metric)
                for column_metric in column_metrics
            ]
        )

    async def get_column_info_by_id(
        self, id: str, dataset: str = "dw"
    ) -> ColumnInfo | None:
        """按字段 id 查询字段元数据，供召回信息合并阶段补齐字段上下文"""

        result = await self.session.execute(
            text("select * from column_info where id = :id and dataset = :dataset"),
            {"id": id, "dataset": dataset},
        )
        row = result.mappings().first()
        if row:
            return ColumnInfo(**dict(row))
        else:
            return None

    async def get_table_info_by_id(
        self, id: str, dataset: str = "dw"
    ) -> TableInfo | None:
        """按表 id 查询表元数据，最终组装成提示词里的表结构信息"""

        result = await self.session.execute(
            text("select * from table_info where id = :id and dataset = :dataset"),
            {"id": id, "dataset": dataset},
        )
        row = result.mappings().first()
        if row:
            return TableInfo(**dict(row))
        else:
            return None

    async def get_key_columns_by_table_id(
        self, table_id: str, dataset: str = "dw"
    ) -> list[ColumnInfo]:
        """查询指定表的主外键字段，避免 Join 关键字段被向量召回漏掉"""

        # 主外键字段用于后续生成 join 条件，不能完全依赖向量召回命中
        sql = "select * from column_info where table_id = :table_id and dataset = :dataset and role in ('primary_key','foreign_key')"
        # :table_id 是 SQLAlchemy text SQL 的占位符，实际值通过第二个参数传入
        result = await self.session.execute(
            text(sql), {"table_id": table_id, "dataset": dataset}
        )
        # mappings() 会把结果行转成类似字典的结构，便于解包成 ColumnInfo
        return [ColumnInfo(**dict(row)) for row in result.mappings().fetchall()]

    async def save_query_audit(self, audit: QueryAudit):
        """持久化一次查询审计记录。"""

        await self.session.merge(QueryAuditMapper.to_model(audit))
