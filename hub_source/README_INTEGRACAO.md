# MSN Messenger Hub — execução local

A HUB é um cliente React/Vite do servidor Python MSN. O Express em `server/index.ts` apenas serve os arquivos frontend; autenticação, sessões, conversas, mensagens e SQLite continuam no servidor Python.

## 1. Instalar dependências

Na pasta deste projeto:

```bash
pnpm install --frozen-lockfile
```

## 2. Iniciar o servidor Python

Em um terminal, usando a pasta do projeto do servidor:

```bash
cd ../msn_server_final
python3 -m server.main
```

O padrão é `ws://localhost:8765`.

## 3. Configurar a HUB

A HUB usa `ws://localhost:8765` por padrão. Para outro endereço, crie um arquivo `.env` na raiz:

```text
VITE_MSN_SERVER_URL=ws://192.168.0.10:8765
```

Quando o servidor tiver TLS, a mesma variável poderá usar `wss://...`.

## 4. Executar em desenvolvimento

```bash
pnpm dev
```

Abra o endereço exibido pelo Vite, normalmente `http://localhost:3000` ou `http://localhost:5173`.

## 5. Validar o build

```bash
pnpm check
pnpm build
```

## 6. Fluxo de uso

Crie uma conta pela tela de registro, entre com login, aguarde a sincronização e converse com outra conta real. A conta, as conversas e as mensagens ficam no SQLite do servidor, não no navegador. O botão Sair envia `LOGOUT` antes de voltar à tela de login.

## Arquivos principais

| Arquivo | Função |
|---|---|
| `client/src/network/config.ts` | Endpoint único do WebSocket |
| `client/src/network/protocol.ts` | Tipos do protocolo Python |
| `client/src/network/websocket.ts` | Conexão e reconexão |
| `client/src/state/messenger.tsx` | Estado real do cliente |
| `client/src/pages/Home.tsx` | Interface visual preservada |
| `INTEGRACAO_HUB.md` | Relatório técnico completo |
