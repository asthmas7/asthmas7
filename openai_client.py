"""Calls to the OpenAI Responses API."""

import os

from openai import OpenAI
from pydantic import BaseModel

DEFAULT_MODEL = "gpt-4.1-mini"


class OpenAIConfigurationError(RuntimeError):
    """The API credential or model configuration is missing."""


class OpenAIResponseError(RuntimeError):
    """The API did not return a complete text response."""


def generate_text(prompt: str, *, instructions: str | None = None, model: str | None = None) -> str:
    """Send the supplied text and return a complete response.

    Callers choose which data to send. SDK transport and API errors propagate
    so callers can distinguish authentication, rate limits, and timeouts.
    """
    if not prompt.strip():
        raise ValueError("请输入要发送给 OpenAI 的文本。")
    return _generate(prompt, instructions=instructions, model=model)


def configured() -> bool:
    return bool(os.getenv("OPENAI_API_KEY", "").strip())


def generate_structured(content: list, *, instructions: str, schema: type[BaseModel]):
    result = _generate([{"role": "user", "content": content}], instructions=instructions,
                       text={"format": {"type": "json_schema", "name": schema.__name__,
                                        "schema": schema.model_json_schema(), "strict": True}})
    return schema.model_validate_json(result)


def _generate(input_data, *, instructions=None, model=None, **options):
    api_key = os.getenv("OPENAI_API_KEY", "").strip()
    if not api_key:
        raise OpenAIConfigurationError("请在运行进程中设置 OPENAI_API_KEY。")
    selected_model = (model if model is not None else os.getenv("OPENAI_MODEL", DEFAULT_MODEL)).strip()
    if not selected_model:
        raise OpenAIConfigurationError("请提供非空的 OpenAI 模型名称。")

    with OpenAI(api_key=api_key, timeout=30.0, max_retries=2) as client:
        response = client.responses.create(
            model=selected_model,
            input=input_data,
            instructions=instructions,
            store=False,
            **options,
        )
    if response.status != "completed" or not response.output_text.strip():
        raise OpenAIResponseError("OpenAI 未返回完整的文本结果。")
    return response.output_text
