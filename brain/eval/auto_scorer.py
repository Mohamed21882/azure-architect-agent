from __future__ import annotations

import json
import re

import requests

_FALLBACK: dict = {
    "constraint_adherence": 0.5,
    "security_posture":     0.5,
    "completeness":         0.5,
    "overall":              0.5,
    "flags":                ["auto-score unavailable"],
}

_SYSTEM = (
    "You are a strict Azure architecture evaluator. "
    "Respond ONLY with a valid JSON object — no markdown, no prose, no code fences."
)


def _build_prompt(
    architecture_summary: str,
    form_values: dict,
    retrieved_chunks: list,
) -> str:
    constraints = (
        f"Region: {form_values.get('region', 'N/A')}\n"
        f"Compliance: {form_values.get('compliance', 'N/A')}\n"
        f"Budget: {form_values.get('budget', 'N/A')}\n"
        f"Hub VNet: {form_values.get('hub_vnet', 'N/A')}\n"
        f"Additional: {form_values.get('additional_constraints', 'None') or 'None'}\n"
    )
    return (
        "Evaluate the following Azure architecture against the hard constraints.\n\n"
        f"## Constraints\n{constraints}\n"
        f"## Architecture\n{architecture_summary[:3000]}\n\n"
        "Score each dimension 0.0–1.0:\n"
        "1. constraint_adherence — does it respect region, compliance, budget, "
        "and additional constraints?\n"
        "2. security_posture — are private endpoints, NSG, RBAC, and Key Vault "
        "present where appropriate?\n"
        "3. completeness — are all components required for the described workload present?\n"
        "4. overall — weighted: constraint_adherence×0.4 + security_posture×0.3 "
        "+ completeness×0.3\n\n"
        "IMPORTANT — service availability flags:\n"
        "You must ONLY flag service availability concerns if you have high confidence "
        "based on well-established facts. Do NOT flag service availability for mainstream "
        "Azure networking services (Azure Firewall, VPN Gateway, Bastion, AKS, AI Search, "
        "Storage, Key Vault) in any generally available Azure region. If you are uncertain "
        "about availability, omit the flag entirely. Uncertainty is not a reason to flag — "
        "only confirmed unavailability is.\n\n"
        "Return ONLY this JSON (fill in real values):\n"
        '{"constraint_adherence":0.0,"security_posture":0.0,'
        '"completeness":0.0,"overall":0.0,"flags":[]}'
    )


def _call_llm(
    prompt: str,
    engine_mode: str,
    model: str,
    provider: str,
    api_key: str,
) -> str:
    messages = [{"role": "user", "content": prompt}]
    full     = [{"role": "system", "content": _SYSTEM}] + messages

    if engine_mode == "Local (Ollama)":
        res = requests.post(
            "http://localhost:11434/api/chat",
            json={
                "model": model,
                "messages": full,
                "stream": False,
                "options": {"num_predict": 512, "temperature": 0.1},
            },
            timeout=120,
        ).json()
        return res.get("message", {}).get("content", "")

    api_key = (api_key or "").strip()
    if not api_key:
        return ""

    if provider == "OpenRouter":
        res = requests.post(
            "https://openrouter.ai/api/v1/chat/completions",
            headers={
                "Authorization": f"Bearer {api_key}",
                "HTTP-Referer":  "http://localhost:8501",
                "X-Title":       "TE-1 Azure Architect",
                "Content-Type":  "application/json",
            },
            json={"model": model, "max_tokens": 512, "temperature": 0.1, "messages": full},
            timeout=120,
        ).json()
        return res["choices"][0]["message"]["content"]

    if provider == "OpenAI":
        res = requests.post(
            "https://api.openai.com/v1/chat/completions",
            headers={"Authorization": f"Bearer {api_key}"},
            json={"model": model, "max_tokens": 512, "temperature": 0.1, "messages": full},
            timeout=120,
        ).json()
        return res["choices"][0]["message"]["content"]

    if provider == "Claude":
        non_system = [m for m in full if m["role"] != "system"]
        res = requests.post(
            "https://api.anthropic.com/v1/messages",
            headers={
                "x-api-key":         api_key,
                "anthropic-version": "2023-06-01",
                "content-type":      "application/json",
            },
            json={
                "model": model, "max_tokens": 512, "temperature": 0.1,
                "system": _SYSTEM, "messages": non_system,
            },
            timeout=120,
        ).json()
        return res["content"][0]["text"]

    if provider == "Gemini":
        gemini_msgs = []
        for m in messages:
            role = "model" if m["role"] == "assistant" else "user"
            gemini_msgs.append({"role": role, "parts": [{"text": m["content"]}]})
        if gemini_msgs:
            gemini_msgs[0]["parts"][0]["text"] = (
                _SYSTEM + "\n\n" + gemini_msgs[0]["parts"][0]["text"]
            )
        res = requests.post(
            f"https://generativelanguage.googleapis.com/v1beta/models/"
            f"{model}:generateContent?key={api_key}",
            json={"contents": gemini_msgs, "generationConfig": {"temperature": 0.1}},
            timeout=120,
        ).json()
        return res["candidates"][0]["content"]["parts"][0]["text"]

    return ""


def _parse(raw: str) -> dict:
    try:
        match = re.search(r"\{.*\}", raw, re.DOTALL)
        if not match:
            return dict(_FALLBACK)
        data = json.loads(match.group())
        result = {
            "constraint_adherence": float(data.get("constraint_adherence", 0.5)),
            "security_posture":     float(data.get("security_posture",     0.5)),
            "completeness":         float(data.get("completeness",         0.5)),
            "overall":              float(data.get("overall",              0.5)),
            "flags":                data.get("flags", []),
        }
        for k in ("constraint_adherence", "security_posture", "completeness", "overall"):
            result[k] = max(0.0, min(1.0, result[k]))
        return result
    except Exception:
        return dict(_FALLBACK)


def score_architecture(
    architecture_summary: str,
    form_values: dict,
    retrieved_chunks: list,
    engine_mode: str = "Local (Ollama)",
    model: str = "mistral-small:latest",
    provider: str = "OpenRouter",
    api_key: str = "",
) -> dict:
    """Score an architecture on four dimensions. Returns fallback dict on any error."""
    try:
        prompt = _build_prompt(architecture_summary, form_values, retrieved_chunks)
        raw    = _call_llm(prompt, engine_mode, model, provider, api_key)
        return _parse(raw)
    except Exception:
        return dict(_FALLBACK)
