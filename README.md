# Commerce Insight Agent

面向电商结构化数据的自然语言问数应用。后端基于 FastAPI 和 LangGraph 编排混合检索、业务 DSL 规划、SQL 编译、策略校验与查询执行，前端通过 SSE 展示执行进度和结果。

## 功能

- 字段、指标和字段取值的混合检索：Qdrant 语义召回 + Elasticsearch 全文检索 + MySQL 元数据。
- 自然语言问题转换为受控业务 DSL，再由确定性编译器生成 SQL。
- 通过语义目录约束指标、维度、关联关系和时间范围。
- SQL AST 校验，以及演示用的数据集访问、行级范围和列级策略。
- `dw` 示例数仓和 `olist` 数据集隔离，查询审计写入 `meta.query_audit`。
- React 聊天式界面，展示执行进度、DSL、SQL 和查询结果。

当前查询主链路：

```text
问题 → 关键词抽取 → 字段 / 指标 / 取值召回 → 上下文整理
     → DSL 规划与校验 → SQL 编译 → 策略应用与 AST 校验 → 执行 → 审计
```

## 目录

```text
app/
  agent/           LangGraph 节点、状态与运行时上下文
  api/             FastAPI 路由、依赖注入与请求结构
  clients/         数据库、向量库和 Embedding 客户端
  enterprise/      业务 DSL、语义目录、编译器、策略和数据导入
  repositories/    MySQL、Qdrant、Elasticsearch 数据访问
  services/        元数据构建与问数服务
conf/              服务配置、元数据配置与业务本体
docker/            本地基础服务与 MySQL 初始化脚本
frontend/          React + Vite + Tailwind CSS
prompts/           检索过滤与 SQL 相关模板
scripts/           Smoke 检查与 Olist 回归评测
tests/             编译器、业务本体与 AST 网关测试
benchmarks/        可复用评测用例；本地报告不提交
```

## 本地运行

需要 Python 3.14+、Conda（或 Miniconda）、Docker Compose、Node.js 和 pnpm。

### 1. 安装后端依赖

在仓库根目录运行：

```bash
conda env create -f environment.yml
conda activate commerce-insight-agent
```

也可以在 Python 3.14+ 环境使用 uv：

```bash
uv sync --dev
```

使用 uv 时，下文 Python 命令前加 `uv run`，例如 `uv run fastapi dev main.py`。

### 2. 配置环境变量

复制 `.env.example` 为 `.env`（PowerShell 使用 `Copy-Item .env.example .env`），设置：

```dotenv
LLM_API_KEY=your_dashscope_api_key_here
LLM_MODEL=qwen-plus
LLM_BASE_URL=https://dashscope.aliyuncs.com/compatible-mode/v1
```

默认使用阿里云 DashScope 的 OpenAI 兼容接口。其他平台可通过 `LLM_MODEL` 和 `LLM_BASE_URL` 切换。数据库、Embedding 和检索服务地址也在 `.env.example` 中列出。

### 3. 准备 Embedding 模型

下载 `BAAI/bge-large-zh-v1.5` 到 Docker 挂载目录：

```bash
hf download BAAI/bge-large-zh-v1.5 --local-dir docker/embedding/bge-large-zh-v1.5
```

模型文件与本地数据集不纳入 Git。

### 4. 启动基础服务

从仓库根目录执行，显式加载根目录的环境变量文件：

```bash
docker compose --env-file .env -f docker/docker-compose.yaml up -d --build
```

| 服务 | 默认本机端口 |
| --- | --- |
| MySQL | 13308 |
| Elasticsearch | 9200 |
| Kibana | 5601 |
| Qdrant HTTP / gRPC | 6333 / 6334 |
| Embedding | 8081 |

MySQL 在数据卷首次初始化时执行 `docker/mysql/` 中的 SQL。已有数据卷不会自动重复执行初始化脚本。

### 5. 构建元数据知识库

等待基础服务就绪后：

```bash
python -m app.scripts.build_meta_knowledge -c conf/meta_config.yaml
```

使用 Olist 时，先自行获取 [Brazilian E-Commerce Public Dataset by Olist](https://www.kaggle.com/datasets/olistbr/brazilian-ecommerce)，遵守其许可条款，将 CSV 解压到 `.env` 中 `OLIST_DATA_DIR` 指定的目录，再执行：

```bash
python -m app.enterprise.olist_import
python -m app.scripts.build_meta_knowledge -c conf/meta_olist_config.yaml
```

注意：Olist 导入会删除并重建目标数据库中的同名数据表，请仅对专用本地数据集数据库运行，并提前备份需要保留的数据。

### 6. 启动应用

后端：

```bash
fastapi dev main.py
```

前端，在另一个终端运行：

```bash
cd frontend
pnpm install --frozen-lockfile
pnpm dev
```

前端默认将 `/api` 代理到 `http://127.0.0.1:8000`。自定义代理或跨域部署配置见 [前端说明](frontend/README.md)。

## 接口

`POST http://127.0.0.1:8000/api/query`

```json
{
  "query": "统计华北地区的销售总额",
  "dataset": "dw"
}
```

数据集可选 `dw`、`olist`。接口通过 SSE 返回节点进度、DSL / SQL 信息、查询结果或错误。

## 验证

无需运行数据库的单元测试：

```bash
python -m unittest discover -s tests -v
```

依赖已启动的基础服务：

```bash
python scripts/smoke_enterprise.py --skip-api
```

后端启动后检查端到端 SSE：

```bash
python scripts/smoke_enterprise.py --dataset dw
python scripts/smoke_enterprise.py --dataset olist
```

Olist 数据导入后的回归评测：

```bash
python scripts/benchmark_olist.py --limit 100
```

该评测使用预设 DSL 检查编译和执行路径，不代表自然语言理解的端到端准确率。报告默认输出到被 Git 忽略的 `benchmarks/reports/`。

前端检查：

```bash
cd frontend
pnpm lint
pnpm build
```

## 安全与部署边界

- 当前身份与权限策略用于本地演示：接口允许传入 `principal`，且缺省身份具有管理员角色，不是可信的身份认证机制。
- Docker Compose 使用开发口令，Elasticsearch 关闭了认证，服务端口会发布到宿主机。不要直接将这套配置暴露到公网。
- 生产部署需接入服务端认证、数据库最小权限账号、网络隔离、密钥管理、限流和超时控制。
- `.env`、本地数据、模型、日志、构建产物和评测报告不应提交。公开截图和评测结果前，应自行确认不含真实业务或个人信息。

## 许可证

代码采用 [MIT License](LICENSE)，保留原有版权声明；修改与再分发同样需要遵守许可证条款。第三方依赖、Elasticsearch 插件、模型和数据集分别适用各自的许可证。
