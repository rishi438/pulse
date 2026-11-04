"""Foundry model calls using the VM's managed identity."""
import os

from azure.core.exceptions import AzureError
from azure.identity import DefaultAzureCredential, get_bearer_token_provider
from openai import OpenAI, OpenAIError


class FoundryError(RuntimeError):
    """Foundry could not return a complete response."""


def foundry_reply(messages, *, json_mode=False):
    endpoint = os.getenv("AZURE_OPENAI_ENDPOINT", "").strip()
    model = os.getenv("AZURE_OPENAI_DEPLOYMENT", "").strip()

    if not endpoint or not model:
        raise FoundryError("Foundry endpoint and deployment must be configured")

    try:
        with DefaultAzureCredential() as credential:
            token_provider = get_bearer_token_provider(
                credential, "https://ai.azure.com/.default"
            )
            with OpenAI(
                base_url=endpoint.rstrip("/") + "/",
                api_key=token_provider,
                timeout=90.0,
                max_retries=1,
            ) as client:
                response = client.responses.create(
                    model=model,
                    input=messages,
                    store=False,
                    max_output_tokens=4096,
                    text={"format": {
                        "type": "json_object" if json_mode else "text"
                    }},
                )
    except (AzureError, OpenAIError) as error:
        raise FoundryError("Foundry model request failed") from error

    if response.status != "completed" or not response.output_text.strip():
        raise FoundryError("Foundry returned an incomplete or empty response")

    return response.output_text.strip()
