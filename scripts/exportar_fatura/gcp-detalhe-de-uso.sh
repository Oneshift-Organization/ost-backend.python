#!/usr/bin/env bash
# Baixa o detalhe de uso de uma conta de faturamento do Google Cloud e grava um
# arquivo no formato que a OneShift importa.
#
# Onde rodar: Cloud Shell (ícone >_ no topo do console), que já tem gcloud, bq e
# python3 e já está logado. Em outra máquina: instale o Google Cloud SDK e rode
# gcloud auth login.
#
# Precisa do export do Cloud Billing para o BigQuery já ativo (Billing > Billing
# export > BigQuery export). O "Detailed usage cost" traz o ID de cada recurso e
# é o preferido; o "Standard usage cost" também serve. O export só tem dados a
# partir de quando foi ativado: sem ele, baixe o Cost table pelo console (o guia
# de importação da OneShift mostra o caminho).
#
# Uso:
#   bash gcp-detalhe-de-uso.sh                          # último mês fechado, acha a tabela sozinho
#   bash gcp-detalhe-de-uso.sh 2026-09                  # mês escolhido
#   bash gcp-detalhe-de-uso.sh 2026-09 projeto.dataset.tabela   # tabela escolhida
#
# A consulta lê só o mês pedido; o BigQuery cobra pelos dados lidos acima da cota
# gratuita mensal (1 TiB), o que cobre a maior parte das contas.
set -euo pipefail

falha() { echo "ERRO: $*" >&2; exit 1; }

for cmd in gcloud bq python3; do
  command -v "$cmd" >/dev/null || falha "comando '$cmd' não encontrado. Rode no Cloud Shell."
done
[[ -n "$(gcloud auth list --filter=status:ACTIVE --format='value(account)' 2>/dev/null)" ]] ||
  falha "não está logado no Google Cloud. Rode: gcloud auth login"

