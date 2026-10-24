#!/usr/bin/env bash
# Baixa o detalhe de uso de uma conta AWS e grava um arquivo no formato que a
# OneShift importa.
#
# Onde rodar: AWS CloudShell (ícone >_ no topo do console), que já tem aws,
# jq e python3 e já está logado. Em outra máquina: instale a AWS CLI v2 e
# python3 e rode aws configure (ou aws sso login).
#
# O que ele faz, nesta ordem:
#   1. Se a conta já tem um export CUR 2.0 (Billing > Data Exports), baixa os
#      arquivos do mês do S3 e junta num só .csv.gz. É o arquivo mais completo:
#      traz o ID de cada recurso.
#   2. Se não tem, monta a fatura detalhada pela API do Cost Explorer (conta x
#      serviço x tipo de uso, custo amortizado, sem impostos e créditos). Serve
#      para a análise inteira; só não traz o ID de cada recurso. Cada chamada à
#      API do Cost Explorer custa US$ 0,01 (são poucas por mês).
#
# Uso:
#   bash aws-detalhe-de-uso.sh               # último mês fechado
#   bash aws-detalhe-de-uso.sh 2026-09       # mês escolhido
#   FONTE=ce bash aws-detalhe-de-uso.sh      # pula o CUR e usa o Cost Explorer
set -euo pipefail

falha() { echo "ERRO: $*" >&2; exit 1; }

for cmd in aws python3; do
  command -v "$cmd" >/dev/null || falha "comando '$cmd' não encontrado. Rode no AWS CloudShell."
done

conta="$(aws sts get-caller-identity --query Account --output text 2>/dev/null)" ||
  falha "não está logado na AWS. Rode: aws configure (ou aws sso login)"

