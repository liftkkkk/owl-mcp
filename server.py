#!/usr/bin/env python3
"""
Protégé MCP Server
==================
一个为 Protégé/OWL 本体提供 MCP（Model Context Protocol）接口的服务器。
支持加载本体、SPARQL 查询、添加类/属性/个体、运行推理器等操作。

依赖: pip install mcp owlready2
运行: python server.py
"""

import json
import sys
import io
import traceback
from pathlib import Path
from typing import Any, Optional

try:
    from mcp.server import Server
    from mcp.server.stdio import stdio_server
    from mcp.types import (
        Tool,
        TextContent,
        CallToolResult,
    )
except ImportError:
    print("❌ 缺少 mcp 包，请运行: pip install mcp", file=sys.stderr)
    sys.exit(1)

try:
    import owlready2
    from owlready2 import (
        get_ontology,
        World,
        sync_reasoner_pellet,
        sync_reasoner_hermit,
        default_world,
    )
except ImportError:
    print("ERROR: owlready2 not installed. Run: pip install owlready2", file=sys.stderr)
    sys.exit(1)

try:
    import rdflib
    from rdflib import URIRef
except ImportError:
    print("ERROR: rdflib not installed. Run: pip install rdflib", file=sys.stderr)
    sys.exit(1)

# ─────────────────────────────────────────────
# 全局状态
# ─────────────────────────────────────────────
_world: Optional[World] = None
_onto: Optional[Any] = None
_graph: Optional[Any] = None  # rdflib.Graph, 用于 SPARQL 和兜底查询
_loaded_path: Optional[str] = None

app = Server("protege-mcp")


