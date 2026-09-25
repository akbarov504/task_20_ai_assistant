from __future__ import annotations

import mimetypes
import os
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Literal
from app.analyse.schemas import VoiceAnalysisResult
import asyncio
from langchain.messages import HumanMessage

ProviderName = Literal["gemini","openai", "anthropic" ]

@dataclass(slots=True)
class RemoteFile:
    provider: ProviderName
    file_id: str
    filename: str
    mime_type: str

class ProviderAdapter:
    name: ProviderName

    async def upload(self, path: Path, mime_type: str) -> RemoteFile:
        raise NotImplementedError

    async def ask(self, remote: RemoteFile, prompt: str, model: str | None = None) -> str:
        raise NotImplementedError

class OpenAIAdapter(ProviderAdapter):
    name: ProviderName = "openai"

    async def upload(self, path: Path, mime_type: str) -> RemoteFile:
        return await asyncio.to_thread(
            self._upload_sync,
            path,
            mime_type,
        )
    
    def _upload_sync(
        self,
        path: Path,
        mime_type: str,
        ) -> RemoteFile:
            from openai import OpenAI

            client = OpenAI()

            with path.open("rb") as file:
                uploaded = client.files.create(
                    file=file,
                    purpose="user_data",
                )

            return RemoteFile(
                provider=self.name,
                file_id=uploaded.id,
                filename=path.name,
                mime_type=mime_type,
            )

    async def ask(
        self,
        remote: RemoteFile,
        prompt: str,
        model: str | None = None,
    ) -> VoiceAnalysisResult:
        from langchain_openai import ChatOpenAI

        llm = ChatOpenAI(
            model=model or os.getenv("OPENAI_MODEL", "gpt-5.4-mini"),
            use_responses_api=True,
        )

        structured_llm = llm.with_structured_output(VoiceAnalysisResult)

        message = HumanMessage(
            content=[
                {"type": "text", "text": prompt},
                {"type": "file", "file_id": remote.file_id},
            ]
        )

        report: VoiceAnalysisResult = await structured_llm.ainvoke([message])
        return report
class AnthropicAdapter(ProviderAdapter):
    name: ProviderName = "anthropic"

    async def upload(
        self,
        path: Path,
        mime_type: str,
    ) -> RemoteFile:
        return await asyncio.to_thread(
            self._upload_sync,
            path,
            mime_type,
        )

    def _upload_sync(
        self,
        path: Path,
        mime_type: str,
    ) -> RemoteFile:
        import anthropic

        client = anthropic.Anthropic()

        with path.open("rb") as file:
            uploaded = client.beta.files.upload(
                file=(path.name, file, mime_type),
            )

        return RemoteFile(
            provider=self.name,
            file_id=uploaded.id,
            filename=path.name,
            mime_type=mime_type,
        )
    
    async def ask(
        self,
        remote: RemoteFile,
        prompt: str,
        model: str | None = None,
    ) -> VoiceAnalysisResult:
        from langchain_anthropic import ChatAnthropic

        llm = ChatAnthropic(
            model=model or os.getenv("ANTHROPIC_MODEL", "claude-sonnet-4-6"),
            betas=["files-api-2025-04-14"],
        )

        structured_llm = llm.with_structured_output(VoiceAnalysisResult)

        message = HumanMessage(
            content=[
                {"type": "text", "text": prompt},
                {
                    "type": "file",
                    "file_id": remote.file_id,
                    "mime_type": remote.mime_type,
                },
            ]
        )

        report: VoiceAnalysisResult = await structured_llm.ainvoke([message])
        return report

class GeminiAdapter(ProviderAdapter):
    name: ProviderName = "gemini"

    async def upload(
        self,
        path: Path,
        mime_type: str,
    ) -> RemoteFile:
        return await asyncio.to_thread(
            self._upload_sync,
            path,
            mime_type,
        )

    def _upload_sync(
        self,
        path: Path,
        mime_type: str,
    ) -> RemoteFile:
        from google import genai

        client = genai.Client()
        uploaded = client.files.upload(file=str(path))

        while uploaded.state and uploaded.state.name == "PROCESSING":
            time.sleep(1)
            uploaded = client.files.get(name=uploaded.name)

        if uploaded.state and uploaded.state.name == "FAILED":
            raise RuntimeError(
                f"Gemini failed to process {path.name}"
            )

        return RemoteFile(
            provider=self.name,
            file_id=uploaded.uri,
            filename=path.name,
            mime_type=uploaded.mime_type or mime_type,
        )

    async def ask(
        self,
        remote: RemoteFile,
        prompt: str,
        model: str | None = None,
    ) -> VoiceAnalysisResult:
        from langchain_google_genai import ChatGoogleGenerativeAI

        llm = ChatGoogleGenerativeAI(
            model=model or os.getenv("GEMINI_MODEL", "gemini-3.7-flash"),
        )

        structured_llm = llm.with_structured_output(VoiceAnalysisResult)

        message = HumanMessage(
            content=[
                {"type": "text", "text": prompt},
                {
                    "type": "file",
                    "file_id": remote.file_id,
                    "mime_type": remote.mime_type,
                },
            ]
        )

        report: VoiceAnalysisResult = await structured_llm.ainvoke([message])
        return report

_ADAPTERS: dict[ProviderName, ProviderAdapter] = {
    "openai": OpenAIAdapter(),
    "anthropic": AnthropicAdapter(),
    "gemini": GeminiAdapter(),
}

def get_adapter(provider: ProviderName) -> ProviderAdapter:
    return _ADAPTERS[provider]

def detect_mime_type(filename: str, supplied: str | None = None) -> str:
    if supplied and supplied != "application/octet-stream":
        return supplied
    return mimetypes.guess_type(filename)[0] or "application/octet-stream"
