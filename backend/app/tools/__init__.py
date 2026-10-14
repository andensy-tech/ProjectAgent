"""Built-in Tools library for document processing.

Tools are in-process Python functions registered at startup
into the ActionExecutor alongside Skills and MCP clients.
"""

from .registry import ToolDef, ToolRegistry, tool_registry

# Tool handler functions
from .docx_tool import read_docx, write_docx
from .pdf_tool import read_pdf, extract_pdf_metadata
from .xlsx_tool import read_xlsx, write_xlsx
from .pptx_tool import read_pptx, write_pptx
from .image_tool import read_image_metadata, ocr_image
from .eml_tool import read_eml, write_eml
from .html_tool import read_html, write_html
from .text_tool import read_text_file
from .gerrit_tool import gerrit_submit_change, gerrit_review_change, gerrit_get_change_detail, gerrit_batch_get_changes
from .confluence_tool import confluence_get_page_content, confluence_ocr_page_image
from .jira_filter_tool import jira_resolve_filter
from .focus_set_tool import create_focus_set
from .progress_tool import judge_progress
from .risk_analysis_tool import risk_analyze
from .context_spill_tool import context_spill_read
from .scheduled_task_tool import create_scheduled_task, list_scheduled_tasks
from .file_delivery_tool import deliver_file
from .ci_build_tool import ci_build_query, ci_build_download

__all__ = [
    "ToolDef", "ToolRegistry", "tool_registry",
    "read_docx", "write_docx",
    "read_pdf", "extract_pdf_metadata",
    "read_xlsx", "write_xlsx",
    "read_pptx", "write_pptx",
    "read_image_metadata", "ocr_image",
    "read_eml", "write_eml",
    "read_html", "write_html",
    "read_text_file",
    "gerrit_submit_change", "gerrit_review_change", "gerrit_get_change_detail",
    "gerrit_batch_get_changes",
    "confluence_get_page_content",
    "confluence_ocr_page_image",
    "jira_resolve_filter",
    "create_focus_set",
    "judge_progress",
    "risk_analyze",
    "context_spill_read",
    "create_scheduled_task",
    "list_scheduled_tasks",
    "deliver_file",
    "ci_build_query",
    "ci_build_download",
]


