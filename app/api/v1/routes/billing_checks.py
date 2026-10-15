"""Verificação do arquivo de fatura antes do upload (PLANO_CENARIOS_DE_ENTRADA §3.4).

Lê só a amostra: o formato, se traz o ID de cada recurso e o que isso permite.
Não grava nada. A tela de importação mostra a crítica antes de enviar.
"""

from __future__ import annotations

import contextlib
import tempfile
from pathlib import Path
from typing import Annotated

from fastapi import APIRouter, File, HTTPException, UploadFile, status

from app.api.deps import CurrentUser
from app.core.config import get_settings
from app.core.exceptions import ProviderDetectionError, UnsupportedFormatError
from app.domain.services.diagnostico_de_entrada import (
    COMO_EXPORTAR,
    CRITICO,
    PERFIS,
    Problema,
    problemas_do_arquivo,
)
from app.infrastructure.file_ingestion.detector import inspecionar_formato
from app.schemas.diagnostico_de_entrada import BillingFileCheckResponse, ProblemaDeEntradaOut

router = APIRouter(prefix="/billing-file-checks", tags=["billing"])

EXTENSOES_ACEITAS = {
    ".csv", ".tsv", ".txt", ".gz", ".zip", ".json", ".ndjson", ".jsonl",
    ".xlsx", ".xlsm", ".parquet",
}  # fmt: skip


def verificar_arquivo(caminho: Path) -> BillingFileCheckResponse:
    try:
        formato, com_id = inspecionar_formato(caminho)
    except (ProviderDetectionError, UnsupportedFormatError):
        return BillingFileCheckResponse(
            aceita=False,
            formato=None,
            nome_do_formato=None,
            provedor=None,
            com_id_de_recurso=None,
            problemas=[
                ProblemaDeEntradaOut.de(
                    Problema(
                        CRITICO,
                        "formato_nao_reconhecido",
                        "Arquivo não reconhecido como fatura",
                        "O conteúdo não bate com nenhum export de fatura conhecido (CUR 2.0 da "
                        "AWS, BigQuery do GCP, Cost Details da Azure, relatório de custos da "
                        "OCI). Confira se é o arquivo do export, sem edição, e veja o guia de "
                        "cada nuvem.",
                    )
                )
            ],
            como_exportar=None,
        )
    perfil = PERFIS.get(formato)
    problemas = problemas_do_arquivo(formato, com_id=com_id)
    return BillingFileCheckResponse(
        aceita=not any(p.nivel == CRITICO for p in problemas),
        formato=formato,
        nome_do_formato=perfil.nome if perfil else None,
        provedor=perfil.provedor if perfil else None,
        com_id_de_recurso=perfil.com_id if perfil and perfil.com_id is not None else com_id,
        problemas=[ProblemaDeEntradaOut.de(p) for p in problemas],
        como_exportar=COMO_EXPORTAR.get(perfil.provedor) if perfil else None,
    )


@router.post("", response_model=BillingFileCheckResponse)
async def check_billing_file(
    user: CurrentUser,
    file: Annotated[UploadFile, File(...)],
) -> BillingFileCheckResponse:
    if file.filename is None:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "filename required")
    sufixo = Path(file.filename).suffix.lower()
    if sufixo not in EXTENSOES_ACEITAS:
        raise HTTPException(
            status.HTTP_415_UNSUPPORTED_MEDIA_TYPE,
            f"unsupported file extension {sufixo}. allowed: {sorted(EXTENSOES_ACEITAS)}",
        )
    conteudo = await file.read()
    if len(conteudo) > get_settings().max_upload_size_mb * 1024 * 1024:
        raise HTTPException(
            status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
            f"file exceeds {get_settings().max_upload_size_mb} MB",
        )
    with tempfile.NamedTemporaryFile(suffix=sufixo, delete=False) as tmp:
        tmp.write(conteudo)
        caminho = Path(tmp.name)
    try:
        return verificar_arquivo(caminho)
    finally:
        with contextlib.suppress(OSError):
            caminho.unlink()  # noqa: ASYNC240 - arquivo temporário pequeno
