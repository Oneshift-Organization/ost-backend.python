import pytest
from app.schemas.analysis_v2 import AnalysisV2Response
from pydantic import ValidationError


def test_viability_score_rejects_1000_scale():
    with pytest.raises(ValidationError):
        AnalysisV2Response(viability_score=720)


def test_viability_score_accepts_0_100():
    assert AnalysisV2Response(viability_score=72).viability_score == 72


def test_prompt_contract_version_is_v3():
    assert AnalysisV2Response().prompt_contract_version == "oneshift-operator-v3.0"
