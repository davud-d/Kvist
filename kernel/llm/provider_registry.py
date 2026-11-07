# -*- coding: utf-8 -*-
"""The third-party provider registry: plain data, no imports.

It lives apart from ``llm_clients`` (which imports the OpenAI SDK) so that anything that only needs to
know which providers exist, such as Kvist Cloud's provider picker, can read it without the SDK.
"""
from __future__ import annotations

# Manual-tier entry (see MANUAL_OPENAI_PROVIDERS) that also needs naming here:
# it is the blank-start default and the one manual-tier provider whose
# streaming is verified, so two capability lookups have to reference it.
ANYMODEL_PROVIDER = "anymodel"

# ── Third-party OpenAI-compatible providers ("manual" tier) ═════════════════
#
# Wired exactly like OmniRoute/OpenRouter already are: one API key, a fixed
# default base URL (region-overridable via the matching *_base_url key), and
# no built-in model catalog or defaults — these vendors' model ids change far
# too often for a static default to stay correct, so the user always types
# them, same as OmniRoute. Adding a new one is exactly this dict entry: every
# lookup below keys off MANUAL_OPENAI_PROVIDERS / MANUAL_MODEL_PROVIDERS
# instead of a dedicated per-provider branch.
MANUAL_OPENAI_PROVIDERS: dict[str, dict[str, str]] = {
    "qwen": {
        "label": "Qwen (DashScope)",
        "api_key_key": "qwen_api_key",
        "base_url_key": "qwen_base_url",
        "default_base_url": "https://dashscope-intl.aliyuncs.com/compatible-mode/v1",
    },
    "moonshot": {
        "label": "Moonshot AI (Kimi)",
        "api_key_key": "moonshot_api_key",
        "base_url_key": "moonshot_base_url",
        "default_base_url": "https://api.moonshot.ai/v1",
    },
    "zhipu": {
        "label": "Zhipu AI (BigModel)",
        "api_key_key": "zhipu_api_key",
        "base_url_key": "zhipu_base_url",
        "default_base_url": "https://open.bigmodel.cn/api/paas/v4",
    },
    "zai": {
        "label": "Z.ai (GLM)",
        "api_key_key": "zai_api_key",
        "base_url_key": "zai_base_url",
        "default_base_url": "https://api.z.ai/api/paas/v4",
    },
    "xiaomi": {
        "label": "Xiaomi MiMo API",
        "api_key_key": "xiaomi_api_key",
        "base_url_key": "xiaomi_base_url",
        "default_base_url": "https://api.xiaomimimo.com/v1",
    },
    "xiaomi-tokenplan": {
        "label": "Xiaomi MiMo Token Plan",
        "api_key_key": "xiaomi_tokenplan_api_key",
        "base_url_key": "xiaomi_tokenplan_base_url",
        "default_base_url": "https://token-plan-cn.xiaomimimo.com/v1",
    },
    "minimax": {
        "label": "MiniMax",
        "api_key_key": "minimax_api_key",
        "base_url_key": "minimax_base_url",
        "default_base_url": "https://api.minimax.io/v1",
    },
    "lingyiwanwu": {
        "label": "01.AI (Yi)",
        "api_key_key": "lingyiwanwu_api_key",
        "base_url_key": "lingyiwanwu_base_url",
        "default_base_url": "https://api.lingyiwanwu.com/v1",
    },
    "stepfun": {
        "label": "StepFun",
        "api_key_key": "stepfun_api_key",
        "base_url_key": "stepfun_base_url",
        "default_base_url": "https://api.stepfun.com/v1",
    },
    "siliconflow": {
        "label": "SiliconFlow",
        "api_key_key": "siliconflow_api_key",
        "base_url_key": "siliconflow_base_url",
        "default_base_url": "https://api.siliconflow.com/v1",
    },
    "baichuan": {
        "label": "Baichuan AI",
        "api_key_key": "baichuan_api_key",
        "base_url_key": "baichuan_base_url",
        "default_base_url": "https://api.baichuan-ai.com/v1",
    },
    "qianfan": {
        "label": "Baidu Qianfan (ERNIE)",
        "api_key_key": "qianfan_api_key",
        "base_url_key": "qianfan_base_url",
        "default_base_url": "https://qianfan.baidubce.com/v2",
    },
    "hunyuan": {
        "label": "Tencent Hunyuan",
        "api_key_key": "hunyuan_api_key",
        "base_url_key": "hunyuan_base_url",
        "default_base_url": "https://api.hunyuan.cloud.tencent.com/v1",
    },
    "modelscope": {
        "label": "ModelScope",
        "api_key_key": "modelscope_api_key",
        "base_url_key": "modelscope_base_url",
        "default_base_url": "https://api-inference.modelscope.cn/v1",
    },
    "mistral": {
        "label": "Mistral AI",
        "api_key_key": "mistral_api_key",
        "base_url_key": "mistral_base_url",
        "default_base_url": "https://api.mistral.ai/v1",
    },
    "groq": {
        "label": "Groq",
        # Deliberately a separate credential from FreeFlow's own Groq key
        # (GROQ_API_KEY_KEY): FreeFlow talks to Groq through its own SDK
        # wrapper, this provider talks to it as a plain OpenAI-compatible
        # endpoint, and the two paths must stay independently configurable.
        "api_key_key": "groq_direct_api_key",
        "base_url_key": "groq_direct_base_url",
        "default_base_url": "https://api.groq.com/openai/v1",
    },
    "together": {
        "label": "Together AI",
        "api_key_key": "together_api_key",
        "base_url_key": "together_base_url",
        "default_base_url": "https://api.together.xyz/v1",
    },
    "fireworks": {
        "label": "Fireworks AI",
        "api_key_key": "fireworks_api_key",
        "base_url_key": "fireworks_base_url",
        "default_base_url": "https://api.fireworks.ai/inference/v1",
    },
    "xai": {
        "label": "xAI (Grok)",
        "api_key_key": "xai_api_key",
        "base_url_key": "xai_base_url",
        "default_base_url": "https://api.x.ai/v1",
    },
    "perplexity": {
        "label": "Perplexity",
        "api_key_key": "perplexity_api_key",
        "base_url_key": "perplexity_base_url",
        "default_base_url": "https://api.perplexity.ai",
    },
    "cerebras": {
        "label": "Cerebras",
        "api_key_key": "cerebras_api_key",
        "base_url_key": "cerebras_base_url",
        "default_base_url": "https://api.cerebras.ai/v1",
    },
    "deepinfra": {
        "label": "DeepInfra",
        "api_key_key": "deepinfra_api_key",
        "base_url_key": "deepinfra_base_url",
        "default_base_url": "https://api.deepinfra.com/v1/openai",
    },
    "novita": {
        "label": "Novita AI",
        "api_key_key": "novita_api_key",
        "base_url_key": "novita_base_url",
        "default_base_url": "https://api.novita.ai/v3/openai",
    },
    "vercel": {
        "label": "Vercel AI Gateway",
        "api_key_key": "vercel_api_key",
        "base_url_key": "vercel_base_url",
        "default_base_url": "https://ai-gateway.vercel.sh/v1",
    },
    "baseten": {
        "label": "Baseten",
        "api_key_key": "baseten_api_key",
        "base_url_key": "baseten_base_url",
        "default_base_url": "https://inference.baseten.co/v1",
    },
    "sambanova": {
        "label": "SambaNova",
        "api_key_key": "sambanova_api_key",
        "base_url_key": "sambanova_base_url",
        "default_base_url": "https://api.sambanova.ai/v1",
    },
    "nebius": {
        "label": "Nebius AI Studio",
        "api_key_key": "nebius_api_key",
        "base_url_key": "nebius_base_url",
        "default_base_url": "https://api.studio.nebius.com/v1",
    },
    "friendli": {
        "label": "Friendli AI",
        "api_key_key": "friendli_api_key",
        "base_url_key": "friendli_base_url",
        "default_base_url": "https://api.friendli.ai/serverless/v1",
    },
    "databricks": {
        "label": "Databricks Model Serving",
        "api_key_key": "databricks_api_key",
        "base_url_key": "databricks_base_url",
        # No usable default exists: every workspace has its own URL
        # (<workspace>/serving-endpoints). An empty default forces
        # required-base-url validation in create_llm_client and
        # _discovery_endpoints instead of silently falling through to
        # some other vendor's endpoint with this key.
        "default_base_url": "",
    },
    "gemini": {
        # "Google AI Studio" is the console (aistudio.google.com) where this
        # key comes from; it's the same Gemini Developer API either name.
        "label": "Google AI Studio (Gemini)",
        # Deliberately a separate credential from FreeFlow's own Gemini key
        # (GEMINI_API_KEY_KEY): FreeFlow talks to Gemini through its own SDK
        # wrapper, this provider talks to it as a plain OpenAI-compatible
        # endpoint, and the two paths must stay independently configurable.
        "api_key_key": "gemini_direct_api_key",
        "base_url_key": "gemini_direct_base_url",
        "default_base_url": "https://generativelanguage.googleapis.com/v1beta/openai/",
    },
    "cohere": {
        "label": "Cohere",
        "api_key_key": "cohere_api_key",
        "base_url_key": "cohere_base_url",
        "default_base_url": "https://api.cohere.com/compatibility/v1",
    },
    "ai21": {
        "label": "AI21 Labs (Jamba)",
        "api_key_key": "ai21_api_key",
        "base_url_key": "ai21_base_url",
        "default_base_url": "https://api.ai21.com/studio/v1",
    },
    "doubao": {
        "label": "ByteDance Doubao (Volcengine Ark)",
        "api_key_key": "doubao_api_key",
        "base_url_key": "doubao_base_url",
        "default_base_url": "https://ark.cn-beijing.volces.com/api/v3",
    },
    "github-models": {
        "label": "GitHub Models",
        # Auth is a GitHub PAT with the models:read scope, passed as a
        # plain bearer token — fits the same api_key shape as every other
        # entry here.
        "api_key_key": "github_models_api_key",
        "base_url_key": "github_models_base_url",
        "default_base_url": "https://models.github.ai/inference",
    },
    "iflytek-spark": {
        "label": "iFlytek Spark",
        "api_key_key": "iflytek_spark_api_key",
        "base_url_key": "iflytek_spark_base_url",
        "default_base_url": "https://spark-api-open.xf-yun.com/v1",
    },
    "scaleway": {
        "label": "Scaleway Generative APIs",
        "api_key_key": "scaleway_api_key",
        "base_url_key": "scaleway_base_url",
        "default_base_url": "https://api.scaleway.ai/v1",
    },
    ANYMODEL_PROVIDER: {
        "label": "AnyModel",
        "api_key_key": "anymodel_api_key",
        "base_url_key": "anymodel_base_url",
        "default_base_url": "https://anymodel.org/v1",
    },
}
