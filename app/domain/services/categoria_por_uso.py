"""Plano de controle, segredos, chaves e serverless separados pelo uso (PLANO_PRECO_NAO_COMPUTE R4).

Irmão do `rede_por_uso`. Três baldes da classificação misturam o que tem preço
comparável com o que não tem:

- `gke_control_plane`: a hora do cluster (EKS, GKE, AKS Standard, OKE Enhanced) é cotada;
  suporte estendido, LTS, plano provisionado e nó virtual ficam ao custo atual;
- `secrets`: segredo por mês, chave por mês e as operações de cada um são cotados;
  rotação, certificado, cofre privado e HSM dedicado ficam ao custo atual;
- `serverless`: Cloud Run, Lambda, Functions e Container Apps, ao custo atual. A
  cobrança (vCPU-segundo ativo e ocioso) não se traduz um a um entre as nuvens;
- `file_storage`: Filestore, EFS, Azure Files e File Storage da OCI, ao custo atual.
  Saíam como disco HDD ou objeto, e o Terraform fazia disco deles (T4).

PLANO_EQUIVALENTES_E_TARIFAS §3, cotados pela quantidade quando a unidade é a mesma nas
quatro nuvens; o resto de cada balde fica ao custo atual:
- `logging`: ingestão (padrão e básica) e retenção; consulta (GB varrido) fica;
- `dns`: consulta e zona; resolver, health check e política de roteamento ficam;
- `waf`: requisição, política e regra; Bot Control, Fraud Control e captcha ficam;
- `container_registry`: armazenamento da imagem; a taxa diária do ACR fica;
- `metrics`: amostra de Prometheus gerenciado; métrica customizada, alarme e painel ficam.

PLANO_PRECO_NAO_COMPUTE R5, pela quantidade; o resto fica ao custo atual:
- `bastion`: hora de gateway (Azure Bastion); o destino não cobra o acesso sem IP público;
- `security_posture`: imagem verificada e evento do plano de controle (Defender for
  Cloud, Inspector, GuardDuty, verificação do GCP); servidor, Storage e Key Vault ficam;
- `serverless`: vCPU e memória de contêiner (ativa e ociosa) e requisição, do Container
  Apps e do Cloud Run; Functions e o plano Dedicated ficam.

PLANO_CONFERENCIA_DE_SERVICOS §8 (lote 3), pela quantidade; o resto fica ao custo atual:
- `functions`: memória vezes tempo (GiB-hora) e chamada. A CPU que o GCP cobra à parte entra
  a zero: o GB-segundo do destino já a inclui. Concorrência provisionada, plano Premium
  e instância mínima ficam;
- `dedicated_link`: porta de 1, 10 e 100 Gbps por hora e a saída pelo link. O gateway e o
  anexo que acompanham o link entram a zero: a taxa da porta do destino já os cobre;
- `private_endpoint`: endpoint por hora e dado processado.

Cada categoria cotada tem uma unidade, e o destino é cotado pela quantidade medida
vezes a taxa dele. O fator traduz a unidade da linha para a da categoria (requisição
da AWS contada uma a uma, a categoria em 10 mil). Tabela de dados, sem ``if`` por
nuvem. Módulo puro, sem I/O.
"""

from __future__ import annotations

import re
from collections.abc import Mapping
from dataclasses import dataclass
from decimal import Decimal

from app.domain.services.rede_por_uso import (
    DEZ_MIL,
    GB,
    GB_MES,
    GIB_HORA,
    HORA,
    IMAGEM,
    MES,
    converter_quantidade,
)

