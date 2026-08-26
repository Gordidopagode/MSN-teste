# Relatório — recuperação local por código

## Objetivo

A recuperação por e-mail foi substituída por um código local de recuperação. O MSN agora funciona sem SMTP, Gmail, Railway ou outro serviço externo para criar ou recuperar contas. O launcher não foi alterado.

## 1. Arquitetura anterior

Antes da alteração, o cadastro exigia e-mail e a recuperação seguia o fluxo `REQUEST_PASSWORD_RESET` → SMTP → código temporário → `RESET_PASSWORD` por e-mail. O servidor mantinha uma tabela `password_reset_tokens` com hash, expiração, tentativas e uso.

Esse desenho dependia da configuração SMTP para entregar o código e não correspondia ao novo requisito de recuperação totalmente local.

## 2. Arquitetura nova

O fluxo atual é:

```text
Cadastro sem e-mail
      ↓
Servidor gera código aleatório de 16 caracteres
      ↓
Servidor grava somente o hash no SQLite
      ↓
REGISTER_OK entrega o código uma única vez
      ↓
Hub exibe a tela de entrega
      ↓
Usuário copia e confirma que guardou
      ↓
Username + código + nova senha
      ↓
Backend valida e invalida o código em transação
```

O código usa o alfabeto `ABCDEFGHJKLMNPQRSTUVWXYZ23456789`, que evita caracteres visualmente confusos como `I`, `O`, `0` e `1`. A geração usa `secrets`, portanto não depende de username, senha, e-mail ou dados pessoais.

## 3. Arquivos modificados

| Arquivo | Alteração |
|---|---|
| `server/auth/manager.py` | Geração no cadastro, hash com o hasher de credenciais, validação por username, limite de tentativas e migração de contas antigas no primeiro login. |
| `server/persistence/store.py` | Nova tabela `recovery_codes` e operações atômicas de criação, tentativa e conclusão. |
| `server/core.py` | `REGISTER_OK` entrega o código transitório; `RESET_PASSWORD` recebe username, código e nova senha. |
| `server/network/protocol.py` | Cadastro não exige e-mail; o comando de solicitação por e-mail foi removido; reset usa username. |
| `server/network/handler.py` | Despacho atualizado sem `REQUEST_PASSWORD_RESET`. O launcher e o ciclo de sockets não foram modificados. |
| `server/shared_types.py` | Campos transitórios não serializados para transportar código até a resposta imediata. |
| `hub_source/client/src/network/protocol.ts` | Tipos de registro e autenticação atualizados com `recovery_code`. |
| `hub_source/client/src/state/messenger.tsx` | Código retornado somente para o fluxo de cadastro/login e reset local com conexão WebSocket própria. |
| `hub_source/client/src/pages/Home.tsx` | Cadastro sem e-mail, tela única do código, botão de copiar, confirmação obrigatória e formulário username + código. |
| `hub_source/client/src/index.css` | Estilos mínimos da tela do código, usando a identidade visual existente. |
| `.env.example` | Gmail deixou de aparecer como configuração necessária; variáveis SMTP ficaram comentadas como compatibilidade futura. |
| `server/email/service.py` | Documentação ajustada para registrar que o helper é legado e não é chamado pelo fluxo atual. |
| `hub_source/INTEGRACAO_HUB.md` | Protocolo e validação documentados para o fluxo local. |
| `server/tests/test_auth.py` | Cadastro sem e-mail e entrega do código cobertos. |
| `server/tests/test_password_recovery.py` | Testes locais de hash, uso único, rate limiting, isolamento, restart e conta antiga. |
| `server/tests/test_local_architecture.py` | Testes WebSocket locais, três clientes, chat, launcher, origem e ausência de SMTP. |
| `hub_source/client/public/index.html` e assets gerados | Bundle atualizado pelo build do frontend. |

Não foram criados novos arquivos de produção. O arquivo novo `RELATORIO_RECUPERACAO_LOCAL.md` é somente documentação; a tabela é criada automaticamente pelo schema existente quando o servidor inicializa.

## 4. Como o código é armazenado

O código original existe somente na memória durante a criação da conta e a resposta `REGISTER_OK`. O banco grava apenas `recovery_codes.code_hash`, além de `recovery_id`, `user_id`, contador de tentativas, `created_at` e `used_at`. O código não é incluído em `to_dict`, não é colocado em sessão persistente e não é registrado nos logs.

