# ── Build stage ──────────────────────────────────────────────────────────────
FROM python:3.12-slim AS builder

WORKDIR /build

RUN pip install --upgrade pip

COPY requirements.txt .
RUN pip install --no-cache-dir --prefix=/install -r requirements.txt

# ── Terraform stage ───────────────────────────────────────────────────────────
# O binário e os providers das quatro nuvens entram na imagem para que
# `terraform validate` rode SEM REDE em runtime.
#
# Um cache de plugin (`TF_PLUGIN_CACHE_DIR`) NÃO basta: ele evita o download,
# mas o `init` ainda consulta o registry para resolver a versão, e num pod sem
# egress para a HashiCorp isso falha com "Missing required provider". Testado.
#
# A saída é um **filesystem mirror** — o modo de instalação offline documentado
# pela HashiCorp. Com ele o Terraform nunca toca a rede.
#
# Nome totalmente qualificado: runtimes com `short-name resolution` estrito
# (podman) falham no nome curto, e o build tem que funcionar fora do CI também.
FROM docker.io/hashicorp/terraform:1.17.0 AS terraform

WORKDIR /warm
# Os quatro providers: o cliente escolhe a nuvem de destino, e validar o pacote
# dele exige o schema daquela nuvem. 283 MB no total (aws 147, oci 55, azurerm
# 54, google 28) — o mirror guarda os zips, bem menores que o cache extraído.
RUN printf '%s\n' \
      'terraform {' \
      '  required_providers {' \
      '    google  = { source = "hashicorp/google",  version = "~> 5.0" }' \
      '    aws     = { source = "hashicorp/aws",     version = "~> 5.0" }' \
      '    azurerm = { source = "hashicorp/azurerm", version = "~> 3.0" }' \
      '    oci     = { source = "oracle/oci",        version = "~> 6.0" }' \
      '  }' \
      '}' > versions.tf \
 && terraform providers mirror -platform=linux_amd64 /mirror


# ── Runtime stage ─────────────────────────────────────────────────────────────
FROM python:3.12-slim AS runtime

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PYTHONPATH=/app \
    TF_CLI_CONFIG_FILE=/etc/terraform.rc

WORKDIR /app

# Copy installed packages from builder
COPY --from=builder /install /usr/local

# Terraform: binário e cache de provider pré-aquecido. Usado só por
# `terraform fmt` e `terraform validate` sobre o pacote gerado — a aplicação
# NUNCA roda `plan` nem `apply`, e nunca recebe credencial de nuvem.
COPY --from=terraform /bin/terraform /usr/local/bin/terraform
COPY --from=terraform /mirror /opt/terraform-providers

# `direct { exclude }` é o que fecha a porta: sem ele o Terraform cairia no
# registry quando o mirror não tivesse o provider, e o timeout apareceria em
# produção em vez de aqui.
RUN printf '%s\n' \
      'provider_installation {' \
      '  filesystem_mirror {' \
      '    path    = "/opt/terraform-providers"' \
      '    include = ["registry.terraform.io/*/*"]' \
      '  }' \
      '  direct {' \
      '    exclude = ["registry.terraform.io/*/*"]' \
      '  }' \
      '}' > /etc/terraform.rc

# Copy application source
COPY app/ ./app/
COPY alembic.ini .

# Usuário sem privilégio: um RCE na API não vira root no contêiner.
RUN useradd --create-home --uid 10001 app
USER 10001

EXPOSE 8000

CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
