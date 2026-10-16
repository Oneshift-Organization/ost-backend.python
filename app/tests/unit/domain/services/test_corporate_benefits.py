import json
from urllib.parse import urlparse

from app.domain.services.corporate_benefits import build_corporate_benefits as build_catalog
from app.schemas.corporate_benefits import CorporateBenefits


def build_corporate_benefits():
    return CorporateBenefits.model_validate(build_catalog())


def test_catalog_is_sourced_versioned_and_explicitly_not_approved():
    benefits = build_corporate_benefits()
    assert benefits.catalog_version == "corporate-benefits-v1"
    assert benefits.reviewed_on.isoformat() == "2026-10-14"
    assert benefits.score_effect == "none"
    assert len({o.id for o in benefits.opportunities}) == 8
    assert {o.provider for o in benefits.opportunities} == {"aws", "azure", "gcp", "oci"}
    for offer in benefits.opportunities:
        assert offer.eligibility == "not_verified"
        assert offer.requirements
        url = urlparse(str(offer.source_url))
        assert url.scheme == "https"
        assert url.hostname in {
            "aws.amazon.com",
            "learn.microsoft.com",
            "azure.microsoft.com",
            "cloud.google.com",
            "www.oracle.com",
        }


def test_mutating_one_analysis_does_not_modify_another():
    first = build_corporate_benefits()
    first.opportunities[0].requirements.clear()
    first.opportunities.clear()
    assert len(build_corporate_benefits().opportunities) == 8
    assert build_corporate_benefits().opportunities[0].requirements


def test_catalog_can_be_persisted_as_json_without_custom_encoder():
    benefits = build_corporate_benefits()
    assert json.loads(json.dumps(benefits.model_dump())) == benefits.model_dump(mode="json")
    assert json.loads(json.dumps(build_catalog())) == build_catalog()