# Mantém o nome do campo e a chave do payload de sempre: só a hora do cluster
PLANO_DE_CONTROLE = "gke_control_plane"
KUBERNETES_OUTROS = "kubernetes_other"
SEGREDO = "secrets"
SEGREDO_OPERACOES = "secrets_operations"
CHAVE = "key_management"
CHAVE_OPERACOES = "key_management_operations"
# Chaves que o destino não cobra por mês: a OCI não cobra as primeiras 20 versões de chave
# protegida por HSM no cofre padrão (lista de preços da Oracle, conferida em 16/10/2026).
# Premissa: cada chave da origem é uma versão
FRANQUIA_DE_CHAVES: Mapping[str, Decimal] = {"oci": Decimal("20")}
SEGURANCA_OUTROS = "security_other"
SERVERLESS = "serverless_compute"
ARQUIVOS = "file_storage"
LOGS_INGESTAO = "logs_ingestion"
LOGS_INGESTAO_BASICA = "logs_ingestion_basic"
LOGS_RETENCAO = "logs_retention"
LOGS_OUTROS = "logging_other"
DNS_CONSULTAS = "dns_queries"
DNS_ZONAS = "dns_zones"
DNS_OUTROS = "dns_other"
WAF_REQUISICOES = "waf_requests"
WAF_POLITICAS = "waf_policies"
WAF_REGRAS = "waf_rules"
WAF_OUTROS = "waf_other"
REGISTRO = "container_registry_storage"
REGISTRO_OUTROS = "container_registry_other"
METRICAS = "metric_samples"
METRICAS_OUTROS = "metrics_other"
# R5: bastion, postura de segurança e contêiner serverless
BASTION_HORAS = "bastion_hours"
BASTION_OUTROS = "bastion_other"
SEGURANCA_IMAGENS = "security_image_scans"
SEGURANCA_EVENTOS = "security_control_events"
SERVERLESS_VCPU = "serverless_vcpu_active"
SERVERLESS_VCPU_OCIOSA = "serverless_vcpu_idle"
SERVERLESS_MEMORIA = "serverless_memory_active"
SERVERLESS_MEMORIA_OCIOSA = "serverless_memory_idle"
SERVERLESS_REQUISICOES = "serverless_requests"
# Serviço que o DE-PARA reconhece, mas cuja linha nenhuma regra classifica: ao custo
# atual, com nome (PLANO_CONFERENCIA_DE_SERVICOS C5). "Não classificado" fica só para o
# que ninguém reconhece
RECONHECIDO_SEM_COTACAO = "recognized_unquoted"
# Plano de suporte da nuvem: o destino também cobra o dele; fica ao custo atual
SUPORTE = "support_plan"
# Lote 2 (PLANO_CONFERENCIA_DE_SERVICOS §7): API Gateway, requisição de objeto e arquivos
API_REQUISICOES = "api_requests"
API_OUTROS = "api_gateway_other"
OBJETO_ESCRITA = "object_requests_write"
OBJETO_LEITURA = "object_requests_read"
OBJETO_OUTROS = "object_requests_other"
ARQUIVOS_CAPACIDADE = "file_storage_capacity"
# Lote 3 (PLANO_CONFERENCIA_DE_SERVICOS §8): Functions, link dedicado e endpoint privado
FUNCAO_COMPUTE = "function_compute"
FUNCAO_CPU = "function_cpu"
FUNCAO_REQUISICOES = "function_requests"
FUNCOES_OUTROS = "functions_other"
LINK_1G = "dedicated_link_1g"
LINK_10G = "dedicated_link_10g"
LINK_100G = "dedicated_link_100g"
LINK_DADOS = "dedicated_link_data"
LINK_GATEWAY = "dedicated_link_gateway"
LINK_OUTROS = "dedicated_link_other"
ENDPOINT_HORAS = "private_endpoint_hours"
ENDPOINT_DADOS = "private_endpoint_data"
ENDPOINT_OUTROS = "private_endpoint_other"

# Tipo de componente da linha → categoria do que nenhuma regra separa (ao custo atual)
BALDES: Mapping[str, str] = {
    "gke_control_plane": KUBERNETES_OUTROS,
    "secrets": SEGURANCA_OUTROS,
    "serverless": SERVERLESS,
    "file_storage": ARQUIVOS,
    "logging": LOGS_OUTROS,
    "dns": DNS_OUTROS,
    "waf": WAF_OUTROS,
    "container_registry": REGISTRO_OUTROS,
    "metrics": METRICAS_OUTROS,
    "bastion": BASTION_OUTROS,
    "security_posture": SEGURANCA_OUTROS,
    "support": SUPORTE,
    "api_gateway": API_OUTROS,
    "object_requests": OBJETO_OUTROS,
    "functions": FUNCOES_OUTROS,
    "dedicated_link": LINK_OUTROS,
    "private_endpoint": ENDPOINT_OUTROS,
}

# Observabilidade e borda (PLANO_EQUIVALENTES_E_TARIFAS §3): custo que não vira recurso
OBSERVABILIDADE_E_BORDA: tuple[str, ...] = (
    LOGS_INGESTAO,
    LOGS_INGESTAO_BASICA,
    LOGS_RETENCAO,
    LOGS_OUTROS,
    DNS_CONSULTAS,
    DNS_ZONAS,
    DNS_OUTROS,
    WAF_REQUISICOES,
    WAF_POLITICAS,
    WAF_REGRAS,
    WAF_OUTROS,
    REGISTRO,
    REGISTRO_OUTROS,
    METRICAS,
    METRICAS_OUTROS,
)

