#!/usr/bin/env python3
"""
Jira CL Link Pattern Research Script
Searches for Verified Jiras with label "oem_release_new" and analyzes
comment formats for CL references.
"""

import requests
import json
import re

# Jira MCP Server config
JIRA_MCP_URL = "http://10.0.0.1:8111/mcp"
AUTH_HEADER = '{"username": "your-username", "password": "your-password"}'


def mcp_request(method: str, params: dict = None, request_id: int = 1) -> dict:
    """Send MCP JSON-RPC request."""
    payload = {
        "jsonrpc": "2.0",
        "id": request_id,
        "method": method,
        "params": params or {}
    }
    resp = requests.post(
        JIRA_MCP_URL,
        headers={
            "Authorization": AUTH_HEADER,
            "Content-Type": "application/json"
        },
        json=payload,
        timeout=60
    )
    resp.raise_for_status()
    return resp.json()


def search_verified_issues() -> list:
    """Search for Verified status Jiras with label oem_release_new."""
    jql = 'labels = oem_release_new AND status = Verified'
    print(f"[Search] JQL: {jql}")
    
    result = mcp_request("tools/call", {
        "name": "search_issues",
        "arguments": {"jql": jql, "max_results": 100}
    }, request_id=1)
    
    # Parse response
    content = result.get("result", {}).get("content", [])
    if not content:
        print("  No content in response")
        return []
    
    text_content = content[0].get("text", "[]")
    try:
        issues = json.loads(text_content)
        if isinstance(issues, dict):
            issues = issues.get("issues", issues)
        if not isinstance(issues, list):
            issues = [issues]
        print(f"  Found {len(issues)} issues")
        return issues
    except json.JSONDecodeError as e:
        print(f"  JSON parse error: {e}")
        print(f"  Raw text: {text_content[:500]}")
        return []


def get_issue_comments(issue_key: str) -> dict:
    """Get comments for a specific issue."""
    print(f"[Comments] Fetching for {issue_key}...")
    
    result = mcp_request("tools/call", {
        "name": "get_issue_comments",
        "arguments": {"issue_key": issue_key}
    }, request_id=hash(issue_key) % 10000 + 10)
    
    return result


def extract_cl_patterns(text: str) -> list:
    """Extract CL-related patterns from text."""
    patterns = []
    
    # Pattern 1: CL: 12345 or CL:12345
    for m in re.finditer(r'CL\s*[:#]?\s*(\d+)', text, re.IGNORECASE):
        patterns.append({"type": "CL_number", "match": m.group(0), "value": m.group(1)})
    
    # Pattern 2: Change-Id: I...
    for m in re.finditer(r'Change-Id:\s*(I[a-f0-9]+)', text, re.IGNORECASE):
        patterns.append({"type": "Change-Id", "match": m.group(0), "value": m.group(1)})
    
    # Pattern 3: Gerrit URL patterns
    for m in re.finditer(r'https?://[^\s\)<>]*(?:gerrit|git|code|review)[^\s\)<>]*', text, re.IGNORECASE):
        patterns.append({"type": "URL", "match": m.group(0), "value": m.group(0)})
    
    # Pattern 4: commit hash (7+ hex chars)
    for m in re.finditer(r'\b([a-f0-9]{7,40})\b', text, re.IGNORECASE):
        patterns.append({"type": "commit_hash", "match": m.group(0), "value": m.group(1)})
    
    # Pattern 5: Patch Set references
    for m in re.finditer(r'Patch\s*Set\s*(\d+)', text, re.IGNORECASE):
        patterns.append({"type": "Patch_Set", "match": m.group(0), "value": m.group(1)})
    
    return patterns


