# Relatório final de implementação — rodada integrada de melhorias

## 1. Escopo e preservações

Esta rodada aplica a especificação anexada ao repositório `Gordidopagode/MSN-teste`, mantendo a arquitetura existente e sem modificar o launcher. O fluxo de descoberta do servidor, inicialização, escolha de host/join, conexão inicial do Hub, login, cadastro, recuperação local por código, WebSocket, mensagens de texto, conversas privadas e em grupo, amizades, sincronização, presença, avatar e status personalizado permanece funcional.

O frontend continua sendo compilado em `hub_source/client/public` e sincronizado para `client/public`, a pasta realmente servida pelo launcher. O arquivo `launcher/launcher.py` permaneceu sem alteração. O build final atualizou somente os bundles estáticos e os códigos necessários para os recursos desta rodada.

## 2. Arquitetura e fontes de verdade

O backend continua baseado em Python assíncrono e WebSocket. `server/network/handler.py` traduz frames, valida comandos e despacha operações; `server/core.py` coordena autenticação, autorização, presença, mensagens, anexos, busca, pins e broadcasts; os gerenciadores de domínio e `server/persistence/store.py` concentram as regras persistentes.

A presença usa o `PresenceManager` como origem canônica. No frontend, o mapa `presence` é transformado em `visibleFriends` e `visibleConversations` por `useMemo`; a UI não mantém uma segunda cópia autoritativa de status. Assim, lista de amigos, cabeçalho de conversa, busca de usuários, grupos e participantes usam o mesmo estado de presença.

| Domínio | Fonte de verdade | Comportamento final |
|---|---|---|
| Conta | `users` + `AuthManager` | Display name, senha e avatar são validados e persistidos no backend. |
| Presença | `PresenceManager` + mapa `presence` | Amigos, conversas, busca e grupos refletem o mesmo status. |
| Conversas | `conversations` e participantes | Participação é revalidada no servidor em cada operação sensível. |
| Mensagens | `messages` + `MessageManager` | O remetente é derivado da sessão autenticada. |
| Anexos | Filesystem privado + tabela `attachments` | Bytes ficam fora do JSON; SQLite guarda metadados e referência interna. |
| Pins | `pinned_messages` | Relação única por conversa e mensagem, com operações idempotentes. |
| Preferências do Hub | `localStorage` | Tema é local ao navegador e separado da conta. |
| Notificações | Fila local deduplicada | Avisos de mensagens e conexão não são repetidos indefinidamente. |

## 3. Busca, grupos e presença

A busca de usuários continua aceitando `username` e `display_name`. A interface apresenta nome exibido, `@username` e presença atual, mantendo o username como identificador operacional para amizade e abertura de conversa.

O criador de grupos prioriza amigos aceitos por meio de seleção visual com nome amigável e `@username`. Uma busca alternativa permite adicionar usuários que não são amigos. O frontend remove duplicatas e impede a própria conta; o backend resolve usernames para IDs, rejeita participantes vazios, duplicados e auto-inclusão e continua sendo a autoridade final.

A presença aparece de maneira consistente em amigos, conversas, busca e participantes. O frontend não deve ser tratado como autoridade para conceder acesso: abertura de conversa, criação de grupo, envio de mensagem, busca e anexos continuam sujeitos às verificações do backend.

## 4. Protocolo e segurança

Foram mantidos os comandos existentes e formalizados os fluxos de `BEGIN_ATTACHMENT_UPLOAD`, `FINISH_ATTACHMENT_UPLOAD`, `ABORT_ATTACHMENT_UPLOAD`, `SEARCH_MESSAGES`, `LIST_PINNED_MESSAGES`, `PIN_MESSAGE`, `UNPIN_MESSAGE`, `UPDATE_PROFILE` e `CHANGE_PASSWORD`. O comando genérico `SEND_MESSAGE` rejeita `type=attachment`; anexos somente são criados pelo caminho confiável de finalização de upload.