if [[ $# -ge 1 ]]; then
  [[ "$1" =~ ^[0-9]{4}-(0[1-9]|1[0-2])$ ]] || falha "mês '$1' inválido. Use AAAA-MM, por exemplo 2026-09."
  mes="$1"
else
  mes="$(date -u -d "$(date -u +%Y-%m-01) -1 day" +%Y-%m 2>/dev/null || date -u -v1d -v-1d +%Y-%m)"
fi
mes_fatura="${mes/-/}"

trabalho="$(mktemp -d)"
trap 'rm -rf "$trabalho"' EXIT

# ── 1. A tabela do export ────────────────────────────────────────────────────
tabela="${2:-${TABELA:-}}"
if [[ -z "$tabela" ]]; then
  echo "Procurando a tabela do export de faturamento nos seus projetos..." >&2
  : >"$trabalho/tabelas.txt"
  while read -r projeto; do
    [[ -n "$projeto" ]] || continue
    bq ls --format=json --project_id="$projeto" >"$trabalho/datasets.json" 2>/dev/null || continue
    while read -r dataset; do
      [[ -n "$dataset" ]] || continue
      bq ls --format=json --max_results=1000 "$projeto:$dataset" >"$trabalho/t.json" 2>/dev/null || continue
      python3 - "$trabalho/t.json" "$projeto" "$dataset" >>"$trabalho/tabelas.txt" <<'PY'
import json, re, sys
for t in json.load(open(sys.argv[1])):
    nome = t["tableReference"]["tableId"]
    if re.match(r"gcp_billing_export_(resource_)?v1_", nome):
        print(f"{sys.argv[2]}.{sys.argv[3]}.{nome}")
PY
    done < <(python3 -c 'import json,sys; [print(d["datasetReference"]["datasetId"]) for d in json.load(open(sys.argv[1]))]' "$trabalho/datasets.json" 2>/dev/null)
  done < <(gcloud projects list --format='value(projectId)' 2>/dev/null)

  # O detalhado (resource_v1) de cada conta de faturamento, senão o padrão
  mapfile -t tabelas < <(python3 - "$trabalho/tabelas.txt" <<'PY'
import sys
achadas = [l.strip() for l in open(sys.argv[1]) if l.strip()]
por_conta = {}
for t in achadas:
    conta = t.rsplit("_v1_", 1)[1]
    if conta not in por_conta or "resource_v1_" in t:
        por_conta[conta] = t
print("\n".join(sorted(por_conta.values())))
PY
)
  if [[ ${#tabelas[@]} -eq 0 ]]; then
    falha "não achei o export do Cloud Billing para o BigQuery nos projetos que esta conta enxerga.
  Para ativar: Billing > Billing export > BigQuery export > Detailed usage cost. Os dados
  começam na ativação; até lá, baixe o Cost table do mês pelo console.
  Se a tabela está num projeto que esta conta não lista, passe o nome:
    bash gcp-detalhe-de-uso.sh $mes projeto.dataset.gcp_billing_export_resource_v1_XXXXXX"
  fi
  if [[ ${#tabelas[@]} -gt 1 ]]; then
    echo "Achei o export de mais de uma conta de faturamento:" >&2
    printf '  %s\n' "${tabelas[@]}" >&2
    falha "escolha uma e rode de novo: bash gcp-detalhe-de-uso.sh $mes <tabela>"
  fi
  tabela="${tabelas[0]}"
fi
[[ "$tabela" =~ ^[A-Za-z0-9_-]+[.:][A-Za-z0-9_]+\.[A-Za-z0-9_]+$ ]] ||
  falha "tabela '$tabela' inválida. Use projeto.dataset.tabela."
tabela="${tabela/:/.}"
echo "Tabela: $tabela | mês da fatura: $mes" >&2

consulta() {
  bq query --quiet --use_legacy_sql=false --format=json --max_rows="$1" "$2" 2>"$trabalho/erro.txt"
}

# ── 2. O mês inteiro ─────────────────────────────────────────────────────────
filtro="invoice.month = '$mes_fatura'"
n="$(consulta 1 "SELECT COUNT(*) AS n FROM \`$tabela\` WHERE $filtro" |
  python3 -c 'import json,sys; print(json.load(sys.stdin)[0]["n"])')" ||
  falha "o BigQuery recusou a consulta. A conta logada precisa de BigQuery Data Viewer e BigQuery Job User no projeto da tabela. Resposta: $(head -c 300 "$trabalho/erro.txt")"
[[ "$n" -gt 0 ]] || falha "a tabela não tem linhas do mês $mes. Confira o mês e se o export já estava ativo nele."
echo "  $n linhas no mês" >&2
if [[ "$n" -gt 2000000 ]]; then
  falha "o mês tem $n linhas, demais para baixar por aqui. Exporte pelo BigQuery para um bucket e baixe de lá:
  bq query --use_legacy_sql=false --destination_table=<projeto>:<dataset>.fatura_$mes_fatura \"SELECT * FROM \\\`$tabela\\\` WHERE $filtro\"
  bq extract --destination_format=NEWLINE_DELIMITED_JSON --compression=GZIP <projeto>:<dataset>.fatura_$mes_fatura gs://<bucket>/fatura-$mes-*.json.gz"
fi

# Datas em ISO: o formato padrão do bq não diz o fuso
saida="gcp-billing-export-${mes}.json.gz"
consulta "$n" "SELECT * REPLACE (
    FORMAT_TIMESTAMP('%Y-%m-%dT%H:%M:%SZ', usage_start_time) AS usage_start_time,
    FORMAT_TIMESTAMP('%Y-%m-%dT%H:%M:%SZ', usage_end_time) AS usage_end_time,
    FORMAT_TIMESTAMP('%Y-%m-%dT%H:%M:%SZ', export_time) AS export_time)
  FROM \`$tabela\` WHERE $filtro" | gzip >"$saida" ||
  falha "o BigQuery parou no meio da consulta. Rode de novo. Resposta: $(head -c 300 "$trabalho/erro.txt")"

# ── 3. Conferência antes de entregar o arquivo ───────────────────────────────
python3 - "$saida" <<'PY'
import gzip, json, sys
from collections import defaultdict
from decimal import Decimal
linhas = json.load(gzip.open(sys.argv[1], "rt", encoding="utf-8"), parse_float=Decimal)
total = defaultdict(Decimal)
for l in linhas:
    total[l.get("currency") or "?"] += Decimal(str(l.get("cost") or 0))
print(f"  {len(linhas)} linhas", file=sys.stderr)
for moeda, valor in sorted(total.items()):
    print(f"  custo antes de créditos: {moeda} {valor.quantize(Decimal('0.01'))}", file=sys.stderr)
if not linhas:
    sys.exit("ERRO: nenhuma linha no arquivo")
PY

echo >&2
echo "Pronto: $saida" >&2
echo "Baixe o arquivo (menu ⋮ do Cloud Shell > Download) e importe na OneShift." >&2
