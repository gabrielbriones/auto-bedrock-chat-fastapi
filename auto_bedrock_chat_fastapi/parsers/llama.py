"""Llama model parser with tool calling support"""

import json
import logging
import re
from typing import Any, Dict, List, Optional

from .base import Parser

logger = logging.getLogger(__name__)


class LlamaParser(Parser):
    """Parser for Meta Llama models with XML-based tool calling"""

    def parse_response(self, response: Dict[str, Any]) -> Dict[str, Any]:
        """
        Parse Llama model response with tool call support

        Args:
            response: Raw response from Llama API

        Returns:
            Dict with keys: content, tool_calls, metadata
        """
        generation = response.get("generation", "").lstrip()

        # Extract tool calls from Llama's response (uses <tool_call> tags)
        tool_calls = []
        content = generation

        # Look for tool calls in format: <tool_call>function_name({"args"})</tool_call>
        tool_pattern = r'<tool_call>([\w_]+)\((.*?)\)</tool_call>'
        matches = re.findall(tool_pattern, generation, re.DOTALL)

        if matches:
            # Extract content before first tool call (if any text before tool calls)
            first_tool_pos = generation.find('<tool_call>')
            if first_tool_pos > 0:
                # There's text before the tool calls - keep just that as readable content
                content = generation[:first_tool_pos].strip()
            else:
                # No text before tool calls - keep the FULL generation including tool calls
                # This is important so Llama can see what it requested in the next turn
                content = generation

            # Parse tool calls
            for func_name, args_str in matches:
                try:
                    # Parse arguments as JSON
                    args = json.loads(args_str) if args_str.strip() else {}
                    tool_calls.append({
                        "id": f"llama-tool-{len(tool_calls)}",
                        "name": func_name,
                        "arguments": args
                    })
                except json.JSONDecodeError:
                    logger.warning(f"Failed to parse tool arguments for {func_name}: {args_str}")

        return {
            "content": content,
            "tool_calls": tool_calls,
            "metadata": {
                "generation_token_count": response.get("generation_token_count"),
                "prompt_token_count": response.get("prompt_token_count"),
                "stop_reason": response.get("stop_reason"),
            },
        }

    def format_bedrock_messages(self, messages: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        """
        Format messages for Llama API.
        Llama uses similar message format to OpenAI, but with XML-based tool calling.

        Args:
            messages: List of message dicts (from session/context)

        Returns:
            List of messages formatted for Llama API
        """
        bedrock_messages = []

        for msg in messages:
            role = msg.get("role")
            content = msg.get("content")
            tool_calls = msg.get("tool_calls", [])
            tool_results = msg.get("tool_results", [])

            if role in ["user", "system"]:
                # Simple messages: just role + content
                bedrock_messages.append({"role": role, "content": content})

            elif role == "assistant":
                # Assistant messages: may contain tool call instructions in content
                # For Llama, tool calls are typically included as text instructions in the response
                bedrock_messages.append({"role": "assistant", "content": content})

            elif role == "tool" and tool_results:
                # Add individual tool result messages
                # Get the corresponding tool_calls to know which function was called
                for i, tool_result in enumerate(tool_results):
                    # For Llama, tool results are added as user messages
                    # Mark them with metadata so truncation can identify them
                    tool_call_id = tool_result.get("tool_call_id") or tool_result.get("tool_use_id") or f"tool-result-{i}"
                    
                    # Get the function name from tool_calls if available
                    func_name = "unknown"
                    if i < len(tool_calls):
                        func_name = tool_calls[i].get("name", "unknown")
                    
                    if "error" in tool_result:
                        result_content = f"[Tool Result for {func_name}({tool_call_id})]\nError: {tool_result['error']}"
                    else:
                        result_data = str(tool_result.get("result", "No result"))
                        # Add context header so Llama knows this is a tool response
                        result_content = f"[Tool Result for {func_name}({tool_call_id})]\n{result_data}"

                    bedrock_messages.append({
                        "role": "user",
                        "content": result_content,
                        # Mark this as a tool result for truncation detection
                        "is_tool_result": True,
                        "tool_call_id": tool_call_id,
                    })

        return bedrock_messages

    def format_messages(
        self,
        messages: List[Dict[str, Any]],
        tools_desc: Optional[Dict] = None,
        temperature: Optional[float] = None,
        max_tokens: Optional[int] = None,
        **kwargs
    ) -> Dict[str, Any]:
        """
        Format messages for Llama API using proper prompt format with tool support

        Args:
            messages: List of conversation messages
            tools_desc: Tool/function descriptions (optional)
            temperature: Sampling temperature (optional)
            max_tokens: Maximum tokens in response (optional)
            **kwargs: Additional parameters

        Returns:
            Request body dict for Llama API
        """
        # Convert messages to Llama's prompt format with special tokens
        prompt_parts = ["<|begin_of_text|>"]

        # Check if first message is system prompt
        start_idx = 0
        if messages and messages[0].get("role") == "system":
            system_content = messages[0]["content"]
            # Add tool definitions to system prompt if tools are available
            if tools_desc:
                system_content += self._format_tools_for_llama(tools_desc)
            prompt_parts.extend(
                [
                    "<|start_header_id|>system<|end_header_id|>",
                    f"\n{system_content}<|eot_id|>",
                ]
            )
            start_idx = 1
        else:
            # Add default system message if none present
            system_prompt = self.config.get_system_prompt() if self.config else "You are a helpful assistant."
            if tools_desc:
                system_prompt += self._format_tools_for_llama(tools_desc)
            prompt_parts.extend(
                [
                    "<|start_header_id|>system<|end_header_id|>",
                    f"\n{system_prompt}<|eot_id|>",
                ]
            )

        # Add conversation messages
        for msg in messages[start_idx:]:
            role = msg["role"]
            content = msg["content"]

            if role == "user":
                prompt_parts.extend(
                    [
                        "<|start_header_id|>user<|end_header_id|>",
                        f"\n{content}<|eot_id|>",
                    ]
                )
            elif role == "assistant":
                prompt_parts.extend(
                    [
                        "<|start_header_id|>assistant<|end_header_id|>",
                        f"\n{content}<|eot_id|>",
                    ]
                )
            elif role == "tool":
                # Format tool results for Llama
                prompt_parts.extend(
                    [
                        "<|start_header_id|>user<|end_header_id|>",
                        f"\n<tool_result>\n{content}\n</tool_result><|eot_id|>",
                    ]
                )

        # End with assistant header for completion
        prompt_parts.append("<|start_header_id|>assistant<|end_header_id|>")

        formatted_prompt = "".join(prompt_parts)

        return {
            "prompt": formatted_prompt,
            "max_gen_len": max_tokens if max_tokens is not None else (self.config.max_tokens if self.config else 4096),
            "temperature": temperature if temperature is not None else (self.config.temperature if self.config else 0.7),
            "top_p": kwargs.get("top_p") or (getattr(self.config, "top_p", 0.9) if self.config else 0.9),
        }

    def _format_tools_for_llama(self, tools_desc: Dict[str, Any]) -> str:
        """Format tool definitions for Llama in a way it can understand"""
        if not tools_desc:
            return ""

        tool_instructions = "\n\nYou have access to the following tools:\n"
        tool_names = []

        # Handle OpenAI-style format: {"type": "function", "functions": [...]}
        if isinstance(tools_desc, dict) and "functions" in tools_desc:
            functions = tools_desc.get("functions", [])
            if isinstance(functions, list):
                for func in functions:
                    if isinstance(func, dict):
                        name = func.get('name', 'unknown')
                        tool_names.append(name)
                        tool_instructions += f"\nTool: {name}\n"
                        tool_instructions += f"Description: {func.get('description', 'No description')}\n"
                        if func.get('parameters'):
                            tool_instructions += f"Parameters: {json.dumps(func['parameters'], indent=2)}\n"

        # Handle list of function objects
        elif isinstance(tools_desc, list):
            for func in tools_desc:
                if isinstance(func, dict):
                    name = func.get('name', 'unknown')
                    tool_names.append(name)
                    tool_instructions += f"\nTool: {name}\n"
                    tool_instructions += f"Description: {func.get('description', 'No description')}\n"
                    if func.get('parameters'):
                        tool_instructions += f"Parameters: {json.dumps(func['parameters'], indent=2)}\n"

        # Handle dict where values might be tool objects or strings
        elif isinstance(tools_desc, dict):
            for key, tool in tools_desc.items():
                # Handle case where tool is a dict with tool metadata
                if isinstance(tool, dict):
                    name = tool.get('name', key)
                    tool_names.append(name)
                    tool_instructions += f"\nTool: {name}\n"
                    tool_instructions += f"Description: {tool.get('description', 'No description')}\n"
                    if tool.get('inputSchema'):
                        tool_instructions += f"Parameters: {json.dumps(tool['inputSchema'], indent=2)}\n"
                    elif tool.get('parameters'):
                        tool_instructions += f"Parameters: {json.dumps(tool['parameters'], indent=2)}\n"
                # Handle case where tool is just a string (tool name)
                elif isinstance(tool, str):
                    tool_names.append(tool)
                    tool_instructions += f"\nTool: {tool}\n"
                    tool_instructions += f"Description: No description available\n"

        if logger.isEnabledFor(logging.DEBUG):
            logger.debug(f"Formatted tools for Llama: {tool_names}")

        tool_instructions += "\nWhen you need to call a tool, use this exact format:\n"
        tool_instructions += '<tool_call>function_name({"param1": "value1", "param2": "value2"})</tool_call>\n'
        tool_instructions += f"Available tool names: {', '.join(tool_names)}\n"
        tool_instructions += "Replace function_name with the actual tool name and provide JSON arguments.\n"

        return tool_instructions