if [[ $# -ge 1 ]]; then
  [[ "$1" =~ ^[0-9]{4}-(0[1-9]|1[0-2])$ ]] || falha "mês '$1' inválido. Use AAAA-MM, por exemplo 2026-09."
  mes="$1"
else
  mes="$(date -u -d "$(date -u +%Y-%m-01) -1 day" +%Y-%m 2>/dev/null || date -u -v1d -v-1d +%Y-%m)"
fi
inicio="${mes}-01"
fim="$(date -u -d "$inicio +1 month" +%F 2>/dev/null || date -u -j -v+1m -f %F "$inicio" +%F)"
echo "Conta: $conta | mês: $mes ($inicio a $fim, exclusivo)" >&2
if [[ "$fim" > "$(date -u +%F)" ]]; then
  echo "  aviso: o mês ainda não terminou; o custo vem parcial (até hoje)." >&2
fi

trabalho="$(mktemp -d)"
trap 'rm -rf "$trabalho"' EXIT

# ── 1. CUR 2.0 já configurado ────────────────────────────────────────────────
baixar_cur() {
  aws bcm-data-exports list-exports --region us-east-1 --output json >"$trabalho/exports.json" 2>/dev/null ||
    return 1
  local arn pasta chave
  while read -r arn; do
    [[ -n "$arn" ]] || continue
    aws bcm-data-exports get-export --region us-east-1 --export-arn "$arn" --output json \
      >"$trabalho/export.json" 2>/dev/null || continue
    # Só o CUR 2.0 (tabela COST_AND_USAGE_REPORT) em CSV; FOCUS e Parquet ficam de fora
    pasta="$(python3 - "$trabalho/export.json" "$mes" <<'PY'
import json, sys
e = json.load(open(sys.argv[1]))["Export"]
consulta = e["DataQuery"]["QueryStatement"].upper()
s3 = e["DestinationConfigurations"]["S3Destination"]
if "COST_AND_USAGE_REPORT" in consulta and s3["S3OutputConfigurations"].get("Format") == "TEXT_OR_CSV":
    partes = [p for p in (s3["S3Prefix"].strip("/"), e["Name"], "data", f"BILLING_PERIOD={sys.argv[2]}") if p]
    print(f's3://{s3["S3Bucket"]}/' + "/".join(partes) + "/")
PY
)" || continue
    [[ -n "$pasta" ]] || continue
    echo "Export CUR 2.0 encontrado: $pasta" >&2
    aws s3 ls "$pasta" --recursive >"$trabalho/objetos.txt" 2>"$trabalho/erro.txt" || {
      echo "  sem acesso ao bucket: $(head -c 200 "$trabalho/erro.txt")" >&2
      continue
    }
    # Com "Create new data export file", cada entrega fica numa subpasta própria:
    # vale só a mais recente. Com "Overwrite", os arquivos ficam direto na pasta.
    mkdir -p "$trabalho/cur"
    while read -r chave; do
      aws s3 cp "s3://$(cut -d/ -f3 <<<"$pasta")/$chave" "$trabalho/cur/" >/dev/null ||
        falha "não consegui baixar s3://$(cut -d/ -f3 <<<"$pasta")/$chave"
    done < <(python3 - "$trabalho/objetos.txt" <<'PY'
import sys
chaves = [l.split()[-1] for l in open(sys.argv[1]) if l.strip().endswith(".csv.gz")]
if chaves:
    versao = lambda c: c.rsplit("/", 1)[0]
    ultima = max(versao(c) for c in chaves)
    print("\n".join(c for c in chaves if versao(c) == ultima))
PY
)
    if compgen -G "$trabalho/cur/*.csv.gz" >/dev/null; then
      return 0
    fi
    echo "  nenhum arquivo do mês nesse export" >&2
  done < <(python3 -c 'import json,sys; [print(e["ExportArn"]) for e in json.load(open(sys.argv[1])).get("Exports", [])]' "$trabalho/exports.json")
  return 1
}

if [[ "${FONTE:-}" != "ce" ]] && baixar_cur; then
  saida="aws-cur2-${mes}.csv.gz"
  # Um mês grande vem em mais de um arquivo (-00001, -00002...): um cabeçalho só
  python3 - "$saida" "$trabalho"/cur/*.csv.gz <<'PY'
import gzip, sys
saida, *partes = sys.argv[1:]
with gzip.open(saida, "wt", newline="", encoding="utf-8") as out:
    cabecalho = None
    for parte in sorted(partes):
        with gzip.open(parte, "rt", newline="", encoding="utf-8") as fh:
            primeira = fh.readline()
            if cabecalho is None:
                cabecalho = primeira
                out.write(primeira)
            elif primeira != cabecalho:
                sys.exit(f"ERRO: {parte} tem outras colunas; envie os arquivos separados")
            for linha in fh:
                out.write(linha)
PY
  echo >&2
  echo "Pronto: $saida (CUR 2.0, com o ID de cada recurso)" >&2
  echo "Baixe o arquivo (Actions > Download file) e importe na OneShift." >&2
  exit 0
fi

# ── 2. Cost Explorer ─────────────────────────────────────────────────────────
echo "Sem export CUR 2.0 do mês; montando a fatura detalhada pelo Cost Explorer..." >&2

# GET com nova tentativa: o Cost Explorer limita chamadas (ThrottlingException).
pedir() {
  local destino="$1"; shift
  local tentativa
  for tentativa in 1 2 3 4 5; do
    if aws ce "$@" --region us-east-1 --output json >"$destino" 2>"$trabalho/erro.txt"; then
      return 0
    fi
    if grep -qiE "throttl|rate exceeded|timed out|50[234]" "$trabalho/erro.txt" && [[ $tentativa -lt 5 ]]; then
      echo "  a AWS pediu para esperar; tentando de novo em $((tentativa * 10))s..." >&2
      sleep $((tentativa * 10))
      continue
    fi
    return 1
  done
}

pedir "$trabalho/contas.json" get-dimension-values --dimension LINKED_ACCOUNT \
  --time-period "Start=$inicio,End=$fim" ||
  falha "o Cost Explorer recusou a consulta. A conta logada precisa da permissão ce:GetCostAndUsage (e o Cost Explorer precisa estar ativado em Billing). Resposta: $(head -c 300 "$trabalho/erro.txt")"

mapfile -t contas < <(python3 -c '
import json, sys
for v in json.load(open(sys.argv[1])).get("DimensionValues", []):
    nome = (v.get("Attributes") or {}).get("description", "")
    print(v["Value"] + "\t" + nome)' "$trabalho/contas.json")
[[ ${#contas[@]} -gt 0 ]] || falha "o Cost Explorer não tem custo no mês $mes para esta conta."
echo "  ${#contas[@]} conta(s) com custo no mês" >&2

# Tudo menos imposto, crédito e reembolso: a análise compara o custo de uso
filtro_tipo='{"Not":{"Dimensions":{"Key":"RECORD_TYPE","Values":["Tax","Credit","Refund"]}}}'
n=0
for linha in "${contas[@]}"; do
  id="${linha%%$'\t'*}"
  filtro="{\"And\":[{\"Dimensions\":{\"Key\":\"LINKED_ACCOUNT\",\"Values\":[\"$id\"]}},$filtro_tipo]}"
  token=""
  while :; do
    n=$((n + 1))
    pagina="$trabalho/ce-$(printf '%05d' "$n").json"
    args=(get-cost-and-usage --time-period "Start=$inicio,End=$fim" --granularity MONTHLY
      --metrics AmortizedCost UsageQuantity --filter "$filtro"
      --group-by Type=DIMENSION,Key=SERVICE Type=DIMENSION,Key=USAGE_TYPE)
    [[ -n "$token" ]] && args+=(--next-page-token "$token")
    pedir "$pagina" "${args[@]}" ||
      falha "o Cost Explorer parou de responder. Rode de novo. Resposta: $(head -c 300 "$trabalho/erro.txt")"
    python3 -c 'import json,sys; d=json.load(open(sys.argv[1])); d["_conta"]=sys.argv[2]; d["_nome"]=sys.argv[3]; json.dump(d, open(sys.argv[1],"w"))' \
      "$pagina" "$id" "${linha#*$'\t'}"
    token="$(python3 -c 'import json,sys; print(json.load(open(sys.argv[1])).get("NextPageToken") or "")' "$pagina")"
    [[ -n "$token" ]] || break
  done
  echo "  conta $id: ok" >&2
done

saida="aws-fatura-detalhada-${mes}.csv"
python3 - "$saida" "$inicio" "$fim" "$trabalho"/ce-*.json <<'PY'
import csv, json, sys
from decimal import Decimal

saida, inicio, fim, *paginas = sys.argv[1:]
data = lambda d: d.replace("-", "/") + " 00:00:00"
colunas = [
    "InvoiceID", "PayerAccountId", "LinkedAccountId", "RecordType", "RecordID",
    "BillingPeriodStartDate", "BillingPeriodEndDate", "InvoiceDate", "PayerAccountName",
    "LinkedAccountName", "TaxationAddress", "PayerPONumber", "ProductCode", "ProductName",
    "SellerOfRecord", "UsageType", "Operation", "RateId", "ItemDescription", "UsageStartDate",
    "UsageEndDate", "UsageQuantity", "BlendedRate", "CurrencyCode", "CostBeforeTax", "Credits",
    "TaxAmount", "TaxType", "TotalCost",
]
linhas, total, moedas = 0, Decimal(0), set()
with open(saida, "w", newline="", encoding="utf-8") as fh:
    w = csv.DictWriter(fh, fieldnames=colunas)
    w.writeheader()
    for pagina in paginas:
        d = json.load(open(pagina))
        for periodo in d.get("ResultsByTime", []):
            for g in periodo.get("Groups", []):
                servico, uso = g["Keys"]
                custo = g["Metrics"]["AmortizedCost"]
                quantidade = g["Metrics"]["UsageQuantity"]["Amount"]
                if Decimal(custo["Amount"]) == 0 and Decimal(quantidade) == 0:
                    continue
                moedas.add(custo["Unit"])
                total += Decimal(custo["Amount"])
                linhas += 1
                w.writerow({
                    "LinkedAccountId": d["_conta"], "RecordType": "LinkedLineItem",
                    "BillingPeriodStartDate": data(inicio), "BillingPeriodEndDate": data(fim),
                    "PayerAccountName": d["_nome"], "LinkedAccountName": d["_nome"],
                    "ProductCode": servico,
                    "ProductName": servico, "UsageType": uso,
                    "UsageStartDate": data(inicio), "UsageEndDate": data(fim),
                    "UsageQuantity": quantidade, "CurrencyCode": custo["Unit"],
                    "CostBeforeTax": custo["Amount"], "Credits": "0", "TaxAmount": "0",
                    "TotalCost": custo["Amount"],
                })
print(f"LINHAS={linhas}")
print(f"TOTAL={'/'.join(sorted(moedas)) or 'USD'} {total.quantize(Decimal('0.01'))}")
PY
[[ -s "$saida" ]] || falha "nenhuma linha gerada."

echo >&2
echo "Pronto: $saida (fatura detalhada pelo Cost Explorer, sem o ID de cada recurso)" >&2
echo "Para o custo de cada recurso, crie um export CUR 2.0 com 'Include resource IDs'" >&2
echo "em Billing and Cost Management > Data Exports; ele começa a gravar em até 24 h." >&2
echo "Baixe o arquivo (Actions > Download file) e importe na OneShift." >&2
