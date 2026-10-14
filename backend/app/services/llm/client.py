"""
LLM Client - Unified interface for both commercial and local LLMs.

Both commercial (DeepSeek, Kimi) and local (company internal) LLMs
use OpenAI-compatible API format.
"""

import json
import asyncio
from typing import AsyncGenerator, Optional, List, Dict, Any
from openai import AsyncOpenAI
from app.core.config import settings
from app.core.exceptions import LLMException


class LLMClient:
    """Unified LLM client supporting multiple providers."""
    
    def __init__(self):
        # Commercial LLM client
        self._commercial_client: Optional[AsyncOpenAI] = None
        if settings.COMMERCIAL_LLM_API_KEY:
            self._commercial_client = AsyncOpenAI(
                api_key=settings.COMMERCIAL_LLM_API_KEY,
                base_url=settings.COMMERCIAL_LLM_API_BASE,
            )
        
        # Local LLM client (company internal deployment)
        self._local_client: Optional[AsyncOpenAI] = None
        if settings.LOCAL_LLM_API_BASE:
            self._local_client = AsyncOpenAI(
                api_key=settings.LOCAL_LLM_API_KEY or "not-needed",
                base_url=settings.LOCAL_LLM_API_BASE,
            )
    
    @property
    def commercial_client(self) -> AsyncOpenAI:
        """Get commercial LLM client."""
        if not self._commercial_client:
            raise LLMException(
                "Commercial LLM not configured",
                provider=settings.COMMERCIAL_LLM_PROVIDER
            )
        return self._commercial_client
    
    @property
    def local_client(self) -> AsyncOpenAI:
        """Get local LLM client."""
        if not self._local_client:
            raise LLMException(
                "Local LLM not configured. Please set LOCAL_LLM_API_BASE.",
                provider="local"
            )
        return self._local_client
    
    @staticmethod
    def _is_context_overflow_error(e: Exception) -> bool:
        """判断异常是否为"上下文超出模型窗口"类错误（层 B/C，2026-09-08）。

        各厂商报错文案不同，保守匹配特征串；误判风险由 engine 的压缩重试兜底
        （压缩后重试仍失败才升级为通用错误）。触发 engine 的上下文压缩恢复流程。
        """
        text = f"{type(e).__name__} {str(e)}".lower()
        keywords = (
            "context_length_exceeded",
            "maximum context length",
            "reduce the length of the messages",
            "reduce the length",
            "too many tokens",
            "token limit",
            "request entity too large",
            "context window",
            "exceeds the maximum",
            "prompt is too long",
            "message too long",
            "invalid_request_error",  # OpenAI 400 类（部分 provider 用它包装 overflow）
        )
        if any(k in text for k in keywords):
            return True
        # OpenAI SDK 的 BadRequestError + 400 状态码，文案含 length/context/token 时判定
        status = getattr(e, "status_code", None)
        if status == 400 and any(k in text for k in ("length", "context", "token")):
            return True
        return False

    @staticmethod
    def _format_llm_error(e: Exception, model_type: str) -> str:
        """Convert an LLM-call exception to a user-friendly Chinese error message.

        Handles connection errors, auth errors, model-not-found, and timeouts
        — cases that happen before the API returns a response (so _validate_response
        never fires).
        """
        error_str = str(e).lower()
        label = "商用" if model_type == "commercial" else "本地"

        if isinstance(e, ConnectionError) or any(k in error_str for k in ("connection", "connect", "dns", "resolve", "refused")):
            return f"无法连接到{label}大模型，请检查API地址（api_base）配置是否正确"
        if isinstance(e, TimeoutError) or "timeout" in error_str:
            return f"{label}大模型请求超时，请检查网络连接或API地址配置"
        if any(k in error_str for k in ("authentication", "unauthorized", "api key", "invalid key", "auth")):
            return f"{label}大模型认证失败，请检查API Key配置是否正确"
        if any(k in error_str for k in ("model not found", "model does not exist", "not support", "model name")):
            return f"模型名称不存在或不支持此操作，请检查{label}大模型的模型名称配置"
        if any(k in error_str for k in ("bad gateway", "502", "503", "service unavailable", "server error")):
            return f"{label}大模型服务暂时不可用，请稍后重试"
        return f"{label}大模型调用失败，请检查大模型配置是否正确"

    @staticmethod
    def _validate_response(response: Any, provider: str, model_name: str) -> None:
        """Check that the API response is a valid object with choices.

        Raises LLMException with a descriptive message if the response
        is a string (HTML page, plain-text error, etc.) instead of the
        expected OpenAI response format.
        """
        if isinstance(response, str):
            snippet = response[:200].replace("\n", " ")
            if response.strip().startswith("<"):
                hint = (
                    f"LLM 接口返回了 HTML 页面（非 JSON）。请检查 {provider} 大模型的 "
                    f"API 地址是否正确，以及服务是否正常启动。"
                )
            else:
                hint = (
                    f"LLM 接口返回了非预期的响应。请检查 {provider} 大模型的 "
                    f"配置是否正确。"
                )
            raise LLMException(
                f"{hint} 响应内容: {snippet}",
                provider=provider,
                details={"model": model_name, "response_type": "string", "preview": snippet},
            )

    async def complete(
        self,
        messages: List[Dict[str, str]],
        model_type: str = "commercial",
        model: Optional[str] = None,
        temperature: float = 0.7,
        max_tokens: Optional[int] = None,
        stream: bool = False,
        json_mode: bool = False,
        api_key: Optional[str] = None,
        api_base: Optional[str] = None,
    ) -> str:
        """
        Send a completion request to LLM.

        Args:
            messages: List of message dicts with 'role' and 'content'
            model_type: "commercial" or "local"
            model: Specific model name (uses default if None)
            temperature: Sampling temperature
            max_tokens: Maximum tokens to generate
            stream: Whether to stream the response
            json_mode: Whether to request JSON output
            api_key: Per-user API key override (uses settings default if None)
            api_base: Per-user API base URL override

        Returns:
            Generated text response
        """
        client = await self._get_client(model_type, api_key=api_key, api_base=api_base)

        # When per-user api_key is provided, model must come from user config —
        # do NOT fall back to .env defaults (which may be empty or wrong provider).
        if model:
            model_name = model
        elif api_key:
            raise LLMException(
                f"模型名称未配置。请在设置中为 {'商用' if model_type == 'commercial' else '本地'}大模型配置模型名称。",
                provider=model_type,
            )
        else:
            model_name = (
                settings.COMMERCIAL_LLM_MODEL if model_type == "commercial"
                else settings.LOCAL_LLM_MODEL
            )

        try:
            kwargs = {
                "model": model_name,
                "messages": messages,
                "temperature": temperature,
                "stream": stream,
            }
            if max_tokens:
                kwargs["max_tokens"] = max_tokens
            if json_mode:
                kwargs["response_format"] = {"type": "json_object"}

            if stream:
                return await self._stream_completion(client, kwargs)

            response = await client.chat.completions.create(**kwargs)
            self._validate_response(response, model_type, model_name)
            return response.choices[0].message.content or ""

        except Exception as e:
            hint = self._format_llm_error(e, model_type)
            details: Dict[str, Any] = {"model": model_name}
            # 层 B/C：上下文溢出类错误打 overflow 标，engine 据此刻触发压缩恢复而非直接报错
            if self._is_context_overflow_error(e):
                details["overflow"] = True
            raise LLMException(
                f"{hint}。原始错误: {str(e)[:200]}",
                provider=model_type,
                details=details
            )

    async def _stream_completion(self, client: AsyncOpenAI, kwargs: dict) -> str:
        """Handle streaming completion."""
        full_response = []
        response = await client.chat.completions.create(**kwargs)
        if isinstance(response, str):
            snippet = response[:200].replace("\n", " ")
            raise LLMException(
                f"LLM 流式接口返回了非预期响应，请检查大模型配置。响应内容: {snippet}",
                provider="unknown",
            )
        async for chunk in response:
            if chunk.choices[0].delta.content:
                full_response.append(chunk.choices[0].delta.content)
        return "".join(full_response)
    
    async def _get_client(self, model_type: str, *,
                          api_key: Optional[str] = None,
                          api_base: Optional[str] = None) -> AsyncOpenAI:
        """Get or create an LLM client, optionally with per-user credentials.

        When api_key/api_base are provided, creates a temporary client.
        Otherwise returns the shared default client.
        """
        if api_key or api_base:
            return AsyncOpenAI(
                api_key=api_key or (settings.COMMERCIAL_LLM_API_KEY if model_type == "commercial"
                                    else settings.LOCAL_LLM_API_KEY or "not-needed"),
                base_url=api_base or (settings.COMMERCIAL_LLM_API_BASE if model_type == "commercial"
                                      else settings.LOCAL_LLM_API_BASE),
            )
        return self.commercial_client if model_type == "commercial" else self.local_client

    async def complete_with_tools(
        self,
        messages: List[Dict[str, str]],
        tools: List[Dict],
        model_type: str = "commercial",
        model: Optional[str] = None,
        tool_choice: Optional[str] = None,
        temperature: float = 0.7,
        max_tokens: Optional[int] = None,
        api_key: Optional[str] = None,
        api_base: Optional[str] = None,
    ) -> tuple[Optional[str], Optional[List[Dict]]]:
        """
        Send a completion request with tool calling support.

        Args:
            messages: List of message dicts with 'role' and 'content'
            tools: List of tool definitions in OpenAI function calling format
            model_type: "commercial" or "local"
            model: Specific model name (uses default if None)
            tool_choice: "auto" (default), "required", "none", or {"type": "function", "function": {"name": "..."}}
            temperature: Sampling temperature
            max_tokens: Maximum tokens to generate
            api_key: Per-user API key override
            api_base: Per-user API base URL override

        Returns:
            Tuple of (content: str | None, tool_calls: list[dict] | None)
        """
        client = await self._get_client(model_type, api_key=api_key, api_base=api_base)

        # When per-user api_key is provided, model must come from user config —
        # do NOT fall back to .env defaults.
        if model:
            model_name = model
        elif api_key:
            raise LLMException(
                f"模型名称未配置。请在设置中为 {'商用' if model_type == 'commercial' else '本地'}大模型配置模型名称。",
                provider=model_type,
            )
        else:
            model_name = (
                settings.COMMERCIAL_LLM_MODEL if model_type == "commercial"
                else settings.LOCAL_LLM_MODEL
            )

        try:
            kwargs = {
                "model": model_name,
                "messages": messages,
                "tools": tools,
                "temperature": temperature,
            }
            if max_tokens:
                kwargs["max_tokens"] = max_tokens
            if tool_choice:
                kwargs["tool_choice"] = tool_choice

            response = await client.chat.completions.create(**kwargs)
            self._validate_response(response, model_type, model_name)
            choice = response.choices[0].message

            # Extract content
            content = choice.content

            # Extract tool calls
            tool_calls = None
            if choice.tool_calls:
                tool_calls = [
                    {
                        "id": tc.id,
                        "type": tc.type,
                        "function": {
                            "name": tc.function.name,
                            "arguments": tc.function.arguments,
                        },
                    }
                    for tc in choice.tool_calls
                ]

            return content, tool_calls

        except Exception as e:
            hint = self._format_llm_error(e, model_type)
            details: Dict[str, Any] = {"model": model_name, "tool_count": len(tools)}
            # 层 B/C：上下文溢出类错误打 overflow 标，engine 据此刻触发压缩恢复而非直接报错
            if self._is_context_overflow_error(e):
                details["overflow"] = True
            raise LLMException(
                f"{hint}。原始错误: {str(e)[:200]}",
                provider=model_type,
                details=details,
            )

    async def complete_structured(
        self,
        messages: List[Dict[str, str]],
        model_type: str = "commercial",
        model: Optional[str] = None,
        schema: Optional[Dict[str, Any]] = None,
        api_key: Optional[str] = None,
        api_base: Optional[str] = None,
    ) -> Dict[str, Any]:
        """
        Get structured (JSON) response from LLM.

        Args:
            messages: List of message dicts
            model_type: "commercial" or "local"
            model: Specific model name override
            schema: JSON schema for response validation
            api_key: Per-user API key override
            api_base: Per-user API base URL override

        Returns:
            Parsed JSON response as dict
        """
        # Add JSON instruction to system message
        json_instruction = "You must respond with valid JSON only. No markdown, no explanations."

        # Check if first message is system
        if messages and messages[0].get("role") == "system":
            messages[0]["content"] = messages[0]["content"] + "\n\n" + json_instruction
        else:
            messages.insert(0, {"role": "system", "content": json_instruction})

        response_text = await self.complete(
            messages=messages,
            model_type=model_type,
            model=model,
            json_mode=True,
            api_key=api_key,
            api_base=api_base,
        )
        
        try:
            return json.loads(response_text)
        except json.JSONDecodeError as e:
            raise LLMException(
                f"Failed to parse LLM response as JSON: {str(e)}",
                provider=model_type,
                details={"response": response_text[:500]}
            )
    
    async def embed(self, texts: List[str], model_type: str = "local",
                    api_key: Optional[str] = None, api_base: Optional[str] = None) -> List[List[float]]:
        """
        Get embeddings for texts.

        Args:
            texts: List of texts to embed
            model_type: "commercial" or "local"
            api_key: Per-user API key override
            api_base: Per-user API base URL override

        Returns:
            List of embedding vectors
        """
        client = await self._get_client(model_type, api_key=api_key, api_base=api_base)
        
        try:
            # Try to use embedding endpoint
            response = await client.embeddings.create(
                model="text-embedding-ada-002",  # Default embedding model
                input=texts,
            )
            if isinstance(response, str):
                raise LLMException(
                    "Embedding 接口返回了非预期响应，请检查大模型配置。",
                    provider=model_type,
                )
            return [item.embedding for item in response.data]
        except Exception as e:
            # Fallback: generate embeddings using completion
            hint = self._format_llm_error(e, model_type)
            raise LLMException(
                f"{hint}。原始错误: {str(e)[:200]}",
                provider=model_type
            )


# Global LLM client instance
llm_client = LLMClient()
