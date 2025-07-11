import asyncio
import logging
import os
import sys
from typing import Optional, override

# Handle imports for both direct execution and app usage
try:
    from agents.base_agent import BaseAgent
except ImportError:
    # If running as script, add the agentic_ai directory to path
    current_dir = os.path.dirname(os.path.abspath(__file__))
    agentic_ai_dir = os.path.dirname(os.path.dirname(os.path.dirname(current_dir)))
    sys.path.insert(0, agentic_ai_dir)
    from agents.base_agent import BaseAgent

from semantic_kernel.agents import Agent, ChatCompletionAgent, GroupChatOrchestration
from semantic_kernel.agents.orchestration.group_chat import (
    BooleanResult,
    GroupChatManager,
    MessageResult,
    StringResult,
)
from semantic_kernel.agents.runtime import InProcessRuntime
from semantic_kernel.connectors.ai.chat_completion_client_base import (
    ChatCompletionClientBase,
)
from semantic_kernel.connectors.ai.function_choice_behavior import (
    FunctionChoiceBehavior,
)
from semantic_kernel.connectors.ai.open_ai import AzureChatCompletion
from semantic_kernel.connectors.mcp import MCPSsePlugin
from semantic_kernel.contents import AuthorRole, ChatHistory, ChatMessageContent
from semantic_kernel.functions import KernelArguments
from semantic_kernel.kernel import Kernel
from semantic_kernel.prompt_template import KernelPromptTemplate, PromptTemplateConfig

# Configure logging
logging.basicConfig(
    level=logging.INFO, format="%(asctime)s - %(levelname)s - %(message)s"
)
logger = logging.getLogger(__name__)