# ─────────────────────────────────────────────
# 工具列表
# ─────────────────────────────────────────────
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
                        "description": "完整的 SPARQL SELECT 查询语句",
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
            description="向本体添加一个新类，可指定父类。",
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
                    "comment": {
                        "type": "string",
                        "description": "可选：rdfs:comment 注释",
                    },
                },
                "required": ["class_name"],
            },
        ),
        Tool(
            name="add_individual",
            description="向本体添加一个新个体，并指定所属类。",
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
                        "description": "可选：属性键值对，如 {\"amount\": 100.0, \"currency\": \"CNY\"}",
                    },
                },
                "required": ["individual_name", "class_name"],
            },
        ),
        Tool(
            name="add_object_property_assertion",
            description="为个体添加对象属性断言（individual1 --prop--> individual2）。",
            inputSchema={
                "type": "object",
                "properties": {
                    "subject": {"type": "string", "description": "主体个体 local name"},
                    "property": {
                        "type": "string",
                        "description": "对象属性 local name",
                    },
                    "object": {"type": "string", "description": "客体个体 local name"},
                },
                "required": ["subject", "property", "object"],
            },
        ),
        Tool(
            name="run_reasoner",
            description="运行推理器（Pellet 或 HermiT）对本体进行推理，检查一致性并推断隐含知识。",
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
            description="将当前本体保存到文件（覆盖原文件或指定新路径）。",
            inputSchema={
                "type": "object",
                "properties": {
                    "path": {
                        "type": "string",
                        "description": "可选：另存为路径。不填则覆盖原文件。",
                    },
                    "format": {
                        "type": "string",
                        "enum": ["rdfxml", "ntriples", "turtle"],
                        "description": "序列化格式，默认 rdfxml",
                        "default": "rdfxml",
                    },
                },
            },
        ),
        Tool(
            name="search_entity",
            description="按名称关键词搜索类、个体、属性（模糊匹配）。",
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


# ─────────────────────────────────────────────
# 辅助函数
# ─────────────────────────────────────────────
def _check_loaded():
    if _onto is None:
        raise RuntimeError("❌ 尚未加载本体。请先调用 load_ontology 工具。")


def _find_class(name: str):
    _check_loaded()
    # 先尝试直接查找
    cls = _onto.search_one(iri=f"*#{name}") or _onto.search_one(iri=f"*/{name}")
    if cls is None:
        # 遍历所有类
        for c in _onto.classes():
            if c.name == name:
                return c
    return cls


def _find_individual(name: str):
    _check_loaded()
    for ind in _onto.individuals():
        if ind.name == name:
            return ind
    return None


def _find_property(name: str):
    _check_loaded()
    for prop in list(_onto.object_properties()) + list(_onto.data_properties()):
        if prop.name == name:
            return prop
    return None


def _safe_str(val):
    try:
        return str(val)
    except Exception:
        return repr(val)


# ─────────────────────────────────────────────
# 工具实现
# ─────────────────────────────────────────────
@app.call_tool()
async def call_tool(name: str, arguments: dict) -> CallToolResult:
    global _world, _onto, _loaded_path, _graph

    try:
        # ── load_ontology ──────────────────────────────────
        if name == "load_ontology":
            path = arguments["path"]
            _world = World()

            # 支持本地路径
            local = Path(path)
            if local.exists():
                file_uri = local.resolve().as_uri()
                ext = local.suffix.lower()

                # 始终用 rdflib 解析（用于 SPARQL 和兜底查询）
                rdflib_format = {
                    ".ttl": "turtle",
                    ".turtle": "turtle",
                    ".n3": "n3",
                    ".jsonld": "json-ld",
                    ".json": "json-ld",
                    ".owl": "xml",
                    ".rdf": "xml",
                    ".xml": "xml",
                    ".nt": "nt",
                    ".ntriples": "nt",
                }.get(ext, "xml")

                _graph = rdflib.Graph()
                _graph.parse(str(local.resolve()), format=rdflib_format)

                # 补全 owl:NamedIndividual 声明，让 owlready2 能正确识别个体
                from rdflib.namespace import RDF, RDFS, OWL
                for s, p, o in list(_graph.triples((None, RDF.type, None))):
                    if isinstance(o, URIRef) and (o, RDF.type, OWL.Class) in _graph:
                        _graph.add((s, RDF.type, OWL.NamedIndividual))

                # 移除无法访问的 owl:imports，避免 owlready2 尝试下载远程本体失败
                from rdflib.namespace import RDF, RDFS, OWL
                _graph.remove((None, OWL.imports, None))

                # 用 rdflib 转 RDF/XML 再喂给 owlready2
                rdfxml_data = _graph.serialize(format="xml").encode("utf-8")

                # Monkey-patch owlready2 的 import 下载行为，跳过无法访问的远程 import
                _orig_urlopen = None
                def _mock_urlopen_no_network(url_or_req, *args, **kwargs):
                    """拦截 owlready2 的远程 import 下载请求，直接抛出异常让 owlready2 忽略"""
                    raise urllib.error.URLError("Network access disabled for import resolution")
                import urllib.request
                _orig_urlopen = urllib.request.urlopen
                urllib.request.urlopen = _mock_urlopen_no_network

                try:
                    _onto = _world.get_ontology(file_uri).load(
                        fileobj=io.BytesIO(rdfxml_data),
                        reload=True
                    )
                finally:
                    # 恢复原始 urlopen
                    urllib.request.urlopen = _orig_urlopen

                _loaded_path = str(local.resolve())
            else:
                # 远程 IRI
                _graph = rdflib.Graph()
                _graph.parse(path)
                from rdflib.namespace import RDF, RDFS, OWL
                for s, p, o in list(_graph.triples((None, RDF.type, None))):
                    if isinstance(o, URIRef) and (o, RDF.type, OWL.Class) in _graph:
                        _graph.add((s, RDF.type, OWL.NamedIndividual))
                # 移除无法访问的 owl:imports
                _graph.remove((None, OWL.imports, None))
                rdfxml_data = _graph.serialize(format="xml").encode("utf-8")

                import urllib.request
                _orig_urlopen = urllib.request.urlopen
                urllib.request.urlopen = _mock_urlopen_no_network
                try:
                    _onto = _world.get_ontology(path).load(
                        fileobj=io.BytesIO(rdfxml_data),
                        reload=True
                    )
                finally:
                    urllib.request.urlopen = _orig_urlopen
                _loaded_path = path

            classes_n = sum(1 for _ in _onto.classes())
            inds_n = sum(1 for _ in _onto.individuals())
            props_n = sum(1 for _ in _onto.object_properties()) + sum(
                1 for _ in _onto.data_properties()
            )
            return CallToolResult(
                content=[
                    TextContent(
                        type="text",
                        text=json.dumps(
                            {
                                "status": "ok",
                                "iri": str(_onto.base_iri),
                                "classes": classes_n,
                                "individuals": inds_n,
                                "properties": props_n,
                                "loaded_from": _loaded_path,
                            },
                            ensure_ascii=False,
                            indent=2,
                        ),
                    )
                ]
            )

        # ── get_ontology_info ──────────────────────────────
        elif name == "get_ontology_info":
            _check_loaded()
            classes_n = sum(1 for _ in _onto.classes())
            # 用 rdflib 图统计个体（更可靠）
            inds_n = len(
                list(
                    _graph.query(
                        """
                        SELECT DISTINCT ?ind WHERE {
                            ?ind a ?type .
                            ?type a owl:Class .
                            FILTER(?type != owl:Class)
                        }
                        """
                    )
                )
            ) if _graph else sum(1 for _ in _onto.individuals())
            obj_props = sum(1 for _ in _onto.object_properties())
            data_props = sum(1 for _ in _onto.data_properties())
            ann_props = sum(1 for _ in _onto.annotation_properties())
            return CallToolResult(
                content=[
                    TextContent(
                        type="text",
                        text=json.dumps(
                            {
                                "iri": str(_onto.base_iri),
                                "loaded_from": _loaded_path,
                                "classes": classes_n,
                                "individuals": inds_n,
                                "object_properties": obj_props,
                                "data_properties": data_props,
                                "annotation_properties": ann_props,
                            },
                            ensure_ascii=False,
                            indent=2,
                        ),
                    )
                ]
            )

        # ── list_classes ────────────────────────────────────
        elif name == "list_classes":
            _check_loaded()
            prefix = arguments.get("prefix", "").lower()
            limit = arguments.get("limit", 100)
            results = []
            for cls in _onto.classes():
                if prefix and not cls.name.lower().startswith(prefix):
                    continue
                results.append(
                    {
                        "name": cls.name,
                        "iri": str(cls.iri),
                        "label": str(cls.label.first()) if cls.label else None,
                    }
                )
                if len(results) >= limit:
                    break
            return CallToolResult(
                content=[
                    TextContent(
                        type="text",
                        text=json.dumps(
                            {"total_shown": len(results), "classes": results},
                            ensure_ascii=False,
                            indent=2,
                        ),
                    )
                ]
            )

        # ── list_individuals ────────────────────────────────
        elif name == "list_individuals":
            _check_loaded()
            class_name = arguments.get("class_name")
            limit = arguments.get("limit", 100)
            results = []

            if _graph:
                # 用 rdflib SPARQL 查询个体（更可靠）
                if class_name:
                    # 查找该类及其子类的个体
                    sparql = """
                        PREFIX owl: <http://www.w3.org/2002/07/owl#>
                        PREFIX rdfs: <http://www.w3.org/2000/01/rdf-schema#>
                        SELECT DISTINCT ?ind ?indType WHERE {
                            ?ind a ?indType .
                            ?indType a owl:Class .
                            ?indType rdfs:subClassOf* ?targetClass .
                            FILTER(?indType != owl:Class)
                        }
                    """
                    cls = _find_class(class_name)
                    if cls is None:
                        # 也可能在 rdflib 图里找
                        raise ValueError(f"类 '{class_name}' 不存在")
                    target_iri = str(cls.iri)
                    # 直接查该类的实例
                    sparql = """
                        SELECT DISTINCT ?ind ?indType WHERE {
                            ?ind a <%s> .
                            OPTIONAL { ?ind a ?indType . ?indType a owl:Class . }
                        }
                    """ % target_iri
                    rows = list(_graph.query(sparql))
                else:
                    sparql = """
                        PREFIX owl: <http://www.w3.org/2002/07/owl#>
                        SELECT DISTINCT ?ind WHERE {
                            ?ind a ?type .
                            ?type a owl:Class .
                            FILTER(?type != owl:Class)
                        }
                    """
                    rows = list(_graph.query(sparql))

                seen = set()
                for row in rows:
                    ind_iri = str(row[0])
                    if ind_iri in seen:
                        continue
                    seen.add(ind_iri)
                    local_name = ind_iri.split("#")[-1] if "#" in ind_iri else ind_iri.split("/")[-1]
                    types = []
                    if len(row) > 1 and row[1] is not None:
                        type_iri = str(row[1])
                        types.append(type_iri.split("#")[-1] if "#" in type_iri else type_iri.split("/")[-1])
                    results.append({"name": local_name, "iri": ind_iri, "types": types})
                    if len(results) >= limit:
                        break
            else:
                source = _onto.individuals()
                if class_name:
                    cls = _find_class(class_name)
                    if cls is None:
                        raise ValueError(f"类 '{class_name}' 不存在")
                    source = cls.instances()
                for ind in source:
                    results.append(
                        {
                            "name": ind.name,
                            "iri": str(ind.iri),
                            "types": [str(t.name) for t in ind.is_a if hasattr(t, "name")],
                        }
                    )
                    if len(results) >= limit:
                        break
            return CallToolResult(
                content=[
                    TextContent(
                        type="text",
                        text=json.dumps(
                            {"total_shown": len(results), "individuals": results},
                            ensure_ascii=False,
                            indent=2,
                        ),
                    )
                ]
            )

        # ── list_properties ─────────────────────────────────
        elif name == "list_properties":
            _check_loaded()
            prop_type = arguments.get("prop_type", "all")
            results = []
            if prop_type in ("object", "all"):
                for p in _onto.object_properties():
                    results.append(
                        {
                            "name": p.name,
                            "type": "ObjectProperty",
                            "domain": [d.name for d in p.domain if hasattr(d, "name")],
                            "range": [r.name for r in p.range if hasattr(r, "name")],
                        }
                    )
            if prop_type in ("data", "all"):
                for p in _onto.data_properties():
                    results.append(
                        {
                            "name": p.name,
                            "type": "DataProperty",
                            "domain": [d.name for d in p.domain if hasattr(d, "name")],
                            "range": [_safe_str(r) for r in p.range],
                        }
                    )
            return CallToolResult(
                content=[
                    TextContent(
                        type="text",
                        text=json.dumps(
                            {"total_shown": len(results), "properties": results},
                            ensure_ascii=False,
                            indent=2,
                        ),
                    )
                ]
            )

        # ── describe_class ───────────────────────────────────
        elif name == "describe_class":
            _check_loaded()
            cls = _find_class(arguments["class_name"])
            if cls is None:
                raise ValueError(f"类 '{arguments['class_name']}' 不存在")

            parents = [p.name for p in cls.is_a if hasattr(p, "name")]
            children = [c.name for c in cls.subclasses()]
            equivalents = [
                e.name for e in cls.equivalent_to if hasattr(e, "name")
            ]
            instances = [i.name for i in cls.instances()][:50]
            restrictions = [
                _safe_str(r) for r in cls.is_a if not hasattr(r, "name")
            ]

            return CallToolResult(
                content=[
                    TextContent(
                        type="text",
                        text=json.dumps(
                            {
                                "name": cls.name,
                                "iri": str(cls.iri),
                                "label": str(cls.label.first()) if cls.label else None,
                                "comment": str(cls.comment.first())
                                if cls.comment
                                else None,
                                "parents": parents,
                                "children": children,
                                "equivalent_to": equivalents,
                                "restrictions": restrictions,
                                "instances_count": sum(1 for _ in cls.instances()),
                                "instances_sample": instances,
                            },
                            ensure_ascii=False,
                            indent=2,
                        ),
                    )
                ]
            )

        # ── describe_individual ──────────────────────────────
        elif name == "describe_individual":
            _check_loaded()
            ind_name = arguments["individual_name"]
            ind = _find_individual(ind_name)

            if ind is None and _graph:
                # 用 rdflib 图查找
                sparql = """
                    PREFIX owl: <http://www.w3.org/2002/07/owl#>
                    SELECT DISTINCT ?ind ?iri WHERE {
                        ?ind a ?type .
                        ?type a owl:Class .
                        FILTER(?type != owl:Class)
                    }
                """
                for row in _graph.query(sparql):
                    iri_str = str(row[0])
                    local = iri_str.split("#")[-1] if "#" in iri_str else iri_str.split("/")[-1]
                    if local == ind_name:
                        ind = {"name": local, "iri": iri_str}
                        break

            if ind is None:
                raise ValueError(f"个体 '{ind_name}' 不存在")

            ind_iri = str(ind.iri) if hasattr(ind, "iri") else ind["iri"]

            if _graph:
                # 用 rdflib 图获取所有属性值（最可靠）
                types_sparql = """
                    SELECT DISTINCT ?type WHERE {
                        <%s> a ?type .
                        ?type a owl:Class .
                    }
                """ % ind_iri
                types = []
                for row in _graph.query(types_sparql):
                    type_iri = str(row[0])
                    types.append(type_iri.split("#")[-1] if "#" in type_iri else type_iri.split("/")[-1])

                props_sparql = """
                    SELECT ?pred ?obj WHERE {
                        <%s> ?pred ?obj .
                        FILTER(?pred != rdf:type)
                    }
                """ % ind_iri
                props = {}
                for row in _graph.query(props_sparql):
                    pred_iri = str(row[0])
                    pred_name = pred_iri.split("#")[-1] if "#" in pred_iri else pred_iri.split("/")[-1]
                    obj_val = str(row[1])
                    if pred_name not in props:
                        props[pred_name] = []
                    props[pred_name].append(obj_val)

                return CallToolResult(
                    content=[
                        TextContent(
                            type="text",
                            text=json.dumps(
                                {
                                    "name": ind_name,
                                    "iri": ind_iri,
                                    "types": types,
                                    "properties": props,
                                },
                                ensure_ascii=False,
                                indent=2,
                            ),
                        )
                    ]
                )
            else:
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
                            props[prop.name] = (
                                list(vals) if hasattr(vals, "__iter__") else [vals]
                            )
                        except Exception:
                            pass

                return CallToolResult(
                    content=[
                        TextContent(
                            type="text",
                            text=json.dumps(
                                {
                                    "name": ind.name,
                                    "iri": str(ind.iri),
                                    "types": types,
                                    "properties": props,
                                },
                                ensure_ascii=False,
                                indent=2,
                            ),
                        )
                    ]
                )

        # ── sparql_query ─────────────────────────────────────
        elif name == "sparql_query":
            _check_loaded()
            query = arguments["query"]
            limit = arguments.get("limit", 50)

            if _graph:
                rows = []
                for row in _graph.query(query):
                    rows.append([_safe_str(cell) for cell in row])
                    if len(rows) >= limit:
                        break
            else:
                graph = _world.as_rdflib_graph()
                rows = []
                for row in graph.query(query):
                    rows.append([_safe_str(cell) for cell in row])
                    if len(rows) >= limit:
                        break

            return CallToolResult(
                content=[
                    TextContent(
                        type="text",
                        text=json.dumps(
                            {"total_shown": len(rows), "results": rows},
                            ensure_ascii=False,
                            indent=2,
                        ),
                    )
                ]
            )

        # ── add_class ────────────────────────────────────────
        elif name == "add_class":
            _check_loaded()
            cls_name = arguments["class_name"]
            parent_name = arguments.get("parent_class", "Thing")
            label = arguments.get("label")
            comment = arguments.get("comment")

            # 找父类
            if parent_name == "Thing":
                parent = owlready2.Thing
            else:
                parent = _find_class(parent_name)
                if parent is None:
                    raise ValueError(f"父类 '{parent_name}' 不存在")

            with _onto:
                new_cls = type(cls_name, (parent,), {"namespace": _onto})
                if label:
                    new_cls.label = [label]
                if comment:
                    new_cls.comment = [comment]

            return CallToolResult(
                content=[
                    TextContent(
                        type="text",
                        text=json.dumps(
                            {
                                "status": "ok",
                                "created": cls_name,
                                "iri": str(new_cls.iri),
                                "parent": parent_name,
                            },
                            ensure_ascii=False,
                            indent=2,
                        ),
                    )
                ]
            )

        # ── add_individual ────────────────────────────────────
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

            return CallToolResult(
                content=[
                    TextContent(
                        type="text",
                        text=json.dumps(
                            {
                                "status": "ok",
                                "created": ind_name,
                                "iri": str(ind.iri),
                                "type": cls_name,
                                "properties_set": list(extra_props.keys()),
                            },
                            ensure_ascii=False,
                            indent=2,
                        ),
                    )
                ]
            )

        # ── add_object_property_assertion ─────────────────────
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

            return CallToolResult(
                content=[
                    TextContent(
                        type="text",
                        text=json.dumps(
                            {
                                "status": "ok",
                                "triple": f"{subj_name} --{prop_name}--> {obj_name}",
                            },
                            ensure_ascii=False,
                        ),
                    )
                ]
            )

        # ── run_reasoner ──────────────────────────────────────
        elif name == "run_reasoner":
            _check_loaded()
            reasoner = arguments.get("reasoner", "hermit")

            with _onto:
                if reasoner == "pellet":
                    sync_reasoner_pellet(infer_property_values=True)
                else:
                    sync_reasoner_hermit(infer_property_values=True)

            classes_n = sum(1 for _ in _onto.classes())
            inds_n = sum(1 for _ in _onto.individuals())
            return CallToolResult(
                content=[
                    TextContent(
                        type="text",
                        text=json.dumps(
                            {
                                "status": "ok",
                                "reasoner": reasoner,
                                "message": "推理完成，本体一致性验证通过",
                                "classes_after": classes_n,
                                "individuals_after": inds_n,
                            },
                            ensure_ascii=False,
                            indent=2,
                        ),
                    )
                ]
            )

        # ── save_ontology ─────────────────────────────────────
        elif name == "save_ontology":
            _check_loaded()
            out_path = arguments.get("path") or _loaded_path
            fmt = arguments.get("format", "rdfxml")

            if not out_path:
                raise ValueError("无法确定保存路径，请指定 path 参数")

            if fmt == "turtle":
                # 用 rdflib 图直接保存为 Turtle
                if _graph:
                    _graph.serialize(destination=out_path, format="turtle")
                else:
                    # 回退：owlready2 存为 RDF/XML 再转
                    import tempfile
                    with tempfile.NamedTemporaryFile(
                        mode="wb", suffix=".owl", delete=False
                    ) as tmp:
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
                # rdfxml
                _onto.save(file=out_path, format="rdfxml")
                # 同步更新 rdflib 图
                _graph = rdflib.Graph()
                _graph.parse(out_path, format="xml")
            return CallToolResult(
                content=[
                    TextContent(
                        type="text",
                        text=json.dumps(
                            {"status": "ok", "saved_to": out_path, "format": fmt},
                            ensure_ascii=False,
                        ),
                    )
                ]
            )

        # ── search_entity ─────────────────────────────────────
        elif name == "search_entity":
            _check_loaded()
            kw = arguments["keyword"].lower()
            limit = arguments.get("limit", 30)
            results = []

            for cls in _onto.classes():
                if kw in cls.name.lower():
                    results.append({"type": "Class", "name": cls.name, "iri": str(cls.iri)})

            if _graph:
                # 用 rdflib 图搜索个体
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
                    iri_str = str(row[0])
                    if iri_str in seen:
                        continue
                    seen.add(iri_str)
                    local = iri_str.split("#")[-1] if "#" in iri_str else iri_str.split("/")[-1]
                    if kw in local.lower():
                        results.append({"type": "Individual", "name": local, "iri": iri_str})
            else:
                for ind in _onto.individuals():
                    if kw in ind.name.lower():
                        results.append(
                            {"type": "Individual", "name": ind.name, "iri": str(ind.iri)}
                        )

            for p in list(_onto.object_properties()) + list(_onto.data_properties()):
                if kw in p.name.lower():
                    results.append(
                        {"type": "Property", "name": p.name, "iri": str(p.iri)}
                    )

            return CallToolResult(
                content=[
                    TextContent(
                        type="text",
                        text=json.dumps(
                            {
                                "keyword": arguments["keyword"],
                                "total_found": len(results),
                                "results": results[:limit],
                            },
                            ensure_ascii=False,
                            indent=2,
                        ),
                    )
                ]
            )

        else:
            raise ValueError(f"未知工具: {name}")

    except Exception as e:
        tb = traceback.format_exc()
        return CallToolResult(
            isError=True,
            content=[
                TextContent(
                    type="text",
                    text=json.dumps(
                        {"error": str(e), "traceback": tb},
                        ensure_ascii=False,
                        indent=2,
                    ),
                )
            ],
        )


# ─────────────────────────────────────────────
# 入口
# ─────────────────────────────────────────────
async def main():
    async with stdio_server() as (read_stream, write_stream):
        await app.run(read_stream, write_stream, app.create_initialization_options())


if __name__ == "__main__":
    import asyncio

    asyncio.run(main())
