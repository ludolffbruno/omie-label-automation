# 🏷️ Omie Label Automation

> Sistema profissional de **automação de impressão de etiquetas logísticas** integrado ao ERP **Omie via API oficial**.
> Monitora faturamentos em tempo real, gera etiquetas ZPL e imprime automaticamente em impressoras Honeywell PC42t 203 DPI.

---

## 📋 Sumário

- [Funcionalidades](#-funcionalidades)
- [Arquitetura do Projeto](#-arquitetura-do-projeto)
- [Pré-requisitos](#-pré-requisitos)
- [Instalação](#-instalação)
- [Configuração](#-configuração)
- [Como Usar](#-como-usar)
- [Templates de Etiquetas](#-templates-de-etiquetas)
- [Regras de Clientes](#-regras-de-clientes)
- [Testes](#-testes)
- [Referência de Comandos CLI](#-referência-de-comandos-cli)

---

## ✨ Funcionalidades

| Funcionalidade | Descrição |
|---|---|
| **Integração Omie** | Consulta NF-es aprovadas via API oficial JSON-RPC com paginação e retry automático |
| **Templates ZPL** | Gera etiquetas 100×150mm com código de barras Code 128 para Default, Claro e GSK |
| **Multi-volume** | Imprime uma etiqueta por caixa (`Volume X de Y`) automaticamente |
| **Impressão RAW** | Envia ZPL bruto diretamente ao spooler Windows via `win32print` |
| **Anti-duplicação** | Banco de dados SQLite local garante que nenhuma nota é impressa duas vezes |
| **Interface Gráfica** | Dashboard PySide6 com tema escuro premium, logs em tempo real e controles visuais |
| **Monitor Automático** | Thread de polling configurável que verifica o ERP a cada N segundos |
| **Fallback de Simulação** | Sem impressora física? Salva arquivos `.zpl` localmente para inspeção |
| **Regras por Cliente** | Motor de extração regex/JSON configurável por cliente em `rules.json` |

---

## 🏗️ Arquitetura do Projeto

```
AUTOMACAO__ETIQUETAS/
├── main.py                     # Ponto de entrada (CLI + UI)
├── requirements.txt            # Dependências Python
├── .env                        # Credenciais (não versionado)
├── .env.example                # Modelo de credenciais
├── config.json                 # Configurações da aplicação (gerado automaticamente)
│
├── app/
│   ├── api/
│   │   ├── omie_client.py      # Cliente JSON-RPC para a API Omie
│   │   ├── zpl_generator.py    # Motor de templates ZPL (Default, Claro, GSK)
│   │   └── printer_service.py  # Spooler de impressão (win32print + simulação)
│   │
│   ├── core/
│   │   ├── config.py           # Configuração centralizada via .env e config.json
│   │   ├── logger.py           # Logging com loguru (console + arquivo rotativo)
│   │   ├── polling_worker.py   # Thread de monitoramento em background (QThread)
│   │   └── rules.json          # Regras de extração por cliente (regex/JSONPath)
│   │
│   ├── database/
│   │   └── models.py           # Modelos Pydantic + DatabaseManager (SQLite)
│   │
│   └── ui/
│       ├── ui_main.py          # Janela principal PySide6
│       └── styles.py           # Tema escuro premium (stylesheet global)
│
├── tests/
│   ├── test_omie_client.py     # Testes da API Omie e normalização
│   ├── test_zpl_generator.py   # Testes dos templates ZPL
│   └── test_printer_service.py # Testes do serviço de impressão
│
├── logs/                       # Logs rotativos da aplicação
└── temp_labels/                # Arquivos ZPL gerados no modo simulação
```

---

## 🖥️ Pré-requisitos

| Requisito | Versão mínima |
|---|---|
| Python | 3.10+ |
| Windows | 10 / 11 (para impressão física via `win32print`) |
| Omie ERP | Conta ativa com App Key + App Secret |
| Impressora Honeywell | PC42t 203 DPI com linguagem ZPL compatível |

> **Nota:** A aplicação roda em qualquer OS para desenvolvimento, mas a impressão física via `win32print` requer Windows.

---

## ⚙️ Instalação

### 1. Clone o repositório

```bash
git clone <url-do-repositorio>
cd AUTOMACAO__ETIQUETAS
```

### 2. Crie e ative o ambiente virtual

```bash
python -m venv .venv

# Windows
.venv\Scripts\activate

# Linux/macOS
source .venv/bin/activate
```

### 3. Instale as dependências

```bash
pip install -r requirements.txt
```

---

## 🔑 Configuração

### 1. Configure as credenciais Omie

Copie o arquivo de exemplo e preencha com suas credenciais:

```bash
copy .env.example .env
```

Edite o arquivo `.env`:

```env
# Obtido em: Omie ERP → Configurações → Meus Aplicativos
OMIE_APP_KEY=sua_app_key_aqui
OMIE_APP_SECRET=seu_app_secret_aqui
```

> 💡 Para obter as credenciais: acesse o [Portal do Desenvolvedor Omie](https://developer.omie.com.br/), crie um aplicativo e copie o `App Key` e `App Secret`.

### 2. Ajuste as configurações da aplicação

O arquivo `config.json` é gerado automaticamente na primeira execução. Edite conforme necessário:

```json
{
    "polling_interval": 30,
    "auto_print": false,
    "printer_name": "",
    "log_dir": "logs",
    "db_path": "omie_automation.db"
}
```

| Parâmetro | Descrição | Padrão |
|---|---|---|
| `polling_interval` | Segundos entre cada varredura automática | `30` |
| `auto_print` | Imprimir automaticamente toda nova NF-e | `false` |
| `printer_name` | Nome da impressora padrão | `""` |
| `log_dir` | Pasta dos arquivos de log | `logs/` |
| `db_path` | Caminho do banco de dados SQLite | `omie_automation.db` |

---

## 🚀 Como Usar

### Interface Gráfica (recomendado)

```bash
python main.py
```

A interface abre automaticamente quando nenhum argumento é fornecido.

![Dashboard](.github/dashboard_preview.png)

**Fluxo básico:**
1. Selecione a Honeywell PC42t no dropdown (ou `SIMULADO_ZEBRA_01` para testes)
2. Ajuste o intervalo de varredura (padrão: 30 segundos)
3. Marque **"Imprimir automaticamente"** se desejar impressão sem confirmação
4. Clique em **"Iniciar Monitor"**

### Modo de Linha de Comando

```bash
# Verificar status e configurações
python main.py --status

# Testar conexão com a API Omie
python main.py --test-connection

# Listar impressoras disponíveis no sistema
python main.py --list-printers

# Enviar etiqueta de teste para uma impressora
python main.py --print-test-label "Honeywell PC42t"

# Enviar etiqueta de teste no modo simulado (gera arquivo .zpl)
python main.py --print-test-label SIMULADO_ZEBRA_01

# Buscar e exibir NF-es a partir de uma data
python main.py --fetch-date 01/05/2026
```

---

## 🏷️ Templates de Etiquetas

O sistema gera etiquetas **100×150mm** (4×6 polegadas) em código **ZPL** com **Code 128** da chave de acesso NF-e.

### Template Padrão (`default`)
Usado para clientes sem regra específica cadastrada.

```
┌────────────────────────────────┐
│  ETIQUETA LOGISTICA            │
├────────────────────────────────┤
│  Destinatário: [NOME CLIENTE]  │
│  CNPJ: [XX.XXX.XXX/0001-XX]   │
├────────────────────────────────┤
│  Nota Fiscal: [NF]             │
│  Pedido Omie: [PEDIDO]         │
│  Pedido Cliente: [PED CLI]     │
├────────────────────────────────┤
│      VOLUME 1 DE 2             │
├────────────────────────────────┤
│  ┌──────────────────────────┐  │
│  │ ▐▌▌▐▌▐▌▌▐▌▌▐▌▌▐▌▐▌▌ │  │
│  └──────────────────────────┘  │
│  [CHAVE NF-e em blocos de 4]   │
└────────────────────────────────┘
```

### Template Claro (`claro`)
Inclui destaque para **Ordem de Compra (OC)**, **Requisitante (A/C)** e **Número de Ordem**.

### Template GSK (`gsk`)
Inclui destaque para **GSK-OC**, **Solicitante** e **Pedido de Venda Omie**.

---

## ⚙️ Regras de Clientes

O arquivo `app/core/rules.json` define como extrair campos customizados das observações da NF-e:

```json
{
    "CLARO": {
        "template": "claro",
        "mappings": {
            "oc": {
                "source": "observacoes",
                "regex": "(?:OC|PEDIDO COMPRA|ORDEM DE COMPRA):?\\s*([A-Za-z0-9\\-]+)"
            },
            "requisitante": {
                "source": "observacoes",
                "regex": "(?:A/C|REQUISITANTE|SOLICITANTE):?\\s*([^|\\n;]+)"
            },
            "numero_ordem": {
                "source": "observacoes",
                "regex": "(?:N[Ooº]\\s*ORDEM|ORDEM):?\\s*([A-Za-z0-9\\-]+)"
            }
        }
    },
    "MEU_CLIENTE": {
        "template": "default",
        "mappings": {
            "oc": {
                "source": "observacoes",
                "regex": "MINHA_OC:\\s*([A-Za-z0-9\\-]+)"
            }
        }
    }
}
```

**Como adicionar um novo cliente:**
1. Abra `app/core/rules.json`
2. Adicione uma nova chave com parte do nome do cliente (ex: `"PETROBRAS"`)
3. Defina o `template` (`default`, `claro` ou `gsk`) e as expressões regex para extração
4. Reinicie a aplicação

---

## 🧪 Testes

```bash
# Rodar toda a suíte de testes
python -m pytest tests/ -v

# Resultado esperado
# 14 passed in ~1.5s
```

| Arquivo de Teste | Cobertura |
|---|---|
| `test_omie_client.py` | Autenticação, normalização de NF-e, paginação, regras Claro/GSK |
| `test_zpl_generator.py` | Geração de ZPL, sanitização de acentos, multi-volume |
| `test_printer_service.py` | Listagem de impressoras, impressão RAW win32, modo simulado |

---

## 📖 Referência de Comandos CLI

```
usage: main.py [-h] [--test-connection] [--fetch-date DATA]
               [--list-printers] [--print-test-label IMPRESSORA]
               [--ui] [--status]

Omie Label Automation - CLI Tool

options:
  --ui                    Abre a interface gráfica (padrão sem argumentos)
  --test-connection       Valida credenciais e testa conexão com a API Omie
  --fetch-date DD/MM/AAAA Busca NF-es aprovadas a partir dessa data
  --list-printers         Lista todas as impressoras do sistema
  --print-test-label NOME Envia etiqueta de teste para a impressora especificada
  --status                Exibe o status atual das configurações
```

---

## 🔗 Links Úteis

- [Portal do Desenvolvedor Omie](https://developer.omie.com.br/)
- [Lista de APIs Omie](https://developer.omie.com.br/service-list/)
- [Linguagem ZPL — referência Zebra](https://www.zebra.com/us/en/support-downloads/knowledge-articles/zpl-zbi2-pm-programming-guide.html)
- [Labelary ZPL Viewer](http://labelary.com/viewer.html) — Visualize os arquivos `.zpl` gerados em `temp_labels/`

---

## 📄 Licença

Projeto proprietário — Antigravity Projetos © 2026. Todos os direitos reservados.
