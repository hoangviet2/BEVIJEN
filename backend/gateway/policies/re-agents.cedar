// AgentCore Policy for re-gateway (AGENT.md §4). Attach in LOG_ONLY first, then ENFORCE.
// Gateway authorizer is AWS_IAM, so principals are the agents' AgentCore Runtime
// execution roles (AgentCore::IamEntity, session name removed).
// Placeholders replaced by infra/deploy.sh: __GATEWAY_ARN__, __ACCOUNT_ID__.
// Targets: re-tools (OpenAPI -> /tools/*), web-tools + assist-tools (Lambda), web-search (connector).

// 1. Research + findings tools for the verifier
permit(
  principal == AgentCore::IamEntity::"arn:aws:sts::__ACCOUNT_ID__:assumed-role/re-verifier-agent-role",
  action in [
    AgentCore::Action::"re-tools___get_record",
    AgentCore::Action::"re-tools___parse_schedule",
    AgentCore::Action::"re-tools___record_finding",
    AgentCore::Action::"re-tools___flag_for_specialist",
    AgentCore::Action::"web-tools___fetch_page",
    AgentCore::Action::"web-tools___check_site",
    AgentCore::Action::"web-tools___geocode",
    AgentCore::Action::"web-tools___pdf_text",
    AgentCore::Action::"web-search___WebSearch",
    AgentCore::Action::"assist-tools___search_knowledge",
    AgentCore::Action::"assist-tools___translate_text",
    AgentCore::Action::"assist-tools___send_message"
  ],
  resource == AgentCore::Gateway::"__GATEWAY_ARN__"
);

// 2. Discovery: research, matching, findings/flags
permit(
  principal == AgentCore::IamEntity::"arn:aws:sts::__ACCOUNT_ID__:assumed-role/re-discovery-agent-role",
  action in [
    AgentCore::Action::"re-tools___match_existing",
    AgentCore::Action::"re-tools___parse_schedule",
    AgentCore::Action::"re-tools___record_finding",
    AgentCore::Action::"re-tools___flag_for_specialist",
    AgentCore::Action::"web-tools___fetch_page",
    AgentCore::Action::"web-tools___check_site",
    AgentCore::Action::"web-tools___geocode",
    AgentCore::Action::"web-tools___places_near",
    AgentCore::Action::"web-tools___pdf_text",
    AgentCore::Action::"web-search___WebSearch",
    AgentCore::Action::"assist-tools___search_knowledge",
    AgentCore::Action::"assist-tools___translate_text",
    AgentCore::Action::"assist-tools___send_message"
  ],
  resource == AgentCore::Gateway::"__GATEWAY_ARN__"
);

// 3. Only discovery may create, and only inside a discovery run
permit(
  principal == AgentCore::IamEntity::"arn:aws:sts::__ACCOUNT_ID__:assumed-role/re-discovery-agent-role",
  action == AgentCore::Action::"re-tools___create_record",
  resource == AgentCore::Gateway::"__GATEWAY_ARN__"
)
when { context.input.run_id like "dr-*" };

// 4. Defense in depth: nobody but discovery-agent ever creates records
forbid(
  principal is AgentCore::IamEntity,
  action == AgentCore::Action::"re-tools___create_record",
  resource == AgentCore::Gateway::"__GATEWAY_ARN__"
)
unless { principal == AgentCore::IamEntity::"arn:aws:sts::__ACCOUNT_ID__:assumed-role/re-discovery-agent-role" };
