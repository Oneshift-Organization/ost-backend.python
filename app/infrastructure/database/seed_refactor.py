"""Enhanced seed for service_ontology + cloud_sku_catalog with E2E test data.

Usage:
    python -m app.infrastructure.database.seed_refactor
"""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime
from decimal import Decimal
from uuid import uuid4

from sqlalchemy import delete, select, text

from app.infrastructure.database.models.cloud_services_comparation import (
    CloudServiceComparisonModel,
)
from app.infrastructure.database.models.cloud_sku_catalog import CloudSkuCatalogModel
from app.infrastructure.database.models.service_ontology import ServiceOntologyModel
from app.infrastructure.database.session import get_sessionmaker

# Service name → (category, service_type, usage_type) mappings for common services
# These ensure the ontology lookup by service name (since service_raw = service name
# for AWS detailed billing) finds matches.
_SERVICE_ONTOLOGY_OVERRIDES: dict[str, tuple[str, str, str]] = {
    # AWS
    "AmazonEC2": ("compute", "virtual_server", "Amazon EC2"),
    "AmazonRDS": ("database", "relational_database", "Amazon RDS"),
    "AmazonS3": ("storage", "object_storage", "Amazon S3"),
    "AmazonVPC": ("networking", "vpc", "Amazon VPC"),
    "AmazonCloudWatch": ("monitoring", "monitoring", "Amazon CloudWatch"),
    "AmazonEFS": ("storage", "file_storage", "Amazon EFS"),
    "AmazonElastiCache": ("database", "cache", "Amazon ElastiCache"),
    "AmazonECR": ("container", "container_registry", "Amazon ECR"),
    "Amazon Route 53": ("networking", "dns", "Amazon Route 53"),
    "Amazon CloudFront": ("networking", "cdn", "Amazon CloudFront"),
    "Amazon Elastic Load Balancing": ("networking", "load_balancing", "Amazon ELB"),
    "Amazon Simple Notification Service": ("messaging", "pubsub", "Amazon SNS"),
    "Amazon Simple Queue Service": ("messaging", "queue", "Amazon SQS"),
    "Amazon Glacier": ("storage", "archive_storage", "Amazon Glacier"),
    "Amazon Translate": ("ai_ml", "translation", "Amazon Translate"),
    "Amazon Comprehend": ("ai_ml", "nlp", "Amazon Comprehend"),
    "AWS Lambda": ("compute", "serverless", "AWS Lambda"),
    "AWS Backup": ("management", "backup", "AWS Backup"),
    "AWS Certificate Manager": ("security", "certificate_manager", "AWS ACM"),
    "AWS CloudFormation": ("management", "infrastructure_as_code", "AWS CloudFormation"),
    "AWS CloudShell": ("management", "cloud_shell", "AWS CloudShell"),
    "AWS Config": ("management", "configuration", "AWS Config"),
    "AWS Cost Explorer": ("management", "cost_tool", "AWS Cost Explorer"),
    "AWS Directory Service": ("security", "directory", "AWS Directory Service"),
    "AWS Glue": ("data_analytics", "etl", "AWS Glue"),
    "AWS IoT": ("iot", "iot_core", "AWS IoT"),
    "AWS Key Management Service": ("security", "key_management", "AWS KMS"),
    "AWS Secrets Manager": ("security", "secrets_manager", "AWS Secrets Manager"),
    "AWS Security Hub": ("security", "security_hub", "AWS Security Hub"),
    "AWS Service Catalog": ("management", "service_catalog", "AWS Service Catalog"),
    "AWS Step Functions": ("compute", "workflow", "AWS Step Functions"),
    "AWS WAF": ("security", "waf", "AWS WAF"),
    "AWS X-Ray": ("monitoring", "tracing", "AWS X-Ray"),
    "Amazon Managed Streaming for Apache Kafka": ("data_analytics", "streaming", "Amazon MSK"),
    "Amazon Managed Workflows for Apache Airflow": ("data_analytics", "workflow_orchestration", "Amazon MWAA"),
    "Amazon Elastic Container Service for Kubernetes": ("container", "container_orchestration", "Amazon EKS"),
    "Savings Plans for AWS Compute usage": ("cost_management", "savings_plan", "AWS Savings Plans"),
    "Tax": ("cost_management", "tax", "Tax"),
    "unknown": ("other", "unknown", "Unknown"),
    "CloudWatch Events": ("monitoring", "events", "CloudWatch Events"),
    "Amazon Elastic Container Registry Public": ("container", "container_registry", "Amazon ECR Public"),
    "Kiro": ("other", "third_party", "Kiro"),
    "Bitnami LMS powered by Moodle(TM) LMS": ("compute", "saas", "Bitnami LMS"),
    # GCP
    "Compute Engine": ("compute", "virtual_server", "Compute Engine"),
    "Cloud SQL": ("database", "relational_database", "Cloud SQL"),
    "Cloud Storage": ("storage", "object_storage", "Cloud Storage"),
    "Networking": ("networking", "networking_generic", "Networking"),
    "App Engine": ("compute", "platform_as_a_service", "App Engine"),
    "Kubernetes Engine": ("container", "container_orchestration", "Kubernetes Engine"),
    "Cloud Scheduler": ("management", "scheduler", "Cloud Scheduler"),
    "Cloud Logging": ("monitoring", "logging", "Cloud Logging"),
    "Cloud Filestore": ("storage", "file_storage", "Cloud Filestore"),
    "Gemini API": ("ai_ml", "gemini", "Gemini API"),
    "Networking": ("networking", "networking_generic", "Networking"),
    "Kubernetes Engine": ("container", "container_orchestration", "Kubernetes Engine"),
    "Cloud Run": ("compute", "serverless", "Cloud Run"),
    "Cloud Run Functions": ("compute", "serverless_functions", "Cloud Run Functions"),
    "Cloud Monitoring": ("monitoring", "monitoring", "Cloud Monitoring"),
    "Cloud Logging": ("monitoring", "logging", "Cloud Logging"),
    "Cloud DNS": ("networking", "dns", "Cloud DNS"),
    "Secret Manager": ("security", "secrets_manager", "Secret Manager"),
    "Artifact Registry": ("container", "container_registry", "Artifact Registry"),
    "Cloud Filestore": ("storage", "file_storage", "Cloud Filestore"),
}


