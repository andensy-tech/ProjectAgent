# TOOLS — 环境配置

## 可用系统
- **Jira**：任务跟踪系统，issue key 格式如 TV-12345
- **Confluence**：文档/知识库
- **Gerrit**：代码审核系统，CL = Change List

## 本地服务
- MCP 服务器：Jira/Confluence/Gerrit 通过用户配置的 MCP 服务器访问，**服务器名以设置页「MCP 服务」中的配置为准**（如 jira、confluence、gerrit_gerrit1、gerrit_gerrit2 等）。调用 call_mcp_tool 或 {server}__{tool} 时必须使用配置中实际的服务器名，否则会报"服务器不存在"
- 部署端口: 8000（FastAPI 后端）

## 文件回传（把文件发给用户）
生成的文件（write_docx / write_xlsx / write_html 等工具产出、技能产物）会**自动**以下载卡片回传到 Web Chat。
用户索要**已有文件**时，调用内置工具 `deliver_file`（传 file_path 或 file_name）即可把该文件以下载卡片发给用户——
**不存在"无法发送附件"这类限制**。遇到"把 XXX 文件发给我/我要下载 XXX"，一律调用 `deliver_file`，
不要反问用户"用哪种方式交付"。

## 内置文档工具
支持处理：
- Word (.docx)
- PDF 文本提取 + OCR
- Excel (.xlsx)
- PowerPoint (.pptx)
- 图片 OCR (.png, .jpg)
- 文本文件 (.txt, .log, .md, .csv, .json, .xml, .yaml, .ini)
- HTML (.html)
- EML 邮件 (.eml)
