Feature: Projected scenario behavior

  Background:
    Given a target AI system with projected attack steps

  Scenario: Hijacking the Klarna Assistant's runtime environment via shell command injection

    Given Injection surface and command/scripting interpreter access mapped (shell or code interpreter exposed as an agent tool).
    When user prompts via Klarna app and web flow (input)
    Then Direct interaction access to the agent's LLM obtained.
    Given Tool-hijack injection crafted against the interpreter tool; any required external payloads or receiving infrastructure s
    When Injection verified effective with benign test payloads before escalation.; then Crafted prompt injected directly into th (reasoning)
    Then Crafted prompt injected directly into the agent's input channel.
    And The injected directive activates, steering the agent toward the attacker-chosen interpreter invocation.
    When The hijacked tool executes the attacker-chosen command through the command/scripting interpreter: the first observable u [AML.T0050] (tool_execution)
    Then The hijacked tool executes the attacker-chosen command through the command/scripting interpreter: the first observable unauthorized execution.
