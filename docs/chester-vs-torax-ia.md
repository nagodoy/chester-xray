# Chester (demo) × Torax IA: por que os números divergem

**Data:** 2 de setembro de 2026
**Motivo:** um mesmo tórax pontuado nos dois produtos deu leituras diferentes, e o
tag DICOM exportado trouxe `FIBROSIS\CONFIDENT` para um score bruto de 0.0121.

## Resumo executivo

Os pesos são os mesmos e a aritmética também. `docs/onnx-parity.md` registra que
o ONNX reproduz o GraphModel TensorFlow.js do Chester com erro máximo de
2.7e-07 **quando os dois recebem o mesmo tensor 224×224**. Logo, nenhuma
divergência observada entre os dois produtos vem do modelo.

O que difere é o que chega ao modelo, e isso importa muito mais do que parece:
uma janela VOI cobrindo 80% da faixa de dados em vez de 100% já muda **5 dos 12
vereditos** na mesma imagem. Comparar uma leitura do demo (que recebe um JPEG já
renderizado pelo PACS) com uma do Torax IA (que aplica a janela do próprio DICOM)
não é comparar dois modelos — é comparar duas imagens.

Três achados concretos, medidos e não estimados, estão abaixo. O terceiro é um
defeito.

## 1. O comparativo

### O que é comparável e o que não é

O demo do Chester desenha a barra na posição do score **normalizado pelo ponto
operacional** (`op_norm`, com o boost `SCALE_UPPER = 1.3` acima de 0.6). O Torax
IA mostra as duas colunas separadas: `SAÍDA BRUTA` (sigmoid puro) e
`SCORE NORMALIZADO`.

Comparar a barra do demo com a coluna `SAÍDA BRUTA` compara grandezas
diferentes, e é o erro que faz o Torax IA parecer "achatado": um score bruto de
0.0121 na Fibrosis vira 0.501 normalizado, porque o ponto operacional dessa
saída é 0.0101. A coluna a comparar com o demo é `SCORE NORMALIZADO`.

### Quanto o pré-processamento move os scores

Modelo fixo, imagem fixa, só o pré-processamento variando. Deslocamento absoluto
máximo entre as 12 saídas reportadas:

| Variação | `bc370d05…jpeg` | `Pneumonia-X-rays-7.jpg` |
| --- | --- | --- |
| Redimensionar sem recortar (squash) em vez de crop central | 0.1765 (Lung Opacity) | 0.1910 (Consolidation) |
| Gamma 0.8 — o mesmo pixel, renderizado mais claro | 0.0695 (Lung Opacity) | 0.2088 (Atelectasis) |
| Esticar min/max quando a imagem já ocupa 0..255 | 0.0000 | 0.0000 |

E a janela VOI, que é a diferença real entre receber um DICOM e receber um JPEG
exportado (`00000001_002-Cardiomegaly-Effusion.png`):

| Janela sobre a faixa de dados | Deslocamento máximo | Vereditos alterados |
| --- | --- | --- |
| 80% | 0.0921 | 5 de 12 |
| 60% | 0.3047 | 5 de 12 |
| 40% | 0.4395 | 7 de 12 |

Na janela de 60%, a Fibrosis cai de 0.0554 para 0.0003 — de `CONFIDENT` para
`ABSENT` — sem que um único pixel de anatomia mude. Este modelo é sensível ao
mapeamento de intensidade, e qualquer comparação entre os dois produtos que não
fixe o raster antes do modelo mede a janela, não o classificador.

### Sobre o exame do screenshot

Os scores baixos (todos abaixo de 0.04) não indicam pipeline quebrado. Rodando o
mesmo modelo sobre `Pneumonia-X-rays-Pictures-7.jpg`, um caso com achado franco,
o pipeline atual devolve Effusion 0.9228, Lung Opacity 0.8618 e Atelectasis
0.8192. A faixa alta é alcançável; aquele tórax simplesmente não a alcançou.

Para um comparativo válido, o passo que falta é gravar o raster 0..255 que sai de
`render_frame_for_model` e alimentar o demo com **esse** arquivo, em vez de com
um JPEG exportado do PACS.

## 2. O pipeline do Torax IA

Verificado ponta a ponta. Está correto e faz o que documenta:

1. **Seleção de incidência** — `imaging/validation.py` reconhece PA/AP como
   frontal e recusa lateral; "PERFIL" está em `LATERAL_WORDS`. No estudo do
   screenshot, `INCIDÊNCIA: PA` confirma que o filme frontal foi o analisado.
