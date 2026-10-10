# AI agent contract (v1.0)

AI agents analyse platform data and produce *suggestions*. They never act on their own.

## Rules enforced by the runtime (`apps/api/app/services/agents.py`)

* **Registration grants nothing.** An agent gets only the read scopes in its approved version's manifest.
* **Forbidden scopes** are rejected at registration and at run time: `trade.execute`, `bot.control`, `secrets.read`,
  `credentials.read`, `billing.write`, `users.write`. Agents cannot place trades, change bots, or read passwords/tokens.
* **Tenant-scoped input.** The runtime builds the input from the subject's tenant only (e.g. one bot instance's last
  24 h of events and order statuses). Agents never receive "all customers".
* **Limits.** Time budget (`limits.timeout_ms`), input size (`limits.max_events`), output size (20 kB).
* **Traceability.** Every run is an `ai_tasks` row: agent + version, requester, subject, input, output, status,
  error code (`AI_FAILURE`), duration. Retention: `RETENTION_AI_TASKS_DAYS` (default 180).
* **Labelled output.** Outputs carry `generated: true`, the generator id and a disclaimer; the UI shows them apart from
  verified facts (events, orders, balances).
* **Human authorization.** Any action an agent suggests is carried out by a person through the normal, audited UI.

## Manifest

```json
{"contract_version": "1.0", "code": "instance_diagnostics", "version": "1.0.0", "subject_type": "bot_instance",
 "inputs": {...}, "outputs": {...}, "scopes": ["bot_events.read", "bot_instances.read", "order_intents.read"],
 "limits": {"timeout_ms": 5000, "max_events": 500}, "provider": "builtin-rules",
 "human_authorization_required_for_actions": true}
```

## Built-in agent

`instance_diagnostics` (deterministic, rule-based — no external model): summarises an instance's last 24 h, maps error
codes to findings and suggested checks. Available to customers (their own bots) and staff (any bot).

## Adding an LLM-backed agent

Implement an `AgentAdapter` (`build_input` from allowed scopes, `run` calling the provider with the time budget),
declare `provider` and scopes in the manifest, register it in `AGENTS`, add tests that assert scope enforcement and
output labelling. Provider credentials go in the API environment, never in the database. No LLM provider is
configured in this deployment.