Os envelopes de upload incluem `ATTACHMENT_UPLOAD_READY`, `ATTACHMENT_UPLOAD_PROGRESS` e `ATTACHMENT_UPLOAD_COMPLETE`. Também continuam disponíveis os envelopes de mensagem, histórico, pins, perfil, presença, sincronização e senha.

As verificações principais são executadas no servidor:

| Operação | Validações relevantes |
|---|---|
| Upload | Sessão autenticada, participação na conversa, tamanho, nome sanitizado, chunk válido e contagem de uploads. |
| Finalização | Proprietário da sessão, vínculo do upload à conversa, participação atual, tamanho físico e hash SHA-256. |
| Download | Assinatura HMAC, expiração, existência do anexo e participação do usuário na conversa. |
| Mensagem de anexo | Somente `trusted_attachment=True` no caminho interno de finalização. |
| Busca de mensagens | Sessão autenticada e participação na conversa. |
| Pin/unpin | Participação na conversa, existência da mensagem e operação idempotente. |
| Conta | Sessão própria, senha atual e validações de tamanho/formato. |

Nenhuma senha SMTP, senha de usuário, token completo de recuperação ou segredo HMAC foi adicionado ao código, ao frontend, aos testes ou ao Git.

## 5. Anexos genéricos, persistência e previews

O upload usa frames binários WebSocket em chunks, sem converter o arquivo inteiro para Base64 ou colocá-lo em um JSON grande. O servidor grava temporariamente em `<data_dir>/attachments/.uploads`, calcula SHA-256 ao concluir e move os bytes para um caminho interno aleatório. O nome informado pelo usuário é apenas um rótulo persistido e exibido; nunca é usado como caminho de filesystem.

A tabela `attachments` mantém identificador, conversa, proprietário, mensagem vinculada, nome sanitizado, MIME, tamanho, referência interna, hash e data de criação. O SQLite não recebe URLs assinadas. Ao sincronizar ou entregar uma mensagem, o servidor recria a URL por destinatário e por sessão de leitura.

A aceitação de MIME foi ampliada para arquivos genéricos. O valor é normalizado para um MIME sintaticamente válido ou `application/octet-stream`; não existe mais uma whitelist que bloqueie extensões legítimas. O limite padrão continua sendo 25 MiB por arquivo, com chunks de 128 KiB e até três uploads simultâneos por conexão. Nomes passam por normalização de separadores, remoção de NUL e caracteres não imprimíveis, limite de comprimento e armazenamento fora da raiz pública.

O servidor identifica previews somente quando o conteúdo é compatível com a mídia declarada. Imagens são verificadas com Pillow, incluindo proteção contra imagem inválida ou decompression bomb. Vídeos são reconhecidos por assinaturas de contêiner comuns, como MP4/QuickTime, WebM/Matroska, AVI e Ogg. Quando a validação passa, o payload recebe `preview_kind` e uma `preview_url` assinada com modo inline. Caso contrário, o arquivo continua disponível como download genérico, sem tentativa de reprodução.

| Tipo | Exibição no ChatView | Download |
|---|---|---|
| JPG/PNG e outras imagens reconhecidas | Preview inline com dimensões limitadas e lightbox ao clicar. | Link separado para baixar o original. |
| MP4/WebM e outros vídeos reconhecidos | Player HTML5 com controles e limite visual de altura. | Link separado para baixar o original. |
| PDF | Card compacto com categoria e tamanho. | Download forçado. |
| ZIP/RAR/7z | Card de arquivo compactado. | Download forçado. |
| TXT, código e texto | Card com extensão/categoria. | Download forçado. |
| DOCX, planilhas e apresentações | Card com categoria legível. | Download forçado. |
| Extensão ou MIME desconhecido | Card genérico, sem preview executável. | Download forçado. |

O download usa um listener HTTP separado, pois um GET HTTP comum no listener WebSocket recebe `426 Upgrade Required`. A configuração padrão é:

