# ost-<contexto>.<ferramenta>

> Substitua o título e esta descrição pelo nome e propósito do repositório.

---

## Stack

| Item | Tecnologia |
|------|-----------|
| Linguagem | <!-- ex: TypeScript, Python --> |
| Framework | <!-- ex: Next.js, FastAPI --> |
| Infra | <!-- ex: Kubernetes, Terraform --> |

---

## Como rodar localmente

```bash
# 1. Clone o repositório
git clone https://github.com/oneshift-org/<repo>.git
cd <repo>

# 2. Instale as dependências
# ex: npm install / pip install -r requirements.txt

# 3. Configure as variáveis de ambiente
cp .env.example .env
# Preencha os valores em .env (nunca commite este arquivo)

# 4. Inicie o servidor de desenvolvimento
# ex: npm run dev / uvicorn main:app --reload
```

---

## Variáveis de ambiente

| Variável | Descrição | Obrigatória |
|----------|-----------|-------------|
| `EXEMPLO_VAR` | Descrição da variável | Sim |

> Secrets são gerenciados via HashiCorp Vault. Consulte a equipe de DevOps para obter acesso.

---

## Gitflow

| Branch | Ambiente |
|--------|----------|
| `main` | produção |
| `develop` | desenvolvimento |

Branches seguem o padrão `feat/<card-linear>`, `fix/<card-linear>` ou `hotfix/<card-linear>`.

---

## Documentação

Documentação completa em [docs.oneshift.com.br](https://docs.oneshift.com.br).
