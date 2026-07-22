"""Tests for resilient_parse degradation flag."""

from app.schemas.analysis_v2 import AnalysisV2Response


def test_fallback_object_carries_parsing_flag():
    """Fallback object should carry PARSE_FALLBACK flag when parsing fails."""
    out = AnalysisV2Response.resilient_parse({"viability_score": "not-a-number"})
    assert any(f.get("code") == "PARSE_FALLBACK" for f in out.flags.get("parsing", []))


def test_alias_normalization_still_reaches_data():
    """Happy path: normalized data should parse successfully without flag."""
    out = AnalysisV2Response.resilient_parse(
        {"providers": [{"provider": "OCI"}], "score": 80}
    )
    assert out.provider_comparison[0].provider == "OCI"
    assert out.viability_score == 80
    assert "parsing" not in out.flags  # caminho feliz sem flag
