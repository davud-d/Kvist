# -*- coding: utf-8 -*-
"""Where and how "Sign in with ChatGPT" talks to OpenAI: plain constants, no imports.

Apart from ``chatgpt_auth`` (which needs the JWT library and the user config) so that Kvist Cloud, which
runs the same sign-in on its own server, reads the one copy.
"""
from __future__ import annotations

ISSUER = "https://auth.openai.com"
AUTHORIZE_ENDPOINT = ISSUER + "/api/accounts/authorize"
TOKEN_ENDPOINT = ISSUER + "/api/accounts/oauth/token"
RESOURCE = "https://api.openai.com/v1"
PLAN_SCOPE = "chatgpt.tokens.use.direct"
SCOPES = "openid profile email offline_access resource.invoke " + PLAN_SCOPE
# The placeholder client of a host that has not registered yet: the callback brings back its real client_id.
AGENT_CLIENT_ID = "dynamic_agent_client"