O hash usa `hash_password`, o mesmo mecanismo Argon2 ou fallback PBKDF2-SHA256 utilizado para senhas. Não é usado MD5, SHA-1 ou SHA-256 simples.

## 5. Como o código é entregue e mostrado uma vez

Após o cadastro, o backend retorna `recovery_code` no `REGISTER_OK`. O provider do Hub encaminha esse valor apenas ao componente `RecoveryCodeView`. O componente mostra o código, oferece `Copiar código`, apresenta confirmação visual e mantém `Continuar` desabilitado até o checkbox “Já guardei meu código em um lugar seguro” ser marcado.

Depois de `Continuar`, o estado transitório é descartado e o Hub é exibido. Não existe botão normal para mostrar o código novamente. Se a janela, aba ou processo for encerrado antes de o usuário guardar o código, o código é perdido e não é reexibido automaticamente; essa decisão evita transformar a credencial de recuperação em um segredo recuperável pelo próprio sistema.

## 6. Como funciona a recuperação

Na tela “Esqueci minha senha”, o usuário informa username, código, nova senha e confirmação. O frontend envia `RESET_PASSWORD`; a validação acontece no backend. O servidor usa mensagens genéricas para username inexistente, código errado, código usado ou limite excedido.

Cada erro incrementa `attempts`. Ao atingir o limite configurado por `MSN_RESET_MAX_ATTEMPTS`, o código deixa de ser utilizável. O valor padrão permanece 5 tentativas.

## 7. Uso único e persistência

A redefinição acontece dentro de uma transação SQLite `BEGIN IMMEDIATE`. O backend confirma que o registro pertence ao username e ainda não foi usado, altera o hash da senha, marca `used_at` e invalida as sessões da conta. Uma segunda tentativa com o mesmo código é rejeitada.

O código não tem expiração automática: permanece válido até ser usado ou até atingir o limite de tentativas, conforme a preferência definida nas instruções. O registro continua persistido após reinicialização do servidor.

## 8. Contas existentes

Contas antigas que não possuem registro em `recovery_codes` não são apagadas nem têm código derivado de senha, username ou dados pessoais. No primeiro login bem-sucedido, o servidor gera um código novo, grava somente o hash e o inclui na resposta `AUTH_OK` uma única vez. Se o usuário não guardar esse código, ele não será reexibido automaticamente.

Contas antigas que já possuem o registro legado de `password_reset_tokens` continuam com seus dados no banco, mas o fluxo atual não depende dessa tabela nem de SMTP. O e-mail cadastrado permanece como dado compatível do perfil, sem ser obrigatório para novos cadastros.

## 9. Testes executados

| Teste | Resultado |
|---|---|
| Suíte Python completa | **73 aprovados, 0 falhas** com `PYTHONPATH=. pytest -q` |
| Cadastro sem e-mail | Aprovado |
| Código diferente para múltiplas contas | Aprovado |
| Hash no SQLite e ausência do código puro | Aprovado |
| Recuperação correta | Aprovado |
| Código reutilizado | Aprovado e rejeitado |
| Código incorreto e mensagens genéricas | Aprovado |
| Rate limiting de tentativas | Aprovado |
| Três clientes durante recuperação concorrente | Aprovado; chat e WebSocket permaneceram ativos |
| Reinicialização com recuperação posterior | Aprovado |
| Conta antiga sem código | Aprovado; código criado no primeiro login |
| Servidor sem SMTP | Aprovado |
| Launcher sem alteração e origem local | Aprovado |
| TypeScript | **Aprovado** com `pnpm check` |
| Build do Hub | **Aprovado** com `pnpm build` |
| Teste manual no navegador | Aprovado: cadastro, tela única, copiar, confirmação, Continuar, logout e tela username + código |

Durante o teste manual foi usada uma conta descartável em banco temporário. O código exibido não foi incluído neste relatório, em logs ou em arquivos do projeto.

## 10. Problemas e limitações restantes

O código é uma credencial permanente até uso ou bloqueio por tentativas. Se for perdido antes da confirmação, não há recuperação automática; será necessário um procedimento administrativo futuro, caso o projeto venha a precisar dele. O reset continua invalidando as sessões da conta, portanto o usuário deverá fazer login novamente após recuperar a senha.

O servidor permanece sem TLS/WSS nativo, como na arquitetura anterior; isso não faz parte desta alteração. O helper SMTP ainda existe apenas para compatibilidade futura, mas não é importado pelo core da recuperação atual e nenhuma variável SMTP é necessária.
