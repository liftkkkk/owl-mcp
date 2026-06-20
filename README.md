<div align="center">

# 🦉 OWL MCP Server

**让 AI 直接读写你的 OWL / TTL / RDF 本体文件**

[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](https://opensource.org/licenses/MIT)
[![Python](https://img.shields.io/badge/Python-3.8+-blue.svg)](https://www.python.org/)
[![MCP](https://img.shields.io/badge/MCP-Compatible-green.svg)](https://modelcontextprotocol.io/)
[![owlready2](https://img.shields.io/badge/owlready2-latest-orange.svg)](https://pypi.org/project/owlready2/)

[English](#english) · [快速开始](#快速开始) · [工具列表](#工具列表) · [示例](#示例用法)

</div>

---

## 简介

**OWL MCP Server** 是一个基于 [Model Context Protocol (MCP)](https://modelcontextprotocol.io/) 的本体操作服务器。

它让 Claude、Cursor、WorkBuddy 等 AI 助手能够像操作普通文件一样，**直接查询、修改、推理**你的 OWL/TTL/RDF 本体文件 —— 无需手写 SPARQL，无需了解 OWL 语法细节，用自然语言对话即可完成复杂的知识图谱操作。

```
你："找出所有 BankStatement 类的个体，按金额降序排列"
AI：[调用 sparql_query] → 返回结构化结果
```

### 适用场景

- 🏦 **金融领域**：对账单、票据本体的增删改查
- 🏥 **医疗健康**：临床知识图谱探索与推理
- 🏗️ **工程建模**：设备、流程本体的管理与验证
- 🎓 **学术研究**：OWL 本体的快速原型开发与测试

---

## 快速开始

### 第一步：安装依赖

```bash
pip install mcp owlready2 rdflib
```

或使用 requirements.txt：

```bash
pip install -r requirements.txt
```

> ⚠️ **推理器依赖 Java**：使用 `run_reasoner`（Pellet/HermiT）需要本地安装 **JDK 8+**

### 第二步：配置 MCP 客户端

#### WorkBuddy

在 `~/.workbuddy/mcp.json` 中添加：

```json
{
  "mcpServers": {
    "owl": {
      "command": "python",
      "args": ["/绝对路径/server.py"]
    }
  }
}
```

> 💡 **推荐**：使用虚拟环境的 Python 绝对路径，避免依赖冲突：
> ```json
> "command": "C:/Users/yourname/.workbuddy/binaries/python/envs/default/Scripts/python.exe"
> ```

#### Claude Desktop

在 `claude_desktop_config.json` 中添加：

```json
{
  "mcpServers": {
    "owl-mcp": {
      "command": "python",
      "args": ["/绝对路径/server.py"]
    }
  }
}
```

#### Cursor

在 `.cursor/mcp.json` 中添加同样的配置，重启 Cursor 后即可在 Agent 模式中使用。

### 第三步：启用并开始对话

在 WorkBuddy 连接器管理页面找到 `owl`，点击 **Trust** 启用，然后直接开始自然语言对话：

```
"加载本体文件 C:/work/bip-travel-v3.3.3.ttl"
"列出所有类，只显示以 bip: 开头的"
"描述 BankStatement 类的完整结构"
"运行 HermiT 推理器，检查本体一致性"
"把新增的类保存回原文件"
```

---

## 工具列表

### 📖 查询类

| 工具 | 说明 |
|------|------|
| `load_ontology` | 加载本体文件（支持本地路径或 IRI，格式：TTL / OWL / RDF） |
| `get_ontology_info` | 获取本体概览（类数、个体数、属性数、命名空间） |
| `list_classes` | 列出所有类，支持前缀过滤与数量限制 |
| `list_individuals` | 列出所有个体，支持按类过滤 |
| `list_properties` | 列出对象属性 / 数据属性 |
| `describe_class` | 详细描述类（父类、子类、限制、已知个体） |
| `describe_individual` | 描述个体（所属类型、全部属性值） |
| `search_entity` | 按关键词模糊搜索类 / 个体 / 属性 |
| `sparql_query` | 执行 SPARQL 1.1 SELECT 查询 |

### ✏️ 修改类

| 工具 | 说明 |
|------|------|
| `add_class` | 添加新类（可指定父类、rdfs:label、rdfs:comment） |
| `add_individual` | 添加新个体（可指定初始属性值） |
| `add_object_property_assertion` | 添加对象属性三元组断言 |
| `save_ontology` | 保存本体（支持 rdfxml / turtle / ntriples） |

### 🧠 推理类

| 工具 | 说明 |
|------|------|
| `run_reasoner` | 运行推理器（Pellet 或 HermiT），检查一致性并推断隐含知识 |

> **注意**：`add_*` 系列修改操作不会自动保存，需显式调用 `save_ontology`。

---

## 示例用法

### 探索本体结构

```
你：加载 bip-travel-v3.3.3.ttl，给我一个整体介绍
AI：该本体共有 47 个类、213 个个体、18 个对象属性……

你：描述一下 BankStatement 类
AI：BankStatement 是 FinancialDocument 的子类，拥有以下数据属性：amount、currency、date……
```

### SPARQL 查询

```sparql
-- 查找所有 BankStatement 及其金额（降序）
PREFIX rdf: <http://www.w3.org/1999/02/22-rdf-syntax-ns#>
PREFIX : <http://bip.yonyou.com/travel-reconciliation#>

SELECT ?ind ?amount WHERE {
  ?ind rdf:type :BankStatement .
  ?ind :amount ?amount .
}
ORDER BY DESC(?amount)
```

### 增量修改

```
你：新增一个 ElectronicInvoice 类，父类是 FinancialDocument，标签为"电子发票"
AI：[调用 add_class] → 已添加 ElectronicInvoice 类

你：把这个改动保存为 turtle 格式
AI：[调用 save_ontology] → 已保存到 bip-travel-v3.3.3-updated.ttl
```

### 一致性检查

```
你：运行 HermiT 推理器，检查本体有没有逻辑矛盾
AI：[调用 run_reasoner] → 本体一致，推断出 3 个新的隐含类成员关系……
```

---

## 技术栈

| 组件 | 用途 |
|------|------|
| [owlready2](https://pypi.org/project/owlready2/) | OWL 本体加载、修改、推理器调用 |
| [rdflib](https://rdflib.readthedocs.io/) | SPARQL 1.1 查询引擎 |
| [mcp](https://github.com/modelcontextprotocol/python-sdk) | Model Context Protocol SDK |

---

## 注意事项

- **推理器需要 Java**：Pellet / HermiT 依赖 JDK 8+，请确保 `java` 在系统 PATH 中
- **大型本体**：超过 10 万三元组时，`list_*` 系列操作请配合 `limit` 参数使用
- **修改不自动保存**：所有 `add_*` 操作仅更新内存模型，需手动调用 `save_ontology`
- **SPARQL 语法**：使用标准 SPARQL 1.1 语法，由 rdflib 引擎执行

---

## 兼容的 MCP 客户端

- ✅ WorkBuddy
- ✅ Claude Desktop
- ✅ Cursor (Agent 模式)
- ✅ 任何支持 MCP 标准的 AI 客户端

---

## License

[MIT](LICENSE)

---

<div align="center">

如果这个项目对你有帮助，欢迎 ⭐ Star 支持！

</div>