# Lote 3: custo que não vira recurso no Terraform
FUNCOES_LINK_E_ENDPOINT: tuple[str, ...] = (
    FUNCAO_COMPUTE,
    FUNCAO_CPU,
    FUNCAO_REQUISICOES,
    FUNCOES_OUTROS,
    LINK_1G,
    LINK_10G,
    LINK_100G,
    LINK_DADOS,
    LINK_GATEWAY,
    LINK_OUTROS,
    ENDPOINT_HORAS,
    ENDPOINT_DADOS,
    ENDPOINT_OUTROS,
)

# R5: custo que não vira recurso no Terraform
BASTION_SEGURANCA_E_SERVERLESS: tuple[str, ...] = (
    BASTION_HORAS,
    BASTION_OUTROS,
    SEGURANCA_IMAGENS,
    SEGURANCA_EVENTOS,
    SERVERLESS_VCPU,
    SERVERLESS_VCPU_OCIOSA,
    SERVERLESS_MEMORIA,
    SERVERLESS_MEMORIA_OCIOSA,
    SERVERLESS_REQUISICOES,
)

CATEGORIAS: tuple[str, ...] = (
    PLANO_DE_CONTROLE,
    KUBERNETES_OUTROS,
    SEGREDO,
    SEGREDO_OPERACOES,
    CHAVE,
    CHAVE_OPERACOES,
    SEGURANCA_OUTROS,
    SERVERLESS,
    ARQUIVOS,
    *OBSERVABILIDADE_E_BORDA,
    *BASTION_SEGURANCA_E_SERVERLESS,
    RECONHECIDO_SEM_COTACAO,
    SUPORTE,
    API_REQUISICOES,
    API_OUTROS,
    OBJETO_ESCRITA,
    OBJETO_LEITURA,
    OBJETO_OUTROS,
    ARQUIVOS_CAPACIDADE,
    *FUNCOES_LINK_E_ENDPOINT,
)

CHAVE_NO_PAYLOAD: Mapping[str, str] = {
    PLANO_DE_CONTROLE: "gke_plano_controle",
    KUBERNETES_OUTROS: "kubernetes_outros",
    SEGREDO: "segredos",
    SEGREDO_OPERACOES: "segredos_operacoes",
    CHAVE: "chaves",
    CHAVE_OPERACOES: "chaves_operacoes",
    SEGURANCA_OUTROS: "seguranca_outros",
    SERVERLESS: "computacao_serverless",
    ARQUIVOS: "armazenamento_arquivos",
    LOGS_INGESTAO: "logs_ingestao",
    LOGS_INGESTAO_BASICA: "logs_ingestao_basica",
    LOGS_RETENCAO: "logs_retencao",
    LOGS_OUTROS: "logs_outros",
    DNS_CONSULTAS: "dns_consultas",
    DNS_ZONAS: "dns_zonas",
    DNS_OUTROS: "dns_outros",
    WAF_REQUISICOES: "waf_requisicoes",
    WAF_POLITICAS: "waf_politicas",
    WAF_REGRAS: "waf_regras",
    WAF_OUTROS: "waf_outros",
    REGISTRO: "registro_imagens",
    REGISTRO_OUTROS: "registro_outros",
    METRICAS: "metricas_amostras",
    METRICAS_OUTROS: "metricas_outros",
    BASTION_HORAS: "bastion_horas",
    BASTION_OUTROS: "bastion_outros",
    SEGURANCA_IMAGENS: "seguranca_imagens",
    SEGURANCA_EVENTOS: "seguranca_eventos",
    SERVERLESS_VCPU: "serverless_vcpu_ativo",
    SERVERLESS_VCPU_OCIOSA: "serverless_vcpu_ocioso",
    SERVERLESS_MEMORIA: "serverless_memoria_ativa",
    SERVERLESS_MEMORIA_OCIOSA: "serverless_memoria_ociosa",
    SERVERLESS_REQUISICOES: "serverless_requisicoes",
    RECONHECIDO_SEM_COTACAO: "reconhecidos_sem_cotacao",
    SUPORTE: "suporte",
    API_REQUISICOES: "api_requisicoes",
    API_OUTROS: "api_outros",
    OBJETO_ESCRITA: "objeto_requisicoes_escrita",
    OBJETO_LEITURA: "objeto_requisicoes_leitura",
    OBJETO_OUTROS: "objeto_requisicoes_outros",
    ARQUIVOS_CAPACIDADE: "arquivos_capacidade",
    FUNCAO_COMPUTE: "funcoes_computacao",
    FUNCAO_CPU: "funcoes_cpu",
    FUNCAO_REQUISICOES: "funcoes_requisicoes",
    FUNCOES_OUTROS: "funcoes_outros",
    LINK_1G: "link_dedicado_1g",
    LINK_10G: "link_dedicado_10g",
    LINK_100G: "link_dedicado_100g",
    LINK_DADOS: "link_dedicado_dados",
    LINK_GATEWAY: "link_dedicado_gateway",
    LINK_OUTROS: "link_dedicado_outros",
    ENDPOINT_HORAS: "endpoint_privado_horas",
    ENDPOINT_DADOS: "endpoint_privado_dados",
    ENDPOINT_OUTROS: "endpoint_privado_outros",
}

