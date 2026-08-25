# MSN Messenger Hub — relatório de integração

## Escopo

A HUB visual existente foi transformada em cliente do servidor Python já implementado. A identidade visual retro, a janela principal, a tela de login, a tela de registro e o layout de três colunas foram preservados. O Express em `server/index.ts` continua servindo arquivos estáticos e não contém autenticação, mensagens ou um segundo banco.

## Arquivos criados

| Arquivo | Responsabilidade |
|---|---|
| `client/src/network/config.ts` | Endpoint único; usa `VITE_MSN_SERVER_URL` ou `ws://localhost:8765` |
| `client/src/network/protocol.ts` | Tipos dos comandos, envelopes, presença, conversas, mensagens e sync |
| `client/src/network/websocket.ts` | WebSocket, estados `connecting/connected/disconnected/reconnecting`, reconexão e fechamento |
| `client/src/state/messenger.tsx` | Estado temporário da UI, autenticação, sessão, sync, presença, histórico e mensagens |

## Arquivos alterados

| Arquivo | Alteração |
|---|---|
| `client/src/App.tsx` | Inclusão de `MessengerProvider` sem mudar o roteamento visual |
| `client/src/pages/Home.tsx` | Remoção de mocks; conexão da UI a ações reais; estados vazios, erros, grupo, status, logout e endpoint real |
| `client/src/index.css` | Estilos mínimos para erro, loading lógico, estado vazio e composer de grupo; paleta existente preservada |
| `client/public/assets/*` | Inclusão local dos assets da HUB publicada para evitar imagens quebradas no pacote |

## Comunicação

A HUB abre um WebSocket usando `VITE_MSN_SERVER_URL`. Se a variável não existir, o endereço padrão é `ws://localhost:8765`. O Express da HUB não duplica regras de autenticação ou mensagens.

No login, o cliente envia `LOGIN`, aguarda `AUTH_OK`, guarda a sessão apenas em memória e envia `REQUEST_SYNC`. No registro, envia `REGISTER`, aguarda `REGISTER_OK` e executa o login separado exigido pelo protocolo do servidor. No logout, envia `LOGOUT`, aguarda `LOGOUT_OK`, fecha a conexão e retorna à tela de login.

Depois de uma queda de conexão, a camada de rede tenta reconectar com backoff limitado quando existe uma sessão em memória. Ao abrir o novo socket, envia `RECONNECT` com o `session_id`, aguarda `RECONNECT_OK` e envia `REQUEST_SYNC`. Em `SESSION_TAKEN`, a sessão é limpa e a UI volta ao login com mensagem compreensível.

## Protocolo usado

A implementação usa somente `REGISTER`, `LOGIN`, `RECONNECT`, `REQUEST_SYNC`, `CHANGE_STATUS`, `SEND_MESSAGE`, `GET_HISTORY`, `CREATE_GROUP` e `LOGOUT`. Os eventos tratados são `SYNC_DATA`, `MESSAGE_ACK`, `MESSAGE`, `HISTORY`, `CONVERSATION_CREATED`, `USER_STATUS_CHANGED`, `SESSION_TAKEN` e `ERROR`.

As mensagens não são adicionadas ao chat pelo clique. A HUB aguarda `MESSAGE_ACK` e então busca `GET_HISTORY`, garantindo que a linha exibida represente uma mensagem persistida. Eventos espontâneos `MESSAGE` também atualizam a conversa sem refresh.

## Dados e ausência de mocks

`initialConversations`, `simulateConnection`, o usuário `Mu`, João, Ana, Bia, Grupo, `messenger.local`, `mu@grupo.local`, mensagens de exemplo e estados simulados foram removidos do fluxo de produção. A lista de conversas inicia vazia e é preenchida exclusivamente com `SYNC_DATA` e `CONVERSATION_CREATED`. O único uso de `localStorage` existente no projeto continua sendo a preferência visual do tema; contas, senhas, sessões, conversas e mensagens não são persistidas no navegador.

## Validação realizada

A validação foi executada com o servidor Python real em `127.0.0.1:8765`:

| Cenário | Resultado |
|---|---|
| Build inicial da HUB | Aprovado antes da integração |
| TypeScript após integração | Aprovado com `pnpm check` |
| Build final | Aprovado com `pnpm build` |
| Assets locais e identidade visual | Aprovado visualmente no navegador |
| Abrir tela de registro | Aprovado |
| Criar conta pela HUB | Aprovado |
| Login após reload | Aprovado; conta encontrada no SQLite do servidor |
| Estado vazio sem conversas falsas | Aprovado |
| Criar grupo pela HUB | Aprovado com duas contas reais de teste |
| Enviar mensagem pela HUB | Aprovado; ACK e histórico do servidor atualizaram a UI |
| Entrega em tempo real a outro cliente | Aprovado; o segundo cliente recebeu `MESSAGE` |
| Alterar presença para Ausente | Aprovado com `CHANGE_STATUS` |
| Logout | Aprovado com `LOGOUT_OK`, fechamento e retorno ao login |
| Suíte do servidor Python | 45 testes aprovados |

As contas `hub_alice_20260822` e `hub_bob_20260822` foram criadas somente durante a validação manual no banco temporário e não são seed do código.

## Limitações atuais

O servidor atual não oferece TLS/WSS, portanto o endpoint padrão usa `ws://`; a configuração já aceita futuramente uma URL `wss://`. O frontend ainda não implementa arquivos, imagens, áudio, vídeo, chamadas ou WebRTC, conforme proibido nesta etapa. O servidor também não expõe uma lista global de usuários independente de presença; por isso a UI reconstrói nomes a partir da presença sincronizada e dos participantes fornecidos pelo servidor, sem inventar dados locais.

## Referências internas

[1]: client/src/network/config.ts "Configuração do endpoint"
[2]: client/src/network/protocol.ts "Contratos do protocolo"
[3]: client/src/network/websocket.ts "Transporte WebSocket"
[4]: client/src/state/messenger.tsx "Estado real do Messenger"
[5]: client/src/pages/Home.tsx "Interface preservada e conectada"
[6]: server/index.ts "Servidor Express apenas estático"
