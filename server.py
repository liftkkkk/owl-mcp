#!/usr/bin/env python3
"""
OWL MCP Server
==============
为 OWL/TTL/RDF 本体提供 MCP（Model Context Protocol）接口。
支持加载本体、SPARQL 查询、添加类/属性/个体、运行推理器等操作。

依赖: pip install mcp owlready2 rdflib
运行: python server.py
"""

import io
import json
import sys
import tempfile
import traceback
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any, Optional

try:
    from mcp.server import Server
    from mcp.server.stdio import stdio_server
    from mcp.types import CallToolResult, TextContent, Tool
except ImportError:
    print("❌ 缺少 mcp 包，请运行: pip install mcp", file=sys.stderr)
    sys.exit(1)

try:
    import owlready2
    from owlready2 import (
        World,
        default_world,
        get_ontology,
        sync_reasoner_hermit,
        sync_reasoner_pellet,
    )
except ImportError:
    print("❌ 缺少 owlready2 包，请运行: pip install owlready2", file=sys.stderr)
    sys.exit(1)

try:
    import rdflib
    from rdflib import URIRef
    from rdflib.namespace import OWL, RDF, RDFS, XSD
except ImportError:
    print("❌ 缺少 rdflib 包，请运行: pip install rdflib", file=sys.stderr)
    sys.exit(1)


# ─────────────────────────────────────────────────────────────
# 全局状态
# ─────────────────────────────────────────────────────────────
_world: Optional[World] = None
_onto: Optional[Any] = None
_graph: Optional[rdflib.Graph] = None   # rdflib 图，用于 SPARQL 及兜底查询
_loaded_path: Optional[str] = None

app = Server("owl-mcp")


# ─────────────────────────────────────────────────────────────
# 辅助函数
# ─────────────────────────────────────────────────────────────
def _check_loaded() -> None:
    """确认本体已加载，否则抛出友好错误。"""
    if _onto is None:
        raise RuntimeError("尚未加载本体，请先调用 load_ontology 工具。")


def _local_name(iri: str) -> str:
    """从 IRI 提取 local name。"""
    return iri.split("#")[-1] if "#" in iri else iri.split("/")[-1]


def _find_class(name: str) -> Optional[Any]:
    """在已加载本体中按 local name 查找类。"""
    _check_loaded()
    # 优先精确匹配 IRI 后缀
    cls = _onto.search_one(iri=f"*#{name}") or _onto.search_one(iri=f"*/{name}")
    if cls is None:
        for c in _onto.classes():
            if c.name == name:
                return c
    return cls


def _find_individual(name: str) -> Optional[Any]:
    """在已加载本体中按 local name 查找个体。"""
    _check_loaded()
    for ind in _onto.individuals():
        if ind.name == name:
            return ind
    return None


def _find_property(name: str) -> Optional[Any]:
    """在已加载本体中按 local name 查找属性（对象属性或数据属性）。"""
    _check_loaded()
    for prop in list(_onto.object_properties()) + list(_onto.data_properties()):
        if prop.name == name:
            return prop
    return None


def _safe_str(val: Any) -> str:
    try:
        return str(val)
    except Exception:
        return repr(val)


def _xsd_range_label(r: Any) -> str:
    """将 owlready2 数据属性的 range 转为可读 XSD 类型名。"""
    s = str(r)
    # owlready2 返回 Python 类型，如 <class 'str'>、<class 'float'>
    mapping = {
        "<class 'str'>": "xsd:string",
        "<class 'int'>": "xsd:integer",
        "<class 'float'>": "xsd:decimal",
        "<class 'bool'>": "xsd:boolean",
        "<class 'datetime.datetime'>": "xsd:dateTime",
        "<class 'datetime.date'>": "xsd:date",
    }
    return mapping.get(s, s)


def _sync_graph_from_onto() -> None:
    """
    将 owlready2 内存模型同步回 _graph（rdflib）。
    用于 add_class / add_individual 之后，确保后续 SPARQL 查询和 turtle 保存都能
    看到最新改动。
    """
    global _graph
    with tempfile.NamedTemporaryFile(mode="wb", suffix=".owl", delete=False) as tmp:
        tmp_path = tmp.name
    try:
        _onto.save(file=tmp_path, format="rdfxml")
        _graph = rdflib.Graph()
        _graph.parse(tmp_path, format="xml")
    finally:
        Path(tmp_path).unlink(missing_ok=True)


