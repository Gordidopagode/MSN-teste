# MSN Messenger — Launcher para Windows

O launcher é a porta de entrada do programa. Ele fica fora do Hub e do servidor: apenas detecta/inicia o servidor Python, verifica um handshake WebSocket real, serve a cópia compilada do Hub e abre o navegador padrão.

> O launcher não autentica usuários, não encaminha mensagens, não armazena chats e não implementa um segundo backend.

## Uso para o usuário final

Na distribuição Windows, basta abrir `MSN Messenger.exe`. O launcher mostra duas opções:

| Opção | Comportamento |
|---|---|
| **Iniciar ou usar servidor local** | Detecta `localhost:8765`; se não houver servidor WebSocket ativo, inicia somente o servidor local empacotado, espera o handshake real e abre o Hub. |
| **Entrar em servidor existente** | Valida o host e a porta, testa o handshake WebSocket e abre o Hub sem iniciar ou encerrar backend local. |

O endereço escolhido é enviado ao Hub pela query string `?server=ws://host:porta`. A HUB continua sendo o cliente WebSocket e usa o servidor Python existente.

## Arquivos principais

| Arquivo/pasta | Finalidade |
|---|---|
| `launcher/launcher.py` | GUI, configuração, descoberta, processo local, frontend, logs e shutdown |
| `launcher/tests/test_launcher.py` | Testes de endpoint, probe WebSocket, configuração, frontend, falha e servidor duplicado |
| `hub_source/` | Fonte da HUB existente para recompilação durante o build |
| `client/public/` | Cópia compilada da HUB existente |
| `server/` | Cópia do servidor Python MSN existente; não é um novo backend |
| `server_bundle/` | Executável do servidor gerado no build Windows |
| `build_windows.ps1` | Script de empacotamento portátil para Windows |
| `LAUNCHER_AUDIT.md` | Relatório técnico e resultados dos testes |
| `config/launcher.json` | Criado em execução; guarda apenas último host, porta, modo e preferência de shutdown |
| `logs/launcher.log` | Log do launcher |
| `logs/server.log` | Saída técnica do servidor local iniciado pelo launcher |
| `data/` | Dados SQLite do servidor local |

## Build Windows

A ferramenta disponível para a compilação do executável final é o **PyInstaller**, executado no Windows para produzir o formato nativo do ambiente. O build precisa ser realizado em uma máquina Windows com Python oficial contendo Tcl/Tk, Node.js/pnpm e acesso para instalar dependências.

No PowerShell, na raiz deste projeto:

```powershell
Set-ExecutionPolicy -Scope Process Bypass
.\build_windows.ps1
```

O script cria `release\MSN Messenger.exe`, copia o build já existente do Hub para `release\client\public`, compila o servidor Python existente para `release\server_bundle\msn-server.exe` e mantém `data`, `logs` e `config` ao lado do executável.

O usuário final não precisa instalar Python, Node.js, npm ou pytest quando recebe a pasta `release` completa. Durante o build, Node/pnpm são necessários apenas para recompilar o frontend e PyInstaller é necessário apenas para montar os executáveis.

## Desenvolvimento sem empacotar

É possível executar o launcher com Python a partir da raiz:

```powershell
python launcher\launcher.py
```

Nesse modo, ao escolher servidor local, ele executa `python -m server.main` usando caminhos relativos ao projeto. Para testar a descoberta sem abrir a GUI:

```powershell
python launcher\launcher.py --probe localhost 8765
```

## Configuração e segurança

O arquivo `config/launcher.json` não guarda senha, token ou credencial. O launcher persiste somente o host, a porta, o modo selecionado e a preferência de encerramento do servidor local.

O launcher nunca mata processos Python arbitrários. Ele encerra somente o processo que registrou em `LocalServerController.process` e somente quando esse processo foi criado pela mesma instância do launcher.

## Limitação conhecida do ambiente atual

O sandbox Linux usado para preparar este projeto não possui `tkinter`, PyInstaller nem um toolchain Windows. Por isso, o launcher foi testado em modo headless e por compilação estática, mas o `.exe` Windows precisa ser gerado no Windows executando `build_windows.ps1`. O script interrompe com mensagens claras se Tcl/Tk, Python, pnpm ou PyInstaller estiverem ausentes.
