import streamlit as st
import streamlit.components.v1 as components
import requests
import json
import re
import os
import glob

st.set_page_config(page_title="TE-1: Azure Architect Portal", layout="wide")

# --- STATE INITIALIZATION ---
if 'task_input' not in st.session_state:
    st.session_state.task_input = ""
if 'arch_context' not in st.session_state:
    st.session_state.arch_context = ""
if 'summary_text' not in st.session_state:
    st.session_state.summary_text = ""
if 'mermaid_code' not in st.session_state:
    st.session_state.mermaid_code = ""
if 'bicep_code' not in st.session_state:
    st.session_state.bicep_code = ""
if 'messages' not in st.session_state:
    st.session_state.messages = []
if 'retrieved_chunks' not in st.session_state:
    st.session_state.retrieved_chunks = []
if 'form_values' not in st.session_state:
    st.session_state.form_values = {}
if 'crystallised_path' not in st.session_state:
    st.session_state.crystallised_path = ""

# --- AZURE BRANDING & READABILITY ---
st.markdown("""
<style>
    .stButton>button { background-color: #0078d4; color: white; border-radius: 4px; border: none; width: 100%; }
    [data-testid="stStatusWidget"] { display: none !important; }
    .stChatInput { bottom: 20px; }
</style>
""", unsafe_allow_html=True)

WIKI_PATH = "/home/mohelal/Azure-Architect-Wiki"
OLLAMA_URL = "http://localhost:11434/api/generate"


@st.cache_resource(show_spinner=False)
def _load_brain():
    """Load the BM25 index and BrainConfig once per session."""
    try:
        from brain.config import CONFIG
        from brain.search.bm25_index import BM25Index
        bm25 = BM25Index.load(CONFIG.bm25_index_path)
        return bm25, CONFIG
    except Exception:
        return None, None


def get_brain_files():
    return glob.glob(f"{WIKI_PATH}/raw/**/*.md", recursive=True)


def get_local_context(query):
    """Keyword-based fallback context when Brain index is unavailable."""
    context = ""
    keywords = [k.lower() for k in query.split() if len(k) > 3]
    files = get_brain_files()
    found_count = 0
    for f_path in files:
        try:
            with open(f_path, 'r') as f:
                content = f.read()
                if any(kw in content.lower() for kw in keywords):
                    context += f"\n[Brain Data: {os.path.basename(f_path)}]\n{content[:600]}\n"
                    found_count += 1
                if found_count >= 10:
                    break
        except Exception:
            pass
    return context


def get_context(query: str, reinforce: bool = False) -> str:
    """Return retrieval context. Uses hybrid_search when Brain is available,
    falls back to keyword search. reinforce=True only on human approval."""
    bm25, config = _load_brain()
    if bm25 is not None:
        try:
            from brain.search.hybrid_search import hybrid_search
            results = hybrid_search(query, bm25, config, top_k=10, reinforce=reinforce)
            st.session_state.retrieved_chunks = results
            return "\n".join(
                f"\n[Brain: {r.get('title', 'Unknown')}]\n{r.get('content', '')[:600]}\n"
                for r in results
            )
        except Exception:
            pass
    return get_local_context(query)


def call_external_llm(provider, api_key, model_name, prompt):
    try:
        headers = {}
        if provider == "OpenRouter":
            url = "https://openrouter.ai/api/v1/chat/completions"
            headers = {"Authorization": f"Bearer {api_key}", "HTTP-Referer": "https://tensoredge.net"}
            json_data = {"model": model_name or "anthropic/claude-3.5-sonnet", "max_tokens": 8192, "messages": [{"role": "user", "content": prompt}]}
            res = requests.post(url, headers=headers, json=json_data)
        elif provider == "OpenAI":
            url = "https://api.openai.com/v1/chat/completions"
            headers = {"Authorization": f"Bearer {api_key}"}
            json_data = {"model": model_name or "gpt-4o", "max_tokens": 8192, "messages": [{"role": "user", "content": prompt}]}
            res = requests.post(url, headers=headers, json=json_data)
        elif provider == "Claude":
            url = "https://api.anthropic.com/v1/messages"
            headers = {"x-api-key": api_key, "anthropic-version": "2023-06-01", "content-type": "application/json"}
            json_data = {"model": model_name or "claude-haiku-4-5-20251001", "max_tokens": 8192, "messages": [{"role": "user", "content": prompt}]}
            res = requests.post(url, headers=headers, json=json_data)
        elif provider == "Gemini":
            url = f"https://generativelanguage.googleapis.com/v1beta/models/{model_name or 'gemini-1.5-pro'}:generateContent?key={api_key}"
            json_data = {"contents": [{"parts": [{"text": prompt}]}]}
            res = requests.post(url, json=json_data)

        data = res.json()
        if res.status_code != 200:
            error_msg = data.get('error', {}).get('message', str(data))
            return f"⚠️ {provider} Error: {error_msg}"
        if provider in ["OpenRouter", "OpenAI"]:
            return data['choices'][0]['message']['content']
        if provider == "Claude":
            return data['content'][0]['text']
        if provider == "Gemini":
            return data['candidates'][0]['content']['parts'][0]['text']
    except Exception as e:
        return f"❌ System Failure: {str(e)}"


