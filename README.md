![alt text](docs/media/image-1.png)
# Microsoft AI Agentic Workshop Repository  
  
Welcome to the official repository for the Microsoft AI Agentic Workshop! This repository provides all the resources, code, and documentation you need to explore, prototype, and compare various agent-based AI solutions using Microsoft's leading AI technologies.  
  
---  
  
## Quick Links  
  
- [Business Scenario and Agent Design](./SCENARIO.md)  
- [Getting Started (Setup Instructions)](./SETUP.md)  
- [System Architecture Overview](./ARCHITECTURE.md)  
- [Data Sets](./DATA.md)  
- [APIM + MCP Security (Optional)](./mcp/MULTI_TENANT_MCP_SECURITY.md)  
- [Code of Conduct](./CODE_OF_CONDUCT.md)  
- [Security Guidelines](./SECURITY.md)  
- [Support](./SUPPORT.md)  
- [License](./LICENSE)  
  
---  
  
## What You Can Do With This Workshop  
  
- **Design and prototype agent solutions** for real-world business scenarios.  
- **Compare single-agent vs. multi-agent** architectures and approaches.  
- **Develop and contrast agent implementations** using different platforms:  
  - **[Microsoft Agent Framework](https://github.com/microsoft/agent-framework)** (NEW!) - Microsoft's latest agentic AI framework with advanced multi-agent orchestration (Magentic workflows, handoffs, checkpointing)  
  - Azure AI Agent Service  
  - Semantic Kernel  
  - Autogen  
  
---  
  
## Key Features  
  
- **🎯 Microsoft Agent Framework Support (NEW!):** Full integration with [Microsoft's Agent Framework](https://github.com/microsoft/agent-framework) featuring:
  - **Now available via pip!** Install with: `pip install agent-framework` or `uv add agent-framework`
  - **Single-agent** with MCP tools and streaming token-by-token responses
  - **Multi-agent Magentic orchestration** with intelligent task delegation and progress tracking
  - **Handoff-based multi-domain agents** for specialized task routing with smart context transfer
  - **Checkpointing and resumable workflows** for long-running agentic tasks
  - **Real-time WebSocket streaming** with internal agent process visibility
  - 📚 **[See detailed pattern guide and documentation →](agentic_ai/agents/agent_framework/README.md)**
  
- **🖥️ Advanced UI Options:**  
  - **React Frontend:** Real-time streaming visualization with agent internal processes, tool calls, orchestrator planning, and turn-by-turn history tracking
  - **Streamlit Frontend:** Simple, elegant chat interface for quick prototyping and demos
  
- **Configurable LLM Backend:** Use the latest Azure OpenAI GPT models (e.g., GPT-5, GPT-4.1, GPT-4o).  
- **MCP Server Integration:** Advanced tools to enhance agent orchestration and capabilities with Model Context Protocol.  
- **A2A (Agent-to-Agent) Protocol Support:** Enables strict cross-domain, black-box multi-agent collaboration using [Google's A2A protocol](https://github.com/google-a2a/A2A). [Learn more &rarr;](agentic_ai/agents/semantic_kernel/multi_agent/a2a).  
- **Durable Agent Pattern:** Includes a demo of a robust agent that persists its state, survives restarts, and manages long-running workflows. [Learn more &rarr;](agentic_ai/scenarios/durable_agent/README.md)  
- **Flexible Agent Architecture:**  
  - Supports single-agent, multi-agent, or reflection-based agents (selectable via `.env`).  
  - Agents can self-loop, collaborate, reflect, or take on dynamic roles as defined in modules.  
  - Multiple frameworks: Agent Framework, Autogen, Semantic Kernel, Azure AI Agent Service.  
- **Session-Based Chat:** Persistent conversation history for each session.  
- **Full-Stack Application:**  
  - FastAPI backend with WebSocket and RESTful endpoints (chat, reset, history, etc.).  
  - Choice of frontend: React (advanced streaming visualization) or Streamlit (simple chat).  
- **Environment-Based Configuration:** Easily configure the system using `.env` files.  
  
---  
  
## Getting Started  
  
1. Review the [Setup Instructions](./SETUP.md) for environment prerequisites and step-by-step installation.  
2. Explore the [Business Scenario and Agent Design](./SCENARIO.md) to understand the workshop challenge.  
3. Check out the **[Agent Framework Implementation Patterns](agentic_ai/agents/agent_framework/README.md)** to choose the right multi-agent approach (single-agent, Magentic orchestration, or handoff pattern).
4. Dive into [System Architecture](./ARCHITECTURE.md) before building and customizing your agent solutions.  
5. Explore **[Workflow Examples & Production Demos](agentic_ai/workflow/)** to learn advanced orchestration patterns.
6. Utilize the [Support Guide](./SUPPORT.md) for troubleshooting and assistance.  
  
---  
  
## Workflow Orchestration  
  
The workshop includes comprehensive **workflow orchestration** capabilities built on the Microsoft Agent Framework's Pregel-style execution engine. Workflows enable complex multi-agent coordination with type-safe messaging, checkpointing, and real-time observability.  
  
### 🎯 Featured Demo: Fraud Detection System  
  
A production-ready fraud detection workflow showcasing enterprise-grade patterns:  
  
- **Real-time React dashboard** with live workflow visualization  
- **Fan-out/fan-in patterns** for parallel specialist agent analysis  
- **Human-in-the-loop** analyst review with checkpointing  
- **MCP tool integration** for customer data access  
- **WebSocket streaming** for live event updates  
  
**[→ Try the Fraud Detection Demo](agentic_ai/workflow/fraud_detection/)**  
  
### 📚 Learning Resources  
  
| Resource | Description |  
|----------|-------------|  
| **[Workflow Architecture Guide](agentic_ai/workflow/README.md)** | Core concepts, execution model, and API reference |  
| **[Human-in-the-Loop Patterns](agentic_ai/workflow/human-in-the-loop.md)** | Comprehensive guide to approval workflows |  
| **[Fraud Detection Demo](agentic_ai/workflow/fraud_detection/)** | Production-ready workflow with real-time UI |  
  
### Key Capabilities  
  
✅ **Control Flow**: Switch/case routing, conditional edges, dynamic branching  
✅ **Parallelism**: Fan-out/fan-in, concurrent execution, aggregation patterns  
✅ **Checkpointing**: Pause/resume workflows, long-running processes, crash recovery  
✅ **Human-in-the-Loop**: External approvals, RequestInfoExecutor, response handling  
✅ **State Management**: Executor-local state, shared state, persistence  
✅ **Observability**: OpenTelemetry tracing, event streaming, real-time monitoring  
✅ **Composition**: Nested workflows, workflow-as-agent, modular design  
  
---  
  
## Contributing  
  
Please review our [Code of Conduct](./CODE_OF_CONDUCT.md) and [Security Guidelines](./SECURITY.md) before contributing.  
  
---  
  
## License  
  
This project is licensed under the terms described in the [LICENSE](./LICENSE) file.  
  