def register_all_tools():
    """Register all built-in document tools with the global tool_registry."""
    tools = [
        ToolDef(
            name="read_docx", display_name="Read Word Document",
            description="Extract text and tables from .docx files",
            handler=read_docx, input_types=["docx", "doc"], output_types=["text"],
            keywords=["word", "docx", "文档", "word文档"],
        ),
        ToolDef(
            name="write_docx", display_name="Write Word Document",
            description="Create a .docx file from structured text and tables",
            handler=write_docx, input_types=["text"], output_types=["docx"],
            keywords=["生成word", "创建文档", "生成文档"],
        ),
        ToolDef(
            name="read_pdf", display_name="Read PDF",
            description="Extract text from PDF files, supports page range and table extraction",
            handler=read_pdf, input_types=["pdf"], output_types=["text"],
            keywords=["pdf", "PDF", "pdf文件"],
        ),
        ToolDef(
            name="extract_pdf_metadata", display_name="PDF Metadata",
            description="Extract metadata from PDF files (title, author, page count)",
            handler=extract_pdf_metadata, input_types=["pdf"], output_types=["text"],
            keywords=[],
        ),
        ToolDef(
            name="read_xlsx", display_name="Read Excel",
            description="Read .xlsx spreadsheets and return structured row/column data",
            handler=read_xlsx, input_types=["xlsx", "xls"], output_types=["text"],
            keywords=["excel", "xlsx", "表格", "电子表格"],
        ),
        ToolDef(
            name="write_xlsx", display_name="Write Excel",
            description="Create an .xlsx file from headers and row data",
            handler=write_xlsx, input_types=["text"], output_types=["xlsx"],
            keywords=["生成excel", "生成表格", "导出excel"],
        ),
        ToolDef(
            name="read_pptx", display_name="Read PowerPoint",
            description="Extract text from .pptx presentations, per-slide",
            handler=read_pptx, input_types=["pptx", "ppt"], output_types=["text"],
            keywords=["ppt", "pptx", "幻灯片", "演示文稿"],
        ),
        ToolDef(
            name="write_pptx", display_name="Write PowerPoint",
            description="Create a .pptx presentation from structured slide data",
            handler=write_pptx, input_types=["text"], output_types=["pptx"],
            keywords=["生成ppt", "生成幻灯片"],
        ),
        ToolDef(
            name="read_image_metadata", display_name="Read Image Metadata",
            description="Extract metadata from image files (format, dimensions, EXIF)",
            handler=read_image_metadata, input_types=["png", "jpg", "jpeg", "gif", "bmp", "tiff"],
            output_types=["text"], keywords=["图片", "image", "照片"],
        ),
        ToolDef(
            name="ocr_image", display_name="OCR Image",
            description="Extract text from images using OCR (supports Chinese + English)",
            handler=ocr_image, input_types=["png", "jpg", "jpeg", "gif", "bmp", "tiff"],
            output_types=["text"], keywords=["ocr", "识别", "文字识别", "扫描"],
        ),
        ToolDef(
            name="read_eml", display_name="Read EML Email",
            description="Parse .eml email files: extract headers (from, to, subject, date), body text, and attachment list",
            handler=read_eml, input_types=["eml"], output_types=["text"],
            keywords=["email", "eml", "邮件", "电子邮件"],
        ),
        ToolDef(
            name="write_eml", display_name="Write EML Email",
            description="Create an .eml file from sender, recipients, subject, body, and optional attachments",
            handler=write_eml, input_types=["text"], output_types=["eml"],
            keywords=["生成邮件", "创建邮件", "导出eml"],
        ),
        ToolDef(
            name="read_html", display_name="Read HTML",
            description="Read an HTML file and extract visible text content with title, script/style content is filtered out",
            handler=read_html, input_types=["html", "htm"], output_types=["text"],
            keywords=["html", "网页", "页面"],
        ),
        ToolDef(
            name="write_html", display_name="Write HTML",
            description="Write HTML content to a file",
            handler=write_html, input_types=["text"], output_types=["html"],
            keywords=["生成html", "生成网页", "导出html"],
        ),
        ToolDef(
            name="gerrit_submit_change", display_name="Gerrit Submit Change",
            description="通过Gerrit REST API提交变更合并(submit)。提交前会检查是否存在未合并的CL。需要已验证的Gerrit MCP配置",
            handler=gerrit_submit_change,
            keywords=["gerrit", "合并", "submit", "merge", "提交合并", "合入"],
        ),
        ToolDef(
            name="gerrit_review_change", display_name="Gerrit Review Change",
            description="通过Gerrit REST API为变更打分(review)。可设置 Code-Review/Verified 等标签",
            handler=gerrit_review_change,
            keywords=["gerrit", "review", "评审", "打分", "+2", "verified"],
        ),
        ToolDef(
            name="gerrit_get_change_detail", display_name="Gerrit Change Detail",
            description="通过Gerrit REST API查询变更的详细信息，包括标签评分、可合并状态、提交信息等",
            handler=gerrit_get_change_detail,
            keywords=["gerrit", "查询", "详情", "detail", "状态"],
        ),
        ToolDef(
            name="gerrit_batch_get_changes", display_name="Gerrit Batch Get Changes",
            description="批量查询多个 Gerrit CL 的状态（一次调用返回多个 CL 的编号/标题/状态/owner/评分/是否可合并）。"
                        "当需要查询大量 CL（>3个）时务必用此工具，传入 change_ids 列表，不要逐个调用 get_change_detail 循环。",
            handler=gerrit_batch_get_changes,
            keywords=["gerrit", "批量", "batch", "多个cl", "cl状态", "列表"],
        ),
        ToolDef(
            name="read_text_file", display_name="Read Text File",
            description="Read plain text files (.txt, .log, .md, .csv, .json, etc.) with auto-encoding detection",
            handler=read_text_file, input_types=["txt", "log", "md", "csv", "json", "xml", "yaml", "ini", "cfg"],
            output_types=["text"],
            keywords=["文本文件", "text", "log", "日志", "配置文件"],
        ),
        ToolDef(
            name="confluence_get_page_content", display_name="Confluence Page Content",
            description="通过 Confluence REST API 获取页面完整内容（含正文）。"
                        "与 MCP 的 get_page_mcp_confluence 不同，此工具能返回页面正文 body。"
                        "需要 page_id 参数，可选 confluence_base_url 参数。"
                        "先用 search_pages_mcp_confluence 获取 page_id，再用此工具获取正文。",
            handler=confluence_get_page_content,
            keywords=["confluence", "页面", "wiki", "文档", "正文", "内容"],
        ),
        ToolDef(
            name="confluence_ocr_page_image", display_name="Confluence OCR Image",
            description="下载 Confluence 页面中的图片附件并进行 OCR 文字识别。"
                        "先用 confluence_get_page_content 获取页面内容和图片列表（含 filename/url），"
                        "再对感兴趣的图片调用此工具提取图中文字。"
                        "需要 page_id 和 filename（或 image_url）参数。",
            handler=confluence_ocr_page_image,
            keywords=["confluence", "图片", "ocr", "识别", "截图", "附件"],
        ),
        ToolDef(
            name="jira_resolve_filter", display_name="Jira Resolve Filter",
            description="解析 Jira filter（关注集合）：读取 filter 的 name/JQL 并拉取成员列表。"
                        "用于 PM 下发关注指令（如发送 https://jira.../issues/?filter=60513 后识别关注集合）。"
                        "需要 filter_id 参数（可传 filter 编号或完整链接）。返回 filter 名称、JQL、成员列表。",
            handler=jira_resolve_filter,
            keywords=["jira", "filter", "关注", "集合", "看板", "jql"],
        ),
        ToolDef(
            name="create_focus_set", display_name="Create Focus Set",
            description="【关注指令必须调用】创建/更新关注集合。当 PM 下发关注指令——包含 filter/epic 链接，"
                        "或「关注/重点跟踪/本周需要达成/某日前达到 X 指标」等表述——必须调用本工具把该 filter/epic "
                        "保存为关注集合（含 deadline/metric_target/priority_semantics），否则看板「我的关注指令」不显示。"
                        "参数：identifier(或 filter_id)+ **jql（推荐直接传成员范围 JQL，可先用 search_issues 确认）**"
                        "+ 可选 deadline(YYYY-MM-DD)/metric_type/metric_target(如0.8)。仅 filter_id 时尝试 REST 解析（"
                        "MCP 代理下可能失败，失败时应先查 JQL 再传 jql）。注意：仅查询/统计 filter 不算建立关注集合。"
                        "**同一 filter/epic 支持多个不同目标**：identifier 加 `#目标标签` 后缀区分"
                        "（如 TV-1#week80 与 TV-1#month100 是两个独立集合，互不覆盖）"
                        "——当 PM 对同一范围下达不同 deadline/指标的关注指令时必须加后缀。",
            handler=create_focus_set,
            keywords=["关注", "集合", "filter", "目标", "截止", "达标", "fix率", "重点", "跟踪", "本周"],
        ),
        ToolDef(
            name="judge_progress", display_name="Judge Project Progress",
            description="判断项目进展：当前阶段/下个里程碑/关注集合状态（P-E）。"
                        "结合项目计划知识库（阶段/目标日期）与关注集合（Fix rate/倒计时/停滞）"
                        "输出结构化进展信息，供判断问题是否相对计划偏差、是否预警。"
                        "当 PM 问'项目进度如何/下个里程碑/某问题是否超期'时调用。",
            handler=judge_progress,
            keywords=["进展", "进度", "里程碑", "阶段", "计划", "超期", "偏差"],
        ),
        ToolDef(
            name="risk_analyze", display_name="Risk Analysis",
            description="对高紧迫度 Jira 问题跑 LLM 风险精判（P-G）：结合成员状态/停滞/优先级，"
                        "判断每个问题是否真正超期或有合理解释，输出结构化风险分析（问题/原因/建议/责任人）+ 智能摘要。"
                        "输入 focus_set_id 或 issue_keys。当 PM 问'哪些问题有风险/为什么超期/建议怎么推动'时调用。",
            handler=risk_analyze,
            keywords=["风险", "分析", "精判", "超期", "原因", "推动", "建议", "summary"],
        ),
        ToolDef(
            name="context_spill_read", display_name="Read Spilled Data Slice",
            description="有界读取先前因数据过大而暂存（spill）的原始结果切片。"
                        "当某次查询结果提示『全文/更早内容已暂存，可调 context_spill_read 查回』时，"
                        "如确实需要未展示的细节，调用本工具精确取回：传 spill_id + offset/limit "
                        "（**必须给具体 limit，勿整读**）+ 可选 query 关键词过滤（对评论/表格按内容筛选）。"
                        "优先用 query/limit 精确取所需条目，避免把大段数据整块拉入上下文。",
            handler=context_spill_read,
            keywords=["spill", "暂存", "查回", "完整", "全文", "更多", "未展示", "切片", "offset", "评论原文"],
        ),
        ToolDef(
            name="create_scheduled_task", display_name="Create Scheduled Task",
            description="【定时任务必须调用】创建无人值守定时任务。当 PM 表达「每隔 N 重复做 X 直到某日 / "
                        "每天定时做 X / N 天没更新就自动提醒」类诉求时必须调用本工具（Agent 自身无法定时，"
                        "由平台引擎按调度执行）。config 为声明式规则：source(jira_jql 查询范围) + "
                        "conditions(status_not_in 排除状态 / stale_days 停滞天数, basis 可选 last_comment|updated) + "
                        "action(jira_comment 提醒模板，变量 {key} {summary} {owner} {stale_days}，"
                        "remind_interval_days 同一 issue 提醒去重间隔)。参数：name + config + "
                        "interval_minutes(默认60) + 可选 run_until(YYYY-MM-DD 截止自动停止)。"
                        "例：跟踪 filter=61364 连续3天未更新自动comment提醒owner到9/20 → "
                        "source jql 'filter = 61364' + conditions [status_not_in [Verified,Closed], "
                        "stale_days 3 last_comment] + action 模板。已建任务可用 list_scheduled_tasks 查询，"
                        "看板「定时任务」页可启停/删除。",
            handler=create_scheduled_task,
            keywords=["定时", "定时任务", "定时提醒", "自动提醒", "每隔", "每天定时", "无人值守",
                      "自动comment", "自动评论", "schedule", "周期", "到期前"],
        ),
        ToolDef(
            name="list_scheduled_tasks", display_name="List Scheduled Tasks",
            description="列出当前用户已创建的定时任务（名称/类型/间隔/下次运行/最近状态/已跑次数）。"
                        "PM 问「我设了哪些定时任务/那个自动提醒还在跑吗」时调用。",
            handler=list_scheduled_tasks,
            keywords=["定时任务", "任务列表", "自动提醒列表", "schedule", "在跑", "已创建任务"],
        ),
        ToolDef(
            name="deliver_file", display_name="Deliver File To User",
            description="【用户索要文件时必须调用】把服务器上已有的文件发给用户——文件会以**下载卡片**"
                        "的形式直接出现在聊天里。当用户说「把 XXX 文件发给我 / 给我 XXX / 我要下载 XXX / "
                        "把刚才生成的文件发我」时**必须调用本工具**，**禁止回答『我无法发送附件/无法在聊天框发文件』**"
                        "（那是错误认知：文件可以发）。参数：file_path（已知确切路径时传）或 file_name"
                        "（只有文件名时传，工具会在项目目录/技能目录/tmp 等位置查找，同名按最新优先）；"
                        "可选 max_files（默认 3）。注：Agent 自己刚用 write_* 工具或技能生成的文件已自动回传，"
                        "无需再调；用户再次索要时仍可调用。",
            handler=deliver_file,
            keywords=["发给我", "发送文件", "给我文件", "下载", "附件", "发个文件", "send file", "deliver"],
        ),
        ToolDef(
            name="ci_build_query", display_name="CI Build Query",
            description="查询 Google CI (Android Build) 关注分支上的最新成功构建信息："
                        "最新 build id、RC 名称、是否签名、构建时间、产物清单。"
                        "当 PM 问「CHIPX1 最新构建是什么/最新 build id/构建出来了没有」时调用。"
                        "参数均可选：branch/target（缺省用设置页「CI 构建」配置）、"
                        "build_id（指定时改为列出该构建的产物清单）、build_type(user/userdebug)。"
                        "私有分支需先在设置页配置 OAuth 凭据并完成一次性授权脚本。",
            handler=ci_build_query,
            keywords=["ci", "构建", "build", "google ci", "android build", "build id",
                      "最新构建", "固件", "firmware", "gtvu"],
        ),
        ToolDef(
            name="ci_build_download", display_name="CI Build Download & Upload",
            description="下载指定 build id（或最新）的 Google CI 构建产物并自动上传到配置好的 FTPS。"
                        "当 PM 说「下载构建 15977914/把最新 userdebug 包传到 FTP/拉一个包」时调用。"
                        "参数均可选：build_id（缺省查最新）、build_type(user/userdebug/all，"
                        "默认取设置页配置)、artifacts（指定产物名列表，缺省自动解析）。"
                        "产物数 GB，任务提交后台执行（返回 task_id 可查进度），"
                        "完成后文件在 FTP 的 {remote_dir}/{日期}-{build_id}/{user|userdebug}/ 目录，"
                        "不经 Web 下载卡片回传。前置条件：设置页「CI 构建」已配置 branch/target/FTP，"
                        "且私有分支已完成 OAuth 一次性授权。",
            handler=ci_build_download,
            keywords=["下载构建", "拉包", "传ftp", "ftp", "下载固件", "ci下载",
                      "fetch build", "upload ftp", "userdebug包"],
        ),
    ]
    for t in tools:
        tool_registry.register(t)
    return tool_registry