2. **Rasterização** — `render_frame_for_model` aplica rescale slope/intercept,
   depois a janela VOI quando `WindowWidth > 1` (caindo para a faixa completa de
   dados quando não há janela utilizável), normaliza para 0..255 e inverte em
   MONOCHROME1.
3. **Pré-processamento** — `inference.preprocess` redimensiona o lado menor para
   224 (bilinear, PIL), recorta 224×224 no centro e mapeia 0..255 para
   `[-1024, 1024]`.
4. **Inferência** — ONNX Runtime, 18 saídas, seis suprimidas por decisão clínica
   herdada (Infiltration, Pneumothorax, Pneumonia, Nodule, Lung Lesion,
   Fracture).
5. **Apresentação** — bruto, normalizado pelo ponto operacional e o veredito de
   confiança, os três guardados separadamente.

Uma diferença de forma vale registro, ainda que pequena: o torchxrayvision
recorta o quadrado central na resolução original e só depois redimensiona; aqui
é o inverso. A região é a mesma; a reamostragem, não exatamente. Fica na ordem
do ruído de interpolação, muito abaixo do efeito da janela.

## 3. O tag DICOM exportado

```text
MASS\DOUBT\EDEMA\ABSENT\HERNIA\ABSENT\EFFUSION\ABSENT\FIBROSIS\CONFIDENT\
EMPHYSEMA\DOUBT\ATELECTASIS\ABSENT\CARDIOMEGALY\ABSENT\LUNGOPACITY\ABSENT\
CONSOLIDATION\ABSENT\PLEURALTHICKENING\ABSENT\ENLARGEDCARDIOMEDIASTINUM\ABSENT
```

