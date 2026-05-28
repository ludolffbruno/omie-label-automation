<div align="center">

<br/>

# 🏷️ Omie Label Automation

**Monitor de NF-e Omie com enriquecimento por DANFE e impressão logística em Honeywell PC42t Direct Protocol.**

[![Python](https://img.shields.io/badge/Python-3.10+-3776AB?style=flat-square&logo=python&logoColor=white)](https://www.python.org)
[![PySide6](https://img.shields.io/badge/UI-PySide6-41CD52?style=flat-square&logo=qt&logoColor=white)](https://doc.qt.io/qtforpython-6/)
[![HTML](https://img.shields.io/badge/Preview-HTML5-E34F26?style=flat-square&logo=html5&logoColor=white)](https://developer.mozilla.org/docs/Web/HTML)
[![CSS](https://img.shields.io/badge/Styles-CSS3-1572B6?style=flat-square&logo=css3&logoColor=white)](https://developer.mozilla.org/docs/Web/CSS)
[![SQLite](https://img.shields.io/badge/Cache-SQLite-003B57?style=flat-square&logo=sqlite&logoColor=white)](https://sqlite.org)
[![PyInstaller](https://img.shields.io/badge/Build-PyInstaller-6B7280?style=flat-square)](https://pyinstaller.org)
[![pytest](https://img.shields.io/badge/Tests-pytest-0A9EDC?style=flat-square&logo=pytest&logoColor=white)](https://pytest.org)
[![Windows](https://img.shields.io/badge/Windows-10%2F11-0078D4?style=flat-square&logo=windows&logoColor=white)](https://www.microsoft.com/windows)
[![Honeywell](https://img.shields.io/badge/Printer-PC42t_DP-f59e0b?style=flat-square)](https://sps.honeywell.com)
[![Omie](https://img.shields.io/badge/API-Omie-0891d1?style=flat-square)](https://developer.omie.com.br/)

[Funcionalidades](#-funcionalidades) · [Stack](#-stack-técnica) · [Como Rodar](#-como-rodar) · [Gerar EXE](#-gerar-exe)

</div>

---

## ✨ Funcionalidades

| Funcionalidade | Descrição |
|---|---|
| 🔎 **Busca por data exata** | Consulta NF-e faturada no Omie somente na data selecionada |
| 🧾 **Enriquecimento via DANFE** | Preenche UF, pedido, protocolo e requisitante a partir do PDF da DANFE |
| 💾 **Cache local SQLite** | Evita chamadas repetidas ao Omie e reaproveita DANFEs já processados |
| 🖨️ **Impressão Honeywell DP** | Gera Direct Protocol nativo para Honeywell PC42t 203 dpi |
| 🏷️ **Etiqueta padrão** | Modelo para clientes comuns, com pedido, NF-e, volume, UF e requisitante |
| 📦 **Claro dividida** | Agrupa Claro/Telmex/Claro NXT em duas NF-e por etiqueta quando possível |
| 🧩 **Modelos por cliente** | Cadastro visual de regras, observações, ordem e padrão por cliente |
| ✏️ **Edição rápida** | Edição de etiqueta com opção de criar/atualizar modelo reutilizável |
| ⏱️ **Controle de cooldown** | Evita erro Omie `REDUNDANT` respeitando tempo de nova tentativa |
| 📊 **Logs operacionais** | Logs agregados e profissionais para busca, DANFE, cache e impressão |
| 👁️ **Preview HTML** | Visualização antes da impressão, com paginação e observação no padrão |
| 🧪 **Modo simulado** | Gera arquivos de impressão em `temp_labels/` sem enviar para impressora |

---

## 🛠️ Stack Técnica

| Camada | Tecnologia |
|---|---|
| **Linguagem** | Python 3.10+ |
| **Interface** | PySide6 |
| **Preview** | HTML |
| **Estilo** | Qt Stylesheets + CSS inline para preview |
| **API** | Omie JSON-RPC |
| **HTTP** | httpx + retry/backoff |
| **PDF/DANFE** | pypdf |
| **Banco local** | SQLite |
| **Impressão** | win32print RAW |
| **Linguagem de etiqueta** | Honeywell Direct Protocol (DP) |
| **Build Windows** | PyInstaller |
| **Testes** | pytest |

---

## 🔒 Segurança

- Credenciais Omie ficam somente no `.env` local.
- Banco, logs, DANFEs, PDFs e arquivos temporários não são versionados.
- No build `.exe`, dados locais ficam ao lado do executável.
- `rules.json` pode ser versionado: guarda regras/modelos, não credenciais.

---

## ▶️ Como Rodar

### Pré-requisitos

- Windows 10/11
- Python 3.10+
- Honeywell PC42t instalada no Windows
- Credenciais Omie: `OMIE_APP_KEY` e `OMIE_APP_SECRET`

### 1. Instale dependências

```powershell
python -m venv .venv
.\.venv\Scripts\activate
python -m pip install --upgrade pip
pip install -r requirements.txt
```

### 2. Configure ambiente

Crie `.env` na raiz:

```env
OMIE_APP_KEY=sua_app_key
OMIE_APP_SECRET=sua_app_secret
```

### 3. Execute

```powershell
python main.py
```

---

## 📦 Gerar EXE

Com o ambiente já configurado, execute na raiz do projeto:

```powershell
python -m PyInstaller --noconfirm --clean --windowed --name "OmieLabelAutomation" --add-data "app/core/rules.json;app/core" --hidden-import win32timezone main.py
```

Saída:

```text
dist\OmieLabelAutomation\OmieLabelAutomation.exe
```

Para instalar em outro computador, copie a pasta inteira:

```text
dist\OmieLabelAutomation\
```

No computador destino, crie `.env` ao lado do `.exe`:

```env
OMIE_APP_KEY=sua_app_key
OMIE_APP_SECRET=sua_app_secret
```

---

## 🧪 Comandos Úteis

```powershell
# Interface
python main.py

# Status local
python main.py --status

# Testar credenciais Omie
python main.py --test-connection

# Listar impressoras
python main.py --list-printers

# Etiqueta de teste
python main.py --print-test-label "Honeywell PC42t"

# Teste fisico isolado do barcode DP
python main.py --print-dp-barcode-test "Honeywell PC42t"

# Simulacao sem impressora
python main.py --print-test-label SIMULADO_ZEBRA_01
python main.py --print-dp-barcode-test SIMULADO_ZEBRA_01
```

---

## 🏷️ Modelos de Etiqueta

| Modelo | Quando usar | Comportamento |
|---|---|---|
| **Padrão** | Clientes comuns | Uma NF-e por etiqueta |
| **Claro dividida** | Claro SA, Telmex, Claro NXT | Duas NF-e por etiqueta quando a seleção permitir |
| **GSK** | Regras específicas GSK | Usa template e extrações próprias |

No botão **Modelos Etiquetas**:

- `+ Novo Modelo` cria regra por cliente/texto/cidade/UF.
- `Observação` imprime no modelo padrão e aparece no preview HTML.
- `Tornar Padrão` marca a regra preferida para aquele cliente.
- `↑` e `↓` organizam a lista de modelos.
- Lixeira remove modelos criados; `Padrão` e `Claro Dividida` são protegidos.
- Na grade, `ONS *` indica modelo aplicado com observação.

No botão **Editar**, alterações estáveis podem virar modelo:

- Salva nome exibido do cliente, requisitante, observação e layout.
- Não salva dados voláteis como NF-e, pedido, protocolo, volume, data ou UF.

Regras ficam em:

```text
app/core/rules.json
```

---

## 📁 Estrutura do Projeto

```text
AUTOMACAO__ETIQUETAS/
├── main.py
├── requirements.txt
├── OmieLabelAutomation.spec
├── app/
│   ├── api/
│   │   ├── omie_client.py
│   │   ├── printer_service.py
│   │   ├── zpl_generator.py
│   │   └── dp_generator.py
│   ├── core/
│   │   ├── config.py
│   │   ├── logger.py
│   │   ├── polling_worker.py
│   │   └── rules.json
│   ├── database/
│   │   └── models.py
│   └── ui/
│       ├── ui_main.py
│       └── styles.py
└── tests/
```

---

## ✅ Testes

```powershell
python -m pytest tests -q
```

Teste específico do gerador DP:

```powershell
python -m pytest tests/test_dp_generator.py -q
```

Resultado atual validado:

```text
42 passed
```

---

## 👤 Autor

<div align="center">

· **MrLudolff**

[![GitHub](https://img.shields.io/badge/GitHub-ludolffbruno-181717?style=flat-square&logo=github)](https://github.com/ludolffbruno)

</div>

---

## 📄 Licença

Projeto proprietário. Uso interno/autorizado.

---

<div align="center">
  <sub>Desenvolvido para operação logística com Omie + Honeywell PC42t · <strong>MrLudolff</strong></sub>
</div>