def _load_ontology_from_bytes(world: World, iri: str, data: bytes) -> Any:
    """
    用给定的 RDF/XML bytes 加载本体到 world，同时拦截网络请求以避免尝试
    下载远程 owl:imports。
    """
    orig = urllib.request.urlopen

    def _block_network(url_or_req, *args, **kwargs):
        raise urllib.error.URLError("Network access disabled during ontology load")

    urllib.request.urlopen = _block_network
    try:
        onto = world.get_ontology(iri).load(
            fileobj=io.BytesIO(data), reload=True
        )
    finally:
        urllib.request.urlopen = orig
    return onto


def _prepare_graph_for_owlready2(g: rdflib.Graph) -> bytes:
    """
    预处理 rdflib 图：
    1. 补全 owl:NamedIndividual 声明（让 owlready2 正确识别个体）
    2. 移除 owl:imports（避免加载时尝试下载远程本体）
    3. 序列化为 RDF/XML bytes
    """
    for s, p, o in list(g.triples((None, RDF.type, None))):
        if isinstance(o, URIRef) and (o, RDF.type, OWL.Class) in g:
            g.add((s, RDF.type, OWL.NamedIndividual))
    g.remove((None, OWL.imports, None))
    return g.serialize(format="xml").encode("utf-8")


# ─────────────────────────────────────────────────────────────
# 工具列表
# ─────────────────────────────────────────────────────────────
@app.list_tools()
async def list_tools():
    return [
        Tool(
            name="load_ontology",
            description="加载一个 OWL/TTL/RDF 本体文件（本地路径或 IRI）。后续所有操作都在这个本体上进行。",
            inputSchema={
                "type": "object",
                "properties": {
                    "path": {
                        "type": "string",
                        "description": "本体文件的本地绝对路径（如 C:/work/my.ttl）或 IRI（如 http://...）",
                    }
                },
                "required": ["path"],
            },
        ),
        Tool(
            name="get_ontology_info",
            description="获取当前加载本体的基本信息：IRI、类数量、个体数量、属性数量等。",
            inputSchema={"type": "object", "properties": {}},
        ),
        Tool(
            name="list_classes",
            description="列出本体中所有类（OWL Class）。可选按名称前缀过滤。",
            inputSchema={
                "type": "object",
                "properties": {
                    "prefix": {
                        "type": "string",
                        "description": "可选：按类名前缀过滤（大小写不敏感）",
                    },
                    "limit": {
                        "type": "integer",
                        "description": "最多返回多少条，默认 100",
                        "default": 100,
                    },
                },
            },
        ),
        Tool(
            name="list_individuals",
            description="列出本体中所有个体（OWL Individual）。可选按类名过滤。",
            inputSchema={
                "type": "object",
                "properties": {
                    "class_name": {
                        "type": "string",
                        "description": "可选：只列出该类的个体（类的 local name）",
                    },
                    "limit": {
                        "type": "integer",
                        "description": "最多返回多少条，默认 100",
                        "default": 100,
                    },
                },
            },
        ),
        Tool(
            name="list_properties",
            description="列出本体中所有对象属性（ObjectProperty）和数据属性（DataProperty）。",
            inputSchema={
                "type": "object",
                "properties": {
                    "prop_type": {
                        "type": "string",
                        "enum": ["object", "data", "all"],
                        "description": "属性类型：object / data / all，默认 all",
                        "default": "all",
                    }
                },
            },
        ),
        Tool(
            name="describe_class",
            description="描述一个类：父类、子类、等价类、限制条件、所有个体。",
            inputSchema={
                "type": "object",
                "properties": {
                    "class_name": {
                        "type": "string",
                        "description": "类的 local name（如 BankStatement）",
                    }
                },
                "required": ["class_name"],
            },
        ),
        Tool(
            name="describe_individual",
            description="描述一个个体：所属类、属性值（数据属性和对象属性）。",
            inputSchema={
                "type": "object",
                "properties": {
                    "individual_name": {
                        "type": "string",
                        "description": "个体的 local name",
                    }
                },
                "required": ["individual_name"],
            },
        ),
        Tool(
            name="sparql_query",
            description="对当前本体执行 SPARQL SELECT 查询，返回结果列表。",
            inputSchema={
                "type": "object",
                "properties": {
                    "query": {
                        "type": "string",
                        "description": "完整的 SPARQL 1.1 SELECT 查询语句",
                    },
                    "limit": {
                        "type": "integer",
                        "description": "最多返回多少条，默认 50",
                        "default": 50,
                    },
                },
                "required": ["query"],
            },
        ),
        Tool(
            name="add_class",
            description="向本体添加一个新类，可指定父类、标签和注释。修改不会自动保存，需调用 save_ontology。",
            inputSchema={
                "type": "object",
                "properties": {
                    "class_name": {"type": "string", "description": "新类的 local name"},
                    "parent_class": {
                        "type": "string",
                        "description": "父类的 local name，默认为 owl:Thing",
                        "default": "Thing",
                    },
                    "label": {"type": "string", "description": "可选：rdfs:label 标签"},
                    "comment": {"type": "string", "description": "可选：rdfs:comment 注释"},
                },
                "required": ["class_name"],
            },
        ),
        Tool(
            name="add_individual",
            description="向本体添加一个新个体，并指定所属类。修改不会自动保存，需调用 save_ontology。",
            inputSchema={
                "type": "object",
                "properties": {
                    "individual_name": {
                        "type": "string",
                        "description": "新个体的 local name",
                    },
                    "class_name": {
                        "type": "string",
                        "description": "所属类的 local name",
                    },
                    "properties": {
                        "type": "object",
                        "description": '可选：初始属性键值对，如 {"amount": 100.0, "currency": "CNY"}',
                    },
                },
                "required": ["individual_name", "class_name"],
            },
        ),
        Tool(
            name="add_object_property_assertion",
            description="为个体添加对象属性断言（subject --property--> object）。修改不会自动保存，需调用 save_ontology。",
            inputSchema={
                "type": "object",
                "properties": {
                    "subject": {"type": "string", "description": "主体个体 local name"},
                    "property": {"type": "string", "description": "对象属性 local name"},
                    "object": {"type": "string", "description": "客体个体 local name"},
                },
                "required": ["subject", "property", "object"],
            },
        ),
        Tool(
            name="run_reasoner",
            description="运行推理器（Pellet 或 HermiT）对本体进行推理，检查一致性并推断隐含知识。需要本机安装 JDK 8+。",
            inputSchema={
                "type": "object",
                "properties": {
                    "reasoner": {
                        "type": "string",
                        "enum": ["pellet", "hermit"],
                        "description": "推理器名称：pellet 或 hermit，默认 hermit",
                        "default": "hermit",
                    }
                },
            },
        ),
        Tool(
            name="save_ontology",
            description="将当前本体（含所有未保存的修改）保存到文件。",
            inputSchema={
                "type": "object",
                "properties": {
                    "path": {
                        "type": "string",
                        "description": "可选：另存为路径。不填则覆盖原文件。",
                    },
                    "format": {
                        "type": "string",
                        "enum": ["rdfxml", "turtle", "ntriples"],
                        "description": "序列化格式，默认 rdfxml",
                        "default": "rdfxml",
                    },
                },
            },
        ),
        Tool(
            name="search_entity",
            description="按名称关键词搜索类、个体、属性（大小写不敏感模糊匹配）。",
            inputSchema={
                "type": "object",
                "properties": {
                    "keyword": {
                        "type": "string",
                        "description": "搜索关键词（大小写不敏感）",
                    },
                    "limit": {
                        "type": "integer",
                        "description": "最多返回多少条，默认 30",
                        "default": 30,
                    },
                },
                "required": ["keyword"],
            },
        ),
    ]