async def seed_service_ontology(session) -> int:
    # First, clear and re-seed from comparison table
    await session.execute(delete(ServiceOntologyModel))
    await session.flush()

    count = 0

    # Seed from comparison table (usage_type = service_type, e.g. "virtual_server")
    result = await session.execute(
        select(CloudServiceComparisonModel)
    )
    rows = result.scalars().all()
    seen: set[tuple[str, str]] = set()
    for row in rows:
        key = (row.provider, row.service_type or "default")
        if key in seen:
            continue
        seen.add(key)
        session.add(
            ServiceOntologyModel(
                id=uuid4(),
                provider=row.provider,
                usage_type=row.service_type or "default",
                service_name=row.service_name,
                category=row.category,
                service_type=row.service_type or "default",
                confidence="high",
                version=1,
                active=True,
            )
        )
        count += 1

    # Seed overrides from _SERVICE_ONTOLOGY_OVERRIDES
    # Each override creates TWO ontology entries:
    #   (a) usage_type = canonical service name (e.g. "Amazon EC2")
    #   (b) usage_type = actual service_raw value (e.g. "AmazonEC2") — matches parser output
    for service_name, (category, service_type, usage_type) in _SERVICE_ONTOLOGY_OVERRIDES.items():
        service_lower = service_name.lower()
        if service_lower.startswith(("amazon", "aws", "cloudwatch")) or service_name in ("unknown", "Tax", "Kiro", "Bitnami LMS powered by Moodle(TM) LMS", "Savings Plans for AWS Compute usage"):
            provider = "aws"
        elif service_lower.startswith(("compute engine", "cloud sql", "cloud storage", "networking", "app engine", "kubernetes", "cloud scheduler", "cloud logging", "cloud filestore", "gemini")):
            provider = "gcp"
        else:
            provider = "gcp"

        # Entry keyed by canonical usage type (e.g. "Amazon EC2") — for fallback lookups
        session.add(
            ServiceOntologyModel(
                id=uuid4(),
                provider=provider,
                usage_type=usage_type,
                service_name=service_name,
                category=category,
                service_type=service_type,
                confidence="high",
                version=1,
                active=True,
            )
        )
        count += 1

        # Entry keyed by the actual service name from CSV (e.g. "AmazonEC2") if different
        if service_name != usage_type:
            session.add(
                ServiceOntologyModel(
                    id=uuid4(),
                    provider=provider,
                    usage_type=service_name,
                    service_name=service_name,
                    category=category,
                    service_type=service_type,
                    confidence="high",
                    version=1,
                    active=True,
                )
            )
            count += 1

    await session.flush()
    return count


