#!/usr/bin/env python3
"""
Confluence MCP Server 连接测试 Demo
验证公司Confluence MCP服务是否可以被正常访问

使用方法:
    python mcp_confluence_test.py

配置信息:
    URL: http://10.0.0.1:8112/mcp
    协议: streamableHttp (MCP 2024-11-05)
    认证: Authorization Header
"""

import requests
import json

# Confluence MCP Server 配置
# 注意：实际项目中这些配置应从环境变量或配置文件读取
MCP_CONFIG = {
    "url": "http://10.0.0.1:8112/mcp",
    "headers": {
        "Authorization": '{"username": "your-username", "password": "your-password"}'
    }
}


def mcp_request(method: str, params: dict = None, request_id: int = 1) -> dict:
    """
    发送MCP JSON-RPC请求
    
    Args:
        method: MCP方法名 (如 "initialize", "tools/list", "tools/call")
        params: 请求参数
        request_id: 请求ID
    
    Returns:
        解析后的JSON响应
    """
    payload = {
        "jsonrpc": "2.0",
        "id": request_id,
        "method": method,
        "params": params or {}
    }
    
    resp = requests.post(
        MCP_CONFIG["url"],
        headers={
            **MCP_CONFIG["headers"],
            "Content-Type": "application/json"
        },
        json=payload,
        timeout=30
    )
    resp.raise_for_status()
    return resp.json()


def test_initialize() -> dict:
    """测试MCP初始化连接"""
    print("[Test 1] MCP Initialize...")
    result = mcp_request("initialize", {
        "protocolVersion": "2024-11-05",
        "capabilities": {},
        "clientInfo": {"name": "project-agent-demo", "version": "0.1.0"}
    }, request_id=1)
    
    server_info = result.get("result", {}).get("serverInfo", {})
    print(f"  ✅ Server: {server_info.get('name')} v{server_info.get('version')}")
    print(f"  ✅ Protocol: {result.get('result', {}).get('protocolVersion')}")
    return result


def test_tools_list() -> list:
    """测试获取可用工具列表"""
    print("\n[Test 2] Tools List...")
    result = mcp_request("tools/list", {}, request_id=2)
    tools = result.get("result", {}).get("tools", [])
    print(f"  ✅ 可用工具数: {len(tools)}")
    for tool in tools:
        print(f"     - {tool['name']}")
    return tools


def test_resources_list() -> list:
    """测试获取可用资源列表"""
    print("\n[Test 3] Resources List...")
    result = mcp_request("resources/list", {}, request_id=3)
    resources = result.get("result", {}).get("resources", [])
    print(f"  ✅ 可用资源数: {len(resources)}")
    for res in resources:
        print(f"     - {res['uri']}: {res['name']}")
    return resources


def test_search_pages(cql: str = "title ~ \"test\"", limit: int = 3) -> dict:
    """
    测试CQL搜索页面
    
    Args:
        cql: Confluence Query Language 查询语句
        limit: 最大返回结果数
    """
    print(f"\n[Test 4] Search Pages (CQL: {cql})...")
    result = mcp_request("tools/call", {
        "name": "search_pages",
        "arguments": {"cql": cql, "limit": limit}
    }, request_id=4)
    
    content = result.get("result", {}).get("content", [])
    if content:
        text_content = content[0].get("text", "{}")
        try:
            data = json.loads(text_content)
            results = data.get("results", [])
            print(f"  ✅ 搜索结果: {len(results)} 条")
            for item in results[:3]:
                print(f"     - [{item.get('type')}] {item.get('title', 'N/A')[:50]}")
        except json.JSONDecodeError:
            print(f"  ⚠️ 响应解析失败")
    return result


def test_get_spaces() -> dict:
    """测试获取Confluence空间列表"""
    print("\n[Test 5] Get Spaces...")
    result = mcp_request("tools/call", {
        "name": "get_spaces",
        "arguments": {}
    }, request_id=5)
    
    content = result.get("result", {}).get("content", [])
    if content:
        text_content = content[0].get("text", "{}")
        try:
            data = json.loads(text_content)
            spaces = data.get("results", [])
            print(f"  ✅ 空间数: {len(spaces)}")
            for space in spaces[:5]:
                print(f"     - {space.get('key')}: {space.get('name', 'N/A')}")
        except json.JSONDecodeError:
            print(f"  ⚠️ 响应解析失败")
    return result


def main():
    """运行所有测试"""
    print("=" * 60)
    print("Confluence MCP Server 连接测试")
    print("=" * 60)
    print(f"URL: {MCP_CONFIG['url']}")
    print()
    
    try:
        # Test 1: Initialize
        test_initialize()
        
        # Test 2: Tools List
        tools = test_tools_list()
        
        # Test 3: Resources List
        resources = test_resources_list()
        
        # Test 4: Search Pages
        test_search_pages()
        
        # Test 5: Get Spaces
        test_get_spaces()
        
        print("\n" + "=" * 60)
        print("✅ 所有测试通过 - Confluence MCP Server 连接正常!")
        print("=" * 60)
        
        # 返回关键信息供后续使用
        return {
            "status": "ok",
            "tools_count": len(tools),
            "resources_count": len(resources),
            "tools": [t["name"] for t in tools]
        }
        
    except requests.exceptions.ConnectionError as e:
        print(f"\n❌ 连接失败: 无法连接到MCP Server")
        print(f"   错误: {e}")
        return {"status": "error", "reason": "connection_failed"}
    except requests.exceptions.Timeout:
        print(f"\n❌ 连接超时: MCP Server响应超时")
        return {"status": "error", "reason": "timeout"}
    except Exception as e:
        print(f"\n❌ 测试失败: {type(e).__name__}: {e}")
        return {"status": "error", "reason": str(e)}


if __name__ == "__main__":
    result = main()
    print(f"\n测试结果: {json.dumps(result, indent=2, ensure_ascii=False)}")