```dotenv
MSN_PORT=8765
MSN_ATTACHMENT_HTTP_PORT=8766
MSN_DATA_DIR=./data
MSN_ATTACHMENT_MAX_BYTES=26214400
MSN_ATTACHMENT_CHUNK_BYTES=131072
MSN_ATTACHMENT_MAX_PER_MESSAGE=3
# Em hospedagem HTTPS/proxy:
# MSN_PUBLIC_BASE_URL=https://seu-dominio.example
```

Em hospedagem externa, a rota `/attachments/` deve ser encaminhada para o listener HTTP de anexos e `MSN_PUBLIC_BASE_URL` deve apontar para uma origem HTTPS acessível pelo navegador. O servidor ainda lê o corpo completo na resposta HTTP, adequado ao limite de 25 MiB e ao MSN privado pequeno; uma implantação maior deve migrar para streaming ou storage dedicado.

## 6. ChatView e experiência de anexos

O ChatView agora renderiza anexos por categoria. Imagens possuem preview com `loading="lazy"`, botão de ampliação, lightbox com fechamento por clique fora e tecla Escape, além de download explícito. Vídeos possuem player com `controls` e `preload="metadata"`. Outros arquivos usam cards compactos com ícone, categoria, extensão, tamanho, nome truncado com tooltip e botão de download.

A interface continua mostrando progresso de chunks durante upload, sem criar requisições concorrentes ou converter bytes em Base64. A apresentação mantém a identidade visual azul clara do MSN e recebe superfícies adicionais apenas quando necessário para distinguir mídia, arquivo, download e lightbox.

## 7. Busca de mensagens e pins

A busca de mensagens é server-backed, limitada à conversa autorizada, paginável por cursor e com limite de resultados. A UI mostra resultados no contexto do ChatView; clicar em um resultado navega até a mensagem e aplica destaque temporário. A busca textual continua baseada em `LIKE` no SQLite, cobrindo texto e nomes de arquivos persistidos, mas não pesquisa bytes binários nem oferece ranking semântico.

Pins são persistentes e idempotentes. Qualquer participante de conversa privada ou grupo pode fixar ou desafixar uma mensagem. A tabela possui unicidade por `conversation_id` e `message_id`; histórico, sync e eventos ao vivo carregam `is_pinned`. A UI não cria uma segunda mensagem ao atualizar o pin.

## 8. Configurações e temas

As configurações foram separadas em duas abas visíveis:

| Seção | Recursos |
|---|---|
| Minha conta | Display name, status personalizado, avatar, troca de senha e saída. |
| Hub | Tema claro, azul suave e escuro, persistido localmente. |

O painel deixou de ser um overlay flutuante sobre o chat. Ao abrir Perfil, ele ocupa o corpo interno inteiro do Messenger, possui cabeçalho próprio, botão `Voltar ao Hub`, rolagem interna e restauração do estado anterior ao retornar.

O modo escuro foi refinado com gradientes no fundo, superfícies grafite em camadas, bordas azuladas e transparências moderadas. A identidade visual retrô continua presente e os controles mantêm contraste. O tema é reaplicado ao montar o Hub e ao reabrir configurações.

A troca de display name é autoritativa no backend e continua diferente do username. A troca de senha exige a senha atual, atualiza o hash e invalida sessões persistidas e sessões vivas em memória, preservando somente a sessão que confirmou a operação. O avatar continua separado dos anexos de conversa; o navegador aceita `image/*` e o servidor valida/normaliza a imagem com Pillow.

## 9. Responsividade e validação visual

As media queries existentes foram preservadas e ampliadas para configurações, cards de anexos, lightbox e tema escuro. Em viewport estreita, a tela de login reorganiza o painel lateral abaixo do formulário; o Hub colapsa suas colunas; a tela de configurações usa uma única coluna; previews e cards respeitam a largura disponível.

