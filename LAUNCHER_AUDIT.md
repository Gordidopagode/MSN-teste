# Auditoria e implementação do MSN Messenger Launcher

**Autor:** Manus AI  
**Escopo:** criar uma porta de entrada separada para o servidor Python e a HUB já existentes, sem redesenhar o frontend nem criar um segundo backend.

## Resultado executivo

Foi criado `launcher/launcher.py`, um executor desktop baseado na biblioteca padrão do Python/Tkinter. Ele oferece ao usuário final dois caminhos: usar/iniciar um servidor local ou entrar em um servidor existente. O launcher valida host e porta, verifica o handshake WebSocket real, inicia somente o processo local que ele próprio controla, serve a cópia compilada da HUB existente e passa o endpoint pela query string `?server=...`.

A HUB continua sendo responsável por autenticação, reconexão, sincronização, presença, grupos e mensagens. O launcher não intercepta frames, não autentica, não persiste chats e não duplica protocolo.

## Arquivos criados

| Arquivo | Responsabilidade |
|---|---|
| `launcher/launcher.py` | Aplicativo desktop, configuração, probe, processo do servidor, frontend, logs, monitoramento e encerramento |
| `launcher/tests/test_launcher.py` | Testes automatizados de validação, probe, configuração, frontend, falha e duplicação |
| `launcher-requirements.txt` | Dependências de build/teste do launcher |
| `build_windows.ps1` | Compilação do servidor existente, HUB existente e launcher com PyInstaller no Windows |
| `README_LAUNCHER.md` | Manual de uso, build, configuração e limitações |
| `LAUNCHER_AUDIT.md` | Este relatório |
| `server-requirements.txt` | Cópia das dependências do servidor Python para o build portátil |
| `hub_source/` | Fonte da HUB existente para recompilação durante o build Windows |
| `client/public/` | Cópia compilada da HUB existente |
| `server/` | Cópia do servidor Python já auditado; não é uma implementação nova |

## Arquivos alterados

Não foram alterados os arquivos-fonte da HUB nem os arquivos-fonte do servidor original. O único ajuste de integração necessário na HUB foi preparado na versão já conectada entregue anteriormente: `client/src/network/config.ts` aceita `?server=...` como configuração transitória do launcher e mantém a variável `VITE_MSN_SERVER_URL` como fallback.

## Funcionamento

### Servidor local

Ao escolher **Iniciar ou usar servidor local**, o launcher executa primeiro `WebSocketProbe.check`. Se `localhost:8765` já responde a um upgrade WebSocket válido, o launcher marca o servidor como externo àquela instância e não inicia outro processo. Se não houver servidor, ele inicia o servidor empacotado `server_bundle/msn-server.exe` no Windows ou `python -m server.main` durante o desenvolvimento, usando diretórios relativos ao aplicativo.

Depois do início, o launcher não considera o processo suficiente. Ele repete o probe até obter `HTTP/1.1 101` com cabeçalhos de upgrade WebSocket ou até atingir 20 segundos. Se o processo encerrar ou a porta não responder, mostra uma mensagem resumida e grava o detalhe em `logs/server.log`.

### Servidor existente

Ao escolher **Entrar em servidor existente**, o launcher valida host e porta e executa o mesmo handshake WebSocket. Se falhar, mostra `Não foi possível conectar ao servidor informado` e não abre o Hub. Se funcionar, o launcher não inicia, monitora ou encerra qualquer processo remoto.

### Abertura da HUB

O frontend compilado é servido por um pequeno `ThreadingHTTPServer` local usando uma porta livre. O navegador padrão é aberto com uma URL no formato:

```text
http://127.0.0.1:<porta-do-frontend>/?server=ws://host:porta
```

A HUB lê esse endpoint e estabelece a comunicação diretamente com o servidor Python. O launcher não se torna intermediário de mensagens.

### Monitoramento e encerramento

O launcher mantém a referência `Popen` somente para o processo que iniciou. Um monitor periódico detecta encerramento inesperado desse processo, informa o usuário uma vez e não entra em loop infinito de reinicialização.

