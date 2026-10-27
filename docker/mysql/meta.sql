SET NAMES utf8mb4;
CREATE DATABASE meta DEFAULT CHARACTER SET utf8mb4 COLLATE utf8mb4_general_ci;
GRANT ALL PRIVILEGES ON meta.* TO 'appuser'@'%';
CREATE DATABASE IF NOT EXISTS olist DEFAULT CHARACTER SET utf8mb4 COLLATE utf8mb4_general_ci;
GRANT ALL PRIVILEGES ON olist.* TO 'appuser'@'%';

USE meta;

DROP TABLE IF EXISTS table_info;
CREATE TABLE table_info
(
    id          VARCHAR(64) COMMENT '表编号',
    name        VARCHAR(128) COMMENT '表名称',
    role        VARCHAR(32) COMMENT '表类型(fact/dim)',
    description TEXT COMMENT '表描述',
    dataset     VARCHAR(32) NOT NULL DEFAULT 'dw' COMMENT '数据集',
    PRIMARY KEY (id, dataset),
    KEY idx_table_info_dataset (dataset)
);



DROP TABLE IF EXISTS column_info;
CREATE TABLE column_info
(
    id          VARCHAR(64) COMMENT '列编号',
    name        VARCHAR(128) COMMENT '列名称',
    type        VARCHAR(64) COMMENT '数据类型',
    role        VARCHAR(32) COMMENT '列类型(primary_key,foreign_key,measure,dimension)',
    examples    JSON COMMENT '数据示例',
    description TEXT COMMENT '列描述',
    alias       JSON COMMENT '列别名',
    table_id    VARCHAR(64) COMMENT '所属表编号',
    dataset     VARCHAR(32) NOT NULL DEFAULT 'dw' COMMENT '数据集',
    PRIMARY KEY (id, dataset),
    KEY idx_column_info_dataset_table (dataset, table_id)
);

DROP TABLE IF EXISTS metric_info;
CREATE TABLE metric_info
(
    id               VARCHAR(64) COMMENT '指标编码',
    name             VARCHAR(128) COMMENT '指标名称',
    description      TEXT COMMENT '指标描述',
    relevant_columns JSON COMMENT '关联的列',
    alias            JSON COMMENT '指标别名',
    dataset          VARCHAR(32) NOT NULL DEFAULT 'dw' COMMENT '数据集',
    PRIMARY KEY (id, dataset),
    KEY idx_metric_info_dataset (dataset)
);


DROP TABLE IF EXISTS column_metric;
CREATE TABLE column_metric
(
    column_id VARCHAR(64) COMMENT '列编号',
    metric_id VARCHAR(64) COMMENT '指标编号',
    dataset   VARCHAR(32) NOT NULL DEFAULT 'dw' COMMENT '数据集',
    PRIMARY KEY (column_id, metric_id, dataset)
);

DROP TABLE IF EXISTS query_audit;
CREATE TABLE query_audit
(
    request_id     VARCHAR(64) PRIMARY KEY COMMENT '请求编号',
    user_id        VARCHAR(128) COMMENT '用户编号',
    tenant_id      VARCHAR(128) COMMENT '租户编号',
    query          TEXT COMMENT '自然语言问题',
    dataset        VARCHAR(32) NOT NULL COMMENT '数据集',
    dsl            JSON COMMENT '业务 DSL',
    compiled_sql   TEXT COMMENT '编译 SQL',
    final_sql      TEXT COMMENT '最终执行 SQL',
    policy         JSON COMMENT '权限策略',
    ast_validation JSON COMMENT 'AST 校验结果',
    row_count      INT NOT NULL DEFAULT 0 COMMENT '返回行数',
    duration_ms    DOUBLE NULL COMMENT '耗时毫秒',
    created_at     DATETIME NOT NULL COMMENT '创建时间',
    KEY idx_query_audit_dataset_created_at (dataset, created_at),
    KEY idx_query_audit_user_created_at (user_id, created_at)
);
