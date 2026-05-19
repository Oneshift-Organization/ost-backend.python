from app.infrastructure.file_ingestion.parsers.aws_parser import AWSParser
from app.infrastructure.file_ingestion.parsers.azure_parser import AzureParser
from app.infrastructure.file_ingestion.parsers.gcp_parser import GCPParser
from app.infrastructure.file_ingestion.parsers.oci_parser import OCIParser

__all__ = ["AWSParser", "AzureParser", "GCPParser", "OCIParser"]
