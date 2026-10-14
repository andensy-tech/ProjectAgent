> **归档**：2026-06 记忆系统快照，缺 Phase5（FTS5/daily_logger/蒸馏/多用户隔离）全部演进，且把已修复问题当现状；已被 CLAUDE.md 取代。

 ---                                                                                                                                                     
  一、完整记忆系统架构                                                                                                                                    
                                                                                                                                                          
  ┌────────────────────────────────────────────────────────────────────────┐                                                                              
  │                      记忆系统总览: project_agent.db                     │                                                                             
  ├────────────────────────────────────────────────────────────────────────┤                                                                              
  │                                                                        │                                                                              
  │  ┌──────────────┐   ┌───────────────┐   ┌──────────────┐              │                                                                               
  │  │  Layer 1     │   │  Layer 2      │   │  Layer 3     │              │                                                                               
  │  │  System      │   │  Working      │   │  Session     │              │                                                                               
  │  │  Memory      │   │  Memory       │   │  Memory      │              │                                                                               
  │  │              │   │               │   │              │              │                                                                               
  │  │  In-memory   │   │  Per-conv     │   │  Per-session │              │                                                                               
  │  │  只读        │   │  滑动窗口20   │   │  DB持久化     │              │                                                                              
  │  │  启动时构建  │   │  LLM压缩摘要  │   │  服务重启恢复 │              │                                                                              
  │  └──────┬───────┘   └──────┬────────┘   └──────┬───────┘              │                                                                               
  │         │                  │                   │                       │                                                                              
  │         ▼                  ▼                   ▼                       │                                                                              
  │  ┌─────────────────────────────────────────────────────────────────┐   │                                                                              
  │  │               Layer 4: Long-term Memory (DB持久化)              │   │                                                                              
  │  │                                                                │   │                                                                               
  │  │  ┌──────────────────┐  ┌────────────────┐  ┌────────────────┐  │   │                                                                               
  │  │  │  UserProfile     │  │  LearningRecord │  │  ProjectMemory │  │   │                                                                              
  │  │  │  ─────────────  │  │  ───────────── │  │  ───────────── │  │   │                                                                                
  │  │  │  • user_id (PK) │  │  • id (PK)     │  │  • id (PK)     │  │   │                                                                                
  │  │  │  • role         │  │  • user_id     │  │  • project_id  │  │   │                                                                                
  │  │  │  • preferred_   │  │  • pattern     │  │  • key_         │  │   │                                                                               
  │  │  │    projects     │  │  • context     │  │    milestones   │  │   │                                                                               
  │  │  │  • preferred_   │  │  • success_    │  │  • key_         │  │   │                                                                               
  │  │  │    skills       │  │    rate        │  │    decisions    │  │   │                                                                               
  │  │  │  • communi-     │  │  • usage_count │  │  • risk_history │  │   │                                                                               
  │  │  │    cation_style │  │  • last_used   │  │  • team_members │  │   │                                                                               
  │  │  │  • alert_       │  │               │  │                 │  │   │                                                                                
  │  │  │    threshold    │  │               │  │                 │  │   │                                                                                
  │  │  │  • learned_     │  │               │  │                 │  │   │                                                                                
  │  │  │    preferences  │  │               │  │                 │  │   │                                                                                
  │  │  └──────────────────┘  └────────────────┘  └────────────────┘  │   │                                                                               
  │  └─────────────────────────────────────────────────────────────────┘   │                                                                              
  │                                                                        │                                                                              
  │  ┌─────────────────────────────────────────────────────────────────┐   │                                                                              
  │  │          会话历史: conversations 表 + messages 表               │   │                                                                              
  │  │                                                                │   │                                                                               
  │  │  conversations                    messages                      │   │                                                                              
  │  │  ─────────────                    ────────                      │   │                                                                              
  │  │  • id (PK)                        • id (PK)                     │   │                                                                              
  │  │  • user_id (FK)                   • conversation_id (FK)        │   │                                                                              
  │  │  • title                          • role (user/assistant)       │   │                                                                              
  │  │  • messages (JSON, legacy)        • content                     │   │                                                                              
  │  │  • context_vars (JSON)            • message_type (text/card)    │   │                                                                              
  │  │  • entities (JSON)                • extra_data (JSON)           │   │                                                                              
  │  └─────────────────────────────────────────────────────────────────┘   │                                                                              
  └────────────────────────────────────────────────────────────────────────┘                                                                              
                                                                                                                                                          
  Layer 1: System Memory（系统记忆）                                                                                                                      
                                                                                                                                                          
  - 存储：纯内存，SystemMemory 类实例                                                                                                                     
  - 数据来源：启动时由 SkillScanner 注册 skill + MCP 客户端注册 tool + 内置工具注册
  - 内容：技能列表 + 工具列表 + MCP 列表，以文本描述形式存在                                                                                              
  - 生命周期：进程级，随服务启停重建                                                                                                                      
  - 代码位置：system.py:36-61                                                                                                                             
  - 输出格式："技能名: 描述" 的多行文本                                                                                                                   
                                                                                                                                                          
  Layer 2: Working Memory（工作记忆）                                                                                                                     
                                                                                                                                                          
  - 存储：MemorySystem.working_memories 字典，key=conversation_id                                                                                         
  - 数据结构：    
    - messages: List[Dict] — 最多 20 条，溢出时触发压缩                                                                                                   
    - entities: Dict[str, List[Dict]] — 实体追踪                                                                                                          
    - intent_stack: List[str] — 意图栈（未真正使用）                                                                                                      
    - context_vars: Dict[str, Any] — 上下文变量                                                                                                           
    - compacted_summaries: List[str] — 压缩后的历史摘要                                                                                                   
  - DB 恢复：重启后若 wm.messages 为空，从 messages 表加载最近 10 条                                                                                      
  - 压缩策略：_quick_summarize() — 静态方法，仅提取用户前 3 条消息的前 80 字符，检查 assistant 回复是否含 "tool" 关键词                                   
  - 代码位置：system.py:66-151                                                                                                                            
                                                                                                                                                          
  Layer 3: Session Memory（会话记忆）                                                                                                                     
                                                                                                                                                          
  - 存储：内存 + session_memories 表                                                                                                                      
  - 数据结构：    
    - active_project / active_config — 当前项目上下文                                                                                                     
    - skill_results: Dict — 技能结果缓存                                                                                                                  
    - preferences: Dict — 临时偏好覆盖                                                                                                                    
    - expires_at — 过期时间                                                                                                                               
  - DB 恢复：重启后若 sm.skill_results/preferences 为空，从 DB 加载                                                                                       
  - 持久化时机：每次 update_from_execution() 调用时写 DB                                                                                                  
  - 代码位置：system.py:156-192, models/memory.py:73-96                                                                                                   
                                                                                                                                                          
  Layer 4: Long-term Memory（长期记忆）                                                                                                                   
                                                                                                                                                          
  - 存储：DB 三张表                                                                                                                                       
  - 查询入口：MemorySystem.get_context() 中调用
  - 更新入口：                                                                                                                                            
    - 自动更新：update_from_execution() 更新 last_active 和 preferred_skills                                                                              
    - 手动更新：LLM 通过 memory_save 工具调用                                                                                                             
    - 检索：LLM 通过 memory_search 工具调用                                                                                                               
  - 代码位置：system.py:196-232, crud/memory.py                                                                                                           
                                                                                                                                                          
  会话历史（Conversation Storage）                                                                                                                        
                                                                                                                                                          
  - 独立于记忆系统之外，由 crud/conversation.py 管理                                                                                                      
  - messages 表：role / content / message_type / extra_data
  - conversations 表：title / context_vars (JSON) / entities (JSON) — 其中 context_vars 和 entities 字段已定义但从不写入                                  
  - 写入时机：API 层 (chat.py) 在 SSE yield 之后异步写 DB                                                                                                 
                                                                                                                                                          
  ---                                                                                                                                                     
  二、每次 LLM 交互的完整数据流                                                                                                                           
                                                                                                                                                          
  ┌─────────────────────────────────────────────────────────────────────────┐
  │                POST /api/chat/message  (chat.py:51-118)                 │                                                                             
  │                                                                         │                                                                             
  │  1. 收到用户消息 → 创建 event_generator()                               │                                                                             
  │  2. 若含 file_id → 解析文件附件信息                                     │                                                                             
  │  3. 调用 agent_engine.process_message()                                 │                                                                             
  │  4. SSE 流式响应返回到客户端                                             │                                                                            
  │  5. SSE yield 后 → 异步写 DB (创建对话 / 保存消息)                      │                                                                             
  └─────────────────────────────────────────────────────────────────────────┘                                                                             
                                      │                                                                                                                   
                                      ▼                                                                                                                   
  ┌─────────────────────────────────────────────────────────────────────────┐                                                                             
  │               engine.process_message()  (engine.py:55-241)             │                                                                              
  │                                                                         │                                                                             
  │  ┌───────────────────────────────────────────────────────────────────┐  │                                                                             
  │  │  步骤 1: 加载记忆上下文 (engine.py:77-91)                         │  │                                                                             
  │  │  ┌─────────────────────────────────────────────────────────────┐  │  │                                                                             
  │  │  │ memory_system.get_context(db, conversation_id,              │  │  │                                                                             
  │  │  │                              user_id, session_id, message)  │  │  │                                                                             
  │  │  │                                                             │  │  │                                                                             
  │  │  │  Layer1: System Memory                                      │  │  │                                                                             
  │  │  │   ├── available_skills: str ← system_memory.get_skill_      │  │  │                                                                             
  │  │  │   │                         descriptions()                  │  │  │                                                                             
  │  │  │   └── available_tools: str  ← system_memory.get_tool_       │  │  │                                                                             
  │  │  │                               descriptions()                │  │  │                                                                             
  │  │  │                                                             │  │  │                                                                             
  │  │  │  Layer2: Working Memory (system.py:284-297)                 │  │  │                                                                             
  │  │  │   ├── 获取/创建 WorkingMemory(conversation_id)               │  │  │                                                                            
  │  │  │   ├── 若空且DB可用 → 加载最近10条消息                        │  │  │                                                                            
  │  │  │   ├── conversation_context: str ← wm.get_context_summary()  │  │  │                                                                             
  │  │  │   │   (压缩摘要 + 上下文变量 + 实体)                          │  │  │                                                                           
  │  │  │   └── recent_messages: list ← wm.get_recent_messages(6)     │  │  │                                                                             
  │  │  │                                                             │  │  │                                                                             
  │  │  │  Layer3: Session Memory (system.py:300-321)                 │  │  │                                                                             
  │  │  │   ├── 获取/创建 SessionMemory(session_id, user_id)           │  │  │                                                                            
  │  │  │   ├── 若空且DB可用 → 从 session_memories 表恢复               │  │  │                                                                           
  │  │  │   ├── active_project: str (若有)                             │  │  │                                                                            
  │  │  │   └── session_preferences: dict (若有)                       │  │  │                                                                            
  │  │  │                                                             │  │  │                                                                             
  │  │  │  Layer4: Long-term Memory (system.py:324-331)               │  │  │                                                                             
  │  │  │   ├── user_profile: dict ← long_term_memory.                │  │  │                                                                             
  │  │  │   │                          get_user_profile()              │  │  │                                                                            
  │  │  │   └── learned_patterns: list ← long_term_memory.            │  │  │                                                                             
  │  │  │                                get_learning_records()        │  │  │                                                                            
  │  │  └─────────────────────────────────────────────────────────────┘  │  │                                                                             
  │  └───────────────────────────────────────────────────────────────────┘  │                                                                             
  │                                    │                                    │                                                                             
  │                                    ▼                                    │                                                                             
  │  ┌───────────────────────────────────────────────────────────────────┐  │                                                                             
  │  │  步骤 2: 构建 System Prompt (engine.py:102)                       │  │                                                                             
  │  │  build_system_prompt(extra_context=memory_context)                │  │                                                                             
  │  │  ┌─────────────────────────────────────────────────────────────┐  │  │                                                                             
  │  │  │ 1. IDENTITY.md — Agent 身份                                  │  │  │                                                                            
  │  │  │ 2. SOUL.md — 性格、价值观、红线                               │  │  │                                                                           
  │  │  │ 3. AGENTS.md — 行为准则(意图理解、工具使用、记忆管理)         │  │  │                                                                           
  │  │  │ 4. TOOLS.md — 环境配置                                       │  │  │                                                                            
  │  │  │ 5. USER.md  模板渲染 → 用户画像                             │  │  │                                                                             
  │  │  │ 6. MEMORY.md 模板渲染 → 重要决策/用户偏好/学到的教训         │  │  │                                                                            
  │  │  │ 7. 运行时上下文:                                             │  │  │                                                                            
  │  │  │    ├── 可用技能列表 (Layer1)                                  │  │  │                                                                           
  │  │  │    ├── 可用工具列表 (Layer1)                                  │  │  │                                                                           
  │  │  │    ├── 会话偏好 (Layer3)                                      │  │  │                                                                           
  │  │  │    ├── 当前项目 (Layer3)                                      │  │  │                                                                           
  │  │  │    ├── 上传文件信息                                           │  │  │                                                                           
  │  │  │    └── 对话上下文摘要 (Layer2)                                │  │  │                                                                           
  │  │  │ 8. 最终指令(调用工具规则/中文/文件处理)                       │  │  │                                                                           
  │  │  └─────────────────────────────────────────────────────────────┘  │  │                                                                             
  │  └───────────────────────────────────────────────────────────────────┘  │                                                                             
  │                                    │                                    │                                                                             
  │                                    ▼                                    │                                                                             
  │  ┌───────────────────────────────────────────────────────────────────┐  │                                                                             
  │  │  步骤 3: 构建 messages 列表 (engine.py:105-117)                   │  │                                                                             
  │  │  system prompt + 历史消息(最近8条) + 用户当前消息                  │  │                                                                            
  │  └───────────────────────────────────────────────────────────────────┘  │                                                                             
  │                                    │                                    │                                                                             
  │                                    ▼                                    │                                                                             
  │  ┌───────────────────────────────────────────────────────────────────┐  │                                                                             
  │  │  步骤 4: 构建工具定义 (engine.py:120, 292-416)                    │  │                                                                             
  │  │  5 个 Meta-Tool（OpenAI function calling 格式）:                  │  │                                                                             
  │  │  ├── execute_skill        → 子进程执行技能                        │  │                                                                             
  │  │  ├── call_mcp_tool        → MCP 服务器工具调用                    │  │                                                                             
  │  │  ├── call_builtin_tool    → 内置文档处理工具调用                  │  │                                                                             
  │  │  ├── memory_search        → 关键词匹配搜索长期记忆                │  │                                                                             
  │  │  └── memory_save          → 保存到长期记忆                        │  │                                                                             
  │  └───────────────────────────────────────────────────────────────────┘  │                                                                             
  │                                    │                                    │                                                                             
  │                                    ▼                                    │                                                                             
  │  ┌───────────────────────────────────────────────────────────────────┐  │                                                                             
  │  │  步骤 5: LLM + Tool Calling 循环 (engine.py:122-241)             │  │                                                                              
  │  │                                                                   │  │                                                                             
  │  │  ┌──────────┐    ┌───────────────┐    ┌───────────────┐          │  │                                                                              
  │  │  │ LLM 推理  │───→│ 有 tool_call? │    │   无 tool_call          │  │                                                                              
  │  │  │(round N) │    │               │    │              │          │  │                                                                               
  │  │  └──────────┘    └───┬───┬───────┘    └──────┬────────┘          │  │                                                                              
  │  │                      │   │                   │                    │  │                                                                             
  │  │                      │   │                   ▼                    │  │                                                                             
  │  │                      │   │     ┌──────────────────────────┐       │  │                                                                             
  │  │                      │   │     │ yield AgentResponse(text) │       │  │                                                                            
  │  │                      │   │     │ yield AgentResponse(      │       │  │                                                                            
  │  │                      │   │     │   complete)               │       │  │                                                                            
  │  │                      │   │     │  → 跳转步骤 6              │       │  │                                                                           
  │  │                      │   │     └──────────────────────────┘       │  │                                                                             
  │  │                      │   │                                        │  │                                                                             
  │  │              ┌───────┘   └──────────┐                              │  │                                                                            
  │  │              ▼                       ▼                              │  │                                                                           
  │  │  ┌──────────────────┐   ┌────────────────────┐                     │  │                                                                            
  │  │  │ 添加 assistant   │   │ 执行工具调用:       │                     │  │                                                                           
  │  │  │ tool_calls 消息  │   │ _execute_function_ │                     │  │                                                                            
  │  │  └──────────────────┘   │ call()              │                     │  │                                                                           
  │  │                         │                     │                     │  │                                                                           
  │  │                         │ 追踪 tracked_skill, │                     │  │                                                                           
  │  │                         │ tracked_tool,       │                     │  │                                                                           
  │  │                         │ tracked_entities    │                     │  │                                                                           
  │  │                         │  → 构建 Intent      │                     │  │                                                                           
  │  │                         └──────────┬──────────┘                     │  │                                                                           
  │  │                                    │                                │  │                                                                           
  │  │                                    ▼                                │  │                                                                           
  │  │                         ┌────────────────────┐                     │  │                                                                            
  │  │                         │ 添加 tool result   │                     │  │                                                                            
  │  │                         │ 到 messages        │                     │  │                                                                            
  │  │                         │ → 回到 LLM 推理    │                     │  │                                                                            
  │  │                         │ (下一轮)            │                     │  │                                                                           
  │  │                         └────────────────────┘                     │  │                                                                            
  │  └───────────────────────────────────────────────────────────────────┘  │                                                                             
  │                                    │                                    │                                                                             
  │                                    ▼                                    │                                                                             
  │  ┌───────────────────────────────────────────────────────────────────┐  │                                                                             
  │  │  步骤 6: 更新记忆 (engine.py:153-173)                             │  │                                                                             
  │  │  memory_system.update_from_execution(                             │  │                                                                             
  │  │    db, conversation_id, user_id, session_id,                      │  │                                                                             
  │  │    message, result=final_content, intent=intent_obj               │  │                                                                             
  │  │  )                                                                │  │                                                                             
  │  │  ┌─────────────────────────────────────────────────────────────┐  │  │                                                                             
  │  │  │ Layer2: Working Memory                                       │  │  │                                                                            
  │  │  │  ├── 添加 user message 到滑动窗口                             │  │  │                                                                           
  │  │  │  ├── 添加 assistant 回复 (card/text)                          │  │  │                                                                           
  │  │  │  └── 提取 Intent 中的实体和 skill 到 wm.entities/context_vars │  │  │                                                                           
  │  │  │                                                               │  │  │                                                                           
  │  │  │ Layer3: Session Memory                                        │  │  │                                                                           
  │  │  │  ├── 缓存 skill 结果到 sm.skill_results                       │  │  │                                                                           
  │  │  │  └── 写 DB (session_memories 表)                               │  │  │                                                                          
  │  │  │                                                               │  │  │                                                                           
  │  │  │ Layer4: Long-term Memory                                      │  │  │                                                                           
  │  │  │  ├── 更新 user_profile.last_active                            │  │  │                                                                           
  │  │  │  └── 若有 skill_name → 添加/更新 preferred_skills             │  │  │                                                                           
  │  │  └─────────────────────────────────────────────────────────────┘  │  │                                                                             
  │  └───────────────────────────────────────────────────────────────────┘  │                                                                             
  └─────────────────────────────────────────────────────────────────────────┘                                                                             
                                                                                                                                                          
  ---                                                                                                                                                     
  三、按模块拆分的详细交互关系                                                                                                                            
                                                                                                                                                          
  3.1 模块依赖图  
                                                                                                                                                          
  chat.py (API 层)
    ├── agent_engine (engine.py)                                                                                                                          
    │     ├── memory_system (system.py)                                                                                                                   
    │     │     ├── SystemMemory      (仅在内存)                                                                                                          
    │     │     ├── WorkingMemory     (内存 + messages 表恢复)                                                                                            
    │     │     ├── SessionMemory     (内存 + session_memories 表)                                                                                        
    │     │     └── LongTermMemory    (user_profiles + learning_records + project_memories)                                                               
    │     ├── llm_router (router.py)                                                                                                                      
    │     │     └── llm_client (client.py) → OpenAI SDK → 外部 LLM                                                                                        
    │     ├── action_executor (executor.py)                                                                                                               
    │     │     ├── skill_registry (进程内 handler)                                                                                                       
    │     │     ├── mcp_clients (MCP 客户端)                                                                                                              
    │     │     └── tool_registry (内置工具)                                                                                                              
    │     └── build_system_prompt (agent_context/__init__.py)                                                                                             
    │           └── IDENTITY.md / SOUL.md / AGENTS.md / TOOLS.md / USER.md / MEMORY.md                                                                    
    │                                                                                                                                                     
    ├── crud/conversation.py                                                                                                                              
    │     └── conversations 表 + messages 表                                                                                                              
    └── StreamingResponse → SSE → 前端                                                                                                                    
                                                                                                                                                          
  memory_save/memory_search (engine.py:473-579)                                                                                                           
    └── memory.long_term_memory                                                                                                                           
          ├── update_user_profile()                                                                                                                       
          ├── add_learning_record()                                                                                                                       
          └── get_learning_records() / get_user_profile()                                                                                                 
                                                                                                                                                          
  3.2 数据表完整映射                                                                                                                                      
                                                                                                                                                          
  ┌──────────────────┬────────────┬───────────────────────────┬─────────────────────────────────┐                                                         
  │        表        │ 写入什么层 │         写入周期          │             写入点              │
  ├──────────────────┼────────────┼───────────────────────────┼─────────────────────────────────┤                                                         
  │ user_profiles    │ Layer 4    │ 每次交互                  │ system.py:386-397 + memory_save │
  ├──────────────────┼────────────┼───────────────────────────┼─────────────────────────────────┤                                                         
  │ learning_records │ Layer 4    │ 仅当 LLM 调用 memory_save │ engine.py:536-562               │                                                         
  ├──────────────────┼────────────┼───────────────────────────┼─────────────────────────────────┤                                                         
  │ session_memories │ Layer 3    │ 每次交互                  │ system.py:371-382               │                                                         
  ├──────────────────┼────────────┼───────────────────────────┼─────────────────────────────────┤                                                         
  │ messages         │ 会话历史   │ 每次交互                  │ chat.py:94-112                  │
  ├──────────────────┼────────────┼───────────────────────────┼─────────────────────────────────┤                                                         
  │ conversations    │ 会话历史   │ 首次交互                  │ chat.py:88-90                   │
  └──────────────────┴────────────┴───────────────────────────┴─────────────────────────────────┘                                                         
                  
  3.3 上下文注入量统计                                                                                                                                    
                  
  System Prompt 组成部分                  字符量(约)  来源                                                                                                
  ─────────────────────────────────────  ────────  ──────────────────                                                                                     
  IDENTITY.md                               ~500   文件静态                                                                                               
  SOUL.md                                   ~500   文件静态                                                                                               
  AGENTS.md                                ~1500   文件静态                                                                                               
  TOOLS.md                                  ~400   文件静态                                                                                               
  USER.md (模板渲染)                         ~200   DB → 模板变量                                                                                         
  MEMORY.md (模板渲染)                       ~500   DB → 模板变量                                                                                         
  运行时上下文                                不定   memory.get_context()                                                                                 
    可用技能描述                              ~N*50  注册的技能数                                                                                         
    可用工具描述                              ~N*50  注册的工具数                                                                                         
    会话偏好                                 不定   用户设置                                                                                              
    当前项目                                 不定   会话状态                                                                                              
    文件信息                                 不定   上传文件                                                                                              
    对话上下文摘要                            不定   WorkingMemory 压缩                                                                                   
  最终指令                                  ~800   硬编码                                                                                                 
  ─────────────────────────────────────  ────────                                                                                                         
  总计                                     ~5000+  随技能/记录增长                                                                                        
                                                                                                                                                          
  3.4 关键数据流路径                                                                                                                                      
                                                                                                                                                          
  路径 A — 单步 Jira 查询（典型短路径）                                                                                                                   
  用户: "查 TV-XXXXXX 状态"
    → chat.py: agent_engine.process_message("查 TV-XXXXXX 状态")                                                                                          
    → engine.py step1: memory_system.get_context()  → 构建 context dict                                                                                   
    → engine.py step2: build_system_prompt(context) → 构建 system prompt                                                                                  
    → engine.py step3: [system prompt, user message]                                                                                                      
    → engine.py step4: 5 个工具定义                                                                                                                       
    → engine.py step5 第1轮: LLM 推理 → 返回 tool_call: call_mcp_tool(jira, search_issues, {jql: "key=TV-XXXXXX"})                                        
    → engine.py step5: 添加 assistant(tool_calls) 消息                                                                                                    
    → engine.py step5: _execute_function_call("call_mcp_tool", {server:"jira", tool:"search_issues", params:{jql:"key=TV-XXXXXX"}})                       
    → executor.py: _execute_tool("jira/search_issues", ...) → MCP 客户端调用                                                                              
    → engine.py step5: 标记 tracked_tool = "jira/search_issues", tracked_entities = {"jql": "key=TV-XXXXXX"}                                              
    → engine.py step5: 添加 tool result 消息                                                                                                              
    → engine.py step5 第2轮: LLM 推理 → 无 tool_call → 直接回复                                                                                           
    → engine.py step5: yield text + complete                                                                                                              
    → engine.py step6: 构建 Intent(category="query", target_tool="jira/search_issues", entities={jql:...})                                                
    → engine.py step6: memory_system.update_from_execution(intent)                                                                                        
      → Layer2: wm.add_message("user", "查 TV-XXXXXX 状态")                                                                                               
      → Layer2: wm.add_message("assistant", "TV-XXXXXX 当前状态为...")                                                                                    
      → Layer2: wm.add_entity("jql", "key=TV-XXXXXX")                                                                                                     
      → Layer2: wm.set_context_var("last_skill", null)                                                                                                    
      → Layer3: 不缓存（无 skill）                                                                                                                        
      → Layer3: 写 session_memories 表                                                                                                                    
      → Layer4: 更新 user_profile.last_active                                                                                                             
    → chat.py: yield SSE 事件 (text + complete)                                                                                                           
    → chat.py: save user message to messages 表                                                                                                           
    → chat.py: save assistant response to messages 表                                                                                                     
                                                                                                                                                          
  路径 B — 技能执行（批量操作路径）                                                                                                                       
  用户: "汇总 jira 报告"                                                                                                                                  
    → ...同上直到 LLM 第1轮 → 返回 tool_call: execute_skill(jira-confluence-report, {...})
    → engine.py: 标记 tracked_skill = "jira-confluence-report"                                                                                            
    → engine.py: _execute_function_call("execute_skill", {skill_name:"jira-confluence-report", params:{...}})                                             
    → executor.py: _execute_skill("jira-confluence-report", ...) → skill_registry["jira-confluence-report"](params)                                       
    → skill 子进程执行 → 返回结果                                                                                                                         
    → engine.py: 添加 tool result 消息                                                                                                                    
    → LLM 第2轮: 推理结果 → 无 tool_call → 直接回复                                                                                                       
    → engine.py step6: Intent(category="execute", target_skill="jira-confluence-report")                                                                  
      → Layer2: 同路径 A                                                                                                                                  
      → Layer3: sm.cache_skill_result("jira-confluence-report", result)                                                                                   
      → Layer3: 写 session_memories 表                                                                                                                    
      → Layer4: 更新 last_active + preferred_skills += "jira-confluence-report"                                                                           
                  
  ---
  四、当前架构中的关键问题记录（从数据流角度）
                                                                                                                                                          
  问题 1：LLM 回复保存有重复
                                                                                                                                                          
  chat.py:105-112 在 SSE yield 后保存 assistant 消息，但 engine.py:150 的 yield AgentResponse(type="text") 在 update_from_execution 之前。这意味着：      
  - 如果 update_from_execution 中的 DB 写失败，消息已保存但记忆未更新 → 不一致
  - messages 表中的记录和 WorkingMemory 中的记录可能不同步                                                                                                
                                                          
  问题 2：WorkingMemory 与 DB 的双写不一致                                                                                                                
                                                                                                                                                          
  同一轮交互中：                                                                                                                                          
  1. engine.py:351-356 → wm.add_message("assistant", ...) — 写入内存滑动窗口                                                                              
  2. chat.py:105-112 → create_message(db, ...) — 写入 DB messages 表                                                                                      
                                                                    
  但它们是独立的写操作，没有事务保证。如果重启后从 DB 恢复（system.py:287-293），只恢复最近 10 条，可能会跳过压缩摘要中的内容。                           
                                                                                                                                                          
  问题 3：memory_save 只能从 LLM 工具调用触发                                                                                                             
                                                                                                                                                          
  数据流中没有任何自动的记忆提取/保存逻辑。memory_save 完全依赖 LLM 判断"这个信息值得保存"。没有系统级策略（如：每 N 轮自动总结一次关键决策）。           
                  
  问题 4：Layer 4 的 preferred_skills 持续追加                                                                                                            
                  
  crud/memory.py:37-40 — preferred_skills 是 list append 语义。每次调用 skill 都会追加，但没有去重，也没有上限控制。多次运行同一个 skill                  
  会导致列表无限增长。
                                                                                                                                                          
  问题 5：memory_search 是纯线性、纯关键词、无索引                                                                                                        
   
  数据流路径中 _search_memory() 使用的是 query_lower in pattern.lower()                                                                                   
  子串匹配——完全无法利用数据库索引，且没有语义理解能力。这是当前架构中最大的性能/质量短板。
                                                                                                                                                          
  问题 6：Memory 更新是"端后"而非"实时"                                                                                                                   
   
  update_from_execution() 只在整个 LLM 循环结束后调用一次。如果循环有 5 轮工具调用，在前 4 轮中 WorkingMemory 不会更新（不会添加 tool result              
  到滑动窗口）。只有最后的结果被记录。
                                                                                                                                                          
  问题 7：session_memories.expires_at 定义了字段但从未写入                                                                                                
   
  模型定义 SessionMemory 有 expires_at 字段，但 system.py 和 crud/memory.py 的 save_session_memory() 从未设置该字段。这意味着会话记忆永不过期。           
                  