Ao fechar a janela, o usuário confirma se deseja encerrar o servidor local iniciado pela mesma instância. Se a sessão usou um servidor já existente, o launcher jamais o encerra. O frontend HTTP local é encerrado sempre que o launcher termina.

## Configuração

O arquivo `config/launcher.json` é criado automaticamente e guarda apenas:

```json
{
  "host": "localhost",
  "port": 8765,
  "mode": "local",
  "shutdown_local_server": true
}
```

Nenhuma senha, sessão, token ou mensagem é armazenada no arquivo. Os logs ficam em `logs/launcher.log` e a saída do servidor local fica em `logs/server.log`.

## Testes realizados

| Teste | Resultado |
|---|---|
| Validação de host, porta e URL WebSocket | Aprovado |
| Probe sem servidor ativo | Aprovado; retorna falso sem travar |
| Persistência somente de preferências | Aprovado; sem password/token |
| Servidor frontend em porta livre | Aprovado; serviu index e codificou endpoint |
| Modo servidor existente indisponível | Aprovado; recusa abertura do Hub |
| Início de servidor Python real | Aprovado em teste de integração |
| Probe por handshake WebSocket real | Aprovado |
| Segunda instância sem servidor duplicado | Aprovado; segunda instância não cria processo |
| Coordinator local completo | Aprovado; inicia servidor, serve o frontend, injeta endpoint e encerra processo próprio |
| Coordinator com servidor existente | Aprovado; abre frontend sem assumir nem encerrar o processo existente |
| Trava de launcher duplicado | Aprovado; segunda instância sai sem abrir outra janela |
| Encerramento de processo próprio | Aprovado no teste de integração |
| Suíte automatizada do launcher | **10 testes aprovados** |
| CLI `--probe` sem servidor | Aprovado; exibe `Servidor não encontrado` e encerra sem traceback |
| Typecheck e build da HUB integrada | Aprovados antes da montagem do launcher |
| Registro, login, grupo, mensagem, presença e logout na HUB | Aprovados na validação anterior |

## Empacotamento Windows

Foi preparado `build_windows.ps1`. Em uma máquina Windows com Python oficial, Tcl/Tk, Node.js/pnpm e internet para instalar dependências, o script:

1. verifica Tkinter;
2. instala dependências de build;
3. compila o servidor Python existente em `server_bundle/msn-server.exe`;
4. recompila a HUB existente;
5. compila `launcher/launcher.py` como `MSN Messenger.exe` com PyInstaller `--windowed --onedir`;
6. monta uma pasta `release` com `MSN Messenger.exe`, `client/public`, `server_bundle`, `data`, `logs` e `config`.

O usuário final recebe a pasta `release` e abre o executável. Não precisa instalar Python, Node.js, npm ou pytest.

O executável Windows não foi gerado neste ambiente porque o sandbox atual é Linux e não possui `tkinter`, PyInstaller, PowerShell ou toolchain Windows. Não é tecnicamente correto entregar um binário Linux com nome `.exe`. A ausência é detectada e documentada pelo script de build.

## Limitações

A configuração padrão usa `ws://localhost:8765`; o servidor atual não oferece TLS/WSS. A variável `VITE_MSN_SERVER_URL` e o parâmetro `?server=` já aceitam futuros endpoints `wss://`, mas o TLS precisa ser implementado/configurado no servidor antes de usar essa modalidade.

Durante o desenvolvimento a execução direta ainda depende de Python e do servidor-fonte. A distribuição sem terminal depende de rodar `build_windows.ps1` em Windows para gerar os binários. O launcher não implementa reinício automático infinito; após uma queda inesperada ele informa a falha e permite que o usuário tente novamente.

## Referências internas

[1]: launcher/launcher.py "Código do executor"
[2]: launcher/tests/test_launcher.py "Testes automatizados"
[3]: README_LAUNCHER.md "Manual de operação"
[4]: build_windows.ps1 "Build Windows"
[5]: ../hub_audit/INTEGRACAO_HUB.md "Integração real da HUB"
