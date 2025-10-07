import asyncio
import inspect
import json
import logging
import os
from threading import Lock as ThreadLock
from typing import Any, Callable, Dict, Iterable, List, Optional, cast

from agent_framework import (
    ChatAgent,
    MagenticBuilder,
    MCPStreamableHTTPTool,
    WorkflowCheckpoint,
    WorkflowOutputEvent,
    CheckpointStorage,
    MagenticCallbackEvent,
    MagenticCallbackMode,
    MagenticOrchestratorMessageEvent,
    MagenticAgentDeltaEvent,
    MagenticAgentMessageEvent,
    MagenticFinalResultEvent,
)
from agent_framework.azure import AzureOpenAIChatClient  # type: ignore[import]

from agents.base_agent import BaseAgent
from agents.agent_framework.utils import create_filtered_tool_list

logger = logging.getLogger(__name__)


class DictCheckpointStorage(CheckpointStorage):
    """Dictionary-backed checkpoint storage that persists across Agent instances."""

    _RETENTION = 5

    def __init__(self, backing_store: Dict[str, Any]) -> None:
        self._backing = backing_store
        self._checkpoints: Dict[str, Dict[str, Any]] = backing_store.setdefault("checkpoints", {})
        self._async_lock = asyncio.Lock()
        self._sync_lock = ThreadLock()

    async def save_checkpoint(self, checkpoint: WorkflowCheckpoint) -> str:
        async with self._async_lock:
            self._checkpoints[checkpoint.checkpoint_id] = checkpoint.to_dict()
            self._backing["latest_checkpoint"] = checkpoint.checkpoint_id
            self._backing["workflow_id"] = checkpoint.workflow_id

            if len(self._checkpoints) > self._RETENTION:
                sorted_ids = sorted(
                    self._checkpoints.items(),
                    key=lambda item: (item[1].get("timestamp", ""), item[1].get("iteration_count", 0)),
                )
                for checkpoint_id, _ in sorted_ids[:-self._RETENTION]:
                    self._checkpoints.pop(checkpoint_id, None)
            return checkpoint.checkpoint_id

    async def load_checkpoint(self, checkpoint_id: str) -> WorkflowCheckpoint | None:
        async with self._async_lock:
            data = self._checkpoints.get(checkpoint_id)
            if not data:
                return None
            return WorkflowCheckpoint.from_dict(data)

    async def list_checkpoint_ids(self, workflow_id: str | None = None) -> List[str]:
        async with self._async_lock:
            if workflow_id is None:
                return list(self._checkpoints.keys())
            return [cid for cid, data in self._checkpoints.items() if data.get("workflow_id") == workflow_id]

    async def list_checkpoints(self, workflow_id: str | None = None) -> List[WorkflowCheckpoint]:
        async with self._async_lock:
            if workflow_id is None:
                ids = list(self._checkpoints.keys())
            else:
                ids = [cid for cid, data in self._checkpoints.items() if data.get("workflow_id") == workflow_id]
            return [WorkflowCheckpoint.from_dict(self._checkpoints[cid]) for cid in ids]

    async def delete_checkpoint(self, checkpoint_id: str) -> bool:
        async with self._async_lock:
            removed = self._checkpoints.pop(checkpoint_id, None)
            if removed and self._backing.get("latest_checkpoint") == checkpoint_id:
                self._backing.pop("latest_checkpoint", None)
            return removed is not None

    @property
    def latest_checkpoint_id(self) -> str | None:
        with self._sync_lock:
            return self._backing.get("latest_checkpoint")

    def mark_pending_prompt(self, prompt: str) -> None:
        with self._sync_lock:
            self._backing["pending_prompt"] = prompt

    def consume_pending_prompt(self) -> str | None:
        with self._sync_lock:
            prompt = self._backing.get("pending_prompt")
            if prompt is not None:
                self._backing.pop("pending_prompt", None)
            return prompt

    def clear_all(self) -> None:
        with self._sync_lock:
            self._checkpoints.clear()
            self._backing.pop("latest_checkpoint", None)
            self._backing.pop("workflow_id", None)
            self._backing.pop("pending_prompt", None)