def render_mermaid(code):
    """Smart sanitization for Mermaid."""
    code = code.strip()
    if code.startswith("raph"):
        code = "g" + code

    code = re.sub(r'```mermaid\s*', '', code, flags=re.IGNORECASE)
    code = re.sub(r'```', '', code)
    code = code.replace('""', '"')
    code = re.sub(r'\[([^"].*?)\]', r'["\1"]', code)

    html_content = """
    <div id="graph" class="mermaid" style="display: flex; justify-content: center; background: transparent;">
        {code}
    </div>
    <style>
        .node text { fill: white !important; font-family: 'Segoe UI', Tahoma, Geneva, Verdana, sans-serif !important; }
        .edgeLabel text { fill: #0078d4 !important; }
        .cluster rect { fill: #333 !important; stroke: #0078d4 !important; }
    </style>
    <script type="module">
        import mermaid from 'https://cdn.jsdelivr.net/npm/mermaid@10/dist/mermaid.esm.min.mjs';
        mermaid.initialize({
            startOnLoad: true,
            theme: 'dark',
            securityLevel: 'loose',
            flowchart: { useMaxWidth: true, htmlLabels: true, curve: 'basis' }
        });
        async function run() {
            try { await mermaid.run(); }
            catch (e) { document.getElementById('graph').innerHTML = '<p style="color:red;">Mermaid Render Fail. Model generated invalid syntax.</p>'; }
        }
        run();
    </script>
    """
    final_html = html_content.replace("{code}", code)
    components.html(final_html, height=800, scrolling=True)


# --- SIDEBAR ---
with st.sidebar:
    st.header("🛡️ TE-1 Settings")
    engine_mode = st.radio("Engine:", ["Local (Ollama)", "BYOM (External API)"])
    if engine_mode == "Local (Ollama)":
        selected_model = st.selectbox("Model:", ["mistral-small:latest", "qwen3.5:latest"])
    else:
        provider = st.selectbox("Provider:", ["OpenRouter", "OpenAI", "Gemini", "Claude"])
        selected_model = st.text_input("Model ID:", value="claude-haiku-4-5-20251001")
        api_key = st.text_input("API Key:", type="password")

    with st.expander("🗂️ Architecture Parameters"):
        region = st.text_input("Region:", value="East US")
        compliance = st.selectbox(
            "Compliance:",
            ["None", "HIPAA", "PCI-DSS", "SOC 2", "ISO 27001", "FedRAMP"],
        )
        budget = st.text_input("Monthly Budget ($):", value="")
        constraints = st.text_area("Constraints:", height=80, value="")

    with st.expander("🧠 Brain Audit"):
        bm25_obj, _ = _load_brain()
        brain_status = f"✅ Brain loaded ({len(bm25_obj)} chunks)" if bm25_obj else "⚠️ Brain offline (keyword fallback)"
        st.caption(brain_status)
        all_docs = get_brain_files()
        st.write(f"Raw docs: {len(all_docs)}")
        if all_docs:
            st.code("\n".join([os.path.basename(f) for f in all_docs[:15]]))


# --- MAIN UI ---
st.title("🛡️ TE-1 | TensorEdge Infrastructure Architect")
st.markdown("---")

task_input = st.text_area("Describe the Azure Infrastructure task:", height=100)

