#!/usr/bin/env bash
# Baixa os relatórios de custo (cost reports) de um tenancy OCI e grava um
# arquivo no formato que a OneShift importa.
#
# Onde rodar: Cloud Shell da OCI (ícone >_ no topo do console), que já tem oci e
# python3 e já está logado. Em outra máquina: instale a OCI CLI e rode
# oci setup config.
#
# Os relatórios ficam num bucket da Oracle, fora do seu tenancy. Seu grupo precisa
# desta política no tenancy raiz (troque <grupo> pelo nome do grupo):
#   define tenancy usage-report as ocid1.tenancy.oc1..aaaaaaaaned4fkpkisbwjlr56u7cj63lf3wffbilvqknstgtvzub7vhqkggq
#   endorse group <grupo> to read objects in tenancy usage-report
#
# A OCI grava vários relatórios por dia. O script baixa os do mês, junta num
# arquivo só e deixa só as linhas de uso do mês pedido (as correções vêm junto).
#
# Uso:
#   bash oci-detalhe-de-uso.sh                 # último mês fechado
#   bash oci-detalhe-de-uso.sh 2026-09         # mês escolhido
#   TENANCY=ocid1.tenancy.oc1..xxx bash oci-detalhe-de-uso.sh 2026-09
set -euo pipefail

falha() { echo "ERRO: $*" >&2; exit 1; }

for cmd in oci python3; do
  command -v "$cmd" >/dev/null || falha "comando '$cmd' não encontrado. Rode no Cloud Shell da OCI."
done