def main():
    print("=" * 70)
    print("Jira CL Link Pattern Research")
    print("=" * 70)
    print(f"Server: {JIRA_MCP_URL}")
    print()
    
    # Step 1: Initialize
    print("[Step 1] Initializing MCP connection...")
    init_result = mcp_request("initialize", {
        "protocolVersion": "2024-11-05",
        "capabilities": {},
        "clientInfo": {"name": "cl-research", "version": "0.1.0"}
    }, request_id=0)
    server_info = init_result.get("result", {}).get("serverInfo", {})
    print(f"  Connected: {server_info.get('name')} v{server_info.get('version')}")
    print()
    
    # Step 2: Search issues
    print("[Step 2] Searching for Verified issues with label 'oem_release_new'...")
    issues = search_verified_issues()
    
    if not issues:
        print("\n❌ No issues found.")
        return
    
    print(f"\n✅ Found {len(issues)} Verified issue(s)")
    print()
    
    # Step 3: Get comments for each issue
    print("[Step 3] Fetching comments for each issue...")
    print()
    
    all_cl_patterns = []
    sample_comments = []
    raw_comment_structures = []
    
    for i, issue in enumerate(issues):
        if isinstance(issue, dict):
            issue_key = issue.get("key", f"issue_{i}")
            issue_summary = issue.get("fields", {}).get("summary", "N/A")
        else:
            issue_key = str(issue)
            issue_summary = "N/A"
        
        print(f"\n--- Issue {i+1}/{len(issues)}: {issue_key} - {issue_summary} ---")
        
        comments_result = get_issue_comments(issue_key)
        
        # Save raw structure for analysis
        raw_comment_structures.append({
            "issue_key": issue_key,
            "raw_result": comments_result
        })
        
        # Extract content
        content = comments_result.get("result", {}).get("content", [])
        if content:
            text_content = content[0].get("text", "[]")
            print(f"  Raw text length: {len(text_content)}")
            
            try:
                comments_data = json.loads(text_content)
                
                # Handle different structures
                if isinstance(comments_data, dict):
                    comments_list = comments_data.get("comments", [comments_data])
                elif isinstance(comments_data, list):
                    comments_list = comments_data
                else:
                    comments_list = [comments_data]
                
                print(f"  Parsed comments count: {len(comments_list)}")
                
                for j, comment in enumerate(comments_list):
                    if not isinstance(comment, dict):
                        continue
                    
                    body = comment.get("body", "")
                    author = comment.get("author", {})
                    if isinstance(author, dict):
                        author_name = author.get("displayName", author.get("name", "Unknown"))
                    else:
                        author_name = str(author)
                    
                    created = comment.get("created", "N/A")
                    
                    # Look for CL patterns in the comment body
                    patterns = extract_cl_patterns(str(body))
                    
                    if patterns:
                        print(f"\n  [Comment {j+1}] Author: {author_name}, Created: {created}")
                        print(f"  Body preview: {str(body)[:300]}")
                        print(f"  CL Patterns found: {len(patterns)}")
                        for p in patterns:
                            print(f"    - {p['type']}: {p['match']}")
                        
                        all_cl_patterns.extend(patterns)
                        sample_comments.append({
                            "issue_key": issue_key,
                            "comment_index": j,
                            "author": author_name,
                            "created": created,
                            "body_preview": str(body)[:500],
                            "patterns": patterns,
                            "full_body": str(body)
                        })
                    
            except json.JSONDecodeError as e:
                print(f"  JSON parse error: {e}")
                print(f"  Raw (first 500 chars): {text_content[:500]}")
        else:
            print(f"  No content in response")
    
    # Step 4: Summary report
    print("\n" + "=" * 70)
    print("RESEARCH SUMMARY")
    print("=" * 70)
    
    print(f"\n1. TOTAL VERIFIED JIRAS FOUND: {len(issues)}")
    for i, issue in enumerate(issues):
        if isinstance(issue, dict):
            print(f"   - {issue.get('key', '?')}: {issue.get('fields', {}).get('summary', 'N/A')}")
        else:
            print(f"   - {issue}")
    
    print(f"\n2. COMMENTS WITH CL REFERENCES: {len(sample_comments)}")
    
    print(f"\n3. CL PATTERN TYPES FOUND:")
    pattern_types = {}
    for p in all_cl_patterns:
        pt = p["type"]
        pattern_types[pt] = pattern_types.get(pt, 0) + 1
    for pt, count in sorted(pattern_types.items(), key=lambda x: -x[1]):
        print(f"   - {pt}: {count} occurrence(s)")
    
    print(f"\n4. SAMPLE COMMENTS WITH CL REFERENCES:")
    for sc in sample_comments[:5]:
        print(f"\n   Issue: {sc['issue_key']}")
        print(f"   Author: {sc['author']}")
        print(f"   Created: {sc['created']}")
        print(f"   Body: {sc['body_preview'][:400]}...")
        print(f"   Patterns: {[p['match'] for p in sc['patterns']]}")
    
    print(f"\n5. RAW COMMENT DATA STRUCTURE (from get_issue_comments):")
    if raw_comment_structures:
        first = raw_comment_structures[0]["raw_result"]
        print(f"   Top-level keys: {list(first.keys())}")
        if "result" in first:
            result = first["result"]
            print(f"   result keys: {list(result.keys())}")
            if "content" in result:
                content = result["content"]
                print(f"   content type: {type(content)}, length: {len(content)}")
                if content:
                    print(f"   content[0] keys: {list(content[0].keys())}")
                    print(f"   content[0] type: {content[0].get('type', 'N/A')}")
                    print(f"   content[0] text preview (first 800 chars):")
                    text = content[0].get("text", "")
                    print(f"   {text[:800]}")
    
    # Save full results to file
    output = {
        "total_issues": len(issues),
        "issues": [{"key": (i.get("key") if isinstance(i, dict) else str(i)), 
                    "summary": (i.get("fields", {}).get("summary", "N/A") if isinstance(i, dict) else "N/A")} 
                   for i in issues],
        "comments_with_cl": len(sample_comments),
        "sample_comments": sample_comments,
        "all_patterns": all_cl_patterns,
        "raw_structures": [{"issue_key": r["issue_key"], 
                            "raw_keys": list(r["raw_result"].keys())} 
                           for r in raw_comment_structures]
    }
    
    output_file = os.path.join(os.path.dirname(os.path.abspath(__file__)), "jira_cl_research_results.json")
    with open(output_file, "w", encoding="utf-8") as f:
        json.dump(output, f, indent=2, ensure_ascii=False, default=str)
    
    print(f"\n6. Full results saved to: {output_file}")


if __name__ == "__main__":
    main()
