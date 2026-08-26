# Relatório final — rodada integrada de melhorias do MSN

## 1. Escopo e preservações

Esta rodada implementa a nova especificação integrada no repositório `Gordidopagode/MSN-teste`, branch `main`, sem alterar o launcher. O fluxo existente de descoberta do servidor, inicialização, escolha de host/join, conexão inicial do Hub, login, cadastro, recuperação local por código, WebSocket, mensagens de texto, conversas privadas e em grupo, amizades, sincronização, presença, avatar e status personalizado foi preservado.

A regra de distribuição do frontend também foi preservada: o Hub continua sendo compilado em `hub_source/client/public` e sincronizado para `client/public`, que é a pasta servida pelo launcher. A execução de `pnpm build` atualizou o bundle servido, mas `launcher/launcher.py` permaneceu sem qualquer modificação.

## 2. Arquitetura final e fontes de verdade

O backend continua sendo um servidor Python assíncrono baseado em `websockets`. `server/network/handler.py` apenas traduz frames e despacha comandos; `server/core.py` coordena autenticação, autorização, presença, mensagens, anexos e broadcasts; os gerenciadores de domínio e `server/persistence/store.py` concentram regras e persistência.

A presença tem uma fonte canônica no `PresenceManager` e nos snapshots `USER_STATUS_CHANGED`/`SYNC_DATA`. No frontend, o mapa `presence` é transformado por `useMemo` em `visibleFriends` e `visibleConversations`, e essas coleções são as que o contexto exporta para a UI. Assim, a lista de amigos, cabeçalho de conversa, participantes de grupo e avatar/status visualizam o mesmo estado, sem atualizações independentes de status em cada componente.

| Domínio | Fonte final de verdade | Aplicação |
|---|---|---|
| Usuário e conta | `users` + `AuthManager` | Display name, senha, avatar normalizado e credenciais permanecem sob autoridade do backend. |
| Presença | `PresenceManager` + `presence` no provider | Friends, cabeçalhos, busca e grupos derivam do mesmo mapa. |
| Amizades | `friendships` + `FriendshipManager` | A lista de amigos aceitos alimenta prioritariamente o criador de grupos. |
| Conversas | `conversations`/`conversation_participants` | Participação é sempre revalidada no backend. |
| Mensagens | `messages` + `MessageManager` | `sender_id` é definido pela sessão autenticada, nunca pelo cliente. |
| Anexos | Filesystem persistente + tabela `attachments` | O SQLite guarda metadados; os bytes não entram em JSON Base64. |
| Pins | `pinned_messages` com unicidade por mensagem | Qualquer participante pode fixar/desafixar, sem duplicação. |
| Notificações | fila local deduplicada no provider | Mensagens recebidas e transições de conexão geram avisos internos clicáveis. |
| Preferências Hub | `localStorage` do navegador | Tema local não é tratado como dado de conta nem enviado ao servidor. |

## 3. Problemas encontrados e decisões adotadas

A auditoria inicial encontrou uma divergência potencial porque o frontend mantinha status derivados em `presence`, `friends`, `conversations` e sessão. A implementação passou a derivar as coleções apresentadas diretamente da presença canônica. Também foi identificado que a busca de usuários no backend já aceitava `username` e `display_name`, mas a UI comunicava apenas “username”; o texto e os resultados agora deixam explícitos nome exibido, `@username` e presença.

A criação de grupos anteriormente dependia de texto livre separado por vírgulas. O Hub agora mostra amigos aceitos com nome amigável, `@username` e checkboxes, além de uma busca alternativa para usuários não amigos. O servidor resolve os usernames para IDs, remove duplicatas, rejeita auto-inclusão, rejeita lista vazia e continua decidindo autorização e participantes.

Foi encontrada uma lacuna de segurança no fluxo de anexos: um cliente poderia tentar enviar `SEND_MESSAGE` com `type=attachment` e metadados inventados. O parser e o handler agora rejeitam esse caminho, e `MessageManager` somente aceita attachment quando `trusted_attachment=True`, flag usada exclusivamente por `ServerCore.finish_attachment_upload()` depois das verificações de conexão, proprietário, conversa e participação.