class ContosoGroupChatManager(GroupChatManager):
    """Custom group chat manager for Contoso agents."""

    service: ChatCompletionClientBase
    topic: str

    termination_prompt: str = (
        "You are the discussion mediator analysis-planning agent for user questions. Evaluate if we have a complete response:\n"
        "1. For SINGLE-AGENT tasks (billing queries, security issues, product info):\n"
        "   - Terminate once the specialist has used their tools and provided data\n"
        "   - Terminate if specialist indicates they can't help\n"
        "\n"
        "2. For MULTI-AGENT tasks:, where user query might have multiple facets, make sure you route appropriate sections of questions to the right agents\n"
        "   - Terminate when all required information is gathered\n"
        "   - Terminate if we have clear next steps for the user\n"
        "\n"
        "3. ALWAYS terminate if:\n"
        "   - The response starts with 'FINAL ANSWER:'\n"
        "   - We have a clear resolution or need user clarification\n"
        "\n"
        "Respond with True only if these conditions are met, otherwise False."
    )

    selection_prompt: str = (
        "You are the discussion mediator. Select the next agent based on these rules:\n"
        "\n"
        "1. For greetings or general responses, select 'analysis_planning'\n"
        "2. For explicit delegations (e.g., 'crm_billing: <task>'), select that agent\n"
        "3. For questions about:\n"
        "   - Billing, invoices, subscriptions → select 'crm_billing'\n"
        "   - Products, promotions → select 'product_promotions'\n"
        "   - Security, authentication → select 'security_authentication'\n"
        "4. If unclear, select 'analysis_planning'\n"
        "\n"
        "Available agents:\n"
        "\nParticipants and their expertise:\n{{$participants}}\n"
        "\nSelection Rules:\n"
        "1. For DIRECT QUERIES about billing, subscriptions, invoices → crm_billing\n"
        "2. For DIRECT QUERIES about products, promotions, eligibility → product_promotions\n"
        "3. For DIRECT QUERIES about security, access, lockouts → security_authentication\n"
        "4. For COMPLEX QUERIES needing multiple perspectives:\n"
        "   - Choose the agent with most relevant expertise for current sub-task\n"
        "   - Consider previous responses and what information is still needed\n"
        "\nRespond with EXACTLY ONE agent name from the list above, no other text."
    )

    result_filter_prompt: str = (
        "You are the discussion mediator for '{{$topic}}'. Create a clear, actionable response:\n"
        "For any greeting msg like 'hi', 'hello', 'start' ask politely how you could assist the user\n"
        "\nFormatting Rules:\n"
        "1. For SINGLE-AGENT responses:\n"
        "   - Keep the specialist's specific data and policy references\n"
        "   - Maintain any structured format (e.g., invoice details)\n"
        "\n"
        "2. For MULTI-AGENT discussions:\n"
        "   - Combine relevant information from all specialists\n"
        "   - Eliminate redundancies and conflicting information\n"
        "   - Present a unified, coherent response\n"
        "\n"
        "3. ALWAYS ensure the response:\n"
        "   - Directly answers the user's question\n"
        "   - Includes specific data/policies mentioned by specialists\n"
        "   - Is structured and easy to read\n"
        "\nProvide the final answer that addresses the user's request."
    )

    def __init__(
        self,
        topic: str,
        service: ChatCompletionClientBase,
        sk_chat_history: ChatHistory = None,
        **kwargs,
    ) -> None:
        super().__init__(topic=topic, service=service, **kwargs)
        # Create execution settings with max tokens
        settings_class = self.service.get_prompt_execution_settings_class()
        self._execution_settings = settings_class(extension_data={"max_tokens": 2000})
        self._external_sk_chat_history = (
            sk_chat_history  # Reference to external chat history
        )

    async def _render_prompt(self, prompt: str, arguments: KernelArguments) -> str:
        prompt_template_config = PromptTemplateConfig(template=prompt)
        prompt_template = KernelPromptTemplate(
            prompt_template_config=prompt_template_config
        )
        return await prompt_template.render(Kernel(), arguments=arguments)

    @override
    async def should_request_user_input(
        self, chat_history: ChatHistory
    ) -> BooleanResult:
        return BooleanResult(
            result=False,
            reason="This group chat manager does not require user input.",
        )

    @override
    async def should_terminate(self, chat_history: ChatHistory) -> BooleanResult:
        should_terminate = await super().should_terminate(chat_history)
        if should_terminate.result:
            return should_terminate

        # Use external chat history if available, otherwise use the provided one
        history_to_use = (
            self._external_sk_chat_history
            if self._external_sk_chat_history
            else chat_history
        )

        # Create a copy to avoid modifying the original chat history
        temp_history = ChatHistory()
        temp_history.messages = history_to_use.messages.copy()

        temp_history.messages.insert(
            0,
            ChatMessageContent(
                role=AuthorRole.SYSTEM,
                content=await self._render_prompt(
                    self.termination_prompt,
                    KernelArguments(topic=self.topic),
                ),
            ),
        )

        response = await self.service.get_chat_message_content(
            temp_history,
            settings=self._execution_settings,
        )

        return BooleanResult(
            result=response.content.strip().upper().startswith("TRUE")
            or any(
                msg.content and "FINAL ANSWER:" in msg.content
                for msg in history_to_use.messages
                if msg.role == AuthorRole.ASSISTANT
            ),
            reason="Checking for termination conditions or FINAL ANSWER prefix",
        )

    @override
    async def select_next_agent(
        self,
        chat_history: ChatHistory,
        participant_descriptions: dict[str, str],
    ) -> StringResult:
        # Use external chat history if available, otherwise use the provided one
        history_to_use = (
            self._external_sk_chat_history
            if self._external_sk_chat_history
            else chat_history
        )

        # Create a copy to avoid modifying the original chat history
        temp_history = ChatHistory()
        temp_history.messages = history_to_use.messages.copy()

        temp_history.messages.insert(
            0,
            ChatMessageContent(
                role=AuthorRole.SYSTEM,
                content=await self._render_prompt(
                    self.selection_prompt,
                    KernelArguments(
                        topic=self.topic,
                        participants="\n".join(
                            [f"{k}: {v}" for k, v in participant_descriptions.items()]
                        ),
                    ),
                ),
            ),
        )

        response = await self.service.get_chat_message_content(
            temp_history,
            settings=self._execution_settings,
        )

        # Process the response content to get the next agent
        content = response.content.lower().strip()

        # Check for exact agent name or delegation format (e.g., "crm_billing: task")
        for agent_name in participant_descriptions.keys():
            if content == agent_name or content.startswith(f"{agent_name}:"):
                return StringResult(
                    result=agent_name, reason=f"Matched agent {agent_name}"
                )

        # Default to analysis_planning for orchestration
        return StringResult(
            result="analysis_planning",
            reason="No clear specialist match - routing to orchestrator",
        )

    @override
    async def filter_results(
        self,
        chat_history: ChatHistory,
    ) -> MessageResult:
        # Use external chat history if available, otherwise use the provided one
        history_to_use = (
            self._external_sk_chat_history
            if self._external_sk_chat_history
            else chat_history
        )

        if not history_to_use.messages:
            raise RuntimeError("No messages in the chat history.")

        # Create a copy to avoid modifying the original chat history
        temp_history = ChatHistory()
        temp_history.messages = history_to_use.messages.copy()

        temp_history.messages.insert(
            0,
            ChatMessageContent(
                role=AuthorRole.SYSTEM,
                content=await self._render_prompt(
                    self.result_filter_prompt,
                    KernelArguments(topic=self.topic),
                ),
            ),
        )

        response = await self.service.get_chat_message_content(
            temp_history,
            settings=self._execution_settings,
        )

        # Get the final response content and clean it up
        final_content = response.content.strip()

        # Remove "FINAL ANSWER:" prefix if present
        if final_content.upper().startswith("FINAL ANSWER:"):
            final_content = final_content[13:].strip()

        return MessageResult(
            result=ChatMessageContent(
                role=AuthorRole.ASSISTANT,
                content=final_content,
            ),
            reason="Final response summarized from discussion",
        )