def _build_sku_catalog_rows() -> list[CloudSkuCatalogModel]:
    now = datetime.now(UTC)
    skus = [
        CloudSkuCatalogModel(
            sku_id="aws-ec2-t3-medium",
            provider="AWS",
            service_name="Amazon EC2",
            service_type="Compute",
            category="Compute",
            region="us-east-1",
            usage_unit="Hrs",
            price_per_unit=Decimal("0.0416"),
            currency="USD",
            attributes={"instanceType": "t3.medium", "vcpu": "2", "memory": "4 GiB"},
            effective_date=now,
        ),
        CloudSkuCatalogModel(
            sku_id="aws-ec2-c5-large",
            provider="AWS",
            service_name="Amazon EC2",
            service_type="Compute",
            category="Compute",
            region="us-east-1",
            usage_unit="Hrs",
            price_per_unit=Decimal("0.085"),
            currency="USD",
            attributes={"instanceType": "c5.large", "vcpu": "2", "memory": "4 GiB"},
            effective_date=now,
        ),
        CloudSkuCatalogModel(
            sku_id="aws-ec2-c4-4xlarge",
            provider="AWS",
            service_name="Amazon EC2",
            service_type="Compute",
            category="Compute",
            region="us-east-1",
            usage_unit="Hrs",
            price_per_unit=Decimal("0.796"),
            currency="USD",
            attributes={"instanceType": "c4.4xlarge", "vcpu": "16", "memory": "30 GiB"},
            effective_date=now,
        ),
        CloudSkuCatalogModel(
            sku_id="aws-ec2-c5a-large",
            provider="AWS",
            service_name="Amazon EC2",
            service_type="Compute",
            category="Compute",
            region="us-east-1",
            usage_unit="Hrs",
            price_per_unit=Decimal("0.077"),
            currency="USD",
            attributes={"instanceType": "c5a.large", "vcpu": "2", "memory": "4 GiB"},
            effective_date=now,
        ),
        CloudSkuCatalogModel(
            sku_id="aws-ebs-gp3",
            provider="AWS",
            service_name="Amazon EBS",
            service_type="Storage",
            category="Storage",
            region="us-east-1",
            usage_unit="GB-Mo",
            price_per_unit=Decimal("0.08"),
            currency="USD",
            attributes={"volumeType": "gp3", "storageMedia": "SSD"},
            effective_date=now,
        ),
        CloudSkuCatalogModel(
            sku_id="aws-s3-standard",
            provider="AWS",
            service_name="Amazon S3",
            service_type="Storage",
            category="Storage",
            region="us-east-1",
            usage_unit="GB-Mo",
            price_per_unit=Decimal("0.023"),
            currency="USD",
            attributes={"storageClass": "Standard"},
            effective_date=now,
        ),
        CloudSkuCatalogModel(
            sku_id="gcp-compute-n1-standard",
            provider="GCP",
            service_name="Compute Engine",
            service_type="Compute",
            category="Compute",
            region="us-central1",
            usage_unit="hour",
            price_per_unit=Decimal("0.047"),
            currency="BRL",
            attributes={"machineType": "n1-standard-1", "vcpu": "1", "memory": "3.75 GB"},
            effective_date=now,
        ),
        CloudSkuCatalogModel(
            sku_id="gcp-cloudsql-mysql",
            provider="GCP",
            service_name="Cloud SQL",
            service_type="Database",
            category="Database",
            region="us-central1",
            usage_unit="hour",
            price_per_unit=Decimal("0.015"),
            currency="BRL",
            attributes={"databaseType": "MySQL", "tier": "db-f1-micro"},
            effective_date=now,
        ),
        CloudSkuCatalogModel(
            sku_id="gcp-storage-standard",
            provider="GCP",
            service_name="Cloud Storage",
            service_type="Storage",
            category="Storage",
            region="us-central1",
            usage_unit="GiB",
            price_per_unit=Decimal("0.026"),
            currency="BRL",
            attributes={"storageClass": "Standard"},
            effective_date=now,
        ),
    ]
    return skus


async def seed_sku_catalog(session) -> int:
    await session.execute(delete(CloudSkuCatalogModel))
    await session.flush()
    rows = _build_sku_catalog_rows()
    for r in rows:
        session.add(r)
    await session.flush()
    return len(rows)


async def main() -> None:
    sessionmaker = get_sessionmaker()
    async with sessionmaker() as session:
        so_count = await seed_service_ontology(session)
        print(f"Seeded {so_count} service_ontology rows")

        sku_count = await seed_sku_catalog(session)
        print(f"Seeded {sku_count} cloud_sku_catalog rows")

        await session.commit()

        r = await session.execute(text("SELECT count(*) FROM service_ontology"))
        print(f"  service_ontology total: {r.scalar()}")
        r = await session.execute(text("SELECT count(*) FROM cloud_sku_catalog"))
        print(f"  cloud_sku_catalog total: {r.scalar()}")


if __name__ == "__main__":
    asyncio.run(main())