Foi executada uma inspeção real em navegador local com o bundle final. O fluxo de login, cadastro descartável, recuperação local, entrada no Hub, abertura das configurações, alternância para dark mode, retorno ao Hub e reabertura do painel foram verificados. Também foi capturada e inspecionada uma viewport de 390×844; não houve overflow horizontal e os campos permaneceram utilizáveis.

## 10. Arquivos modificados e criados nesta rodada

| Arquivo | Alteração |
|---|---|
| `.env.example` | Documentação de limites de anexos genéricos, porta HTTP e URL pública. |
| `hub_source/client/src/index.css` | Tela completa de configurações, previews, lightbox, cards genéricos, dark mode e responsividade. |
| `hub_source/client/src/network/protocol.ts` | Tipos `preview_url` e `preview_kind` para anexos. |
| `hub_source/client/src/pages/Home.tsx` | Renderização de imagem/vídeo/arquivo, lightbox, Escape, tela interna de configurações e ajustes visuais. |
| `server/attachments/http.py` | Assinatura inline, `Content-Disposition` seguro e nomes UTF-8. |
| `server/attachments/manager.py` | MIME genérico, detecção segura de mídia, integridade física e limpeza reforçada. |
| `server/core.py` | URLs de preview por destinatário e remoção de URLs transitórias do payload persistido. |
| `server/network/handler.py` | Download inline seguro no endpoint legado e fechamento correto de handles. |
| `server/network/protocol.py` | MIME vazio aceito para inferência/fallback e validação de upload. |
| `server/sync/manager.py` | Reidratação de URLs sem reutilizar links transitórios persistidos. |
| `server/tests/test_modern_features.py` | Matriz de nove tipos de arquivo, preview, download, persistência e ausência de URLs no SQLite. |
| `server/tests/fixtures/tiny.mp4` | Fixture MP4 pequeno e válido para testar reconhecimento de vídeo. |
| `client/public/` e `hub_source/client/public/` | Bundle final sincronizado; assets antigos gerados foram removidos. |

Os módulos de anexos, testes modernos, relatório e demais mudanças da rodada anterior continuam incluídos no estado final do repositório.

## 11. Testes e resultados definitivos

A linha de base anterior tinha 74 testes Python. Após esta rodada, a validação foi executada novamente:

| Verificação | Resultado |
|---|---:|
| `python3 -m compileall -q server` | passou |
| `PYTHONPATH=. pytest -q` | **82 passed, 0 failed** |
| `server/tests/test_modern_features.py` | **7 passed, 0 failed** |
| Matriz de arquivos | 9 tipos: JPG, PNG, MP4, PDF, ZIP, TXT, DOCX, código e extensão desconhecida |
| Preview inline | Imagem e vídeo reconhecidos; demais tipos sem preview executável |
| Download HTTP | Download forçado e preview inline autorizados por HMAC |
| URLs transitórias | Não persistidas no payload SQLite |
| E2E WebSocket moderno | passou dentro da suíte completa |
| `cd hub_source && pnpm check` | passou |
| `cd hub_source && pnpm build` | passou |
| Sincronização para `client/public` | passou |
| `git diff --check` | passou |
| Scanner restrito de segredos | nenhum segredo óbvio encontrado |
| `git diff -- launcher/launcher.py` | vazio; launcher intacto |
| Smoke visual desktop | login, Hub, configurações, tema e retorno passaram |
| Smoke visual mobile | viewport 390×844 sem overflow horizontal |

O build final produziu `index-FGXXmVWM.js` e `index-CbLwjlsZ.css` em ambas as pastas públicas. O Vite exibiu somente os avisos já existentes sobre os placeholders de analytics `VITE_ANALYTICS_ENDPOINT` e `VITE_ANALYTICS_WEBSITE_ID`; esses avisos não interromperam o build e não pertencem ao launcher ou ao fluxo do Messenger.

## 12. Limitações restantes

