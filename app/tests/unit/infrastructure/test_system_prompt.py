from app.infrastructure.llm.system_prompt import PROMPT_CONTRACT_VERSION, SYSTEM_PROMPT


def test_prompt_has_grounding_and_scale_rules():
    assert "0 a 100" in SYSTEM_PROMPT
    assert "português brasileiro" in SYSTEM_PROMPT
    assert "Nunca invente preços" in SYSTEM_PROMPT or "nunca invente" in SYSTEM_PROMPT.lower()
    assert PROMPT_CONTRACT_VERSION == "oneshift-operator-v3.0"


def test_prompt_is_static_no_placeholders():
    assert "{" not in SYSTEM_PROMPT.replace("{}", "")  # nenhum f-string placeholder