class Agent(BaseAgent):
    """Agent Framework implementation of the collaborative Magentic team."""

    DEFAULT_MANAGER_INSTRUCTIONS = (
        "You are the Analysis & Planning orchestrator for a team of internal specialists handling Contoso customer support. "
        "**CRITICAL: You are the ONLY agent that communicates directly with the customer. Specialists communicate only with YOU.** "
        "Break down the user's needs, decide which specialist should respond, and integrate their findings into the final customer-facing answer. "
        "**IMPORTANT: Instruct participants to use their tools to retrieve factual data. Do not allow speculative or hallucinated answers.** "
        "Each participant MUST call the appropriate tool and cite the tool results (with IDs, timestamps, or specific data points) in their response to you. "
        "**If a specialist reports they need more information from the user (like customer ID, account details, etc.), "
        "YOU must translate that into a polite customer-facing request and deliver it as FINAL_ANSWER immediately - do NOT loop or wait.** "
        "After gathering sufficient information from specialists (typically 1-3 rounds), synthesize their responses into "
        "a clear, customer-friendly answer and deliver it prefixed with 'FINAL_ANSWER:'. "
        "**DO NOT loop indefinitely - once you have tool-backed answers OR a request for user information, conclude with FINAL_ANSWER.**"
    )

    CUSTOM_PROGRESS_LEDGER_PROMPT = """
Recall we are working on the following request:

{task}

And we have assembled the following team:

{team}

To make progress on the request, please answer the following questions, including necessary reasoning:

    - Is the request fully satisfied? (True if EITHER:
      a) The original request has been SUCCESSFULLY and FULLY addressed with factual, tool-backed answers, OR
      b) We need additional information or clarification from the user that we cannot obtain ourselves
      (e.g., customer ID, account number, email, phone, personal preferences, missing context that only the user can provide).
      
      False if the original request has NOT been addressed AND we have all the information we need to continue working.)
      
    - Are we in a loop where we are repeating the same requests and or getting the same responses as before?
      Loops can span multiple turns, and can include repeated actions. NOTE: If specialists say they "need customer ID"
      or similar user information, that is NOT a loop - it means we should complete with a request to the user
      (is_request_satisfied=True).
      
    - Are we making forward progress? (True if just starting, or recent messages are adding value or identifying needed
      information. False if recent messages show evidence of being stuck in a loop or if there is evidence of significant
      barriers to success. NOTE: Specialists identifying that they need user information IS forward progress - they've
      determined what's needed to proceed.)
      
    - Who should speak next? (select from: {names}. NOTE: If is_request_satisfied is True because we need user
      input, this field is ignored but you must still provide a valid name from the list.)
      
    - What instruction or question would you give this team member? (If is_request_satisfied is True because we
      need user input, phrase this as a polite, customer-facing question asking for the missing information.
      Otherwise, phrase as if speaking directly to the specialist team member, and include any specific information
      they may need to complete their task.)

Please output an answer in pure JSON format according to the following schema. The JSON object must be parsable as-is.
DO NOT OUTPUT ANYTHING OTHER THAN JSON, AND DO NOT DEVIATE FROM THIS SCHEMA:

{{
    "is_request_satisfied": {{
        "reason": string (explain whether we have a complete answer OR need user input to proceed),
        "answer": boolean
    }},
    "is_in_loop": {{
        "reason": string,
        "answer": boolean
    }},
    "is_progress_being_made": {{
        "reason": string,
        "answer": boolean
    }},
    "next_speaker": {{
        "reason": string,
        "answer": string (select from: {names})
    }},
    "instruction_or_question": {{
        "reason": string,
        "answer": string (if is_request_satisfied=True and we need user input, phrase this as a polite user-facing question)
    }}
}}
"""

    def __init__(
        self,
        state_store: Dict[str, Any],
        session_id: str,
        access_token: str | None = None,
        *,
        config: Optional[Dict[str, Any]] = None,
        checkpoint_storage_factory: Optional[
            Callable[[Dict[str, Any], str], CheckpointStorage]
        ] = None,
    ) -> None:
        super().__init__(state_store, session_id)
        self._access_token = access_token
        self._config = self._load_effective_config(config)
        self._checkpoint_storage_factory = (
            checkpoint_storage_factory
            or self.state_store.get("magentic_checkpoint_storage_factory")
        )
        storage_override = self.state_store.get("magentic_checkpoint_storage")
        self._checkpoint_storage_override: Optional[CheckpointStorage] = self._coerce_checkpoint_storage(
            storage_override
        )
        if storage_override and not self._checkpoint_storage_override:
            logger.warning(
                "[AgentFramework-Magentic] Ignoring checkpoint storage override because it does not implement CheckpointStorage."
            )
        self._participant_client: Optional[AzureOpenAIChatClient] = None
        self._manager_client: Optional[AzureOpenAIChatClient] = None
        self._workflow_event_logging_enabled = bool(self._config.get("log_workflow_events", False))
        self._enable_plan_review = bool(self._config.get("enable_plan_review", False))
        self._manager_instructions = self._config.get(
            "manager_instructions", self.DEFAULT_MANAGER_INSTRUCTIONS
        )
        self._max_round_count = int(self._config.get("max_round_count", 4))
        self._max_stall_count = int(self._config.get("max_stall_count", 2))
        self._max_reset_count = int(self._config.get("max_reset_count", 1))
        self._participant_overrides: Dict[str, Dict[str, Any]] = self._config.get("participant_overrides", {})
        self._pending_prompt_state_key = f"{self.session_id}_magentic_pending_prompt"
        self._in_memory_checkpoint_storage: Optional[DictCheckpointStorage] = None
        self._ws_manager = None  # Will be set from backend if available
        self._stream_agent_id: Optional[str] = None
        self._stream_line_open: bool = False
        self._last_agent_message: Optional[str] = None  # Track last agent message for deduplication

    def set_websocket_manager(self, manager: Any) -> None:
        """Allow backend to inject WebSocket manager for streaming events."""
        self._ws_manager = manager

    async def chat_async(self, prompt: str) -> str:
        self._validate_configuration()

        checkpoint_state = self.state_store.setdefault(f"{self.session_id}_magentic_checkpoint", {})
        checkpoint_storage = self._create_checkpoint_storage(checkpoint_state)

        headers = self._build_headers()
        tools = await self._maybe_create_tools(headers)

        # First resume any previous unfinished run before processing the new prompt
        resume_answer = await self._resume_previous_run(checkpoint_storage, tools)
        if resume_answer:
            logger.info("[AgentFramework-Magentic] Resumed unfinished workflow before handling new prompt.")

        participant_client = self._get_participant_client()
        manager_client = self._get_manager_client()

        task = self._render_task_with_history(prompt)
        await self._mark_pending_prompt(checkpoint_storage, prompt)

        workflow = await self._build_workflow(participant_client, manager_client, tools, checkpoint_storage)

        final_answer = await self._run_workflow(workflow, checkpoint_storage, task)
        if final_answer is None:
            logger.warning(
                "[AgentFramework-Magentic] No final answer produced; leaving checkpoint for potential resume."
            )
            return (
                "The agent team is still working through the previous request. Please try again in a moment so we "
                "can resume from the saved progress."
            )

        cleaned_answer = self._sanitize_final_answer(final_answer)
        if cleaned_answer is None:
            return (
                "The Magentic coordinator could not produce a final response. Please try again later or contact support."
            )

        self.append_to_chat_history(
            [
                {"role": "user", "content": prompt},
                {"role": "assistant", "content": cleaned_answer},
            ]
        )

        await self._reset_checkpoint_progress(checkpoint_storage)
        self._setstate({"mode": "magentic_collaboration"})

        return cleaned_answer

    def _validate_configuration(self) -> None:
        if not all([self.azure_openai_key, self.azure_deployment, self.azure_openai_endpoint, self.api_version]):
            raise RuntimeError(
                "Azure OpenAI configuration is incomplete. Ensure AZURE_OPENAI_API_KEY, "
                "AZURE_OPENAI_CHAT_DEPLOYMENT, AZURE_OPENAI_ENDPOINT, and AZURE_OPENAI_API_VERSION are set."
            )

    def _build_headers(self) -> Dict[str, str]:
        headers: Dict[str, str] = {"Content-Type": "application/json"}
        if self._access_token:
            headers["Authorization"] = f"Bearer {self._access_token}"
        return headers

    async def _maybe_create_tools(self, headers: Dict[str, str]) -> List[MCPStreamableHTTPTool] | None:
        if not self.mcp_server_uri:
            logger.warning("MCP_SERVER_URI is not configured; multi-agent team will run without MCP tools.")
            return None

        logger.info(f"[MCP SETUP] Creating MCP tool with URI: {self.mcp_server_uri}")
        request_headers = dict(headers)
        header_overrides = self._config.get("mcp_headers")
        if isinstance(header_overrides, dict):
            request_headers.update({str(key): str(value) for key, value in header_overrides.items()})

        logger.info(f"[MCP SETUP] Request headers: {list(request_headers.keys())}")
        timeout_seconds = int(self._config.get("mcp_timeout_seconds", 30))
        request_timeout_seconds = int(self._config.get("mcp_request_timeout_seconds", timeout_seconds))
        retry_attempts = max(1, int(self._config.get("mcp_startup_retries", 1)))
        retry_backoff = float(self._config.get("mcp_retry_backoff_seconds", 2.0))

        last_error: Exception | None = None
        for attempt in range(1, retry_attempts + 1):
            try:
                tool = MCPStreamableHTTPTool(
                    name="mcp-streamable",
                    url=self.mcp_server_uri,
                    headers=request_headers,
                    timeout=timeout_seconds,
                    request_timeout=request_timeout_seconds,
                )
                logger.info(f"[MCP SETUP] Successfully created MCP tool: {tool}")
                return [tool]
            except Exception as exc:  # pragma: no cover - defensive path
                last_error = exc
                if attempt < retry_attempts:
                    wait_time = retry_backoff * attempt
                    logger.warning(
                        "Failed to initialise MCP tool (attempt %s/%s): %s. Retrying in %.1fs.",
                        attempt,
                        retry_attempts,
                        exc,
                        wait_time,
                    )
                    await asyncio.sleep(wait_time)
                else:
                    logger.error(
                        "Failed to initialise MCP tool after %s attempts: %s",
                        retry_attempts,
                        exc,
                        exc_info=True,
                    )

        return None

    def _get_participant_client(self) -> AzureOpenAIChatClient:
        if self._participant_client is None:
            self._participant_client = self._build_chat_client()
        return self._participant_client

    def _get_manager_client(self) -> AzureOpenAIChatClient:
        if self._manager_client is None:
            self._manager_client = self._build_chat_client()
        return self._manager_client

    def _build_chat_client(self) -> AzureOpenAIChatClient:
        return AzureOpenAIChatClient(
            api_key=self.azure_openai_key,
            deployment_name=self.azure_deployment,
            endpoint=self.azure_openai_endpoint,
            api_version=self.api_version,
        )

    async def _resume_previous_run(
        self,
        checkpoint_storage: CheckpointStorage,
        tools: List[MCPStreamableHTTPTool] | None,
    ) -> str | None:
        resume_id = await self._get_latest_checkpoint_id(checkpoint_storage)
        if not resume_id:
            return None

        logger.info("[AgentFramework-Magentic] Attempting to resume workflow from checkpoint %s", resume_id)
        participant_client = self._get_participant_client()
        manager_client = self._get_manager_client()
        workflow = await self._build_workflow(participant_client, manager_client, tools, checkpoint_storage)

        try:
            final_answer = await self._run_workflow(workflow, checkpoint_storage, None, resume_id)
        except Exception as exc:  # pragma: no cover - defensive resume path
            logger.error("[AgentFramework-Magentic] Failed to resume workflow: %s", exc, exc_info=True)
            await self._reset_checkpoint_progress(checkpoint_storage)
            return None

        if final_answer is None:
            await self._reset_checkpoint_progress(checkpoint_storage)
            return None

        cleaned_answer = self._sanitize_final_answer(final_answer)
        if cleaned_answer is None:
            await self._reset_checkpoint_progress(checkpoint_storage)
            return None
        original_prompt = await self._consume_pending_prompt(checkpoint_storage)
        if original_prompt:
            self.append_to_chat_history(
                [
                    {"role": "user", "content": original_prompt},
                    {"role": "assistant", "content": cleaned_answer},
                ]
            )
        else:
            self.append_to_chat_history(
                [
                    {"role": "assistant", "content": cleaned_answer},
                ]
            )

        await self._reset_checkpoint_progress(checkpoint_storage)
        return cleaned_answer

    async def _build_workflow(
        self,
        participant_client: AzureOpenAIChatClient,
        manager_client: AzureOpenAIChatClient,
        tools: List[MCPStreamableHTTPTool] | None,
        checkpoint_storage: CheckpointStorage,
    ) -> Any:
        participants = await self._create_participants(participant_client, tools)

        builder = MagenticBuilder().participants(**participants)
        
        # Register streaming callback if WebSocket is available (MUST be before with_standard_manager)
        if self._ws_manager:
            logger.info(f"[STREAMING] Registering streaming callback for magentic events, session_id={self.session_id}")
            logger.info(f"[STREAMING] WebSocket manager type: {type(self._ws_manager)}")
            logger.info(f"[STREAMING] Callback function: {self._stream_magentic_event}")
            builder = builder.on_event(self._stream_magentic_event, mode=MagenticCallbackMode.STREAMING)
            logger.info("[STREAMING] Callback registered successfully")
        elif self._workflow_event_logging_enabled:
            logger.info("[STREAMING] Using workflow event logging instead of streaming")
            builder = builder.on_event(self._log_workflow_event)
        
        builder = (
            builder
            .with_standard_manager(
                chat_client=manager_client,
                instructions=self._manager_instructions,
                max_round_count=self._max_round_count,
                max_stall_count=self._max_stall_count,
                max_reset_count=self._max_reset_count,
                progress_ledger_prompt=self.CUSTOM_PROGRESS_LEDGER_PROMPT,
            )
            .with_checkpointing(checkpoint_storage)
        )

        # Optional: enable plan review if available
        if self._enable_plan_review:
            enable_plan_review = getattr(builder, "enable_plan_review", None)
            if callable(enable_plan_review):
                try:
                    builder = enable_plan_review()
                except Exception as exc:
                    logger.warning(
                        "[AgentFramework-Magentic] Failed to enable plan review: %s", exc
                    )
            else:
                logger.debug(
                    "[AgentFramework-Magentic] Plan review requested but not available in this framework version."
                )

        return builder.build()

    async def _create_participants(
        self,
        participant_client: AzureOpenAIChatClient,
        tools: Iterable[MCPStreamableHTTPTool] | None,
    ) -> Dict[str, ChatAgent]:
        # Get base MCP tool (connect once, filter per agent)
        base_mcp_tool = tools[0] if tools else None
        logger.info(f"[MCP PARTICIPANTS] Creating participants with base MCP tool: {base_mcp_tool}")
        
        # CRITICAL: Connect to MCP server BEFORE filtering to load all available tools
        if base_mcp_tool:
            await base_mcp_tool.__aenter__()
            logger.info(f"[MCP PARTICIPANTS] Connected to MCP server, loaded {len(base_mcp_tool.functions)} tools")
        
        base_definitions: Dict[str, Dict[str, Any]] = {
            "crm_billing": {
                "name": "crm_billing",
                "description": (
                    "Agent specializing in customer account, subscription, billing inquiries, invoices, payments, and related policy checks."
                ),
                "tools": [
                    "get_all_customers",
                    "get_customer_detail",
                    "get_subscription_detail",
                    "get_billing_summary",
                    "get_invoice_payments",
                    "pay_invoice",
                    "get_data_usage",
                    "update_subscription",
                    "search_knowledge_base",
                ],
                "instructions": (
                    "You are the CRM & Billing **internal specialist**.\n"
                    "**CRITICAL: You communicate ONLY with the orchestrator, NOT directly with the customer.**\n"
                    "**CRITICAL: You MUST use your tools to retrieve factual data. NEVER guess or hallucinate information.**\n"
                    "- For ANY customer-specific question, call the appropriate tool (get_customer_detail, get_billing_summary, etc.).\n"
                    "- If you don't have necessary identifiers (customer ID, email, phone), inform the orchestrator: "
                    "'I need the customer ID, email, or phone number to retrieve this information.'\n"
                    "- Query structured CRM / billing systems for account, subscription, invoice, and payment information.\n"
                    "- Cross-check Knowledge Base articles on billing policies, payment processing, refund rules, and compliance.\n"
                    "- Reply to the orchestrator with concise, structured information and flag any policy concerns you detect.\n"
                    "- Explicitly cite the tool results (customer ID, invoice numbers, amounts, timestamps) that back your answer.\n"
                    "- If no tool can answer the question, state 'I cannot answer this without the appropriate tool' instead of guessing.\n"
                    "**Remember: The orchestrator will translate your response into customer-friendly language. Focus on accuracy and completeness.**"
                ),
            },
            "product_promotions": {
                "name": "product_promotions",
                "description": (
                    "Agent for retrieving and explaining product availability, promotions, discounts, eligibility, and terms."
                ),
                "tools": [
                    "get_products",
                    "get_product_detail",
                    "get_promotions",
                    "get_eligible_promotions",
                    "get_customer_orders",
                    "search_knowledge_base",
                ],
                "instructions": (
                    "You are the Product & Promotions **internal specialist**.\n"
                    "**CRITICAL: You communicate ONLY with the orchestrator, NOT directly with the customer.**\n"
                    "**CRITICAL: You MUST use your tools to retrieve factual data. NEVER guess or hallucinate information.**\n"
                    "- For ANY product or promotion question, call the appropriate tool (get_products, get_promotions, get_eligible_promotions, etc.).\n"
                    "- If you need more information (customer ID for eligibility, product category, etc.), inform the orchestrator: "
                    "'I need the customer ID to check promotion eligibility.'\n"
                    "- Retrieve promotional offers, product availability, eligibility criteria, and discount information from structured sources.\n"
                    "- Cross-reference Knowledge Base FAQs, terms & conditions, and best practices in every response.\n"
                    "- Provide factual, up-to-date product/promo details to the orchestrator, citing the tool outputs or documents you referenced.\n"
                    "- If no tool can answer the question, state 'I cannot answer this without the appropriate tool' instead of guessing.\n"
                    "**Remember: The orchestrator will translate your response into customer-friendly language. Focus on accuracy and completeness.**"
                ),
            },
            "security_authentication": {
                "name": "security_authentication",
                "description": (
                    "Agent focusing on security incidents, authentication issues, lockouts, and risk mitigation guidance."
                ),
                "tools": [
                    "get_security_logs",
                    "unlock_account",
                    "get_support_tickets",
                    "create_support_ticket",
                    "search_knowledge_base",
                ],
                "instructions": (
                    "You are the Security & Authentication **internal specialist**.\n"
                    "**CRITICAL: You communicate ONLY with the orchestrator, NOT directly with the customer.**\n"
                    "**CRITICAL: You MUST use your tools to retrieve factual data. NEVER guess or hallucinate information.**\n"
                    "- For ANY security or authentication question, call the appropriate tool (get_security_logs, unlock_account, etc.).\n"
                    "- If you need more information (customer ID, account details, etc.), inform the orchestrator: "
                    "'I need the customer ID to retrieve security logs.'\n"
                    "- Investigate authentication logs, account lockouts, and security incidents using your tools.\n"
                    "- Always cross-reference Knowledge Base security policies and troubleshooting guides.\n"
                    "- Return clear risk assessments, list the log entries or tool findings you relied on, and recommend remediation steps grounded in those outputs.\n"
                    "- If no tool can answer the question, state 'I cannot answer this without the appropriate tool' instead of guessing."
                ),
            },
        }

        participants: Dict[str, ChatAgent] = {}
        for participant_id, defaults in base_definitions.items():
            agent_kwargs: Dict[str, Any] = {
                **defaults,
                "chat_client": participant_client,
                "model": self.openai_model_name,
            }
            
            # Apply tool filtering for this participant's domain
            if base_mcp_tool is not None and "tools" in defaults:
                filtered_tools = create_filtered_tool_list(
                    base_mcp_tool=base_mcp_tool,
                    allowed_tool_names=defaults["tools"],
                    agent_name=participant_id
                )
                if filtered_tools:
                    agent_kwargs["tools"] = filtered_tools
                    logger.info(f"[MCP PARTICIPANTS] Assigned {len(filtered_tools)} filtered tools to agent '{participant_id}'")
            elif base_mcp_tool is not None and "tools" not in agent_kwargs:
                # Fallback: if no tool list defined, give all tools
                agent_kwargs["tools"] = base_mcp_tool
                logger.warning(f"[MCP PARTICIPANTS] No tool filter for '{participant_id}', using all tools")

            merged_kwargs = self._apply_participant_overrides(participant_id, agent_kwargs)
            agent = ChatAgent(**merged_kwargs)
            
            # Initialize agent session (MCP tool already connected above)
            await agent.__aenter__()
            logger.info(f"[MCP PARTICIPANTS] Initialized agent session for '{participant_id}'")
            
            participants[participant_id] = agent

        return participants

    def _apply_participant_overrides(self, participant_id: str, defaults: Dict[str, Any]) -> Dict[str, Any]:
        overrides = self._participant_overrides.get(participant_id, {})
        if not overrides:
            return defaults

        merged = {**defaults, **overrides}

        if overrides.get("tools") == "inherit":
            merged["tools"] = defaults.get("tools")

        return merged

    async def _run_workflow(
        self,
        workflow: Any,
        checkpoint_storage: CheckpointStorage,
        task: str | None,
        checkpoint_id: str | None = None,
    ) -> str | None:
        final_answer: str | None = None

        try:
            if checkpoint_id:
                async for event in workflow.run_stream_from_checkpoint(checkpoint_id, checkpoint_storage):
                    if isinstance(event, WorkflowOutputEvent):
                        final_answer = self._extract_text_from_event(event)
            else:
                async for event in workflow.run_stream(task):
                    if isinstance(event, WorkflowOutputEvent):
                        final_answer = self._extract_text_from_event(event)
        except Exception as exc:
            logger.error("[AgentFramework-Magentic] workflow failure: %s", exc, exc_info=True)
            return None

        return final_answer

    @staticmethod
    def _extract_text_from_event(event: WorkflowOutputEvent) -> str:
        data = event.data
        if hasattr(data, "text") and getattr(data, "text"):
            return str(getattr(data, "text"))
        return str(data)

    async def _log_workflow_event(self, event: Any) -> None:
        if isinstance(event, WorkflowOutputEvent):
            logger.debug("[AgentFramework-Magentic] Workflow output event: %s", event.data)
        else:
            logger.debug("[AgentFramework-Magentic] Workflow event emitted: %s", getattr(event, "name", type(event).__name__))

    async def _stream_magentic_event(self, event: MagenticCallbackEvent) -> None:
        """Stream Magentic workflow events to WebSocket clients."""
        if not self._ws_manager:
            return

        try:
            if isinstance(event, MagenticOrchestratorMessageEvent):
                # Manager/orchestrator thinking or planning
                message_text = getattr(event.message, "text", "") if event.message else ""
                await self._ws_manager.broadcast(
                    self.session_id,
                    {
                        "type": "orchestrator",
                        "kind": event.kind,  # e.g., "plan", "progress", "result"
                        "content": message_text,
                    },
                )

            elif isinstance(event, MagenticAgentDeltaEvent):
                # Streaming token from participant agent
                if self._stream_agent_id != event.agent_id or not self._stream_line_open:
                    self._stream_agent_id = event.agent_id
                    self._stream_line_open = True
                    await self._ws_manager.broadcast(
                        self.session_id,
                        {
                            "type": "agent_start",
                            "agent_id": event.agent_id,
                            "show_message_in_internal_process": True,  # Convention: show full agent details
                        },
                    )

                # Check for tool/function calls in the delta event
                if event.function_call_name:
                    await self._ws_manager.broadcast(
                        self.session_id,
                        {
                            "type": "tool_called",
                            "agent_id": event.agent_id,
                            "tool_name": event.function_call_name,
                        },
                    )

                # Stream text tokens
                if event.text:
                    await self._ws_manager.broadcast(
                        self.session_id,
                        {
                            "type": "agent_token",
                            "agent_id": event.agent_id,
                            "content": event.text,
                        },
                    )

            elif isinstance(event, MagenticAgentMessageEvent):
                # Complete message from participant
                if self._stream_line_open:
                    self._stream_line_open = False

                msg = event.message
                if msg:
                    message_text = getattr(msg, "text", "")
                    role = getattr(msg, "role", None)
                    
                    # Store last agent message for deduplication with final result
                    self._last_agent_message = message_text
                    
                    await self._ws_manager.broadcast(
                        self.session_id,
                        {
                            "type": "agent_message",
                            "agent_id": event.agent_id,
                            "role": role.value if role else "assistant",
                            "content": message_text,
                        },
                    )

            elif isinstance(event, MagenticFinalResultEvent):
                # Final workflow result - skip if identical to last agent message
                final_text = getattr(event.message, "text", "") if event.message else ""
                
                # Sanitize the final text to remove FINAL_ANSWER prefix
                cleaned_final_text = self._sanitize_final_answer(final_text) or final_text
                
                # Only send if different from the last agent message
                if final_text != self._last_agent_message:
                    await self._ws_manager.broadcast(
                        self.session_id,
                        {
                            "type": "final_result",
                            "content": cleaned_final_text,
                        },
                    )
                else:
                    logger.info("[STREAMING] Skipping duplicate final_result (same as last agent_message)")
                
                # Reset for next request
                self._last_agent_message = None

        except Exception as exc:
            logger.error("[AgentFramework-Magentic] Failed to stream event: %s", exc, exc_info=True)

    def _render_task_with_history(self, prompt: str) -> str:
        if not self.chat_history:
            return prompt

        formatted_turns = []
        for turn in self.chat_history:
            role = turn.get("role", "user").lower()
            speaker = "User" if role == "user" else "Assistant"
            content = turn.get("content", "")
            formatted_turns.append(f"{speaker}: {content}")

        formatted_turns.append(f"User: {prompt}")
        formatted_turns.append(
            "System: Provide an updated response that respects the prior conversation while focusing on the latest user message."
        )
        return "\n".join(formatted_turns)

    def _sanitize_final_answer(self, final_answer: Optional[str]) -> Optional[str]:
        """Remove FINAL_ANSWER prefix from workflow output."""
        if not final_answer:
            return None

        # Try all known marker variations
        for marker in ["FINAL_ANSWER:", "FINAL ANSWER:", "FINALANSWER:"]:
            if marker in final_answer:
                return final_answer.split(marker, maxsplit=1)[-1].strip()

        # No marker found - return cleaned text
        return final_answer.strip() or None

    def _create_checkpoint_storage(self, checkpoint_state: Dict[str, Any]) -> CheckpointStorage:
        if self._checkpoint_storage_override:
            return self._checkpoint_storage_override

        if self._checkpoint_storage_factory:
            storage = self._checkpoint_storage_factory(checkpoint_state, self.session_id)
            if storage:
                if self._config.get("cache_factory_storage", True):
                    self.state_store["magentic_checkpoint_storage"] = storage
                    self._checkpoint_storage_override = storage
                return storage
            logger.warning(
                "[AgentFramework-Magentic] Provided checkpoint storage factory returned None; falling back to in-memory storage."
            )

        if self._in_memory_checkpoint_storage is None:
            self._in_memory_checkpoint_storage = DictCheckpointStorage(checkpoint_state)
        return self._in_memory_checkpoint_storage

    def _coerce_checkpoint_storage(self, candidate: Any) -> Optional[CheckpointStorage]:
        if candidate is None:
            return None

        required_methods = [
            "save_checkpoint",
            "load_checkpoint",
        ]

        for method_name in required_methods:
            method = getattr(candidate, method_name, None)
            if not callable(method):
                return None

        return cast(CheckpointStorage, candidate)

    def _load_effective_config(self, runtime_config: Optional[Dict[str, Any]]) -> Dict[str, Any]:
        merged: Dict[str, Any] = {}
        env_config = self._load_env_config()
        if env_config:
            merged.update(env_config)

        store_config = self.state_store.get("magentic_config")
        if isinstance(store_config, dict):
            merged.update(store_config)

        if runtime_config:
            merged.update(runtime_config)

        return merged

    def _load_env_config(self) -> Dict[str, Any]:
        env_config: Dict[str, Any] = {}

        manager_instructions = os.getenv("MAGENTIC_MANAGER_INSTRUCTIONS")
        if manager_instructions:
            env_config["manager_instructions"] = manager_instructions.strip()

        max_rounds = self._maybe_parse_int(os.getenv("MAGENTIC_MAX_ROUNDS"))
        if max_rounds is not None:
            env_config["max_round_count"] = max_rounds

        max_stalls = self._maybe_parse_int(os.getenv("MAGENTIC_MAX_STALLS"))
        if max_stalls is not None:
            env_config["max_stall_count"] = max_stalls

        max_resets = self._maybe_parse_int(os.getenv("MAGENTIC_MAX_RESETS"))
        if max_resets is not None:
            env_config["max_reset_count"] = max_resets

        log_events = self._maybe_parse_bool(os.getenv("MAGENTIC_LOG_WORKFLOW_EVENTS"))
        if log_events is not None:
            env_config["log_workflow_events"] = log_events

        plan_review = self._maybe_parse_bool(os.getenv("MAGENTIC_ENABLE_PLAN_REVIEW"))
        if plan_review is not None:
            env_config["enable_plan_review"] = plan_review

        mcp_timeout = self._maybe_parse_int(os.getenv("MAGENTIC_MCP_TIMEOUT_SECONDS"))
        if mcp_timeout is not None:
            env_config["mcp_timeout_seconds"] = mcp_timeout

        mcp_request_timeout = self._maybe_parse_int(os.getenv("MAGENTIC_MCP_REQUEST_TIMEOUT_SECONDS"))
        if mcp_request_timeout is not None:
            env_config["mcp_request_timeout_seconds"] = mcp_request_timeout

        mcp_retry_attempts = self._maybe_parse_int(os.getenv("MAGENTIC_MCP_STARTUP_RETRIES"))
        if mcp_retry_attempts is not None:
            env_config["mcp_startup_retries"] = mcp_retry_attempts

        mcp_retry_backoff = os.getenv("MAGENTIC_MCP_RETRY_BACKOFF_SECONDS")
        if mcp_retry_backoff is not None:
            try:
                env_config["mcp_retry_backoff_seconds"] = float(mcp_retry_backoff)
            except ValueError:
                logger.warning(
                    "[AgentFramework-Magentic] Invalid MAGENTIC_MCP_RETRY_BACKOFF_SECONDS value '%s'; expecting float.",
                    mcp_retry_backoff,
                )

        mcp_headers_raw = os.getenv("MAGENTIC_MCP_HEADERS")
        if mcp_headers_raw:
            try:
                parsed_headers = json.loads(mcp_headers_raw)
                if isinstance(parsed_headers, dict):
                    env_config["mcp_headers"] = parsed_headers
            except json.JSONDecodeError:
                logger.warning(
                    "[AgentFramework-Magentic] Failed to parse MAGENTIC_MCP_HEADERS as JSON; ignoring value.",
                    exc_info=True,
                )

        return env_config

    @staticmethod
    async def _call_maybe_async(fn: Any, *args: Any, **kwargs: Any) -> Any:
        """Call a function that might be sync or async."""
        result = fn(*args, **kwargs)
        return await result if inspect.isawaitable(result) else result

    def _maybe_parse_int(self, value: Optional[str]) -> Optional[int]:
        """Parse string to int, return None if invalid."""
        if not value:
            return None
        try:
            return int(value)
        except ValueError:
            return None

    def _maybe_parse_bool(self, value: Optional[str]) -> Optional[bool]:
        """Parse string to bool, return None if invalid."""
        if not value:
            return None
        normalized = value.strip().lower()
        if normalized in {"1", "true", "yes", "y", "on"}:
            return True
        if normalized in {"0", "false", "no", "n", "off"}:
            return False
        return None

    async def _mark_pending_prompt(self, storage: CheckpointStorage, prompt: str) -> None:
        """Mark a pending prompt in storage."""
        self.state_store[self._pending_prompt_state_key] = prompt
        mark_fn = getattr(storage, "mark_pending_prompt", None)
        if callable(mark_fn):
            try:
                await self._call_maybe_async(mark_fn, prompt)
            except Exception as exc:
                logger.debug("Failed to mark pending prompt: %s", exc)

    async def _consume_pending_prompt(self, storage: CheckpointStorage) -> Optional[str]:
        """Consume and return pending prompt from storage."""
        stored_prompt = self.state_store.get(self._pending_prompt_state_key)
        storage_prompt = None
        
        consume_fn = getattr(storage, "consume_pending_prompt", None)
        if callable(consume_fn):
            try:
                storage_prompt = await self._call_maybe_async(consume_fn)
            except Exception as exc:
                logger.debug("Failed to consume pending prompt: %s", exc)

        if stored_prompt or storage_prompt:
            self.state_store.pop(self._pending_prompt_state_key, None)
        
        return storage_prompt or stored_prompt

    async def _reset_checkpoint_progress(self, storage: CheckpointStorage) -> None:
        await self._purge_checkpoint_storage(storage)
        self.state_store.pop(self._pending_prompt_state_key, None)

    async def _purge_checkpoint_storage(self, storage: CheckpointStorage) -> None:
        """Delete all checkpoints from storage."""
        # Try clear_all first
        clear_fn = getattr(storage, "clear_all", None)
        if callable(clear_fn):
            try:
                await self._call_maybe_async(clear_fn)
                return
            except Exception as exc:
                logger.debug("clear_all failed: %s", exc)

        # Fallback: list and delete individually
        list_fn = getattr(storage, "list_checkpoint_ids", None)
        delete_fn = getattr(storage, "delete_checkpoint", None)
        if not (callable(list_fn) and callable(delete_fn)):
            return

        try:
            checkpoint_ids = await self._call_maybe_async(list_fn)
            if checkpoint_ids:
                for checkpoint_id in checkpoint_ids:
                    try:
                        await self._call_maybe_async(delete_fn, checkpoint_id)
                    except Exception as exc:
                        logger.debug("Failed to delete checkpoint %s: %s", checkpoint_id, exc)
        except Exception as exc:
            logger.debug("Unable to enumerate checkpoints: %s", exc)

    async def _get_latest_checkpoint_id(self, storage: CheckpointStorage) -> Optional[str]:
        """Get the most recent checkpoint ID from storage."""
        # Try latest_checkpoint_id property/method first
        latest_id_attr = getattr(storage, "latest_checkpoint_id", None)
        if callable(latest_id_attr):
            try:
                latest_id = await self._call_maybe_async(latest_id_attr)
                if isinstance(latest_id, str):
                    return latest_id
            except Exception:
                pass
        elif isinstance(latest_id_attr, str):
            return latest_id_attr

        # Try list_checkpoints and get latest
        list_checkpoints_fn = getattr(storage, "list_checkpoints", None)
        if callable(list_checkpoints_fn):
            try:
                checkpoints = await self._call_maybe_async(list_checkpoints_fn)
                if checkpoints:
                    latest = max(checkpoints, key=lambda cp: (
                        getattr(cp, "timestamp", ""),
                        getattr(cp, "iteration_count", 0),
                    ))
                    return latest.checkpoint_id
            except Exception:
                pass

        # Fallback: list checkpoint IDs and return last
        list_ids_fn = getattr(storage, "list_checkpoint_ids", None)
        if callable(list_ids_fn):
            try:
                checkpoint_ids = await self._call_maybe_async(list_ids_fn)
                if checkpoint_ids:
                    return checkpoint_ids[-1]
            except Exception:
                pass

        return None