A busca ainda usa `LIKE` no SQLite, não FTS/tokenização. O conteúdo binário não é pesquisado. A UI envia um anexo por vez, as notificações são internas ao Hub e não push de sistema, e o listener HTTP de anexos exige publicação/proxy separado em hospedagem externa. Para volumes maiores que o cenário privado atual, recomenda-se streaming HTTP, storage dedicado, quotas por usuário e observabilidade específica.

A detecção de vídeo valida assinaturas de contêiner conhecidas; arquivos de vídeo com contêiner incomum continuam como download genérico, por segurança. O servidor não executa nem interpreta documentos genéricos. A recuperação de conta permanece no mecanismo existente por código local; SMTP continua opcional e não foi transformado em dependência desta rodada.

## 13. Rodada atual — estabilização do lightbox, presença e avatares

Esta seção é exclusiva da rodada mais recente especificada em `pasted_content.txt`. A arquitetura, os recursos existentes e `launcher/launcher.py` foram preservados. A investigação foi feita antes das alterações, distinguindo falhas de apresentação derivada, eventos de ciclo de vida e identidade persistida.

### 13.1 Causa-raiz do lightbox

O defeito não era apenas visual. O orçamento vertical era dividido simultaneamente pelo padding do overlay, pelo padding do conteúdo, por `max-height: 94vh`, pela imagem limitada a `80vh` e pelo botão X posicionado absolutamente dentro do mesmo bloco de conteúdo. Em imagens altas ou em telas estreitas, a imagem e os espaçamentos podiam consumir o espaço disponível e pressionar ou recortar a área percebida do botão e do conteúdo. A relação incorreta era entre a altura do elemento de mídia e o contêiner que também hospedava o controle de fechamento.

A correção introduz `.lightbox-viewport` como área flexível interna com `min-height: 0`, limita overlay e conteúdo pela viewport, aplica `overflow: hidden`, usa padding de safe area por `env(safe-area-inset-*)` e mantém o X absoluto em um canto seguro do conteúdo, independente das dimensões nativas da imagem. A imagem usa `max-width: 100%`, `max-height: 100%` e `object-fit: contain`, sem upscale de arquivo pequeno e com redução proporcional de arquivos maiores. Escape e clique fora continuam fechando o lightbox; o componente continua restaurando a conversa que estava aberta.

### 13.2 Causa-raiz da divergência de presença

No backend, o `PresenceManager` já era a fonte canônica e os snapshots de sync já eram indexados por `user_id`. O problema de ciclo de vida era que o servidor não publicava de modo consistente `USER_STATUS_CHANGED` para os pares depois de login, reconexão e desconexão. O problema de apresentação era que `visibleConversations` ainda podia usar campos derivados/raw de `Conversation` como fallback, enquanto a lista de amigos consultava o mapa `presence[user_id]`. Assim, o estado autoritativo correto existia, mas o cabeçalho podia ler uma cópia stale em vez do evento e do mapa canônicos.

`server/core.py` agora publica o snapshot canônico de presença a outros sockets após `AUTH_OK` e `RECONNECT_OK`, excluindo a conexão que ainda está aguardando sua resposta de autenticação, e publica o estado offline aos pares após `client_disconnected`. O frontend centraliza a derivação em `state/presence.ts`: tanto `deriveFriendPresence()` quanto `deriveConversationPresentation()` consultam `presence[user_id]` e `knownUsers[user_id]`, sem fallback de status para a apresentação raw da conversa. Dessa forma, a lista e o cabeçalho consomem o mesmo identificador e o mesmo estado em online, offline e reconexão.

### 13.3 Causa-raiz do isolamento incorreto de avatares

