#!/usr/bin/env python3
"""端到端测试脚本：验证 MCP Server 的核心功能"""
import sys
import os
import json
import asyncio

# 添加当前目录到 path
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

# 导入 server 模块的内部函数
import server

TTL_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "test_ontology.ttl")


async def test_all():
    results = []

    # 1. 加载本体
    print("\n=== 1. load_ontology ===")
    r = await server.call_tool("load_ontology", {"path": TTL_PATH})
    info = json.loads(r.content[0].text)
    print(json.dumps(info, indent=2, ensure_ascii=False))
    assert info["status"] == "ok"
    assert info["classes"] >= 3
    results.append(("load_ontology", "PASS"))

    # 2. get_ontology_info
    print("\n=== 2. get_ontology_info ===")
    r = await server.call_tool("get_ontology_info", {})
    info = json.loads(r.content[0].text)
    print(json.dumps(info, indent=2, ensure_ascii=False))
    assert info["classes"] >= 3
    assert info["individuals"] >= 2
    results.append(("get_ontology_info", "PASS"))

    # 3. list_classes
    print("\n=== 3. list_classes ===")
    r = await server.call_tool("list_classes", {"limit": 20})
    data = json.loads(r.content[0].text)
    print(json.dumps(data, indent=2, ensure_ascii=False))
    assert data["total_shown"] >= 3
    results.append(("list_classes", "PASS"))

    # 4. list_individuals
    print("\n=== 4. list_individuals ===")
    r = await server.call_tool("list_individuals", {"limit": 20})
    data = json.loads(r.content[0].text)
    print(json.dumps(data, indent=2, ensure_ascii=False))
    assert data["total_shown"] >= 2
    results.append(("list_individuals", "PASS"))

    # 5. list_properties
    print("\n=== 5. list_properties ===")
    r = await server.call_tool("list_properties", {"prop_type": "all"})
    data = json.loads(r.content[0].text)
    print(json.dumps(data, indent=2, ensure_ascii=False))
    assert data["total_shown"] >= 3
    results.append(("list_properties", "PASS"))

    # 6. describe_class
    print("\n=== 6. describe_class (Employee) ===")
    r = await server.call_tool("describe_class", {"class_name": "Employee"})
    data = json.loads(r.content[0].text)
    print(json.dumps(data, indent=2, ensure_ascii=False))
    assert "Person" in data["parents"]
    results.append(("describe_class", "PASS"))

    # 7. describe_individual
    print("\n=== 7. describe_individual (alice) ===")
    r = await server.call_tool("describe_individual", {"individual_name": "alice"})
    data = json.loads(r.content[0].text)
    print(json.dumps(data, indent=2, ensure_ascii=False))
    assert "Employee" in data["types"]
    results.append(("describe_individual", "PASS"))

    # 8. sparql_query
    print("\n=== 8. sparql_query ===")
    r = await server.call_tool("sparql_query", {
        "query": """
            PREFIX : <http://example.org/test#>
            SELECT ?ind ?amount WHERE {
                ?ind :amount ?amount .
            }
        """,
        "limit": 10,
    })
    data = json.loads(r.content[0].text)
    print(json.dumps(data, indent=2, ensure_ascii=False))
    assert data["total_shown"] >= 1
    results.append(("sparql_query", "PASS"))

    # 9. search_entity
    print("\n=== 9. search_entity (Bank) ===")
    r = await server.call_tool("search_entity", {"keyword": "Bank"})
    data = json.loads(r.content[0].text)
    print(json.dumps(data, indent=2, ensure_ascii=False))
    assert data["total_found"] >= 1
    results.append(("search_entity", "PASS"))

    # 10. add_class
    print("\n=== 10. add_class ===")
    r = await server.call_tool("add_class", {
        "class_name": "Manager",
        "parent_class": "Employee",
        "label": "Manager",
        "comment": "A manager employee",
    })
    data = json.loads(r.content[0].text)
    print(json.dumps(data, indent=2, ensure_ascii=False))
    assert data["status"] == "ok"
    results.append(("add_class", "PASS"))

    # 11. add_individual
    print("\n=== 11. add_individual ===")
    r = await server.call_tool("add_individual", {
        "individual_name": "bob",
        "class_name": "Manager",
    })
    data = json.loads(r.content[0].text)
    print(json.dumps(data, indent=2, ensure_ascii=False))
    assert data["status"] == "ok"
    results.append(("add_individual", "PASS"))

    # 12. add_object_property_assertion
    print("\n=== 12. add_object_property_assertion ===")
    r = await server.call_tool("add_object_property_assertion", {
        "subject": "bob",
        "property": "hasStatement",
        "object": "stmt1",
    })
    data = json.loads(r.content[0].text)
    print(json.dumps(data, indent=2, ensure_ascii=False))
    assert data["status"] == "ok"
    results.append(("add_object_property_assertion", "PASS"))

    # 13. save_ontology
    print("\n=== 13. save_ontology ===")
    save_path = TTL_PATH.replace(".ttl", "_modified.ttl")
    r = await server.call_tool("save_ontology", {
        "path": save_path,
        "format": "turtle",
    })
    data = json.loads(r.content[0].text)
    print(json.dumps(data, indent=2, ensure_ascii=False))
    assert data["status"] == "ok"
    assert os.path.exists(save_path)
    results.append(("save_ontology", "PASS"))

    # 打印总结
    print("\n" + "=" * 50)
    print("TEST SUMMARY")
    print("=" * 50)
    for name, status in results:
        print(f"  {name}: {status}")
    print(f"\nTotal: {len(results)} tests, ALL PASSED")


if __name__ == "__main__":
    asyncio.run(test_all())