Outra limitação prática foi descoberta no teste do endpoint: o servidor `websockets` responde `426 Upgrade Required` a um GET HTTP comum no mesmo listener WebSocket. Em vez de tratar isso como download, foi criado um listener HTTP assíncrono separado para anexos, com a mesma autorização e o mesmo core. Isso mantém o launcher intacto e torna o caminho explícito: WebSocket em `MSN_PORT` e downloads em `MSN_ATTACHMENT_HTTP_PORT`.

## 4. Protocolo implementado

Foram adicionados ou formalizados os seguintes comandos de cliente: `BEGIN_ATTACHMENT_UPLOAD`, `FINISH_ATTACHMENT_UPLOAD`, `ABORT_ATTACHMENT_UPLOAD`, `SEARCH_MESSAGES`, `LIST_PINNED_MESSAGES`, `PIN_MESSAGE`, `UNPIN_MESSAGE`, `UPDATE_PROFILE` e `CHANGE_PASSWORD`. O comando genérico `SEND_MESSAGE` continua exclusivo para mensagens de texto.

Os envelopes novos incluem `ATTACHMENT_UPLOAD_READY`, `ATTACHMENT_UPLOAD_PROGRESS`, `ATTACHMENT_UPLOAD_COMPLETE`, `MESSAGE_SEARCH_RESULT`, `PINNED_MESSAGES`, `MESSAGE_PINNED` e `PASSWORD_CHANGED`. Os eventos existentes de `PROFILE_UPDATED`, `USER_STATUS_CHANGED`, `FRIENDSHIPS_UPDATED`, `MESSAGE`, `HISTORY` e `SYNC_DATA` foram mantidos e ampliados apenas onde necessário para transportar presença, avatar, display name, pins e URLs assinadas.

## 5. Anexos seguros e persistentes

O navegador inicia o upload com metadados (`filename`, MIME e tamanho). Depois da resposta `ATTACHMENT_UPLOAD_READY`, o `MessengerProvider` envia os bytes como frames binários em blocos de até `chunk_size`; nenhum arquivo é convertido para um grande campo Base64 em JSON. O servidor grava temporariamente em `<data_dir>/attachments/.uploads`, calcula SHA-256 ao concluir, move para um caminho interno aleatório e persiste os metadados em SQLite.

A tabela `attachments` guarda `attachment_id`, conversa, proprietário, mensagem vinculada, nome original sanitizado, MIME validado, tamanho, referência de armazenamento, hash e data de criação. O padrão é 25 MiB por arquivo, 128 KiB por chunk e até três uploads em andamento por conexão. O MIME deve pertencer à lista permitida, o nome passa por `Path(...).name`, NUL e caracteres não imprimíveis são removidos, e referências de armazenamento são resolvidas e verificadas contra a raiz privada antes da abertura.

A mensagem armazena somente identificadores e metadados do anexo. Cada destinatário recebe URL HMAC própria, com usuário e expiração de uma hora na assinatura. O listener HTTP valida a assinatura, procura o attachment persistido e confirma que o usuário assinado pertence à conversa antes de ler o arquivo. O participante consegue baixar; um terceiro recebe `404`, mesmo que tente gerar uma assinatura válida para si. A limpeza de uploads pendentes ocorre em abort, finalização, erro, desconexão e substituição da sessão.

A configuração principal é:

```dotenv
MSN_PORT=8765
MSN_ATTACHMENT_HTTP_PORT=8766
MSN_DATA_DIR=./data
# Em HTTPS/proxy externo:
# MSN_PUBLIC_BASE_URL=https://seu-dominio.example
```

Em hospedagem externa, a porta HTTP de anexos precisa ser exposta ou encaminhada pelo proxy. Para produção com HTTPS, `MSN_PUBLIC_BASE_URL` deve apontar para uma rota HTTPS que encaminhe `/attachments/` ao listener HTTP de anexos; não se deve usar um link HTTP em uma página HTTPS por causa de mixed content.

## 6. Busca de mensagens

