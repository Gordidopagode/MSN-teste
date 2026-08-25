# Direção visual — MSN Messenger Hub

## Abordagens consideradas

### Tema: Retro Desktop Leve
**Introdução:** Um mensageiro compacto com moldura clara, azul de sistema e detalhes de presença em verde, evocando a simplicidade dos clientes de mensagens do início dos anos 2000 sem copiar marcas ou imagens proprietárias. **Probabilidade:** 0,07.

### Tema: Papel de Recados Pessoal
**Introdução:** Uma leitura mais editorial e calorosa, com fundo creme, notas suaves e contatos apresentados como pequenos recados, priorizando sensação de grupo íntimo. **Probabilidade:** 0,04.

### Tema: Janela de Conversa Azul Profundo
**Introdução:** Uma variação de alto contraste com molduras azul-marinho e acentos luminosos para status, ainda compacta, mas menos próxima do MSN clássico. **Probabilidade:** 0,02.

## Abordagem escolhida: Retro Desktop Leve

### Design Movement

**Skeuomorfismo web do início dos anos 2000 reinterpretado com contenção contemporânea.** A interface deve parecer um pequeno programa pessoal aberto no desktop: bordas finas, superfícies sólidas, barras de título claras, separadores discretos e controles que comunicam função antes de decoração.

### Core Principles

1. **Compacto por intenção:** cada tela ocupa apenas o espaço necessário e mantém o foco em login, contatos e conversa.
2. **Presença legível:** os estados Online, Ausente, Ocupado e Offline aparecem com pequenos sinais coloridos consistentes em avatar, lista e cabeçalho.
3. **Nostalgia sem cópia literal:** referências ao MSN vêm da composição, do vocabulário visual e da simplicidade, não de logos, imagens ou elementos proprietários reproduzidos.
4. **Evolução sem excesso:** dados locais mockados e componentes independentes deixam espaço para WebSocket, autenticação e presença real no futuro, sem fingir que essas funções já existem.

### Color Philosophy

O azul-céu é a cor de orientação e conexão, usado em molduras, seleções e ações primárias, nunca como banho de cor em toda a tela. O verde representa presença disponível e proximidade. Amarelo é reservado a pequenos sinais de destaque, como uma estrela de atividade ou aviso sutil. A base marfim e cinza-azulado reduz a sensação de dashboard e faz o Hub parecer uma janela leve de aplicativo pessoal.

**Paleta base:** marfim `#f8fafb`, azul névoa `#e5f1f8`, azul de moldura `#2575ad`, azul profundo de texto `#17364b`, verde presença `#3eae68`, amarelo detalhe `#f3c64d`, coral erro `#d96b62`.

### Layout Paradigm

Uma **janela central assimétrica e compacta**, com trilho lateral estreito para avatar, status e ícones utilitários, uma coluna de conversas dimensionada como lista de contatos e uma área de chat mais ampla. Login e cadastro usam um cartão curto alinhado levemente acima do centro, com o fundo atmosférico visível nas bordas. No Hub, a barra inferior concentra conexão, configurações e logout, como um rodapé funcional de cliente desktop.

### Signature Elements

1. **Mini ícones de presença** em pontos, anéis e pequenos badges, sempre com rótulo textual quando o contexto exigir.
2. **Moldura azul de aplicativo** com barra de título compacta, divisor fino e estados de hover que lembram itens selecionáveis de uma lista de contatos.
3. **Selo de mensagem** — envelope com balão — como detalhe pontual no login e nas áreas vazias, sem competir com o conteúdo.

### Interaction Philosophy

Interações devem ser diretas, previsíveis e discretas. Clicar em uma conversa muda a seleção e o cabeçalho; o seletor de status abre um menu pequeno e legível; entrar e criar conta alternam telas sem rotas complexas; enviar uma mensagem acrescenta um item local à conversa atual. Hover e foco sinalizam o alvo sem transformar a interface em uma vitrine de efeitos.

### Animation

Usar transições curtas de 160–220 ms apenas para seleção de conversa, menus e troca de tela. A entrada inicial pode usar opacidade e deslocamento de 4 px, em uma única vez. Não animar continuamente os ícones de status nem usar pulsing chamativo. Respeitar `prefers-reduced-motion` e manter os atalhos de teclado instantâneos.

### Typography System

Usar **Trebuchet MS** para títulos e labels de aplicativo, pela personalidade amigável e memória visual de interfaces antigas, e **Tahoma** como corpo e microcopy, pela leitura compacta em densidade de desktop. Títulos entre 18–24 px, labels entre 11–12 px, corpo entre 13–14 px e metadados entre 10–11 px. Peso forte apenas em nome de usuário, conversa ativa e ações principais.

### Brand Essence

**Um pequeno mensageiro privado para grupos próximos, feito para conversar sem ruído e crescer junto com o servidor real.** Personalidade: **íntimo, claro, nostálgico**.

### Brand Voice

Headlines e CTAs devem soar pessoais e objetivos, com frases curtas e sem linguagem corporativa. Microcopy explica o estado atual em vez de prometer funcionalidades inexistentes.

> “Seu grupo, ali pertinho.”

> “Escolha uma conversa e continue de onde parou.”

### Wordmark & Logo

O wordmark textual será tipográfico, mas o símbolo principal será um par de balões sobrepostos em forma de asas, com azul e verde e um pequeno ponto amarelo. O símbolo não reproduz o logotipo do MSN e deve funcionar sozinho no avatar do app, no topo da janela e no favicon.

### Signature Brand Color

**Azul Janela `#2575ad`** — um azul médio, utilitário e reconhecível, que ancora barras e seleções sem dominar superfícies ou transformar a interface em um gradiente promocional.

## Ground truth do produto

O briefing anexado pelo usuário é a especificação de referência. A primeira versão será apenas um Hub visual e funcional no navegador, com login, cadastro, status, contatos/conversas mockados, chat local, configurações e logout; não haverá backend real, WebSocket ou autenticação persistente nesta etapa.

## Style Decisions

- Não usar cópia literal do logotipo, imagens ou elementos proprietários do MSN Messenger.
- Priorizar ícones pequenos e claros para contatos, presença, configurações, servidor e logout.
- Evitar dashboard, excesso de cards, neon, glassmorphism, gradientes fortes e animações contínuas.
- Usar os assets originais gerados para o projeto apenas como apoio visual: marca, fundo claro e pequenos detalhes decorativos.
- As telas de login devem parecer diálogos compactos de entrada do aplicativo, sem hierarquia promocional ou encenação de landing page.
- O chrome deve usar molduras finas em `#2575ad`, campos com sombra interna, divisores nítidos e controles levemente biselados.
- A cópia voltada ao visitante fala apenas do produto e de seu estado atual; não descreve a interface como “referência visual”.
