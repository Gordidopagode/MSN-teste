# Implementação do Hub MSN

## Resultado

O Hub agora utiliza o cadastro de usuários existente e o mesmo canal WebSocket do MSN para suportar amizades, perfil, avatar e status personalizado persistentes. As funcionalidades são sincronizadas sem reiniciar o aplicativo e preservam o fluxo original de autenticação, conversas, mensagens, grupos, histórico e reconexão.

## Funcionalidades implementadas

| Área | Entrega |
|---|---|
| Amizades | Pesquisa por username, envio de solicitação, bloqueio de autoadição e duplicatas, aceitação, recusa, remoção e lista persistente. |
| Conversas | Abertura direta de conversa individual a partir de amizade aceita. |
| Perfil | Avatar escolhido localmente, convertido para uma imagem JPEG redimensionada antes do envio, exibido no perfil, lista de amigos e conversas. |
| Validação de avatar | Conferência de extensão, MIME, assinatura real do arquivo, base64, tamanho máximo de 256 KB no servidor, dimensões máximas de 4096 px e imagem corrompida. |
| Status personalizado | Definição, edição e limpeza de frase de até 200 caracteres, independente do status online/ausente/ocupado. |
| Tempo real | Eventos para alterações de perfil, presença, amizades e conversas; clientes envolvidos recebem atualizações sem reiniciar. |
| Build | Vite configurado com `client` como raiz, aliases `@/` e `@shared/`, assets empacotáveis e servidor Express servindo `client/public`. |
| UX | Os botões Mais opções, Anexar arquivo e Adicionar emoji permanecem preservados sem ação falsa; o compositor de grupo agora informa campos obrigatórios vazios. |

## Arquitetura

A persistência foi estendida no SQLite já utilizado pelo projeto. A tabela `users` recebeu `avatar_data`, `avatar_mime` e `custom_status`, enquanto a tabela `friendships` guarda relações ordenadas, solicitante, estado e timestamps. O `FriendshipManager` concentra as regras de negócio; o `ProfileManager` concentra validação e persistência de avatar/status; o `ServerCore` apenas coordena sessão, regras, eventos e envelopes WebSocket.

O envelope `SYNC_DATA` passou a conter a identidade completa, presença enriquecida e lista de amizades. Os eventos `FRIENDSHIPS_UPDATED`, `FRIENDSHIP_UPDATED`, `FRIENDSHIP_REMOVED`, `SEARCH_USERS_RESULT` e `PROFILE_UPDATED` foram adicionados sem substituir os eventos existentes.

## Arquivos principais

| Arquivo | Alteração |
|---|---|
| `server/persistence/store.py` | Migração do schema, perfil, pesquisa de usuários e CRUD de amizades. |
| `server/friends/manager.py` | Regras de solicitação, resposta, remoção e pesquisa. |
| `server/users/profile.py` | Validação e persistência de avatar/status. |
| `server/core.py` | Coordenação dos novos comandos e broadcasts. |
| `server/network/protocol.py` e `server/network/handler.py` | Contratos, validação e despacho WebSocket. |
| `server/sync/manager.py` | Sincronização de perfil, presença e amizades. |
| `server/users/presence.py` | Preservação de status personalizado em mudanças de presença. |
| `hub_source/client/src/network/protocol.ts` | Tipos de comandos, respostas e eventos. |
| `hub_source/client/src/state/messenger.tsx` | Estado, reconexão, eventos, compressão e ações do cliente. |
| `hub_source/client/src/pages/Home.tsx` e `index.css` | Painéis Perfil/Amigos, avatar, status e correção de controles. |
| `hub_source/vite.config.ts`, `tsconfig.json`, `client/vite-env.d.ts` | Configuração funcional do desenvolvimento e distribuição. |
| `server/tests/test_social_features.py` | Testes novos de amizades, perfil, validação, persistência e sincronização. |
| `server/tests/test_sync_persistence.py` | Atualização do teste de logout para validar presença offline nos clientes remanescentes. |

## Verificações executadas

| Verificação | Resultado |
|---|---:|
| Suíte Python existente + novos testes | **59 passaram** |
| TypeScript (`pnpm check`) | **Passou** |
| Build frontend/backend (`pnpm build`) | **Passou** |
| Bundle Express em produção e rota `/` | **HTTP 200** |
| Asset empacotado em produção | **HTTP 200** |
| Smoke test visual de registro/login | **Passou** |
| Smoke test visual de status personalizado | **Passou** |
| Smoke test visual de avatar | **Passou** |
| Smoke test com dois clientes: busca, solicitação, aceite e conversa | **Passou** |

## Dependências

O backend adiciona `Pillow` para decodificar e normalizar formatos reais de imagem; o envio SMTP usa apenas a biblioteca padrão do Python. O frontend utiliza dependências já declaradas no manifesto do Hub, incluindo Vite, React, TypeScript e `@vitejs/plugin-react`.

## Recuperação de senha e segurança

O cadastro agora exige um e-mail único, usado somente para recuperação. O fluxo público utiliza `REQUEST_PASSWORD_RESET` e `RESET_PASSWORD` no WebSocket: o primeiro cria um código aleatório de uso único e envia por SMTP; o segundo valida o código, sua expiração e o limite de tentativas antes de trocar a senha. O SQLite guarda apenas o hash SHA-256 do código, com validade padrão de 15 minutos; a troca invalida as sessões existentes e os tokens anteriores. Para evitar enumeração de contas, a resposta de solicitação é genérica para e-mails existentes e inexistentes, e o código completo nunca aparece nos logs.

A integração SMTP é provider-agnostic e recebe exclusivamente `MSN_SMTP_HOST`, `MSN_SMTP_PORT`, `MSN_SMTP_USERNAME`, `MSN_SMTP_PASSWORD` e `MSN_SMTP_FROM` por ambiente/secrets. O exemplo está em `.env.example`; o `.gitignore` bloqueia `.env`, banco, uploads e logs. O remetente pode ser uma conta Gmail dedicada com senha de app, sem que o frontend tenha acesso à credencial.

## Avatar

A interface aceita qualquer arquivo reconhecido pelo navegador como imagem (`image/*`), incluindo formatos além de PNG/JPEG/WebP quando o navegador conseguir decodificá-los. O cliente redimensiona e converte para JPEG antes do WebSocket; o backend também decodifica os bytes reais com Pillow, verifica dimensões e tamanho e armazena apenas o JPEG canônico de até 256 KB no SQLite. Assim, extensão e MIME declarados não são suficientes para inserir dados arbitrários.

## Limitações restantes

O servidor continua sendo privado e local neste pacote; a publicação externa ainda não foi feita. Avatares continuam armazenados como data URL no SQLite para manter a solução sem um serviço adicional, e a persistência em produção exige volume/disco durável e backup separado. Os controles de anexar arquivo, emoji e mais opções continuam apenas como controles preservados, porque o arquivo de requisitos não especificou os fluxos dessas funcionalidades e a implementação de upload de mensagens exigiria um contrato de mensagem separado.