# STEP 1: Generate Architecture & Diagram
if st.button("Generate Architecture"):
    if task_input:
        # Reset state on new task
        st.session_state.summary_text = ""
        st.session_state.mermaid_code = ""
        st.session_state.bicep_code = ""
        st.session_state.crystallised_path = ""
        st.session_state.messages = []
        st.session_state.retrieved_chunks = []
        st.session_state.task_input = task_input
        st.session_state.form_values = {
            "region": region,
            "compliance": compliance,
            "budget": budget,
            "constraints": constraints,
        }

        with st.status("🌩️ Consulting Azure Brain & Drafting Architecture...", expanded=True) as status:
            context = get_context(task_input, reinforce=False)
            st.session_state.arch_context = context

            prompt = "System: You are TE-1, expert Azure Architect.\n"
            prompt += "Context from Brain: " + context + "\n\n"
            prompt += "USER TASK: " + task_input + "\n\n"
            if region and region != "None":
                prompt += f"Region: {region}\n"
            if compliance and compliance != "None":
                prompt += f"Compliance requirement: {compliance}\n"
            if budget:
                prompt += f"Budget: ${budget}/month\n"
            if constraints:
                prompt += f"Constraints: {constraints}\n"
            prompt += "\nInstructions:\n"
            prompt += "- First, provide a comprehensive Architecture Summary. You MUST include a fully complete, detailed 'Security Best Practices' bulleted list.\n"
            prompt += "- Second, provide the architecture diagram inside a ```mermaid block using 'graph TD'. Use double quotes strictly for labels.\n"
            prompt += "- DO NOT GENERATE BICEP CODE YET. We will do that in the next step."

            if engine_mode == "Local (Ollama)":
                res = requests.post(
                    OLLAMA_URL,
                    json={"model": selected_model, "prompt": prompt, "stream": False, "options": {"num_predict": 8192}},
                ).json()
                full_response = res.get("response", "")
            else:
                full_response = call_external_llm(provider, api_key, selected_model, prompt)

            status.update(label="✅ Architecture Complete", state="complete")

        if "⚠️" in full_response or "❌" in full_response:
            st.error(full_response)
        else:
            mermaid_match = re.search(r"```mermaid\s*(.*?)\s*```", full_response, re.DOTALL | re.IGNORECASE)
            summary = re.sub(r"```mermaid.*?```", "", full_response, flags=re.DOTALL | re.IGNORECASE).strip()

            st.session_state.summary_text = summary
            if mermaid_match:
                st.session_state.mermaid_code = mermaid_match.group(1)

            # Track conversation for crystalliser
            st.session_state.messages = [
                {"role": "user", "content": task_input},
                {"role": "assistant", "content": summary},
            ]

# --- RENDER EXISTING STATE ---
if st.session_state.summary_text:
    st.markdown(st.session_state.summary_text)

if st.session_state.mermaid_code:
    st.markdown("---")
    st.subheader("📊 Architecture Visualization")
    render_mermaid(st.session_state.mermaid_code)

# STEP 2: Approve Architecture & Generate Bicep
# Only shown after architecture is generated. reinforce=True signals human approval
# and writes updated confidence scores back to Qdrant.
if st.session_state.summary_text:
    st.markdown("---")
    st.subheader("⚙️ Infrastructure as Code")

    if st.button("Approve Architecture & Generate Bicep"):
        with st.status("🌩️ Approving architecture & writing complete Bicep codebase...", expanded=True) as status:
            # Reinforce the knowledge base with the approved query
            context = get_context(st.session_state.task_input, reinforce=True)

            bicep_prompt = "System: You are TE-1, expert Azure Architect.\n"
            bicep_prompt += "Context from Brain: " + context + "\n\n"
            bicep_prompt += "USER TASK: " + st.session_state.task_input + "\n\n"
            bicep_prompt += "Architecture Summary:\n" + st.session_state.summary_text + "\n\n"
            bicep_prompt += "Instructions:\n"
            bicep_prompt += "Write the Bicep template for the architecture. Output the code inside a single ```bicep code block. Include ALL necessary resources, parameters, and outputs. Do NOT include any explanations or text outside the code block."

            if engine_mode == "Local (Ollama)":
                res = requests.post(
                    OLLAMA_URL,
                    json={"model": selected_model, "prompt": bicep_prompt, "stream": False, "options": {"num_predict": 8192}},
                ).json()
                bicep_response = res.get("response", "")
            else:
                bicep_response = call_external_llm(provider, api_key, selected_model, bicep_prompt)

            status.update(label="✅ Bicep Generated", state="complete")

        if "⚠️" in bicep_response or "❌" in bicep_response:
            st.error(bicep_response)
        else:
            bicep_match = re.search(r"```bicep\s*(.*?)\s*```", bicep_response, re.DOTALL | re.IGNORECASE)
            bicep_code = bicep_match.group(1) if bicep_match else bicep_response
            st.session_state.bicep_code = bicep_code

            # Track Bicep exchange in messages (Bicep stripped from crystalliser body)
            st.session_state.messages.append({"role": "user", "content": "[Approve Architecture & Generate Bicep]"})
            st.session_state.messages.append({"role": "assistant", "content": "[Bicep code generated — stripped]"})

            # Crystallise the approved session into the wiki
            try:
                from brain.wiki.crystalliser import crystallise_session
                session = {
                    "description": st.session_state.task_input,
                    "form_values": st.session_state.form_values,
                    "messages": st.session_state.messages,
                    "retrieved_chunks": st.session_state.retrieved_chunks,
                    "architecture_summary": st.session_state.summary_text,
                }
                wiki_path = crystallise_session(session)
                st.session_state.crystallised_path = wiki_path
            except Exception:
                pass

    if st.session_state.bicep_code:
        st.code(st.session_state.bicep_code, language="bicep")
        st.download_button(
            label="⬇️ Download main.bicep",
            data=st.session_state.bicep_code,
            file_name="main.bicep",
            mime="text/plain",
        )

    if st.session_state.crystallised_path:
        rel = os.path.relpath(st.session_state.crystallised_path, WIKI_PATH)
        st.success(f"🧠 Session crystallised into wiki/semantic/{os.path.basename(st.session_state.crystallised_path)}")