A persistência do backend não era a origem do vazamento: `set_avatar()` já recebia o `user_id` da sessão, `update_avatar` usava `WHERE user_id = ?`, e `PROFILE_UPDATED.user` já carregava a identidade do usuário atualizado. O risco estava no frontend, onde dados raw/stale de `Conversation.avatarData/avatarMime` podiam sobreviver como fallback e a atualização ampla de sessão, amigos e conversas podia misturar apresentação com estado persistido durante atualizações assíncronas. Em termos exatos, o objeto incorreto era a apresentação raw da conversa; o identificador que precisava governar a mutação era `payload.user.user_id`, nunca o usuário atual, o contato selecionado ou o primeiro participante.

O handler de `PROFILE_UPDATED` foi restringido a `user.user_id`: atualiza explicitamente `presenceRef`, `knownUsersRef`, `presence` e `knownUsers`, atualiza a sessão somente quando o ID pertence à sessão atual e não faz mais mutação ampla de `conversations`. Ausência de avatar é representada explicitamente por `null`. `deriveConversationPresentation()` resolve o peer apenas por `peerId`; em conversas de grupo retorna avatar nulo para não usar acidentalmente o avatar de um participante. O teste de regressão altera primeiro o avatar de B e depois o de A, verificando que cada perfil permanece independente e que cada broadcast carrega o ID correto.

### 13.4 Arquivos desta rodada

| Arquivo | Alteração desta rodada |
|---|---|
| `hub_source/client/src/pages/Home.tsx` | Adição do viewport interno do lightbox; preservação do fechamento por X, Escape e clique fora. |
| `hub_source/client/src/index.css` | Layout flex limitado à viewport, safe areas, contenção de overflow, mídia proporcional e regras mobile do lightbox. |
| `hub_source/client/src/state/messenger.tsx` | Derivação por presença canônica, tratamento de `USER_STATUS_CHANGED` e atualização de perfil estritamente por `user_id`. |
| `hub_source/client/src/state/presence.ts` | Novo módulo puro para derivar presença de amigos e apresentação de conversas. |
| `hub_source/client/src/state/presence.test.ts` | Três testes Vitest de presença compartilhada, isolamento de avatar e grupo sem avatar herdado. |
| `server/core.py` | Broadcast canônico de presença em autenticação, reconexão e desconexão, com exclusão segura da conexão autenticando. |
| `server/tests/test_presence.py` | Regressões de online, offline e reconexão por `user_id`, incluindo entrega ao par. |
| `server/tests/test_social_features.py` | Regressão de atualização independente dos avatares de A e B e dos IDs nos broadcasts. |
| `server/tests/test_sync_persistence.py` | Helpers WS ignoram somente broadcasts assíncronos de presença ao aguardar respostas de comando. |
| `server/tests/test_local_architecture.py` e `server/tests/test_resilience.py` | Espera explícita pelos tipos de evento esperados, sem confundir broadcasts de presença com respostas. |
| `client/public/` e `hub_source/client/public/` | `index.html` e bundles estáticos recompilados/sincronizados pelo build final. |
| `RELATORIO_AUDITORIA_MELHORIAS.md` | Esta seção de auditoria e os resultados finais da rodada. |

`launcher/launcher.py` não foi modificado; não houve publicação, hospedagem permanente ou alteração de infraestrutura nesta rodada. Os processos locais usados para validação são descartáveis e não fazem parte do produto.

### 13.5 Testes automatizados e resultados

A validação final foi executada após o último build, sem falhas. Os testes de caso foram contabilizados separadamente por suíte para não misturar checagens de compilação com testes funcionais.

| Verificação | Total | Passou | Falhou | Resultado |
|---|---:|---:|---:|---|
| `python3 -m compileall -q server` | — | — | — | passou |
| `PYTHONPATH=. pytest -q` | 84 | 84 | 0 | passou em 17,67 s |
| `cd hub_source && pnpm check` | — | — | — | passou (`tsc --noEmit`) |
| `pnpm exec vitest run src/state/presence.test.ts` | 3 | 3 | 0 | passou |
| `pnpm build` + sincronização para `client/public` | — | — | — | passou |
| **Total de casos automatizados** | **87** | **87** | **0** | **100% aprovado** |