# Categoria cotada pela quantidade → unidade dela
UNIDADE: Mapping[str, str] = {
    PLANO_DE_CONTROLE: HORA,
    SEGREDO: MES,
    SEGREDO_OPERACOES: DEZ_MIL,
    CHAVE: MES,
    CHAVE_OPERACOES: DEZ_MIL,
    LOGS_INGESTAO: GB,
    LOGS_INGESTAO_BASICA: GB,
    LOGS_RETENCAO: GB_MES,
    DNS_CONSULTAS: DEZ_MIL,
    DNS_ZONAS: MES,
    WAF_REQUISICOES: DEZ_MIL,
    WAF_POLITICAS: MES,
    WAF_REGRAS: MES,
    REGISTRO: GB_MES,
    METRICAS: DEZ_MIL,
    BASTION_HORAS: HORA,
    SEGURANCA_IMAGENS: IMAGEM,
    SEGURANCA_EVENTOS: DEZ_MIL,
    SERVERLESS_VCPU: HORA,
    SERVERLESS_VCPU_OCIOSA: HORA,
    SERVERLESS_MEMORIA: GIB_HORA,
    SERVERLESS_MEMORIA_OCIOSA: GIB_HORA,
    SERVERLESS_REQUISICOES: DEZ_MIL,
    API_REQUISICOES: DEZ_MIL,
    OBJETO_ESCRITA: DEZ_MIL,
    OBJETO_LEITURA: DEZ_MIL,
    ARQUIVOS_CAPACIDADE: GB_MES,
    FUNCAO_COMPUTE: GIB_HORA,
    FUNCAO_CPU: HORA,
    FUNCAO_REQUISICOES: DEZ_MIL,
    LINK_1G: HORA,
    LINK_10G: HORA,
    LINK_100G: HORA,
    LINK_DADOS: GB,
    LINK_GATEWAY: HORA,
    ENDPOINT_HORAS: HORA,
    ENDPOINT_DADOS: GB,
}

_1 = Decimal(1)
_POR_DEZ_MIL = Decimal("0.0001")
# Segundo (vCPU-segundo, GiB-segundo) em hora, e milhão em lotes de 10 mil
_SEGUNDO = Decimal(1) / Decimal(3600)
_MILHAO = Decimal(100)
# mCPU-hora do Autopilot em vCPU-hora; na Azure, execução em lotes de 10 vira 10 mil
_MILI = Decimal("0.001")
_DEZ_EM_DEZ_MIL = Decimal("0.001")
# Circuito do ExpressRoute cobrado por mês, na categoria por hora
_MES_EM_HORAS = Decimal(730)
_CLASSE_FRIA_DO_GCP = r"(nearline|coldline|archive|durable reduced)"
_AUTOPILOT_FORA = (
    r"(tpu|gpu|a100|h100|h200|b200|l4|t4|accelerator|premium|storage|ephemeral|hyperdisk)"
)

# Provedor → categorias em que a quantidade vem na unidade da regra, e não na do
# rótulo (R5). Na Azure a `Quantity` está na unidade da Retail Prices API, e o rótulo
# do export não diz qual é: o GiB-segundo vem como "10 PiB Seconds", o evento por
# milhão como "10000000" e a imagem como "100" (custo ÷ quantidade = preço da API,
# conferido no export real de 23/10). No GCP, a imagem verificada vem em "count".
PELA_REGRA: Mapping[str, frozenset[str]] = {
    "azure": frozenset(
        {
            BASTION_HORAS,
            SEGURANCA_IMAGENS,
            SEGURANCA_EVENTOS,
            SERVERLESS_VCPU,
            SERVERLESS_VCPU_OCIOSA,
            SERVERLESS_MEMORIA,
            SERVERLESS_MEMORIA_OCIOSA,
            SERVERLESS_REQUISICOES,
            API_REQUISICOES,
            OBJETO_ESCRITA,
            OBJETO_LEITURA,
            ARQUIVOS_CAPACIDADE,
            FUNCAO_COMPUTE,
            FUNCAO_REQUISICOES,
            LINK_1G,
            LINK_10G,
            LINK_100G,
            LINK_DADOS,
            LINK_GATEWAY,
            ENDPOINT_HORAS,
            ENDPOINT_DADOS,
        }
    ),
    "gcp": frozenset({SEGURANCA_IMAGENS}),
    # A OCI dá o arquivo em GB-mês. A chamada não: o parser já multiplica pelo número da
    # unidade ("1,000,000 API Calls Per Month" vira chamada unitária), e o rótulo que
    # sobra diz a escala (§8 da conferência: aqui ela era multiplicada de novo)
    "oci": frozenset({ARQUIVOS_CAPACIDADE}),
}


