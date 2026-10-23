#!/usr/bin/env bash
# Baixa o detalhe de uso de uma assinatura Azure pay-as-you-go (MOSP) e grava
# um CSV no formato que a OneShift importa.
#
# Onde rodar: Azure Cloud Shell (ícone >_ no topo do portal, modo Bash), que já
# tem az e jq e já está logado. Em outra máquina: instale az e jq e rode az login.
#
# Uso:
#   bash azure-detalhe-de-uso.sh                 # assinatura atual, último período fechado
#   bash azure-detalhe-de-uso.sh <assinatura>    # outra assinatura
#   PERIODOS=3 bash azure-detalhe-de-uso.sh      # os 3 últimos períodos fechados
#   bash azure-detalhe-de-uso.sh <assinatura> 2026-08-24 2026-09-23   # datas escolhidas
set -euo pipefail

API_USO="2023-11-01"
API_PERIODOS="2018-03-01-preview"
PERIODOS="${PERIODOS:-1}"

falha() { echo "ERRO: $*" >&2; exit 1; }

for cmd in az jq; do
  command -v "$cmd" >/dev/null || falha "comando '$cmd' não encontrado. Rode no Azure Cloud Shell (Bash)."
done

az account show >/dev/null 2>&1 || falha "não está logado na Azure. Rode: az login"
assinatura="${1:-$(az account show --query id -o tsv)}"
nome="$(az account show --subscription "$assinatura" --query name -o tsv 2>/dev/null)" ||
  falha "assinatura '$assinatura' não encontrada. Veja as disponíveis com: az account list -o table"
echo "Assinatura: $nome ($assinatura)" >&2

trabalho="$(mktemp -d)"
trap 'rm -rf "$trabalho"' EXIT

# GET com nova tentativa: a API de consumo limita chamadas (HTTP 429).
pedir() {
  local destino="$1"; shift
  local tentativa
  for tentativa in 1 2 3 4 5; do
    if az rest --method get "$@" --output json >"$destino" 2>"$trabalho/erro.txt"; then
      return 0
    fi
    if grep -qiE "429|too many|throttl|timed out|50[234]" "$trabalho/erro.txt" && [[ $tentativa -lt 5 ]]; then
      echo "  a Azure pediu para esperar; tentando de novo em $((tentativa * 15))s..." >&2
      sleep $((tentativa * 15))
      continue
    fi
    return 1
  done
}

erro_da_azure() { grep -m1 -E "ERROR|error|message" "$trabalho/erro.txt" | cut -c1-300 || true; }

base="https://management.azure.com/subscriptions/${assinatura}"