A suíte Python cobre o cenário de dois usuários que alteram avatar independentemente: B atualiza primeiro e A atualiza depois; o armazenamento de ambos permanece separado e cada `PROFILE_UPDATED` identifica o proprietário correto. A mesma suíte cobre o ciclo de presença de login, desconexão e reconexão, enquanto os três testes Vitest confirmam que a lista de amigos e o cabeçalho convergem no mesmo status e que nenhum avatar raw/stale contamina outro usuário ou um grupo.

O build produziu `index-DWgDmCX2.js` e `index-K9O_SVh7.css`, sincronizados para `client/public`. Permaneceram apenas os avisos preexistentes do Vite relativos aos placeholders `VITE_ANALYTICS_ENDPOINT` e `VITE_ANALYTICS_WEBSITE_ID`; nenhum aviso foi tratado como falha e nenhum segredo foi inserido.

### 13.6 Validação manual e visual

No navegador desktop real, Alice foi autenticada no Hub, Bob foi criado/conectado por uma sessão WS descartável e a conversa `Lightbox Visual` permaneceu aberta. O cabeçalho exibiu `Online` após a conexão de Bob e `Offline` depois da desconexão natural, sem recarregar ou trocar de conversa. O ciclo de reconexão correspondente também foi verificado na suíte de backend.

A conversa recebeu uma imagem nativa 1×1 e uma imagem vertical de 1200×2200. A imagem 1×1 permaneceu 1×1 no lightbox; o DOM reportou viewport 1280×1100, `scrollWidth`/`scrollHeight` do documento iguais aos limites da viewport e botão X independente de 29×29. A imagem vertical grande foi reduzida sem corte; Escape fechou a visualização e o clique fora também fechou, preservando a conversa. O cabeçalho e a área de download permaneceram separados da imagem.

Para a matriz estreita, o CSS compilado real e a mesma estrutura DOM do lightbox foram renderizados em captura 390×844 com fixtures vertical 1200×2200, quadrado 1600×1600 e horizontal 2200×1200. As três imagens ficaram contidas e proporcionais, com X totalmente visível e download separado; a medição DOM confirmou `document.scrollWidth == clientWidth` e `document.scrollHeight == clientHeight`. O Chromium headless deste ambiente expôs 500×757 CSS px por escala interna, embora a captura tenha 390×844 pixels físicos; essa diferença foi registrada e não foi confundida com overflow.

### 13.7 Limitações transparentes

A viewport do navegador integrado não oferece redimensionamento e o ambiente não possui Playwright/Puppeteer; por isso, a confirmação mobile foi feita por fixture isolado fora do repositório, usando o CSS compilado e a estrutura exata do lightbox, além do fluxo real desktop. Não foi usado um celular físico nem publicada uma instância externa. A rota de anexo aceita GET; um probe HTTP HEAD retornar 405 é comportamento esperado do listener, não falha desta rodada.

A matriz automatizada e manual não altera o escopo: não foram reabertas tarefas de SMTP, hospedagem, launcher ou outras features anteriores. Os avisos de analytics e a escala CSS específica do headless são as únicas ressalvas observadas; não há falhas conhecidas restantes nos três defeitos solicitados.

## Referências internas

[1]: `server/attachments/manager.py` — armazenamento, normalização, hash, assinatura e detecção de mídia.
[2]: `server/attachments/http.py` — listener HTTP de downloads autorizados.
[3]: `server/core.py` — autorização e orquestração dos fluxos.
[4]: `hub_source/client/src/pages/Home.tsx` — ChatView, previews, lightbox e configurações.
[5]: `hub_source/client/src/index.css` — identidade visual, dark mode e responsividade.
[6]: `server/tests/test_modern_features.py` — matriz moderna de testes e round-trip de anexos.
[7]: `hub_source/sync-launcher-public.mjs` — sincronização do bundle para a pasta servida pelo launcher.