A busca é server-backed. `SEARCH_MESSAGES` exige autenticação e participação na conversa, aceita `query`, `limit` limitado a 100 e cursor `before`, e hidrata anexos com a URL assinada do usuário que pesquisou. A UI possui busca dentro da conversa, mostra resultados e, ao clicar, faz scroll até a mensagem e aplica destaque temporário.

A implementação atual usa `LOWER(payload) LIKE` no SQLite, o que atende texto e nomes de arquivos persistidos, mas ainda não é uma busca FTS/tokenizada. Não há stemming, ranking semântico ou busca nos bytes do arquivo; o conteúdo binário continua deliberadamente fora da camada textual.

## 7. Mensagens fixadas

A tabela `pinned_messages` possui relação única por `conversation_id` e `message_id`, além de quem fixou e quando. O backend confirma participação, confirma que a mensagem existe na conversa e usa `INSERT OR IGNORE`/remoção idempotente. Qualquer participante de conversa privada ou grupo pode fixar e desafixar.

O Hub adicionou controle de pin por mensagem, painel de mensagens fixadas e atualização ao vivo para os participantes. Eventos de pin são hidratados por destinatário, o que preserva URLs assinadas corretas para anexos fixados. O histórico e o sync marcam `is_pinned`, e a UI não cria uma segunda cópia da mensagem.

## 8. Configurações de conta e Hub

O painel de configurações foi dividido visualmente em **Minha conta** e **Hub**. Minha conta contém display name, status personalizado, avatar e troca de senha. Hub contém preferências locais de tema claro, azul suave e escuro, persistidas em `localStorage` e reaplicadas ao abrir a interface.

O display name é validado e persistido pelo backend, continua sendo diferente do username e é distribuído por `PROFILE_UPDATED` e presença. A busca usa display name como critério, mas solicitações de amizade e criação de conversa continuam usando o username único como identificador operacional.

A troca de senha exige a senha atual e uma nova senha com tamanho mínimo; o hash continua sendo gerado pelo mecanismo existente. Após sucesso, sessões persistidas e sessões vivas em memória, exceto a sessão corrente, são invalidadas/encerradas. O avatar continua separado dos anexos de conversa: o navegador aceita `image/*`, o backend valida os bytes com Pillow e normaliza para JPEG seguro com limites de tamanho e dimensão.

## 9. Notificações

Foi adicionada uma fila interna de até cinco notificações, com IDs deduplicados e limite de retenção dos IDs já vistos. Mensagens recebidas de outros usuários geram um aviso com remetente e prévia; perda e recuperação real da conexão geram avisos de estado. O aviso de mensagem é clicável e seleciona a conversa correspondente. Não foi adicionada dependência de notificações push do sistema operacional nem pedido automático de permissão do navegador.

## 10. Arquivos modificados

### Arquivos de aplicação modificados

| Arquivo | Finalidade |
|---|---|
| `.env.example` | Documenta `MSN_ATTACHMENT_HTTP_PORT` e `MSN_PUBLIC_BASE_URL`; nenhum segredo foi incluído. |
| `hub_source/client/src/index.css` | Estilos incrementais de settings, grupos, busca, anexos, pins, temas e notificações. |
| `hub_source/client/src/network/protocol.ts` | Tipos de anexos, busca, pins, presença e envelopes de conta. |
| `hub_source/client/src/network/websocket.ts` | Envio de frames binários. |
| `hub_source/client/src/pages/Home.tsx` | UI de configurações separadas, busca, grupos, ChatView, anexos, pins e notificações. |
| `hub_source/client/src/state/messenger.tsx` | APIs do provider, presença derivada, upload binário, busca, pins, conta e fila de notificações. |
| `server/auth/manager.py` | Atualização de display name e troca segura de senha. |
| `server/config/settings.py` | Limites de anexos, porta HTTP e URL pública. |
| `server/core.py` | Orquestração, autorização, distribuição e limpeza dos novos fluxos. |
| `server/main.py` | Inicialização e encerramento do listener HTTP de anexos. |
| `server/messages/manager.py` | Bloqueio de attachment forjado no envio genérico. |
| `server/network/handler.py` | Frames binários, comandos novos e fechamento de handles de download. |
| `server/network/protocol.py` | Parsing/validação dos novos comandos e rejeição de attachment genérico. |
| `server/persistence/store.py` | Schema e APIs de attachments, busca e pins. |
| `server/shared_types.py` | Tipo `MessageType.ATTACHMENT`. |
| `server/sync/manager.py` | Hidratação de URLs assinadas no sync/histórico. |
| `server/tests/test_sync_persistence.py` | Caso E2E real com grupo, upload, download, busca e pin. |

