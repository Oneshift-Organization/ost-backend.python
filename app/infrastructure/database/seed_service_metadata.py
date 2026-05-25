"""Seed cloud_services_metadata and cloud_services_comparation tables.

Run via: python -m app.infrastructure.database.seed_service_metadata
"""

from __future__ import annotations

import asyncio

from app.infrastructure.database.models.cloud_services_comparation import (
    CloudServiceComparisonModel,
)
from app.infrastructure.database.models.cloud_services_metadata import (
    CloudServiceMetadataModel,
)
from app.infrastructure.database.session import get_sessionmaker

# (category, service_type, provider, service_name)
COMPARATION_SEED = [
    # ─── Compute ──────────────────────────────────────────
    ("compute", "virtual_server", "aws", "Amazon EC2"),
    ("compute", "virtual_server", "azure", "Azure Virtual Machine"),
    ("compute", "virtual_server", "gcp", "Compute Engine"),
    ("compute", "virtual_server", "oci", "Oracle Bare Metal Servers"),
    ("compute", "serverless_function", "aws", "AWS Lambda"),
    ("compute", "serverless_function", "azure", "Azure Functions"),
    ("compute", "serverless_function", "gcp", "Cloud Functions"),
    ("compute", "serverless_function", "oci", "Oracle Functions"),
    ("compute", "kubernetes", "aws", "Amazon EKS"),
    ("compute", "kubernetes", "azure", "Azure Kubernetes Service (AKS)"),
    ("compute", "kubernetes", "gcp", "Google Kubernetes Engine (GKE)"),
    ("compute", "kubernetes", "oci", "Oracle Container Engine for Kubernetes (OKE)"),
    ("compute", "container_registry", "aws", "Amazon ECR"),
    ("compute", "container_registry", "azure", "Azure Container Registry"),
    ("compute", "container_registry", "gcp", "Artifact Registry"),
    ("compute", "container_registry", "oci", "Oracle Cloud Infrastructure Registry"),
    # ─── Storage ──────────────────────────────────────────
    ("storage", "object_storage", "aws", "Amazon S3"),
    ("storage", "object_storage", "azure", "Azure Blob Storage"),
    ("storage", "object_storage", "gcp", "Cloud Storage"),
    ("storage", "object_storage", "oci", "Oracle Object Storage"),
    ("storage", "block_storage", "aws", "Amazon EBS"),
    ("storage", "block_storage", "azure", "Azure Managed Disks"),
    ("storage", "block_storage", "gcp", "Persistent Disk"),
    ("storage", "block_storage", "oci", "Oracle Block Volume"),
    ("storage", "file_storage", "aws", "Amazon EFS"),
    ("storage", "file_storage", "azure", "Azure Files"),
    ("storage", "file_storage", "gcp", "Filestore"),
    ("storage", "file_storage", "oci", "Oracle File Storage"),
    # ─── Database ─────────────────────────────────────────
    ("database", "relational", "aws", "Amazon RDS"),
    ("database", "relational", "azure", "Azure SQL Database"),
    ("database", "relational", "gcp", "Cloud SQL"),
    ("database", "relational", "oci", "Oracle Autonomous Database"),
    ("database", "nosql_document", "aws", "Amazon DynamoDB"),
    ("database", "nosql_document", "azure", "Azure Cosmos DB"),
    ("database", "nosql_document", "gcp", "Firestore"),
    ("database", "nosql_document", "oci", "Oracle NoSQL Database"),
    ("database", "in_memory", "aws", "Amazon ElastiCache"),
    ("database", "in_memory", "azure", "Azure Cache for Redis"),
    ("database", "in_memory", "gcp", "Memorystore"),
    ("database", "in_memory", "oci", "Oracle OCI Cache with Redis"),
    # ─── Networking ───────────────────────────────────────
    ("networking", "virtual_network", "aws", "Amazon VPC"),
    ("networking", "virtual_network", "azure", "Azure Virtual Network (VNet)"),
    ("networking", "virtual_network", "gcp", "VPC Network"),
    ("networking", "virtual_network", "oci", "Oracle Virtual Cloud Network (VCN)"),
    ("networking", "load_balancing", "aws", "Elastic Load Balancing (ELB)"),
    ("networking", "load_balancing", "azure", "Azure Load Balancer"),
    ("networking", "load_balancing", "gcp", "Cloud Load Balancing"),
    ("networking", "load_balancing", "oci", "Oracle Flexible Load Balancer"),
    ("networking", "dns", "aws", "Amazon Route 53"),
    ("networking", "dns", "azure", "Azure DNS"),
    ("networking", "dns", "gcp", "Cloud DNS"),
    ("networking", "dns", "oci", "Oracle DNS"),
    ("networking", "cdn", "aws", "Amazon CloudFront"),
    ("networking", "cdn", "azure", "Azure CDN"),
    ("networking", "cdn", "gcp", "Cloud CDN"),
    ("networking", "cdn", "oci", "Oracle CDN"),
    # ─── Security / Identity ─────────────────────────────
    ("security", "iam", "aws", "AWS IAM"),
    ("security", "iam", "azure", "Microsoft Entra ID"),
    ("security", "iam", "gcp", "Cloud IAM"),
    ("security", "iam", "oci", "Oracle Cloud IAM"),
    ("security", "key_management", "aws", "AWS KMS"),
    ("security", "key_management", "azure", "Azure Key Vault"),
    ("security", "key_management", "gcp", "Cloud KMS"),
    ("security", "key_management", "oci", "Oracle Vault"),
    ("security", "ddos_protection", "aws", "AWS Shield"),
    ("security", "ddos_protection", "azure", "Azure DDoS Protection"),
    ("security", "ddos_protection", "gcp", "Google Cloud Armor"),
    ("security", "ddos_protection", "oci", "Oracle DDoS Protection"),
    # ─── Observability ───────────────────────────────────
    ("observability", "monitoring", "aws", "Amazon CloudWatch"),
    ("observability", "monitoring", "azure", "Azure Monitor"),
    ("observability", "monitoring", "gcp", "Cloud Monitoring"),
    ("observability", "monitoring", "oci", "Oracle Monitoring"),
    ("observability", "logging", "aws", "Amazon CloudWatch Logs"),
    ("observability", "logging", "azure", "Azure Log Analytics"),
    ("observability", "logging", "gcp", "Cloud Logging"),
    ("observability", "logging", "oci", "Oracle Logging"),
    # ─── Analytics ───────────────────────────────────────
    ("analytics", "data_warehouse", "aws", "Amazon Redshift"),
    ("analytics", "data_warehouse", "azure", "Azure Synapse Analytics"),
    ("analytics", "data_warehouse", "gcp", "BigQuery"),
    ("analytics", "data_warehouse", "oci", "Oracle Autonomous Data Warehouse"),
]

