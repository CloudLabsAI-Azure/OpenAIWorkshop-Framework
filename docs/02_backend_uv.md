# Part 2: Application Backend Setup (uv Method)

## Pre-requisites
- Complete [Part 0](../SETUP.md)
- Complete [Part 1: MCP Setup (uv)](01_mcp_uv.md)
- MCP server running on `http://localhost:8000/mcp`
- uv installed

## Summary
In this part, you will configure and run the backend service for the Microsoft AI Agentic Workshop using uv. The backend includes full support for Microsoft's Agent Framework with advanced multi-agent capabilities.

## Microsoft Agent Framework Options

**Single Agent (`agents.agent_framework.single_agent`):**
- Basic ChatAgent with MCP tools
- Token-by-token streaming via WebSocket
- Tool call visibility in React UI
- Session state persistence across requests

**Magentic Multi-Agent (`agents.agent_framework.multi_agent.magentic_group`):**
- Intelligent orchestrator coordinates specialist agents (CRM/Billing, Product/Promotions, Security)
- Real-time streaming of orchestrator planning and agent responses
- Custom progress ledger for human-in-the-loop support
- Checkpointing for resumable workflows
- React UI shows full internal process: task ledger, instructions, agent tool calls

**Handoff Multi-Agent (`agents.agent_framework.multi_agent.handoff_multi_domain_agent`):**
- Direct agent-to-user communication with intelligent domain routing
- Configurable context transfer between specialists (preserves customer info, history)
- Smart intent classification for seamless handoffs
- Cost-efficient (33% fewer LLM calls vs orchestrator patterns)

## Steps
[1. Configure Agent Framework](#1-configure-agent-framework)  
[2. Run Backend Service](#2-run-backend-service)  
[3. Choose Frontend Experience](#3-choose-frontend-experience)

### 1. Configure Agent Framework

> **Action Items:**
> Configure the agent type in your `.env` file within the `agentic_ai/applications` folder. This will already have some of your Azure OpenAI settings from Part 0. This will be the type of agent as described above. Make sure to choose the appropriate agent module for your use case:
> ```bash
> # In your .env file in agentic_ai/applications folder, specify which type of agent for agent framework:
> AGENT_MODULE="agents.agent_framework.single_agent"
> ```
> 
> Add additional environment variables to the bottom of your existing `.env` file:
> 
> **For Magentic Orchestration Settings (magentic_group):**
> ```bash
> MAGENTIC_LOG_WORKFLOW_EVENTS=true
> MAGENTIC_ENABLE_PLAN_REVIEW=false  # Set to true for human-in-the-loop plan approval
> MAGENTIC_MAX_ROUNDS=10
> ```
> 
> **For Handoff Agent Context Transfer (handoff_multi_domain_agent):**
> ```bash
> HANDOFF_CONTEXT_TRANSFER_TURNS=-1  # -1=all history, 0=none, N=last N turns
> ```

📚 **[See detailed pattern guide and configuration for Agent Framework→](../agentic_ai/agents/agent_framework/README.md)**

### 2. Run Backend Service

> **Action Items:**
> Open a new terminal window separate than the one running the MCP server.
> ![new terminal](media/01_mcp_new_terminal.png)
> Navigate to the applications directory and start the backend:
> ```bash
> cd agentic_ai/applications
> uv run python backend.py
> ```

### 3. Choose Frontend Experience

## 📊 Frontend Comparison

| Feature | React Frontend | Streamlit Frontend |
|---------|---------------|-------------------|
| **Real-time streaming** | ✅ Token-by-token | ❌ Full response only |
| **Internal process visibility** | ✅ Orchestrator, agents, tools | ❌ Final answer only |
| **Tool call tracking** | ✅ Per-turn history | ❌ Not shown |
| **Multi-agent visualization** | ✅ Agent timeline & planning | ❌ Not shown |
| **Best for Agent Framework** | ✅ **Recommended** | ⚠️ Basic support |
| **Setup complexity** | Medium (npm install) | Low (no additional setup) |
| **Best use case** | Development, demos, debugging | Quick testing, simple chat |

**Recommendation:**
- Use **React** for Agent Framework agents to see the full multi-agent orchestration
- Use **Streamlit** for quick testing of any agent type or simple demos

## Success criteria
- Backend service is running on `http://localhost:7000`
- Agent Framework is properly configured
- Backend can communicate with MCP server
- Ready to connect to frontend

**Next Step**: Choose your frontend - [React Frontend (Recommended)](03_frontend_react.md) | [Streamlit Frontend](03_frontend_streamlit_uv.md)

**📌 Important:** Agent Framework works best with the **React frontend** to visualize the internal agent processes, orchestrator planning, and tool calls in real-time.