O build também atualizou `hub_source/client/public/index.html` e `client/public/index.html`, substituiu os assets hashados antigos em `client/public/assets/` pelos novos assets e sincronizou o bundle final para a pasta realmente servida pelo launcher.

### Arquivos criados

| Arquivo | Finalidade |
|---|---|
| `server/attachments/__init__.py` | Pacote do domínio de anexos. |
| `server/attachments/manager.py` | Armazenamento, metadados, validações, chunks, HMAC e limpeza. |
| `server/attachments/http.py` | Listener HTTP separado para downloads assinados. |
| `server/tests/test_modern_features.py` | Testes de segurança, persistência, busca, pins e presença na busca. |
| `RELATORIO_AUDITORIA_MELHORIAS.md` | Este relatório final. |

## 11. Testes e resultados

A linha de base antes da rodada tinha 74 testes Python passando. Depois das mudanças, os resultados foram:

| Verificação | Resultado |
|---|---:|
| `python3 -m compileall -q server` | passou |
| `PYTHONPATH=. pytest -q` | **81 passed, 0 failed** |
| Novos testes unitários/integrados em `test_modern_features.py` | **6 passed** |
| Novo E2E WebSocket moderno | **1 passed** |
| E2E WebSocket existente de múltiplos clientes | passou dentro da suíte completa |
| `cd hub_source && pnpm check` | passou |
| `cd hub_source && pnpm build` | passou |
| `git diff --check` | passou |
| `git diff -- launcher/launcher.py` | vazio; launcher intacto |

O E2E moderno cobriu criação de grupo, upload em chunks binários, persistência de bytes, URL por destinatário, download HTTP autorizado, download negado a terceiro, busca por nome do anexo, fixação por participante e listagem sem duplicação. Os testes de backend também cobriram MIME inválido, tamanho excedido, path traversal, attachment forjado, busca autorizada/não autorizada, pin idempotente, presença em busca por display name e persistência após restart.

O build produziu o bundle final com JavaScript `index-DpRqUDqH.js` e CSS `index-DACH9Aii.css`, e o script de sincronização atualizou `client/public`. O Vite exibiu apenas os avisos já existentes sobre placeholders de analytics (`VITE_ANALYTICS_ENDPOINT`/`VITE_ANALYTICS_WEBSITE_ID`); eles não interromperam o build e não pertencem ao launcher ou às funcionalidades do MSN.

## 12. Segurança e limites restantes

Nenhuma senha SMTP, senha de usuário, token completo de recuperação ou segredo HMAC foi colocado no código, frontend, testes ou Git. A recuperação local por código único continua sendo o mecanismo existente e o SMTP permanece opcional, sem virar dependência desta rodada. Links de anexos são temporários e HMAC-scoped; o arquivo interno não usa o nome fornecido pelo usuário como caminho.

Os limites restantes são operacionais, não falhas de autorização: a implantação remota precisa publicar/proxyar uma segunda porta HTTP de anexos; o launcher local continua sondando apenas o WebSocket por decisão explícita de compatibilidade; a busca SQLite ainda é `LIKE`, não FTS; a UI envia um anexo por vez; e as notificações são internas ao Hub, não push móvel. O servidor ainda carrega o corpo completo do anexo na resposta HTTP, adequado ao limite padrão de 25 MiB e ao MSN privado pequeno, mas uma escala maior deve migrar para streaming ou storage dedicado.

Não houve hospedagem, alteração irreversível de infraestrutura ou modificação do launcher nesta rodada.