# (provider, category, service_name, metadata)
METADATA_SEED = [
    # ─── AWS ─────────────────────────────────────────────
    ("aws", "compute", "Amazon EC2", {
        "vendor_lock_in_level": "medium",
        "requires_refactoring": False,
        "isolation_level": "shared",
        "pricing_model": "on_demand_reserved_spot",
        "price_per_hour_usd": 0.096,
        "typical_monthly_cost_usd": 70.08,
    }),
    ("aws", "compute", "AWS Lambda", {
        "vendor_lock_in_level": "medium",
        "requires_refactoring": True,
        "isolation_level": "shared",
        "pricing_model": "per_request_duration",
        "price_per_million_requests_usd": 0.20,
    }),
    ("aws", "compute", "Amazon EKS", {
        "vendor_lock_in_level": "low",
        "requires_refactoring": False,
        "isolation_level": "shared",
        "pricing_model": "cluster_hourly",
        "price_per_cluster_hour_usd": 0.10,
    }),
    ("aws", "storage", "Amazon S3", {
        "vendor_lock_in_level": "low",
        "requires_refactoring": False,
        "isolation_level": "shared",
        "pricing_model": "per_gb_monthly",
        "price_per_gb_month_usd": 0.023,
    }),
    ("aws", "storage", "Amazon EBS", {
        "vendor_lock_in_level": "low",
        "requires_refactoring": False,
        "isolation_level": "shared",
        "pricing_model": "per_gb_monthly",
        "price_per_gb_month_usd": 0.08,
    }),
    ("aws", "storage", "Amazon EFS", {
        "vendor_lock_in_level": "low",
        "requires_refactoring": False,
        "isolation_level": "shared",
        "pricing_model": "per_gb_monthly",
        "price_per_gb_month_usd": 0.30,
    }),
    ("aws", "database", "Amazon RDS", {
        "vendor_lock_in_level": "medium",
        "requires_refactoring": True,
        "isolation_level": "dedicated",
        "pricing_model": "instance_hourly_storage",
        "price_per_hour_usd": 0.048,
    }),
    ("aws", "database", "Amazon DynamoDB", {
        "vendor_lock_in_level": "high",
        "requires_refactoring": True,
        "isolation_level": "shared",
        "pricing_model": "per_request_throughput",
        "price_per_million_writes_usd": 1.25,
    }),
    ("aws", "database", "Amazon ElastiCache", {
        "vendor_lock_in_level": "medium",
        "requires_refactoring": True,
        "isolation_level": "dedicated",
        "pricing_model": "instance_hourly",
        "price_per_hour_usd": 0.034,
    }),
    ("aws", "networking", "Amazon VPC", {
        "vendor_lock_in_level": "low",
        "requires_refactoring": False,
        "isolation_level": "shared",
        "pricing_model": "free_included",
        "notes": "No additional charge for VPC itself",
    }),
    ("aws", "networking", "Elastic Load Balancing (ELB)", {
        "vendor_lock_in_level": "low",
        "requires_refactoring": False,
        "isolation_level": "shared",
        "pricing_model": "per_hour_per_gb",
        "price_per_hour_usd": 0.0225,
    }),
    ("aws", "networking", "Amazon Route 53", {
        "vendor_lock_in_level": "low",
        "requires_refactoring": False,
        "isolation_level": "shared",
        "pricing_model": "per_hosted_zone_query",
        "price_per_hosted_zone_month_usd": 0.50,
    }),
    ("aws", "networking", "Amazon CloudFront", {
        "vendor_lock_in_level": "low",
        "requires_refactoring": False,
        "isolation_level": "shared",
        "pricing_model": "per_gb_transfer",
        "price_per_gb_usd": 0.085,
    }),
    ("aws", "security", "AWS IAM", {
        "vendor_lock_in_level": "low",
        "requires_refactoring": True,
        "isolation_level": "shared",
        "pricing_model": "free_included",
        "notes": "No additional charge for IAM",
    }),
    ("aws", "observability", "Amazon CloudWatch", {
        "vendor_lock_in_level": "low",
        "requires_refactoring": False,
        "isolation_level": "shared",
        "pricing_model": "per_metric_log",
        "price_per_metric_month_usd": 0.30,
    }),
    ("aws", "analytics", "Amazon Redshift", {
        "vendor_lock_in_level": "medium",
        "requires_refactoring": True,
        "isolation_level": "dedicated",
        "pricing_model": "instance_hourly",
        "price_per_hour_usd": 0.25,
    }),
    # ─── Azure ───────────────────────────────────────────
    ("azure", "compute", "Azure Virtual Machine", {
        "vendor_lock_in_level": "medium",
        "requires_refactoring": False,
        "isolation_level": "shared",
        "pricing_model": "on_demand_reserved_spot",
        "price_per_hour_usd": 0.096,
    }),
    ("azure", "compute", "Azure Functions", {
        "vendor_lock_in_level": "medium",
        "requires_refactoring": True,
        "isolation_level": "shared",
        "pricing_model": "per_execution_duration",
        "price_per_million_executions_usd": 0.20,
    }),
    ("azure", "compute", "Azure Kubernetes Service (AKS)", {
        "vendor_lock_in_level": "low",
        "requires_refactoring": False,
        "isolation_level": "shared",
        "pricing_model": "free_management",
        "notes": "No charge for cluster management; pay for worker nodes",
    }),
    ("azure", "storage", "Azure Blob Storage", {
        "vendor_lock_in_level": "low",
        "requires_refactoring": False,
        "isolation_level": "shared",
        "pricing_model": "per_gb_monthly",
        "price_per_gb_month_usd": 0.0208,
    }),
    ("azure", "storage", "Azure Managed Disks", {
        "vendor_lock_in_level": "low",
        "requires_refactoring": False,
        "isolation_level": "shared",
        "pricing_model": "per_gb_monthly",
        "price_per_gb_month_usd": 0.08,
    }),
    ("azure", "database", "Azure SQL Database", {
        "vendor_lock_in_level": "high",
        "requires_refactoring": True,
        "isolation_level": "dedicated",
        "pricing_model": "dtu_vcore_hourly",
        "price_per_hour_usd": 0.048,
    }),
    ("azure", "database", "Azure Cosmos DB", {
        "vendor_lock_in_level": "high",
        "requires_refactoring": True,
        "isolation_level": "shared",
        "pricing_model": "per_request_unit",
        "price_per_100_ru_second_usd": 0.008,
    }),
    ("azure", "networking", "Azure Virtual Network (VNet)", {
        "vendor_lock_in_level": "low",
        "requires_refactoring": False,
        "isolation_level": "shared",
        "pricing_model": "free_included",
    }),
    # ─── GCP ─────────────────────────────────────────────
    ("gcp", "compute", "Compute Engine", {
        "vendor_lock_in_level": "medium",
        "requires_refactoring": False,
        "isolation_level": "shared",
        "pricing_model": "on_demand_committed_sustained",
        "price_per_hour_usd": 0.082,
    }),
    ("gcp", "compute", "Cloud Functions", {
        "vendor_lock_in_level": "medium",
        "requires_refactoring": True,
        "isolation_level": "shared",
        "pricing_model": "per_invocation_duration",
        "price_per_million_invocations_usd": 0.40,
    }),
    ("gcp", "compute", "Google Kubernetes Engine (GKE)", {
        "vendor_lock_in_level": "low",
        "requires_refactoring": False,
        "isolation_level": "shared",
        "pricing_model": "cluster_fee_plus_nodes",
        "price_per_cluster_hour_usd": 0.10,
    }),
    ("gcp", "storage", "Cloud Storage", {
        "vendor_lock_in_level": "low",
        "requires_refactoring": False,
        "isolation_level": "shared",
        "pricing_model": "per_gb_monthly",
        "price_per_gb_month_usd": 0.020,
    }),
    ("gcp", "storage", "Persistent Disk", {
        "vendor_lock_in_level": "low",
        "requires_refactoring": False,
        "isolation_level": "shared",
        "pricing_model": "per_gb_monthly",
        "price_per_gb_month_usd": 0.04,
    }),
    ("gcp", "database", "Cloud SQL", {
        "vendor_lock_in_level": "medium",
        "requires_refactoring": True,
        "isolation_level": "dedicated",
        "pricing_model": "instance_hourly_storage",
        "price_per_hour_usd": 0.048,
    }),
    ("gcp", "networking", "VPC Network", {
        "vendor_lock_in_level": "low",
        "requires_refactoring": False,
        "isolation_level": "shared",
        "pricing_model": "free_included",
    }),
    ("gcp", "analytics", "BigQuery", {
        "vendor_lock_in_level": "medium",
        "requires_refactoring": True,
        "isolation_level": "shared",
        "pricing_model": "per_tb_processed",
        "price_per_tb_usd": 5.00,
    }),
    # ─── OCI ─────────────────────────────────────────────
    ("oci", "compute", "Oracle Bare Metal Servers", {
        "vendor_lock_in_level": "medium",
        "requires_refactoring": False,
        "isolation_level": "dedicated",
        "pricing_model": "on_demand_reserved",
        "price_per_hour_usd": 0.053,
    }),
    ("oci", "compute", "Oracle Functions", {
        "vendor_lock_in_level": "medium",
        "requires_refactoring": True,
        "isolation_level": "shared",
        "pricing_model": "per_execution_duration",
        "price_per_million_executions_usd": 0.15,
    }),
    ("oci", "compute", "Oracle Container Engine for Kubernetes (OKE)", {
        "vendor_lock_in_level": "low",
        "requires_refactoring": False,
        "isolation_level": "shared",
        "pricing_model": "free_management",
        "notes": "No charge for cluster management; pay for worker nodes",
    }),
    ("oci", "storage", "Oracle Object Storage", {
        "vendor_lock_in_level": "low",
        "requires_refactoring": False,
        "isolation_level": "shared",
        "pricing_model": "per_gb_monthly",
        "price_per_gb_month_usd": 0.0225,
    }),
    ("oci", "storage", "Oracle Block Volume", {
        "vendor_lock_in_level": "low",
        "requires_refactoring": False,
        "isolation_level": "shared",
        "pricing_model": "per_gb_monthly",
        "price_per_gb_month_usd": 0.0425,
    }),
    ("oci", "database", "Oracle Autonomous Database", {
        "vendor_lock_in_level": "high",
        "requires_refactoring": True,
        "isolation_level": "dedicated",
        "pricing_model": "ocpu_hourly",
        "price_per_ocpu_hour_usd": 0.036,
    }),
    ("oci", "networking", "Oracle Virtual Cloud Network (VCN)", {
        "vendor_lock_in_level": "low",
        "requires_refactoring": False,
        "isolation_level": "shared",
        "pricing_model": "free_included",
    }),
    ("oci", "networking", "Oracle Flexible Load Balancer", {
        "vendor_lock_in_level": "low",
        "requires_refactoring": False,
        "isolation_level": "shared",
        "pricing_model": "per_hour_per_mbps",
        "price_per_hour_usd": 0.020,
    }),
]


async def seed() -> None:
    sessionmaker = get_sessionmaker()
    async with sessionmaker() as session:
        existing = await session.execute(
            __import__("sqlalchemy").select(CloudServiceMetadataModel).limit(1)
        )
        if existing.scalar_one_or_none() is not None:
            print("Tables already seeded — skipping.")
            return

        # Insert metadata
        for provider, category, service_name, metadata in METADATA_SEED:
            session.add(
                CloudServiceMetadataModel(
                    provider=provider,
                    category=category,
                    service_name=service_name,
                    service_metadata=metadata,
                )
            )
        await session.flush()

        # Insert comparation
        for category, service_type, provider, service_name in COMPARATION_SEED:
            session.add(
                CloudServiceComparisonModel(
                    category=category,
                    service_type=service_type,
                    provider=provider,
                    service_name=service_name,
                )
            )
        await session.commit()

    print(f"Seeded {len(METADATA_SEED)} metadata rows, {len(COMPARATION_SEED)} comparation rows.")


if __name__ == "__main__":
    asyncio.run(seed())