class Agent(BaseAgent):
    def __new__(cls, state_store: dict, session_id: str):
        if session_id in state_store:
            return state_store[session_id]
        instance = super().__new__(cls)
        state_store[session_id] = instance
        return instance

    def __init__(self, state_store: dict, session_id: str) -> None:
        if hasattr(self, "_constructed"):
            return
        self._constructed = True
        super().__init__(state_store, session_id)
        self._orchestration: Optional[GroupChatOrchestration] = None
        self._group_chat_runtime: Optional[InProcessRuntime] = None
        self.sk_chat_history = ChatHistory()
        self.max_history_size = 10
        self.session_id = session_id

    def agent_response_callback(self, message: ChatMessageContent) -> None:
        """Callback function to retrieve agent responses."""
        logger.debug(f"**{message.name}**\n{message.content}")

    def get_chat_history(self) -> ChatHistory:
        """Get the chat history."""
        return self.sk_chat_history

    async def _setup_team(self) -> None:
        if getattr(self, "_initialized", False):
            return

        try:
            service = AzureChatCompletion(
                api_key=self.azure_openai_key,
                endpoint=self.azure_openai_endpoint,
                api_version=self.api_version,
                deployment_name=self.azure_deployment,
            )

            self.contoso_plugin = MCPSsePlugin(
                name="ContosoMCP",
                description="Contoso MCP Plugin",
                url=self.mcp_server_uri,
                headers={"Content-Type": "application/json"},
                timeout=30,
            )
            await self.contoso_plugin.connect()

            specialist_kernel = Kernel()
            specialist_kernel.add_service(service)
            specialist_kernel.add_plugin(self.contoso_plugin, plugin_name="ContosoMCP")

            def make_agent(name, description, instructions, included_tools=[]):
                function_choice_behavior = FunctionChoiceBehavior.Auto(
                    filters=(
                        {"included_functions": included_tools}
                        if included_tools
                        else None
                    )
                )
                return ChatCompletionAgent(
                    name=name,
                    description=description,
                    instructions=instructions,
                    service=specialist_kernel.get_service("default"),
                    function_choice_behavior=function_choice_behavior,
                    kernel=specialist_kernel,
                )

            participants = [
                make_agent(
                    name="crm_billing",
                    description="CRM & Billing Agent",
                    instructions=(
                        "You are the CRM & Billing Agent.\n"
                        "- Query structured CRM / billing systems for account, subscription, "
                        "invoice, and payment information as needed.\n"
                        "- For each response you **MUST** cross‑reference relevant *Knowledge Base* articles on billing policies, payment "
                        "processing, refund rules, etc., to ensure responses are accurate "
                        "and policy‑compliant.\n"
                        "- Reply with concise, structured information and flag any policy "
                        "concerns you detect.\n"
                        "Only respond with data you retrieve using your tools.\n"
                        "DO NOT respond to anything out of your domain."
                    ),
                    included_tools=[
                        "ContosoMCP-get_all_customers",
                        "ContosoMCP-get_customer_detail",
                        "ContosoMCP-get_subscription_detail",
                        "ContosoMCP-get_invoice_payments",
                        "ContosoMCP-pay_invoice",
                        "ContosoMCP-get_data_usage",
                        "ContosoMCP-search_knowledge_base",
                        "ContosoMCP-get_customer_orders",
                        "ContosoMCP-update_subscription",
                        "ContosoMCP-get_billing_summary",
                    ],
                ),
                make_agent(
                    name="product_promotions",
                    description="Product & Promo Agent",
                    instructions=(
                        "You are the Product & Promotions Agent.\n"
                        "- Retrieve promotional offers, product availability, eligibility "
                        "criteria, and discount information from structured sources.\n"
                        "- For each response you **MUST** cross‑reference relevant *Knowledge Base* FAQs, terms & conditions, "
                        "and best practices.\n"
                        "- Provide factual, up‑to‑date product/promo details."
                        "Only respond with data you retrieve using your tools.\n"
                        "DO NOT respond to anything out of your domain."
                    ),
                    included_tools=[
                        "ContosoMCP-get_all_customers",
                        "ContosoMCP-get_customer_detail",
                        "ContosoMCP-get_promotions",
                        "ContosoMCP-get_eligible_promotions",
                        "ContosoMCP-search_knowledge_base",
                        "ContosoMCP-get_products",
                        "ContosoMCP-get_product_detail",
                    ],
                ),
                make_agent(
                    name="security_authentication",
                    description="Security & Authentication Agent",
                    instructions=(
                        "You are the Security & Authentication Agent.\n"
                        "- Investigate authentication logs, account lockouts, and security "
                        "incidents in structured security databases.\n"
                        "- For each response you **MUST** cross‑reference relevant *Knowledge Base* security policies and "
                        "lockout troubleshooting guides.\n"
                        "- Return clear risk assessments and recommended remediation steps."
                        "Only respond with data you retrieve using your tools.\n"
                        "DO NOT respond to anything out of your domain."
                    ),
                    included_tools=[
                        "ContosoMCP-get_all_customers",
                        "ContosoMCP-get_customer_detail",
                        "ContosoMCP-get_security_logs",
                        "ContosoMCP-search_knowledge_base",
                        "ContosoMCP-unlock_account",
                    ],
                ),
                make_agent(
                    name="analysis_planning",
                    description="Analysis & Planning Agent",
                    instructions=(
                        "You are the Analysis & Planning Agent (the planner/orchestrator).\n"
                        "\n"
                        "1. Decide if the user’s request can be satisfied directly:\n"
                        "   - If YES (e.g. greetings, very simple Q&A), answer immediately using the prefix:\n"
                        "     FINAL ANSWER: <your reply>\n"
                        "\n"
                        "2. Otherwise you MUST delegate atomic sub‑tasks one‑by‑one to specialists.\n"
                        "   - Output format WHEN DELEGATING (strict):\n"
                        "       <specialist_name>: <task>\n"
                        "     – No other text, no quotation marks, no ‘FINAL ANSWER’.\n"
                        "   - Delegate only one sub‑task per turn, then wait for the specialist’s reply.\n"
                        "\n"
                        "3. After all required information is gathered, compose ONE comprehensive response and\n"
                        "   send it to the user prefixed with:\n"
                        "   FINAL ANSWER: <your synthesized reply>\n"
                        "\n"
                        "4. If you need clarification from the user, ask it immediately and prefix with\n"
                        "   FINAL ANSWER: <your question>\n"
                        "\n"
                        "Specialist directory – choose the SINGLE best match for each sub‑task:\n"
                        "- crm_billing – Accesses CRM & billing systems for account, subscription, invoice,\n"
                        "  payment status, refunds and policy compliance questions.\n"
                        "- product_promotions – Provides product catalogue details, current promotions,\n"
                        "  discount eligibility rules and T&Cs from structured sources & FAQs.\n"
                        "- security_authentication – Investigates authentication logs, account lock‑outs,\n"
                        "  security incidents; references security KBs and recommends remediation steps.\n"
                        "\n"
                        "STRICT RULES:\n"
                        "- Do not emit planning commentary or bullet lists to the user.\n"
                        "- Only ‘FINAL ANSWER’ messages or specialist delegations are allowed.\n"
                        "- After all agents discuss, make sure you respond only relevant information asked as per user request.\n"
                        "- Never include ‘FINAL ANSWER’ when talking to a specialist.\n"
                    ),
                ),
            ]

            self._orchestration = GroupChatOrchestration(
                members=participants,
                manager=ContosoGroupChatManager(
                    topic="Handle user request",
                    service=service,
                    max_rounds=5,
                    sk_chat_history=self.sk_chat_history,
                ),
                agent_response_callback=self.agent_response_callback,
            )

            self._group_chat_runtime = InProcessRuntime()
            self._group_chat_runtime.start()
            self._initialized = True
            logger.info("Team setup completed successfully")

        except Exception as e:
            logger.error(f"Error in team setup: {str(e)}", exc_info=True)
            raise

    async def chat_async(self, user_input: str) -> str:
        try:
            # Initialize team if needed (only when not yet initialized)
            await self._setup_team()

            self.sk_chat_history.add_message(
                ChatMessageContent(role=AuthorRole.USER, content=user_input)
            )

            try:
                result = await self._orchestration.invoke(
                    task=user_input,
                    runtime=self._group_chat_runtime,
                )

                response = await result.get()
                final_response = str(response.content)

                self.sk_chat_history.add_message(
                    ChatMessageContent(
                        role=AuthorRole.ASSISTANT, content=final_response
                    )
                )

                user_dict = {"role": "user", "content": user_input}
                assistant_dict = {"role": "assistant", "content": final_response}
                messages = [user_dict, assistant_dict]
                self.append_to_chat_history(messages)

                if len(self.sk_chat_history.messages) > self.max_history_size:
                    self.sk_chat_history.messages = self.sk_chat_history.messages[
                        -self.max_history_size :
                    ]

                return final_response

            except Exception as chat_error:
                logger.error("Chat orchestration error", exc_info=True)
                raise

        except Exception as e:
            logger.error("Error in chat_async", exc_info=True)
            return f"I encountered an error while processing your request. Please try again or start a new conversation. Error: {str(e)}"

    async def cleanup(self):
        """Cleanup method to properly stop the runtime"""
        if self._group_chat_runtime:
            try:
                await self._group_chat_runtime.stop()
            except Exception as e:
                logger.error(f"Error stopping runtime: {str(e)}")

        if hasattr(self, "contoso_plugin"):
            try:
                await self.contoso_plugin.close()
            except Exception as e:
                logger.error(f"Error closing plugin: {str(e)}")


if __name__ == "__main__":

    async def run_test():
        dummy_state = {}
        session_id = "demo_session"
        agent = Agent(dummy_state, session_id=session_id)

        try:
            print("You can ask questions continuously. Type 'exit' or 'end' to quit.\n")
            while True:
                user_input = input("You: ").strip()
                if user_input.lower() in ("exit", "end"):
                    print("Exiting chat...")
                    break

                response = await agent.chat_async(user_input)
                print("Assistant:", response)

        finally:
            # Ensure proper cleanup
            await agent.cleanup()

    asyncio.run(run_test())