É a sequência privada `(270F,xx03)` achatada pelo visualizador: 12 itens, cada um
com `CodeMeaning` e `TextValue`, unidos pelo delimitador `\` de multivalor. Os 12
correspondem às 12 patologias reportadas.

### O tag está aritmeticamente correto

Os 12 vereditos foram recalculados a partir dos scores brutos e dos pontos
operacionais: **todos os 12 conferem** com `classify_confidence`. A regra é uma
banda de ±10% do ponto operacional em torno dele — dentro, `DOUBT`; abaixo,
`ABSENT`; acima, `CONFIDENT`.

O que incomoda na leitura é real, mas não é erro de cálculo:

| Patologia | Bruto | Ponto op. | Veredito |
| --- | --- | --- | --- |
| Atelectasis | 0.0392 | 0.0742 | ABSENT |
| Effusion | 0.0384 | 0.1032 | ABSENT |
| **Fibrosis** | **0.0121** | **0.0101** | **CONFIDENT** |
| Emphysema | 0.0022 | 0.0022 | DOUBT |

A Fibrosis recebe `CONFIDENT` com um terço do score da Effusion, que recebe
`ABSENT`, porque cada saída tem seu próprio ponto operacional — 0.0101 contra
0.1032. Isso é o comportamento correto de um classificador multirrótulo com
limiares por patologia, e a palavra é que engana: `CONFIDENT` descreve a posição
relativa ao limiar, não a confiança do modelo no achado.

### Defeito: a ordem dos achados é artefato do banco

A ordem do tag não é a do modelo. Verificado:

```text
ordem canônica  : ATELECTASIS, CONSOLIDATION, EDEMA, EMPHYSEMA, FIBROSIS, ...
ordem no tag    : MASS, EDEMA, HERNIA, EFFUSION, FIBROSIS, EMPHYSEMA, ...
```

Os comprimentos dos nomes na ordem observada são 4, 5, 6, 8, 8, 9, 11, 12, 12,
13, 18, 26 — crescentes, com empates resolvidos em ordem alfabética. É
exatamente a ordenação de chaves do `JSONB` do PostgreSQL: por comprimento e
depois por bytes. `report.finding_rows` itera `raw.items()`, e o dicionário volta
do banco reordenado.

A docstring de `finding_rows` afirma "in the model's own order". Em SQLite é
verdade; em PostgreSQL, que é o que roda em produção, não é. O artefato de
armazenamento vaza para a folha do laudo e para o tag DICOM que sai para o PACS.

**Corrigido.** `finding_rows` agora itera `inference.REPORTED_PATHOLOGIES` e busca
cada score, em vez de iterar as chaves do documento. Um nome que o resultado não
carrega é pulado, não reportado como score zero. Um exame novo sai assim:

```text
ATELECTASIS\ABSENT\CONSOLIDATION\ABSENT\EDEMA\ABSENT\EMPHYSEMA\ABSENT\
EFFUSION\ABSENT\PLEURALTHICKENING\ABSENT\CARDIOMEGALY\ABSENT\MASS\ABSENT\
HERNIA\ABSENT\LUNGOPACITY\ABSENT\ENLARGEDCARDIOMEDIASTINUM\ABSENT
```

### Lacuna: o tag não carregava número

Cada item levava `CodeMeaning` e `TextValue`, e nada mais. Um visualizador que
lesse os tags via `FIBROSIS\CONFIDENT` sem meio de saber que eram 0.0121 contra um
limiar de 0.0101. A folha renderizada mostrava o score; os tags, não.

**Corrigido.** Cada item da sequência ganhou um bloco privado próprio, com o mesmo
criador, carregando três `DS`:

| Elemento | Conteúdo |
| --- | --- |
| `01` | score bruto |
| `02` | ponto operacional |
| `03` | score normalizado |

Nada foi removido: um leitor que só conhece `CodeMeaning` e `TextValue` vê
exatamente o que via antes. Os números atravessam o round-trip pelo formato de
arquivo, que é o que o PACS recebe.

## 4. Fibrosis: falso positivo sistemático (suprimida)

Um segundo exame trouxe `Fibrosis` acima do limiar. Medido, não estimado:

Nas 15 imagens de `examples/`, Fibrosis dispara em 11 (73%). Restringindo às 7
cujo rótulo é conhecido e **não** é fibrose, dispara em **7 de 7** — incluindo a
rotulada *No Finding*, a 8,5 vezes o limiar:

| Rótulo verdadeiro | Bruto | × limiar |
| --- | --- | --- |
| Cardiomegaly | 0.0908 | 9.0 |
| Cardiomegaly + Emphysema | 0.0315 | 3.1 |
| Cardiomegaly + Effusion | 0.0550 | 5.5 |
| Cardiomegaly + Effusion | 0.0569 | 5.7 |
| Cardiomegaly + Effusion | 0.0472 | 4.7 |
| **No Finding** | **0.0853** | **8.5** |
| Hernia | 0.0199 | 2.0 |

Não é desalinhamento de índice: `OP_POINT[6] = 0.010060724` no `config.json` do
modelo legado é mesmo o de Fibrosis, verificado posição a posição. É o mesmo
limiar que o demo do Chester usa.

São duas propriedades somadas:

- **O ponto operacional é o segundo mais baixo dos 18** (0.0101). A saída de
  fibrose é minúscula em quase toda imagem e o limiar cai no meio do ruído.
- **É a saída mais sensível ao contraste do conjunto.** Variação mediana entre
  renderizações da mesma anatomia:

| Patologia | Ponto op. | Fator máx/mín | Vereditos que mudam |
| --- | --- | --- | --- |
| **Fibrosis** | 0.0101 | **48.6×** | 11 de 15 |
| Edema | 0.0236 | 14.6× | 10 de 15 |
| Cardiomegaly | 0.0503 | 11.7× | 7 de 15 |
| … | | | |
| Lung Opacity | 0.2020 | 1.5× | 4 de 15 |

Quem decide se Fibrosis lê "acima" é a renderização, não a anatomia. O ponto
operacional não está errado para a população em que foi ajustado; ele não
transfere para esta.

**Fibrosis foi movida para `SUPPRESSED_INDICES`**, junto das seis que o CHESTER
já não reportava. O laudo passa a ter 11 achados. A supressão é aplicada também
onde resultados já gravados são exibidos — a folha, os tags DICOM, o resumo da
worklist e o schema da API — porque um estudo analisado antes da mudança ainda
carrega a saída no documento armazenado.

Voltar a reportá-la exige um limiar calibrado contra exames locais lidos por um
radiologista, não uma mudança de código.

## Recomendações

1. ~~Corrigir a ordem em `report.finding_rows`.~~ Feito.
2. ~~Levar score e limiar para dentro da sequência privada.~~ Feito.
3. **Revisar a palavra `CONFIDENT`.** "Acima do ponto operacional" é o que ela
   significa; é o que ela deveria dizer. Em aberto.
4. **Para qualquer comparativo futuro com o demo**, exportar o raster de
   `render_frame_for_model` e alimentar os dois com esse arquivo. Sem isso, a
   medição é da janela. Em aberto.
5. **Recalibrar o ponto operacional da Fibrosis** contra exames locais lidos por
   um radiologista, se quiser voltar a reportá-la. Em aberto.

## Como reproduzir

Os experimentos deste documento variaram apenas o pré-processamento, com o modelo
e a imagem fixos, medindo o deslocamento absoluto das 12 saídas reportadas e
quantos vereditos de `classify_confidence` mudavam. As imagens vieram de
`examples/`.