@dataclass(frozen=True, slots=True)
class TipoPorUso:
    categoria: str
    # Quanto da quantidade da linha vale na unidade da categoria
    fator: Decimal = _1


def _r(texto: str) -> re.Pattern[str]:
    return re.compile(texto, re.IGNORECASE)


_Regras = tuple[tuple[re.Pattern[str], str, Decimal], ...]

# Balde → provedor → (padrão em "serviço | uso", categoria, fator); o primeiro que casa
REGRAS: Mapping[str, Mapping[str, _Regras]] = {
    "gke_control_plane": {
        "aws": ((_r(r"percluster"), PLANO_DE_CONTROLE, _1),),
        "gcp": (
            (_r(r"(zonal|regional|autopilot) kubernetes clusters"), PLANO_DE_CONTROLE, _1),
            (_r(r"clusters? management fee"), PLANO_DE_CONTROLE, _1),
        ),
        "azure": ((_r(r"uptime sla|hosted control plane"), PLANO_DE_CONTROLE, _1),),
        "oci": ((_r(r"enhanced cluster"), PLANO_DE_CONTROLE, _1),),
    },
    "secrets": {
        "aws": (
            (_r(r"secretsmanager-secrets"), SEGREDO, _1),
            (_r(r"secretsmanager-?api ?requests?"), SEGREDO_OPERACOES, _POR_DEZ_MIL),
            (_r(r"kms-keys"), CHAVE, _1),
            (_r(r"kms-requests"), CHAVE_OPERACOES, _POR_DEZ_MIL),
        ),
        "gcp": (
            (_r(r"secret version"), SEGREDO, _1),
            (_r(r"secret access operations"), SEGREDO_OPERACOES, _1),
            (_r(r"key versions"), CHAVE, _1),
            (_r(r"cryptographic operations"), CHAVE_OPERACOES, _1),
        ),
        "azure": (
            (_r(r"key operations"), CHAVE_OPERACOES, _1),
            (_r(r"\|\s*operations\s*$"), SEGREDO_OPERACOES, _1),
            (_r(r"protected .*key"), CHAVE, _1),
        ),
        "oci": ((_r(r"key versions"), CHAVE, _1),),
    },
    # PLANO_EQUIVALENTES_E_TARIFAS §3. A AWS conta requisição e consulta uma a uma
    # (fator para 10 mil); as outras dizem a unidade no export, e a escala dela entra.
    "logging": {
        "aws": (
            (_r(r"(dataprocessing|vendedlog)[-a-z]*ia-bytes"), LOGS_INGESTAO_BASICA, _1),
            (_r(r"(dataprocessing|vendedlog)[-a-z]*-bytes"), LOGS_INGESTAO, _1),
            (_r(r"timedstorage"), LOGS_RETENCAO, _1),
        ),
        "azure": (
            (_r(r"(basic|auxiliary) logs data ingestion"), LOGS_INGESTAO_BASICA, _1),
            (_r(r"data ingestion"), LOGS_INGESTAO, _1),
            (_r(r"data retention"), LOGS_RETENCAO, _1),
        ),
        "gcp": (
            (_r(r"vended logs storage"), LOGS_INGESTAO_BASICA, _1),
            (_r(r"log storage cost"), LOGS_INGESTAO, _1),
            (_r(r"log retention cost"), LOGS_RETENCAO, _1),
        ),
        # A OCI cobra o armazenamento do log (GB-mês), não a ingestão: como origem, sem a
        # retenção do cliente não há como voltar ao GB ingerido, e o log fica ao custo atual
    },
    "dns": {
        "aws": (
            (_r(r"\|\s*dns-queries$"), DNS_CONSULTAS, _POR_DEZ_MIL),
            (_r(r"\|\s*hostedzone$"), DNS_ZONAS, _1),
        ),
        "azure": (
            (_r(r"(public|private|dns) queries"), DNS_CONSULTAS, _1),
            (_r(r"(public|private) zone"), DNS_ZONAS, _1),
        ),
        "gcp": (
            (_r(r"dns query \(port 53\)"), DNS_CONSULTAS, _1),
            (_r(r"managedzone|managed zone"), DNS_ZONAS, _1),
        ),
        "oci": ((_r(r"dns - queries$|networking - dns$"), DNS_CONSULTAS, _1),),
    },
    "waf": {
        "aws": (
            (
                # Bot Control, Fraud Control, captcha e desafio são complementos: ficam
                _r(
                    r"\|(?!.*(amr|botcontrol|fraud|captcha|challenge))"
                    r".*-(?:shieldprotected-)?request(?:v2)?(?:-tier\d+(?:-\d+wcu)?|-\d+kb)?$"
                ),
                WAF_REQUISICOES,
                _POR_DEZ_MIL,
            ),
            (_r(r"\|.*-webacl(v2)?$"), WAF_POLITICAS, _1),
            (_r(r"\|.*-rule(v2)?$"), WAF_REGRAS, _1),
        ),
        "azure": (
            (_r(r"\bpolicy$"), WAF_POLITICAS, _1),
            (_r(r"\brule$"), WAF_REGRAS, _1),
        ),
        "gcp": (
            (_r(r"cloud armor requests( regional)?$"), WAF_REQUISICOES, _1),
            (_r(r"cloud armor policy( regional)?$"), WAF_POLITICAS, _1),
            (_r(r"cloud armor rule( regional)?$"), WAF_REGRAS, _1),
        ),
        "oci": (
            (_r(r"web application firewall - requests"), WAF_REQUISICOES, _1),
            (_r(r"web application firewall - instance"), WAF_POLITICAS, _1),
        ),
    },
    "container_registry": {
        "aws": ((_r(r"timedstorage"), REGISTRO, _1),),
        "azure": ((_r(r"data stored"), REGISTRO, _1),),
        "gcp": ((_r(r"artifact registry storage"), REGISTRO, _1),),
    },
    "metrics": {
        "aws": ((_r(r"amp:metricsamplecount"), METRICAS, _POR_DEZ_MIL),),
        "azure": ((_r(r"metric samples ingested|metrics ingestion"), METRICAS, _1),),
        "gcp": ((_r(r"prometheus samples ingested"), METRICAS, _1),),
        # A OCI não tem Prometheus gerenciado: o ponto do Monitoring não é amostra de
        # Prometheus e fica ao custo atual
    },
    # Lote 2: chamada de API Gateway (REST e HTTP da AWS; Consumption e v2 da Azure; OCI
    # em lotes de 1 milhão). A unidade do APIM clássico, cache e WebSocket ficam
    "api_gateway": {
        "aws": ((_r(r"apigateway(http)?request$"), API_REQUISICOES, _POR_DEZ_MIL),),
        "azure": (
            (_r(r"\|\s*(consumption|basic v2|standard v2|premium v2) calls$"), API_REQUISICOES, _1),
        ),
        "oci": ((_r(r"api gateway\s*-\s*1,000,000 api calls"), API_REQUISICOES, _1),),
    },
    # Requisição de objeto: escrita (Tier1, Write, Class A) e leitura (Tier2, Read, Class B),
    # só da classe padrão (S3 Standard e Intelligent-Tiering, Blob Hot, Cloud Storage
    # Standard). A escrita da classe fria custa de 2 a 7 vezes mais e fica (§8). A OCI
    # cobra uma taxa só e não separa: como origem, fica ao custo atual
    "object_requests": {
        "aws": (
            (_r(r"requests-(?:int-)?tier1$"), OBJETO_ESCRITA, _POR_DEZ_MIL),
            (_r(r"requests-(?:int-)?tier2$"), OBJETO_LEITURA, _POR_DEZ_MIL),
        ),
        "azure": (
            (
                _r(
                    r"^(?!.*\b(cool|cold|archive)\b)"
                    r".*\b(write|list and create container|list|create container) operations$"
                ),
                OBJETO_ESCRITA,
                _1,
            ),
            (_r(r"^(?!.*\b(cool|cold|archive)\b).*\bread operations$"), OBJETO_LEITURA, _1),
        ),
        "gcp": (
            (_r(r"^(?!.*" + _CLASSE_FRIA_DO_GCP + r").*class a operations"), OBJETO_ESCRITA, _1),
            (_r(r"^(?!.*" + _CLASSE_FRIA_DO_GCP + r").*class b operations"), OBJETO_LEITURA, _1),
        ),
    },
    # Arquivos por GB-mês: EFS Standard e One Zone, Azure Files armazenado ou provisionado,
    # capacidade do Filestore, File Storage da OCI. IA, Archive, operação e snapshot ficam
    "file_storage": {
        "aws": ((_r(r"-timedstorage(-z)?-bytehrs$"), ARQUIVOS_CAPACIDADE, _1),),
        "azure": ((_r(r"\b(data stored|provisioned)$"), ARQUIVOS_CAPACIDADE, _1),),
        "gcp": ((_r(r"filestore (instance )?capacity"), ARQUIVOS_CAPACIDADE, _1),),
        "oci": ((_r(r"file storage - storage$"), ARQUIVOS_CAPACIDADE, _1),),
    },
    # R5. Só a Azure cobra o bastion: Session Manager, IAP e OCI Bastion não cobram
    "bastion": {
        "azure": ((_r(r"\bgateway$"), BASTION_HORAS, _1),),
    },
    # Reverificação de imagem (AWS) e servidor, Storage, Key Vault, DNS e CSPM ficam
    "security_posture": {
        "aws": (
            (_r(r"container-image-initial-scan$"), SEGURANCA_IMAGENS, _1),
            (_r(r"paideventsanalyzed$"), SEGURANCA_EVENTOS, _POR_DEZ_MIL),
        ),
        "azure": (
            (_r(r"\|\s*standard images?$"), SEGURANCA_IMAGENS, _1),
            (_r(r"\|\s*standard events$"), SEGURANCA_EVENTOS, _MILHAO),
        ),
        "gcp": (
            (
                _r(r"container images scanned|successful scans|images scanned"),
                SEGURANCA_IMAGENS,
                _1,
            ),
        ),
    },
    # Contêiner serverless; Functions, Lambda e o plano Dedicated ficam ao custo atual
    "serverless": {
        "azure": (
            # Container Instances: vCPU por hora e memória por GB-hora, sempre ativos
            (_r(r"container instances.*\bvcpu duration"), SERVERLESS_VCPU, _1),
            (_r(r"container instances.*\bmemory duration"), SERVERLESS_MEMORIA, _1),
            (_r(r"standard vcpu active usage"), SERVERLESS_VCPU, _SEGUNDO),
            (_r(r"standard vcpu idle usage"), SERVERLESS_VCPU_OCIOSA, _SEGUNDO),
            (_r(r"standard memory active usage"), SERVERLESS_MEMORIA, _SEGUNDO),
            (_r(r"standard memory idle usage"), SERVERLESS_MEMORIA_OCIOSA, _SEGUNDO),
            (_r(r"\|\s*standard requests$"), SERVERLESS_REQUISICOES, _MILHAO),
        ),
        "gcp": (
            # GKE Autopilot: o pod pede mCPU (por hora) e memória (GiB-hora). GPU, TPU,
            # disco e armazenamento efêmero ficam ao custo atual
            (_r(r"autopilot (?!.*" + _AUTOPILOT_FORA + r").*\bmcpu\b"), SERVERLESS_VCPU, _MILI),
            (_r(r"autopilot (?!.*" + _AUTOPILOT_FORA + r").*\bcpu\b"), SERVERLESS_VCPU, _1),
            (_r(r"autopilot (?!.*" + _AUTOPILOT_FORA + r").*\bmemory\b"), SERVERLESS_MEMORIA, _1),
            (_r(r"services min instance cpu"), SERVERLESS_VCPU_OCIOSA, _1),
            (_r(r"services min instance memory"), SERVERLESS_MEMORIA_OCIOSA, _1),
            # Por instância (instance-based) entra como tempo ativo
            (_r(r"services cpu \((request|instance)-based"), SERVERLESS_VCPU, _1),
            (_r(r"services memory \((request|instance)-based"), SERVERLESS_MEMORIA, _1),
            (_r(r"\|\s*requests$"), SERVERLESS_REQUISICOES, _1),
        ),
    },
    # Lote 3. GB-segundo e chamada; Edge, concorrência provisionada, SnapStart, plano
    # Premium, Always Ready e instância mínima ficam ao custo atual
    "functions": {
        "aws": (
            (_r(r"\|\s*([a-z0-9]+-)?lambda-gb-second(-arm)?$"), FUNCAO_COMPUTE, _SEGUNDO),
            (_r(r"\|\s*([a-z0-9]+-)?request(-arm)?$"), FUNCAO_REQUISICOES, _POR_DEZ_MIL),
        ),
        "azure": (
            (_r(r"\b(standard|on demand) execution time$"), FUNCAO_COMPUTE, _SEGUNDO),
            (_r(r"\b(standard|on demand) total executions$"), FUNCAO_REQUISICOES, _DEZ_EM_DEZ_MIL),
        ),
        "gcp": (
            (_r(r"\|(?!.*min[- ]?instance).*\bmemory\b"), FUNCAO_COMPUTE, _1),
            (_r(r"\|(?!.*min[- ]?instance).*\bcpu\b"), FUNCAO_CPU, _1),
            (_r(r"\|.*\binvocations\b"), FUNCAO_REQUISICOES, _1),
        ),
        # Pelo rótulo: "GB Memory-Seconds" e "1MIL Function Invocations"
        "oci": (
            (_r(r"functions - execution time"), FUNCAO_COMPUTE, _1),
            (_r(r"functions - invocations"), FUNCAO_REQUISICOES, _1),
        ),
    },
    # Porta por velocidade e saída pelo link. Circuitos Unlimited, Premium, Local, Global
    # Reach, portas Metro, de tarifa fixa e de outras velocidades ficam ao custo atual
    "dedicated_link": {
        "aws": (
            # Porta dedicada e conexão hospedada (`HCPortUsage`)
            (_r(r"portusage:1g$"), LINK_1G, _1),
            (_r(r"portusage:10g$"), LINK_10G, _1),
            (_r(r"portusage:100g$"), LINK_100G, _1),
            # O `:dxfr` é outro produto, a US$ 0
            (_r(r"dataxfer-out(:dc\.\d+)?$"), LINK_DADOS, _1),
        ),
        "azure": (
            (_r(r"\bstandard metered data 1 gbps circuit$"), LINK_1G, _MES_EM_HORAS),
            (_r(r"\bstandard metered data 10 gbps circuit$"), LINK_10G, _MES_EM_HORAS),
            (_r(r"\|\s*port 10 gbps port$"), LINK_10G, _MES_EM_HORAS),
            (_r(r"\|\s*port 100 gbps port$"), LINK_100G, _MES_EM_HORAS),
            (_r(r"\bmetered data - data transfer out$"), LINK_DADOS, _1),
            (_r(r"\bgateway$"), LINK_GATEWAY, _1),
        ),
        "gcp": (
            (
                _r(r"\|\s*cloud interconnect - 1gbps vlan attachment via google partner$"),
                LINK_1G,
                _1,
            ),
            (
                _r(r"\|\s*cloud interconnect - 10gbps vlan attachment via google partner$"),
                LINK_10G,
                _1,
            ),
            (_r(r"\|\s*cloud interconnect - 10gbps dedicated circuit$"), LINK_10G, _1),
            (_r(r"\|\s*cloud interconnect - 100gbps dedicated circuit$"), LINK_100G, _1),
            (
                _r(
                    r"\|\s*cloud interconnect - \d+gbps vlan attachment"
                    r" via dedicated interconnect$"
                ),
                LINK_GATEWAY,
                _1,
            ),
            (_r(r"\|\s*cloud interconnect - (local|remote) data transfer"), LINK_DADOS, _1),
        ),
        # A OCI não cobra o dado que passa pelo FastConnect
        "oci": (
            (_r(r"fastconnect 1 gbps$"), LINK_1G, _1),
            (_r(r"fastconnect 10 gbps$"), LINK_10G, _1),
            (_r(r"fastconnect 100 gbps$"), LINK_100G, _1),
        ),
    },
    # Endpoint de interface por hora e dado processado. Endpoint de Gateway Load
    # Balancer, de recurso e de serviço da AWS e o Fixed Private Endpoint ficam
    "private_endpoint": {
        "aws": (
            (_r(r"vpcendpoint-hours$"), ENDPOINT_HORAS, _1),
            (_r(r"vpcendpoint-bytes$"), ENDPOINT_DADOS, _1),
        ),
        "azure": (
            (_r(r"\|\s*standard private endpoint$"), ENDPOINT_HORAS, _1),
            (_r(r"\|\s*standard data processed - (ingress|egress)$"), ENDPOINT_DADOS, _1),
        ),
        "gcp": (
            (
                _r(r"private service connect (consumer|partner select) end ?point$"),
                ENDPOINT_HORAS,
                _1,
            ),
            (
                _r(r"private service connect (?!.*google managed).*data processing"),
                ENDPOINT_DADOS,
                _1,
            ),
        ),
    },
}


def tipo_por_uso(balde: str, provedor: str, servico: str | None, uso: str | None) -> TipoPorUso:
    """Categoria e fator de uma linha de um dos baldes separados pelo uso."""
    texto = f"{servico or ''} | {uso or ''}"
    for padrao, categoria, fator in REGRAS.get(balde, {}).get(provedor.lower(), ()):
        if padrao.search(texto):
            return TipoPorUso(categoria, fator)
    return TipoPorUso(BALDES.get(balde, SERVERLESS))


def quantidade_por_uso(
    provedor: str, tipo: TipoPorUso, quantidade: Decimal | None, unidade: str | None
) -> Decimal | None:
    """Quantidade da linha na unidade da categoria, ou None quando não serve."""
    if tipo.categoria in PELA_REGRA.get(provedor.lower(), frozenset()):
        return quantidade * tipo.fator if quantidade is not None and quantidade > 0 else None
    return converter_quantidade(
        UNIDADE.get(tipo.categoria), provedor, tipo.fator, quantidade, unidade
    )
