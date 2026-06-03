from ...llm.api_provider import *
from openai import AsyncOpenAI


class DeepseekApiProvider(ApiProvider):
    def __init__(self):
        super().__init__(name="deepseek", code="ds")

    def get_client(self) -> AsyncOpenAI:
        return AsyncOpenAI(
            api_key=self.get_api_key(),
            base_url=self.get_base_url(),
        )

    async def sync_quota(self):
        return None