# ─────────────────────────────────────────────────────────────
# 工具实现
# ─────────────────────────────────────────────────────────────
@app.call_tool()
async def call_tool(name: str, arguments: dict) -> CallToolResult:
    global _world, _onto, _loaded_path, _graph

    def ok(data: Any) -> CallToolResult:
        return CallToolResult(
            content=[TextContent(type="text", text=json.dumps(data, ensure_ascii=False, indent=2))]
        )

    try:
        # ── load_ontology ──────────────────────────────────────────
        if name == "load_ontology":
            path = arguments["path"]
            _world = World()

            local = Path(path)
            if local.exists():
                ext = local.suffix.lower()
                rdflib_fmt = {
                    ".ttl": "turtle", ".turtle": "turtle",
                    ".n3": "n3",
                    ".jsonld": "json-ld", ".json": "json-ld",
                    ".owl": "xml", ".rdf": "xml", ".xml": "xml",
                    ".nt": "nt", ".ntriples": "nt",
                }.get(ext, "xml")

                _graph = rdflib.Graph()
                _graph.parse(str(local.resolve()), format=rdflib_fmt)
                rdfxml_data = _prepare_graph_for_owlready2(_graph)
                _onto = _load_ontology_from_bytes(_world, local.resolve().as_uri(), rdfxml_data)
                _loaded_path = str(local.resolve())
            else:
                # 远程 IRI
                _graph = rdflib.Graph()
                _graph.parse(path)
                rdfxml_data = _prepare_graph_for_owlready2(_graph)
                _onto = _load_ontology_from_bytes(_world, path, rdfxml_data)
                _loaded_path = path

            return ok({
                "status": "ok",
                "iri": str(_onto.base_iri),
                "classes": sum(1 for _ in _onto.classes()),
                "individuals": sum(1 for _ in _onto.individuals()),
                "properties": sum(1 for _ in _onto.object_properties()) + sum(1 for _ in _onto.data_properties()),
                "loaded_from": _loaded_path,
            })

        # ── get_ontology_info ──────────────────────────────────────
        elif name == "get_ontology_info":
            _check_loaded()
            # 用 rdflib 统计个体（更可靠，包含只有 rdf:type 没有 owl:NamedIndividual 的情况）
            _IND_SPARQL = """
                PREFIX owl: <http://www.w3.org/2002/07/owl#>
                SELECT DISTINCT ?ind WHERE {
                    ?ind a ?type .
                    ?type a owl:Class .
                    FILTER(?type != owl:Class)
                }
            """
            if _graph:
                inds_n = sum(1 for _ in _graph.query(_IND_SPARQL))
            else:
                inds_n = sum(1 for _ in _onto.individuals())

            return ok({
                "iri": str(_onto.base_iri),
                "loaded_from": _loaded_path,
                "classes": sum(1 for _ in _onto.classes()),
                "individuals": inds_n,
                "object_properties": sum(1 for _ in _onto.object_properties()),
                "data_properties": sum(1 for _ in _onto.data_properties()),
                "annotation_properties": sum(1 for _ in _onto.annotation_properties()),
            })

        # ── list_classes ───────────────────────────────────────────
        elif name == "list_classes":
            _check_loaded()
            prefix = arguments.get("prefix", "").lower()
            limit = int(arguments.get("limit", 100))
            results = []
            for cls in _onto.classes():
                if prefix and not cls.name.lower().startswith(prefix):
                    continue
                results.append({
                    "name": cls.name,
                    "iri": str(cls.iri),
                    "label": str(cls.label.first()) if cls.label else None,
                })
                if len(results) >= limit:
                    break
            return ok({"total_shown": len(results), "classes": results})

        # ── list_individuals ───────────────────────────────────────
        elif name == "list_individuals":
            _check_loaded()
            class_name = arguments.get("class_name")
            limit = int(arguments.get("limit", 100))
            results = []

            if _graph:
                if class_name:
                    cls = _find_class(class_name)
                    if cls is None:
                        raise ValueError(f"类 '{class_name}' 不存在")
                    sparql = f"""
                        PREFIX owl: <http://www.w3.org/2002/07/owl#>
                        SELECT DISTINCT ?ind ?indType WHERE {{
                            ?ind a <{cls.iri}> .
                            OPTIONAL {{ ?ind a ?indType . ?indType a owl:Class . }}
                        }}
                    """
                else:
                    sparql = """
                        PREFIX owl: <http://www.w3.org/2002/07/owl#>
                        SELECT DISTINCT ?ind WHERE {
                            ?ind a ?type .
                            ?type a owl:Class .
                            FILTER(?type != owl:Class)
                        }
                    """
                seen = set()
                for row in _graph.query(sparql):
                    ind_iri = str(row[0])
                    if ind_iri in seen:
                        continue
                    seen.add(ind_iri)
                    entry = {"name": _local_name(ind_iri), "iri": ind_iri, "types": []}
                    if len(row) > 1 and row[1] is not None:
                        entry["types"].append(_local_name(str(row[1])))
                    results.append(entry)
                    if len(results) >= limit:
                        break
            else:
                source = cls.instances() if class_name and (cls := _find_class(class_name)) else _onto.individuals()
                for ind in source:
                    results.append({
                        "name": ind.name,
                        "iri": str(ind.iri),
                        "types": [t.name for t in ind.is_a if hasattr(t, "name")],
                    })
                    if len(results) >= limit:
                        break

            return ok({"total_shown": len(results), "individuals": results})

        # ── list_properties ────────────────────────────────────────
        elif name == "list_properties":
            _check_loaded()
            prop_type = arguments.get("prop_type", "all")
            results = []
            if prop_type in ("object", "all"):
                for p in _onto.object_properties():
                    results.append({
                        "name": p.name,
                        "type": "ObjectProperty",
                        "domain": [d.name for d in p.domain if hasattr(d, "name")],
                        "range": [r.name for r in p.range if hasattr(r, "name")],
                    })
            if prop_type in ("data", "all"):
                for p in _onto.data_properties():
                    results.append({
                        "name": p.name,
                        "type": "DataProperty",
                        "domain": [d.name for d in p.domain if hasattr(d, "name")],
                        # FIX: 将 <class 'float'> 等转换为可读的 xsd: 类型名
                        "range": [_xsd_range_label(r) for r in p.range],
                    })
            return ok({"total_shown": len(results), "properties": results})

        # ── describe_class ─────────────────────────────────────────
        elif name == "describe_class":
            _check_loaded()
            cls = _find_class(arguments["class_name"])
            if cls is None:
                raise ValueError(f"类 '{arguments['class_name']}' 不存在")

            return ok({
                "name": cls.name,
                "iri": str(cls.iri),
                "label": str(cls.label.first()) if cls.label else None,
                "comment": str(cls.comment.first()) if cls.comment else None,
                "parents": [p.name for p in cls.is_a if hasattr(p, "name")],
                "children": [c.name for c in cls.subclasses()],
                "equivalent_to": [e.name for e in cls.equivalent_to if hasattr(e, "name")],
                "restrictions": [_safe_str(r) for r in cls.is_a if not hasattr(r, "name")],
                "instances_count": sum(1 for _ in cls.instances()),
                "instances_sample": [i.name for i in cls.instances()][:50],
            })

        # ── describe_individual ────────────────────────────────────
        elif name == "describe_individual":
            _check_loaded()
            ind_name = arguments["individual_name"]

            if _graph:
                # 在 rdflib 图中查找个体 IRI
                find_sparql = f"""
                    PREFIX owl: <http://www.w3.org/2002/07/owl#>
                    SELECT DISTINCT ?ind WHERE {{
                        ?ind a ?type .
                        ?type a owl:Class .
                        FILTER(STRENDS(STR(?ind), "#{ind_name}") || STRENDS(STR(?ind), "/{ind_name}"))
                    }}
                """
                ind_iri = None
                for row in _graph.query(find_sparql):
                    ind_iri = str(row[0])
                    break

                if ind_iri is None:
                    raise ValueError(f"个体 '{ind_name}' 不存在")

                types_sparql = f"""
                    PREFIX owl: <http://www.w3.org/2002/07/owl#>
                    SELECT DISTINCT ?type WHERE {{
                        <{ind_iri}> a ?type .
                        ?type a owl:Class .
                    }}
                """
                types = [_local_name(str(row[0])) for row in _graph.query(types_sparql)]

                props_sparql = f"""
                    SELECT ?pred ?obj WHERE {{
                        <{ind_iri}> ?pred ?obj .
                        FILTER(?pred != <{RDF.type}>)
                    }}
                """
                props: dict = {}
                for row in _graph.query(props_sparql):
                    pred_name = _local_name(str(row[0]))
                    obj_val = _local_name(str(row[1])) if isinstance(row[1], URIRef) else str(row[1])
                    props.setdefault(pred_name, []).append(obj_val)

                return ok({"name": ind_name, "iri": ind_iri, "types": types, "properties": props})

            else:
                ind = _find_individual(ind_name)
                if ind is None:
                    raise ValueError(f"个体 '{ind_name}' 不存在")
                types = [t.name for t in ind.is_a if hasattr(t, "name")]
                props = {}
                for prop in _onto.object_properties():
                    vals = getattr(ind, prop.name, [])
                    if vals:
                        try:
                            props[prop.name] = [
                                v.name if hasattr(v, "name") else _safe_str(v)
                                for v in (vals if hasattr(vals, "__iter__") else [vals])
                            ]
                        except Exception:
                            pass
                for prop in _onto.data_properties():
                    vals = getattr(ind, prop.name, [])
                    if vals:
                        try:
                            props[prop.name] = list(vals) if hasattr(vals, "__iter__") else [vals]
                        except Exception:
                            pass
                return ok({"name": ind.name, "iri": str(ind.iri), "types": types, "properties": props})

        # ── sparql_query ───────────────────────────────────────────
        elif name == "sparql_query":
            _check_loaded()
            query = arguments["query"]
            limit = int(arguments.get("limit", 50))

            graph = _graph if _graph else _world.as_rdflib_graph()
            rows = []
            for row in graph.query(query):
                rows.append([_safe_str(cell) for cell in row])
                if len(rows) >= limit:
                    break
            return ok({"total_shown": len(rows), "results": rows})

        # ── add_class ──────────────────────────────────────────────
        elif name == "add_class":
            _check_loaded()
            cls_name = arguments["class_name"]
            parent_name = arguments.get("parent_class", "Thing")
            label = arguments.get("label")
            comment = arguments.get("comment")

            parent = owlready2.Thing if parent_name == "Thing" else _find_class(parent_name)
            if parent is None:
                raise ValueError(f"父类 '{parent_name}' 不存在")

            with _onto:
                new_cls = type(cls_name, (parent,), {"namespace": _onto})
                if label:
                    new_cls.label = [label]
                if comment:
                    new_cls.comment = [comment]

            # FIX: 将 owlready2 的改动同步回 _graph，确保后续 SPARQL 和 turtle 保存正确
            _sync_graph_from_onto()

            return ok({
                "status": "ok",
                "created": cls_name,
                "iri": str(new_cls.iri),
                "parent": parent_name,
            })

        # ── add_individual ─────────────────────────────────────────
        elif name == "add_individual":
            _check_loaded()
            ind_name = arguments["individual_name"]
            cls_name = arguments["class_name"]
            extra_props = arguments.get("properties", {})

            cls = _find_class(cls_name)
            if cls is None:
                raise ValueError(f"类 '{cls_name}' 不存在")

            with _onto:
                ind = cls(ind_name)
                for k, v in extra_props.items():
                    prop = _find_property(k)
                    if prop:
                        setattr(ind, k, v)

            # FIX: 同步改动到 _graph
            _sync_graph_from_onto()

            return ok({
                "status": "ok",
                "created": ind_name,
                "iri": str(ind.iri),
                "type": cls_name,
                "properties_set": list(extra_props.keys()),
            })

        # ── add_object_property_assertion ──────────────────────────
        elif name == "add_object_property_assertion":
            _check_loaded()
            subj_name = arguments["subject"]
            prop_name = arguments["property"]
            obj_name = arguments["object"]

            subj = _find_individual(subj_name)
            if subj is None:
                raise ValueError(f"个体 '{subj_name}' 不存在")
            obj = _find_individual(obj_name)
            if obj is None:
                raise ValueError(f"个体 '{obj_name}' 不存在")
            prop = _find_property(prop_name)
            if prop is None:
                raise ValueError(f"属性 '{prop_name}' 不存在")

            with _onto:
                current = getattr(subj, prop_name, [])
                if hasattr(current, "append"):
                    current.append(obj)
                else:
                    setattr(subj, prop_name, [obj])

            # FIX: 同步改动到 _graph
            _sync_graph_from_onto()

            return ok({"status": "ok", "triple": f"{subj_name} --{prop_name}--> {obj_name}"})

        # ── run_reasoner ───────────────────────────────────────────
        elif name == "run_reasoner":
            _check_loaded()
            reasoner = arguments.get("reasoner", "hermit")

            classes_before = sum(1 for _ in _onto.classes())
            inds_before = sum(1 for _ in _onto.individuals())

            with _onto:
                if reasoner == "pellet":
                    sync_reasoner_pellet(infer_property_values=True)
                else:
                    sync_reasoner_hermit(infer_property_values=True)

            classes_after = sum(1 for _ in _onto.classes())
            inds_after = sum(1 for _ in _onto.individuals())

            return ok({
                "status": "ok",
                "reasoner": reasoner,
                "message": "推理完成，本体一致性验证通过",
                "classes_before": classes_before,
                "classes_after": classes_after,
                "individuals_before": inds_before,
                "individuals_after": inds_after,
                # FIX: 返回推断出的新增数量，让 AI 知道推断了什么
                "inferred_classes": classes_after - classes_before,
                "inferred_individuals": inds_after - inds_before,
            })

        # ── save_ontology ──────────────────────────────────────────
        elif name == "save_ontology":
            _check_loaded()
            out_path = arguments.get("path") or _loaded_path
            fmt = arguments.get("format", "rdfxml")

            if not out_path:
                raise ValueError("无法确定保存路径，请指定 path 参数")

            if fmt == "turtle":
                # FIX: 使用已同步的 _graph（含最新改动）保存 turtle
                if _graph:
                    _graph.serialize(destination=out_path, format="turtle")
                else:
                    with tempfile.NamedTemporaryFile(mode="wb", suffix=".owl", delete=False) as tmp:
                        _onto.save(file=tmp.name, format="rdfxml")
                        g = rdflib.Graph()
                        g.parse(tmp.name, format="xml")
                        g.serialize(destination=out_path, format="turtle")
                    Path(tmp.name).unlink(missing_ok=True)
            elif fmt == "ntriples":
                if _graph:
                    _graph.serialize(destination=out_path, format="nt")
                else:
                    _onto.save(file=out_path, format="ntriples")
            else:
                # rdfxml：通过 owlready2 保存，再同步回 _graph
                _onto.save(file=out_path, format="rdfxml")
                _graph = rdflib.Graph()
                _graph.parse(out_path, format="xml")

            return ok({"status": "ok", "saved_to": out_path, "format": fmt})

        # ── search_entity ──────────────────────────────────────────
        elif name == "search_entity":
            _check_loaded()
            kw = arguments["keyword"].lower()
            limit = int(arguments.get("limit", 30))
            results = []

            for cls in _onto.classes():
                if kw in cls.name.lower():
                    results.append({"type": "Class", "name": cls.name, "iri": str(cls.iri)})

            # 用 rdflib 图搜索个体（比 owlready2 更可靠）
            ind_source = _graph if _graph else None
            if ind_source:
                ind_sparql = """
                    PREFIX owl: <http://www.w3.org/2002/07/owl#>
                    SELECT DISTINCT ?ind WHERE {
                        ?ind a ?type .
                        ?type a owl:Class .
                        FILTER(?type != owl:Class)
                    }
                """
                seen = set()
                for row in ind_source.query(ind_sparql):
                    iri_str = str(row[0])
                    if iri_str in seen:
                        continue
                    seen.add(iri_str)
                    local = _local_name(iri_str)
                    if kw in local.lower():
                        results.append({"type": "Individual", "name": local, "iri": iri_str})
            else:
                for ind in _onto.individuals():
                    if kw in ind.name.lower():
                        results.append({"type": "Individual", "name": ind.name, "iri": str(ind.iri)})

            for p in list(_onto.object_properties()) + list(_onto.data_properties()):
                if kw in p.name.lower():
                    results.append({"type": "Property", "name": p.name, "iri": str(p.iri)})

            return ok({
                "keyword": arguments["keyword"],
                "total_found": len(results),
                "results": results[:limit],
            })

        else:
            raise ValueError(f"未知工具: {name}")

    except Exception as e:
        return CallToolResult(
            isError=True,
            content=[TextContent(
                type="text",
                text=json.dumps({"error": str(e), "traceback": traceback.format_exc()},
                                ensure_ascii=False, indent=2),
            )],
        )


# ─────────────────────────────────────────────────────────────
# 入口
# ─────────────────────────────────────────────────────────────
async def main():
    async with stdio_server() as (read_stream, write_stream):
        await app.run(read_stream, write_stream, app.create_initialization_options())


if __name__ == "__main__":
    import asyncio
    asyncio.run(main())
