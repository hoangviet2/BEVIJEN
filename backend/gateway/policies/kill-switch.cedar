// Incident kill switch: create this policy to stop every tool call on the gateway;
// delete it to restore. (DISCOVERY_ENABLED=false is the narrower switch.)
forbid(
  principal,
  action,
  resource is AgentCore::Gateway
);