if [[ $# -ge 3 ]]; then
  # Datas informadas: consulta direta por data, sem a lista de períodos.
  for d in "$2" "$3"; do
    [[ "$d" =~ ^[0-9]{4}-[0-9]{2}-[0-9]{2}$ ]] || falha "data '$d' inválida. Use AAAA-MM-DD, por exemplo 2026-08-24."
  done
  [[ "$2" < "$3" ]] || falha "a data inicial ($2) precisa ser anterior à final ($3)."
  periodos=("datas $2 $3")
else
  # 1. Períodos de cobrança da assinatura (os mesmos da tela Faturas).
  pedir "$trabalho/periodos.json" --url "$base/providers/Microsoft.Billing/billingPeriods" \
    --url-parameters "api-version=$API_PERIODOS" ||
    falha "não consegui listar os períodos de cobrança. A conta logada precisa de permissão de leitura de custos na assinatura (Proprietário, Colaborador, Leitor ou Leitor de Cobrança). Resposta da Azure: $(erro_da_azure)"

  # Fechado há pelo menos 3 dias: o detalhe de uso ainda chega à Azure depois do fim do período.
  hoje="$(date -u -d '3 days ago' +%F 2>/dev/null || date -u -v-3d +%F)"
  mapfile -t periodos < <(jq -r --arg hoje "$hoje" '
    .value
    | map(select(.properties.billingPeriodEndDate < $hoje))
    | sort_by(.properties.billingPeriodStartDate) | reverse
    | .[] | "\(.name) \(.properties.billingPeriodStartDate) \(.properties.billingPeriodEndDate)"
  ' "$trabalho/periodos.json" | head -n "$PERIODOS")
  [[ ${#periodos[@]} -gt 0 ]] || falha "nenhum período de cobrança fechado encontrado nesta assinatura."
fi

# 2. Detalhe de uso de cada período, todas as páginas. A API aceita a consulta de
# mais de um jeito conforme o tipo de assinatura; tenta na ordem até uma responder.
expandir="\$expand=properties/meterDetails,properties/additionalInfo"
n=0

# baixar <rótulo> <url> <parâmetros...>: 0 se baixou todas as páginas.
baixar() {
  local rotulo="$1" url="$2"; shift 2
  local inicio_n=$n primeira=1 pagina
  while [[ -n "$url" ]]; do
    n=$((n + 1))
    pagina="$trabalho/uso-$(printf '%05d' "$n").json"
    if [[ $primeira -eq 1 ]]; then
      if ! pedir "$pagina" --url "$url" --url-parameters "$@"; then
        echo "  [$rotulo] recusado: $(erro_da_azure)" >&2
        rm -f "$pagina"; n=$inicio_n
        return 1
      fi
      primeira=0
    else
      pedir "$pagina" --url "$url" ||
        falha "a Azure parou de responder no meio do download. Rode de novo. Resposta: $(erro_da_azure)"
    fi
    echo "  página $((n - inicio_n)): $(jq '.value | length' "$pagina") linhas" >&2
    url="$(jq -r '.nextLink // empty' "$pagina")"
  done
}

for linha in "${periodos[@]}"; do
  read -r periodo inicio fim <<<"$linha"
  if [[ "$periodo" == "datas" ]]; then
    echo "De $inicio a $fim..." >&2
  else
    echo "Período $periodo ($inicio a $fim)..." >&2
  fi
  if [[ "${fim:0:10}" > "$(date -u +%F)" || "${fim:0:10}" == "$(date -u +%F)" ]]; then
    echo "  aviso: o período ainda não terminou; o custo vem parcial (até hoje)." >&2
  fi
  por_periodo="$base/providers/Microsoft.Billing/billingPeriods/$periodo/providers/Microsoft.Consumption/usageDetails"
  por_data="$base/providers/Microsoft.Consumption/usageDetails"
  filtro="\$filter=properties/usageStart ge '${inicio:0:10}' and properties/usageEnd le '${fim:0:10}'"
  # Pay-as-you-go ("Web Direct") só aceita a consulta por data; o escopo de
  # período fica por último, para os outros tipos de assinatura.
  baixar "A" "$por_data" "api-version=$API_USO" "$filtro" "$expandir" ||
    baixar "B" "$por_data" "api-version=2019-10-01" "$filtro" "$expandir" ||
    { [[ "$periodo" != "datas" ]] && baixar "C" "$por_periodo" "api-version=$API_USO" "$expandir"; } ||
    baixar "D" "$por_data" "api-version=2019-10-01" "$filtro" ||
    falha "a Azure recusou todas as formas de consulta de $inicio a $fim (respostas acima). Mande um print desta tela para o suporte da OneShift."
  ultimo_inicio="${inicio:0:10}"
  [[ -n "${primeiro_fim:-}" ]] || primeiro_fim="${fim:0:10}"
done

saida="azure-cost-details-${ultimo_inicio}_${primeiro_fim}.csv"

# 3. CSV com os nomes de coluna do Cost Details. O formato "legacy" (pay-as-you-go)
# traz o medidor em meterDetails; o "modern" traz tudo no topo.
jq -r -s '
  ["Date","BillingPeriodStartDate","SubscriptionId","SubscriptionName","ResourceGroup",
   "ResourceId","ResourceLocation","MeterCategory","MeterSubCategory","MeterName",
   "Quantity","UnitOfMeasure","CostInBillingCurrency","BillingCurrency","PayGPrice",
   "ChargeType","PricingModel","AdditionalInfo","Tags"],
  (.[].value[]
   | .properties as $p
   | ($p.meterDetails // {}) as $m
   | [$p.date, $p.billingPeriodStartDate,
      ($p.subscriptionId // $p.subscriptionGuid), $p.subscriptionName,
      $p.resourceGroup, ($p.resourceId // $p.instanceName), $p.resourceLocation,
      ($m.meterCategory // $p.meterCategory), ($m.meterSubCategory // $p.meterSubCategory),
      ($m.meterName // $p.meterName // $p.product),
      $p.quantity, ($m.unitOfMeasure // $p.unitOfMeasure),
      ($p.cost // $p.costInBillingCurrency), ($p.billingCurrency // $p.billingCurrencyCode),
      $p.payGPrice, $p.chargeType, $p.pricingModel, $p.additionalInfo,
      (if .tags then (.tags | tojson) else "" end)]
   | map(if . == null then "" else tostring end))
  | @csv
' "$trabalho"/uso-*.json >"$saida"

# 4. Conferência antes de entregar o arquivo.
jq -s -r '
  [.[].value[]] as $l
  | ($l | length) as $n
  | if $n == 0 then "LINHAS=0"
    else
      "LINHAS=\($n)",
      "SEM_CATEGORIA=\([$l[] | select((.properties.meterDetails.meterCategory // .properties.meterCategory // "") == "")] | length)",
      ($l | group_by(.properties.billingCurrency // .properties.billingCurrencyCode)[]
          | "TOTAL \(.[0].properties.billingCurrency // .[0].properties.billingCurrencyCode)=\(map(.properties.cost // .properties.costInBillingCurrency // 0) | add * 100 | round / 100)")
    end
' "$trabalho"/uso-*.json >"$trabalho/resumo.txt"

linhas="$(sed -n 's/^LINHAS=//p' "$trabalho/resumo.txt")"
[[ "$linhas" != "0" ]] || { rm -f "$saida"; falha "a Azure não devolveu nenhuma linha de uso para o período. Confira se é a assinatura certa."; }
sem_categoria="$(sed -n 's/^SEM_CATEGORIA=//p' "$trabalho/resumo.txt")"

echo >&2
echo "Pronto: $saida" >&2
echo "  $linhas linhas" >&2
sed -n 's/^TOTAL /  custo antes de créditos e impostos: /p' "$trabalho/resumo.txt" >&2
if [[ "$sem_categoria" != "0" ]]; then
  echo "  aviso: $sem_categoria linhas vieram sem categoria de medidor" >&2
fi
echo "Baixe o arquivo (Gerenciar arquivos > Baixar) e importe na OneShift." >&2
