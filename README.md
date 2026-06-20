# Protégé MCP Server

一个为 Protégé / OWL 本体提供 **MCP（Model Context Protocol）** 接口的服务器。
让 AI（WorkBuddy / Claude / Cursor 等）能直接操作 `.ttl` / `.owl` / `.rdf` 本体文件。

---

## 功能（工具列表）

| 工具 | 说明 |
|------|------|
| `load_ontology` | 加载 OWL/TTL/RDF 文件（本地路径或 IRI） |
| `get_ontology_info` | 获取本体基本信息（类数、个体数、属性数） |
| `list_classes` | 列出所有类，支持前缀过滤 |
| `list_individuals` | 列出所有个体，支持按类过滤 |
| `list_properties` | 列出对象属性/数据属性 |
| `describe_class` | 详细描述一个类（父类、子类、限制、个体） |
| `describe_individual` | 描述一个个体（类型、所有属性值） |
| `sparql_query` | 执行 SPARQL SELECT 查询 |
| `add_class` | 添加新类（可指定父类、label、comment） |
| `add_individual` | 添加新个体（可指定初始属性值） |
| `add_object_property_assertion` | 添加对象属性三元组断言 |
| `run_reasoner` | 运行推理器（Pellet 或 HermiT） |
| `save_ontology` | 保存本体到文件（支持 rdfxml/turtle/ntriples） |
| `search_entity` | 按关键词模糊搜索类/个体/属性 |

---

## 快速开始

### 1. 安装依赖

```bash
pip install mcp owlready2 rdflib
```

或者使用 requirements.txt：

```bash
pip install -r requirements.txt
```

### 2. 配置 WorkBuddy MCP

在 `~/.workbuddy/mcp.json` 中添加：

```json
{
  "mcpServers": {
    "protege": {
      "command": "python",
      "args": ["D:/mywork/protege-mcp/server.py"]
    }
  }
}
```

> **提示**：将路径改为你的实际 `server.py` 路径。建议使用 Python 虚拟环境的绝对路径，例如：
> ```json
> "command": "C:/Users/yourname/.workbuddy/binaries/python/envs/default/Scripts/python.exe"
> ```

### 3. 在 WorkBuddy 中启用

打开连接器管理页面（右上角），找到 `protege` MCP，点击 **Trust** 启用。

### 4. 开始使用

在对话中直接说：

- "加载本体文件 `C:/work/bip-travel-v3.3.3.ttl`"
- "列出所有类"
- "描述 BankStatement 类的结构"
- "执行 SPARQL 查询：查找所有对账记录"
- "运行 HermiT 推理器检查一致性"

---

## 注意事项

- **推理器依赖 Java**：运行 Pellet/HermiT 需要本地安装 JDK 8+
- **SPARQL 查询**：使用 rdflib 的 SPARQL 引擎，标准 SPARQL 1.1 语法
- **修改操作**：`add_class`、`add_individual` 等修改不会自动保存，需显式调用 `save_ontology`
- **大型本体**：超过 10 万个三元组时，`list_*` 操作建议配合 `limit` 参数

---

## 技术栈

- **owlready2** — OWL 本体加载与操作
- **rdflib** — SPARQL 查询支持
- **mcp** — Model Context Protocol SDK

---

## 示例 SPARQL 查询

```sparql
# 查找所有 BankStatement 的个体及其金额
PREFIX rdf: <http://www.w3.org/1999/02/22-rdf-syntax-ns#>
PREFIX : <http://bip.yonyou.com/travel-reconciliation#>

SELECT ?ind ?amount WHERE {
  ?ind rdf:type :BankStatement .
  ?ind :amount ?amount .
}
ORDER BY DESC(?amount)
```