if [[ $# -ge 1 ]]; then
  [[ "$1" =~ ^[0-9]{4}-(0[1-9]|1[0-2])$ ]] || falha "mês '$1' inválido. Use AAAA-MM, por exemplo 2026-09."
  mes="$1"
else
  mes="$(date -u -d "$(date -u +%Y-%m-01) -1 day" +%Y-%m 2>/dev/null || date -u -v1d -v-1d +%Y-%m)"
fi
inicio="${mes}-01"
fim="$(date -u -d "$inicio +1 month" +%F 2>/dev/null || date -u -j -v+1m -f %F "$inicio" +%F)"

# O Cloud Shell traz o tenancy em OCI_TENANCY; fora dele, vem do ~/.oci/config
tenancy="${TENANCY:-${OCI_TENANCY:-}}"
if [[ -z "$tenancy" && -f "${OCI_CLI_CONFIG_FILE:-$HOME/.oci/config}" ]]; then
  tenancy="$(sed -n 's/^tenancy[[:space:]]*=[[:space:]]*//p' "${OCI_CLI_CONFIG_FILE:-$HOME/.oci/config}" | head -1)"
fi
[[ "$tenancy" == ocid1.tenancy.* ]] ||
  falha "não achei o OCID do tenancy. Rode assim: TENANCY=ocid1.tenancy.oc1..xxx bash oci-detalhe-de-uso.sh $mes"
echo "Tenancy: $tenancy | mês: $mes ($inicio a $fim, exclusivo)" >&2
if [[ "$fim" > "$(date -u +%F)" ]]; then
  echo "  aviso: o mês ainda não terminou; o custo vem parcial (até hoje)." >&2
fi

trabalho="$(mktemp -d)"
trap 'rm -rf "$trabalho"' EXIT

# ── 1. Os relatórios criados no mês e nos dias seguintes ─────────────────────
# A última hora do mês chega no relatório do dia seguinte; 5 dias cobrem o atraso.
oci os object list --namespace-name bling --bucket-name "$tenancy" --prefix reports/cost-csv/ \
  --all --fields name,timeCreated,size --output json >"$trabalho/objetos.json" 2>"$trabalho/erro.txt" ||
  falha "não consegui listar os relatórios. Confira a política 'endorse group <grupo> to read objects in tenancy usage-report' (veja o topo deste arquivo). Resposta: $(head -c 300 "$trabalho/erro.txt")"

mapfile -t objetos < <(python3 - "$trabalho/objetos.json" "$inicio" "$fim" <<'PY'
import json, sys
from datetime import date, timedelta
dados = json.load(open(sys.argv[1])).get("data", [])
de = (date.fromisoformat(sys.argv[2]) - timedelta(days=1)).isoformat()
ate = (date.fromisoformat(sys.argv[3]) + timedelta(days=5)).isoformat()
for o in dados:
    criado = (o.get("time-created") or "")[:10]
    nome = o["name"]
    if nome.startswith("reports/cost-csv/") and nome.endswith(".csv.gz") and de <= criado <= ate:
        print(nome)
PY
)
[[ ${#objetos[@]} -gt 0 ]] ||
  falha "nenhum relatório de custo criado entre $inicio e $fim. Tenancies sem cobrança por uso não têm cost reports."
echo "  ${#objetos[@]} relatórios para baixar" >&2

mkdir -p "$trabalho/rel"
i=0
for objeto in "${objetos[@]}"; do
  i=$((i + 1))
  oci os object get --namespace-name bling --bucket-name "$tenancy" --name "$objeto" \
    --file "$trabalho/rel/$(printf '%05d' "$i").csv.gz" >/dev/null 2>"$trabalho/erro.txt" ||
    falha "não consegui baixar $objeto. Rode de novo. Resposta: $(head -c 300 "$trabalho/erro.txt")"
  (( i % 20 == 0 )) && echo "  $i de ${#objetos[@]}..." >&2
done

# ── 2. Um arquivo só, com as linhas de uso do mês ────────────────────────────
saida="oci-cost-report-${mes}.csv.gz"
python3 - "$saida" "$inicio" "$fim" "$trabalho"/rel/*.csv.gz <<'PY'
import csv, gzip, sys
from collections import defaultdict
from decimal import Decimal, InvalidOperation

saida, inicio, fim, *partes = sys.argv[1:]
csv.field_size_limit(10**8)
# As colunas de tag mudam de um relatório para outro: o cabeçalho é a união delas
colunas, linhas = [], []
for parte in partes:
    with gzip.open(parte, "rt", newline="", encoding="utf-8-sig") as fh:
        leitor = csv.DictReader(fh)
        for c in leitor.fieldnames or []:
            if c not in colunas:
                colunas.append(c)
        por_minuscula = {c.casefold(): c for c in leitor.fieldnames or []}
        coluna_inicio = por_minuscula.get("lineitem/intervalusagestart")
        if coluna_inicio is None:
            sys.exit(f"ERRO: {parte} não parece um cost report da OCI (sem lineItem/intervalUsageStart)")
        for linha in leitor:
            dia = (linha.get(coluna_inicio) or "")[:10]
            if inicio <= dia < fim:
                linhas.append(linha)
if not linhas:
    sys.exit(f"ERRO: os relatórios não têm uso entre {inicio} e {fim}")
total = defaultdict(Decimal)
coluna_custo = next((c for c in colunas if c.casefold() == "cost/mycost"), None)
coluna_moeda = next((c for c in colunas if c.casefold() == "cost/currencycode"), None)
with gzip.open(saida, "wt", newline="", encoding="utf-8") as out:
    w = csv.DictWriter(out, fieldnames=colunas, restval="")
    w.writeheader()
    for linha in linhas:
        w.writerow(linha)
        try:
            total[linha.get(coluna_moeda, "?") or "?"] += Decimal(linha.get(coluna_custo) or 0)
        except InvalidOperation:
            pass
print(f"  {len(linhas)} linhas de uso no mês", file=sys.stderr)
for moeda, valor in sorted(total.items()):
    print(f"  custo: {moeda} {valor.quantize(Decimal('0.01'))}", file=sys.stderr)
PY

echo >&2
echo "Pronto: $saida" >&2
echo "Para o número final do mês, rode de novo alguns dias depois do fechamento: as" >&2
echo "correções da Oracle chegam nos relatórios seguintes." >&2
echo "Baixe o arquivo (menu do Cloud Shell > Download) e importe na OneShift." >&2
