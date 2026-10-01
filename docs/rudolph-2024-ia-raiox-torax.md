# IA no RX de tórax da emergência: Rudolph et al. (CHEST 2024) lido contra o Chester

**Data:** 1 de outubro de 2026
**Artigo:** Rudolph J, Huemmer C, Preuhs A, et al. *Non-radiology Healthcare
Professionals Significantly Benefit from AI Assistance in Emergency-Related Chest
Radiography Interpretation.* CHEST, 2024. doi:
[10.1016/j.chest.2024.01.039](https://doi.org/10.1016/j.chest.2024.01.039)
(versão *pre-proof*).
**Motivo:** o artigo mostra de onde vem o ganho de sensibilidade de uma IA de RX
de tórax e como ele foi medido; este documento separa o que se aplica ao Chester
do que não se aplica, e registra as duas mudanças feitas a partir dele.

## Resumo executivo

Com o apoio de uma IA que marca os achados sobre a imagem, residentes de fora da
radiologia ganharam **20 a 53% de sensibilidade** nos quatro achados testados
(pneumotórax, derrame pleural, consolidação suspeita de pneumonia e nódulo),
mantendo a especificidade. Residentes de radiologia, que já partiam de um nível
alto, ganharam pouco e quase nunca de forma significativa.

Os números **não se transferem** para o Chester: o algoritmo do estudo é outro
(um detector comercial com caixas delimitadoras, treinado com anotações de
radiologistas em 18 centros) e a coorte foi enriquecida. O que se transfere é o
**método**, e dele vieram duas mudanças:

1. **Calibração por Youden e por sensibilidade-alvo** em
   `tools/calibrate_thresholds.py`, com os quatro padrões de referência (RFS I–IV)
   do artigo aplicados a rótulos graduados de 0 a 4.
2. **Topografia por hemitórax e terço** de cada achado, na tela do estudo, na
   folha do laudo e nas tags DICOM — o artigo pontuou cada achado separadamente
   para o hemitórax direito e o esquerdo.

Os pontos operacionais padrão **não mudaram**. Torná-los mais sensíveis é uma
decisão que pede exames locais lidos por um radiologista, e a ferramenta é o que
mede isso.

## 1. O estudo

### Desenho

| Item | Detalhe |
| --- | --- |
| Coorte | 563 RX de tórax PA, em pé, da emergência de um hospital universitário (LMU Munique), 2000–2018, idade ≥ 21 anos |
| Seleção | Enriquecida por um residente de radiologia: prevalência de cerca de 10–20% para cada achado, mais exames sem achado |
| Achados | Derrame pleural, pneumotórax, consolidação suspeita de pneumonia, nódulo (qualquer entidade, incluindo granuloma) |
| Leitores | 3 radiologistas titulados (17, 9 e 7 anos de tórax) — padrão de referência; 3 residentes de radiologia (4, 3 e 2 anos); 3 residentes não radiologistas com experiência de emergência (cardiologia 4 anos, gastroenterologia 3, traumatologia 1) |
| Leitura I | Sem IA |
| Leitura II | Com IA, após *wash-out* de cerca de 12 meses, sem acesso à leitura I |
| Escala | Likert 0–4 **por hemitórax**: 0 sem suspeita, 1 improvável, 2 possível, 3 provável, 4 presença certa. O score do achado é o maior dos dois lados |
| Nódulo | Se Likert > 0, o leitor dizia também se indicaria TC — base do "nódulo clinicamente relevante" |

### A IA avaliada

Detector de objetos *single-shot*: *backbone* residual, *feature pyramid network*
convolucional e uma rede preditora por escala. Entrada redimensionada para
1025 × 1025 (bilinear, mantendo a proporção) com normalização robusta de
intensidade; aumento de dados por *flip* esquerda/direita, recorte e escala
aleatórios, rotação e transformação gama. Perda tripla: *focal loss* de
classificação, regressão de caixas por sobreposição e *centerness*. Treinada em
multiclasse para os quatro achados, com dados de todos os grandes fabricantes e
18 centros (Europa, Ásia, Américas do Norte e do Sul), e padrão de referência de
caixas por votação majoritária de radiologistas titulados.

| Achado (treino) | Treino total / pos. / neg. | Validação total / pos. / neg. | AUC interna |
| --- | --- | --- | --- |
| Pneumotórax (AP + PA) | 11.260 / 1.068 / 10.192 | 318 / 67 / 251 | 0,980 |
| Derrame pleural (PA) | 10.276 / 2.042 / 8.234 | 332 / 74 / 258 | 0,995 |
| Consolidação (AP + PA) | 11.622 / 5.653 / 5.969 | 540 / 261 / 279 | 0,960 |
| Nódulo | 9.784 / 4.986 / 4.798 | 444 / 138 / 306 | 0,950 |

O apoio mostrado aos leitores era uma *secondary capture* com a caixa e uma
confiança de 4 (baixa) a 10 (alta) por achado, ou "No Finding Detected" (Fig. 1
do artigo). Os exemplos ilustram exatamente a topografia: consolidações
bilaterais; seropneumotórax à direita com nível hidroaéreo basal junto de
velamento do recesso costofrênico esquerdo; nódulo solitário no terço inferior
esquerdo.

### Os quatro padrões de referência

O Likert de cada radiologista titulado vira um sim/não de quatro maneiras, e o
consenso dos três é por maioria (Tabela 2 do artigo):

| Padrão | 0 | 1 | 2 | 3 | 4 |
| --- | --- | --- | --- | --- | --- |
| RFS I (muito específico) | neg. | neg. | neg. | neg. | **pos.** |
| RFS II | neg. | neg. | neg. | **pos.** | **pos.** |
| RFS III | neg. | neg. | **pos.** | **pos.** | **pos.** |
| RFS IV (muito sensível) | neg. | **pos.** | **pos.** | **pos.** | **pos.** |

O RFS IV é o "clinicamente relevante" do artigo: conta como positivo o que um
radiologista achou ao menos improvável, e portanto é o padrão que mais cobra
sensibilidade de quem lê.

### Estatística

ROC e AUC com IC 95% e teste de DeLong (pacote `pROC` do R). O ponto operacional
de cada grupo foi escolhido por **Youden J** (máximo de sensibilidade +
especificidade − 1) na curva **sem** IA, já suavizada pelo modelo binormal; a
leitura com IA foi lida na mesma especificidade (**iso-especificidade**), e o
ganho é a sensibilidade e a acurácia a mais nesse ponto.

## 2. Resultados no RFS IV

AUC com IC 95%; sensibilidade e acurácia no ponto de Youden da leitura sem IA,
mantida a especificidade. NRR = residentes não radiologistas (consenso);
RR = residentes de radiologia (consenso).

| Achado (positivos) | AUC da IA | NRR sem → com IA | Esp. NRR | Sens. NRR | Acur. NRR |
| --- | --- | --- | --- | --- | --- |
| Pneumotórax (58; 10,3%) | 0,971 [0,947–0,995] | 0,846 → 0,974 (p < 0,001) | 0,953 | 0,714 → 0,928 (+30%) | 0,928 → 0,950 (+2%) |
| Derrame pleural (133; 23,6%) | 0,980 [0,969–0,992] | 0,855 → 0,949 (p < 0,001) | 0,847 | 0,820 → 0,985 (+20%) | 0,840 → 0,879 (+5%) |
| Consolidação (153; 27,2%) | 0,925 [0,897–0,953] | 0,836 → 0,925 (p < 0,001) | 0,803 | 0,755 → 0,973 (+29%) | 0,790 → 0,849 (+7%) |
| Nódulo, detecção simples (92; 16,3%) | 0,938 [0,913–0,962] | 0,723 → 0,890 (p < 0,001) | 0,778 | 0,585 → 0,894 (+53%) | 0,746 → 0,797 (+7%) |
| Nódulo com TC indicada (52; 9,2%) | 0,931 [0,897–0,964] | 0,720 → 0,751 (p = 0,40) | 0,780 | 0,726 → 0,959 (+32%) | 0,775 → 0,797 (+3%) |

| Achado | RR sem → com IA | Esp. RR | Sens. RR | Acur. RR |
| --- | --- | --- | --- | --- |
| Pneumotórax | 0,973 → 0,990 (p = 0,17) | 0,964 | 0,977 → 0,988 (+1%) | 0,966 → 0,967 |
| Derrame pleural | 0,968 → 0,989 (p < 0,01) | 0,904 | 0,932 → 0,987 (+6%) | 0,910 → 0,923 (+1%) |
| Consolidação | 0,927 → 0,937 (p = 0,52) | 0,867 | 0,888 → 0,944 (+6%) | 0,873 → 0,888 (+2%) |
| Nódulo, detecção simples | 0,797 → 0,860 (p < 0,05) | 0,845 | 0,677 → 0,889 (+31%) | 0,818 → 0,852 (+4%) |
| Nódulo com TC indicada | 0,830 → 0,836 (p = 0,86) | 0,943 | 0,988 → 0,998 (+1%) | 0,947 → 0,948 |

Uma inconsistência do *pre-proof*: o texto de resultados dá 0,947 [0,947–1,000]
para o NRR com IA no pneumotórax; o resumo e a Fig. 2 dão 0,974 [0,947–1,000],
que é o valor usado acima.

### Leitura

- **O ganho está na sensibilidade**, a especificidade é a mesma por construção.
  É o que a IA acrescenta: não deixar passar o achado, não descartar o que está lá.
- **O nódulo é o achado em que a IA mais ajuda** e o único em que até os
  residentes de radiologia ganharam de forma significativa (+31%). Para o nódulo
  que pede TC, porém, o consenso NRR não melhorou o AUC de forma significativa.
- **A IA, sozinha, ficou no nível dos residentes de radiologia** em todos os
  achados, e acima deles no nódulo. O artigo a propõe como "segundo leitor" onde
  não há radiologia 24/7.
- **Riscos que o próprio artigo aponta**: aceitação acrítica pelo leitor menos
  experiente, com aumento de falsos positivos; efeito de treino entre as
  leituras; coorte enriquecida de um só centro; financiamento e três autores da
  Siemens Healthineers, fabricante do algoritmo.

## 3. O que se aplica ao Chester, e o que não

### Correspondência dos achados

| Achado do artigo | Saída do Chester | Ponto operacional | Situação |
| --- | --- | --- | --- |
| Pneumotórax | Pneumothorax | 0,0106 (1,08 × o publicado) | Reportado |
| Derrame pleural | Effusion | 0,1032 | Reportado |
| Consolidação suspeita de pneumonia | Consolidation | 0,0383 | Reportado; Pneumonia está suprimida |
| Nódulo | Nodule | 0,0240 | **Suprimido** — dispara em 7 de 7 imagens de referência cujo rótulo não é nódulo |
| (nódulo, aproximação) | Mass, Lung Lesion | 0,0194, 0,0534 | Reportados |

### O que não se transfere

- **Os números.** O Chester roda um classificador DenseNet-121 de 224 × 224
  (`densenet121-res224-all`, torchxrayvision) com pontos operacionais
  publicados para outra população. O detector do artigo trabalha a 1025 × 1025,
  foi treinado com caixas anotadas e tem AUCs internas de 0,95–0,995. Nada do que
  está na seção 2 diz quanto o Chester acerta.
- **A caixa.** O mapa de evidência do Chester tem 49 posições (7 × 7). Ele pode
  dizer o lado e o terço; não pode desenhar uma caixa em torno de um nódulo sem
  inventar precisão.
- **O nódulo.** Está suprimido por medição, e o artigo não muda essa medição:
  voltar a reportá-lo exige o limiar calibrado em exames locais, não um estudo
  de outro algoritmo.

### O que se transfere: o método

**Pontos operacionais por Youden e por iso-sensibilidade**, medidos em exames
lidos por radiologista, sob um padrão de referência explícito. E **a resposta por
hemitórax**: um achado sem lado obriga o leitor a procurá-lo, que é o trabalho
que o artigo mediu a IA economizando.

## 4. Mudança 1 — calibração por Youden e por sensibilidade

`tools/calibrate_thresholds.py` propunha só o menor limiar que atinge uma
especificidade-alvo. Agora:

| Opção | O que faz |
| --- | --- |
| `--method specificity` | Como antes (padrão): menor limiar com especificidade ≥ `--target-specificity` |
| `--method youden` | Limiar que maximiza J = sens + esp − 1; empate vai para o limiar mais baixo, o lado sensível |
| `--method sensitivity` | Maior limiar que ainda mantém sensibilidade ≥ `--target-sensitivity`, e o quanto de especificidade isso custa |
| `--reference-standard I…IV` | Lê rótulos graduados `Effusion:3` (sem grau = 4) e os binariza pela Tabela 2 |

Cada linha passa a trazer a **AUC** (Mann–Whitney, empates contam meio — a mesma
grandeza que o `pROC` reporta para a curva empírica), o J do limiar sugerido e
uma coluna **bounds**: `ok` quando o Settings aceitaria a sugestão como
*override* do ponto que o servidor de fato usa (0,25× a 4×, de
`server/chester/thresholds.py`), `out` quando não, `supp` quando a saída está
suprimida. Os pontos implantados, as saídas suprimidas e os fatores são lidos do
código do servidor com `ast`, sem importá-lo: a ferramenta continua rodando num
*checkout* sem o pacote, e não há uma segunda cópia para divergir.

Fluxo para um perfil mais sensível, no espírito do RFS IV:

```bash
# graded.csv: caminho,rótulos — ex.: exames/0001.png,Pneumothorax:2;Effusion:4
python tools/calibrate_thresholds.py --manifest graded.csv \
    --reference-standard IV --method youden --json youden.json

# ou: fixar a sensibilidade e ver o custo em especificidade
python tools/calibrate_thresholds.py --manifest graded.csv \
    --reference-standard IV --method sensitivity --target-sensitivity 0.95
```

E então, por saída, decidir com o radiologista e aplicar em **Settings → Model
thresholds**, que já audita quem mudou o quê. Três cautelas:

- O limiar é escolhido e avaliado **no mesmo conjunto**, o que o favorece.
  Confirmar em exames que não entraram na escolha.
- Abaixo de 20 negativos a linha é marcada `~`: é aritmética, não evidência.
- O artigo usou Youden **na leitura sem IA** como âncora de especificidade.
  Aqui não há duas leituras; o análogo é escolher o ponto num conjunto e medir
  a sensibilidade em outro.

## 5. Mudança 2 — topografia: hemitórax e terço, dentro dos pulmões

### A primeira versão errou, e como

A primeira versão dividia o quadrado 224 **inteiro** em metades e terços. Num PA,
esse quadrado tem o pescoço em cima e o abdome superior embaixo, e o classificador
põe evidência nos dois. Num estudo real, Lung Opacity e Atelectasis saíram
"HTD superior/inferior" para uma evidência que estava no pescoço e no estômago.
Cardiomegaly também recebeu um hemitórax ("HTD médio"), e com o lado trocado.

Essas entradas (sem `version`) **não são mais exibidas** — nem na tela, nem na
folha, nem no DICOM — e `python -m chester.retopography` recalcula os estudos
antigos com a versão atual.

### Como é agora

`server/chester/segmentation.py` segmenta, no mesmo quadrado que o classificador
analisou, **pulmão direito, pulmão esquerdo e coração** com o PSPNet ChestX-Det do
torchxrayvision (Lian et al., IEEE TMI 2021), exportado com pesos int8 por
`tools/export_segmentation.py` (`models/chest-segmentation-512-int8.onnx`, 66 MB;
IoU int8 × fp32 nos 15 exemplos ≥ 0,962 nos pulmões e ≥ 0,942 no coração).

`server/chester/topography.py` espalha a evidência positiva do mapa 7 × 7 pelos
pixels do quadrado (cada célula é um bloco de 32 × 32, massa preservada) e só
então mede:

| Regra | Valor |
| --- | --- |
| Hemitórax | O pulmão segmentado, **estendido para baixo** 25% da altura dele e alargado 10 px, menos o corredor mediastinal entre os pulmões e o coração (o pulmão segmentado atrás do coração continua contando). O segmentador contorna pulmão *aerado*; derrame, consolidação basal e atelectasia são justamente pulmão que deixou de ser aerado. Nos quatro exemplos com derrame, 4–28% da evidência caía na máscara crua, e 53–71% no hemitórax assim definido. Nada se estende para cima: o pescoço fica de fora |
| Fora do tórax | Menos de 50% da evidência nos hemitóraces → **"Fora dos campos pulmonares"** (`EXTRAPULMONARY`), e nada mais é dito |
| Lado | O pulmão com ≥ 65% da evidência que está nos pulmões; senão **bilateral** |
| Terço | Terços da altura **de cada pulmão**, não do quadrado; listados com ≥ 25%; os três = **difuso** |
| Achados centrais | Cardiomegaly, Enlarged Cardiomediastinum e Hernia não recebem hemitórax |
| Lateralidade | A dos pixels como são exibidos (direita do paciente à esquerda de quem vê). `PatientOrientation` não é mais usado: no estudo acima, contradizia a imagem. Em vez disso, a anatomia é conferida: coração à esquerda de quem vê (imagem espelhada ou dextrocardia) → **"Lado indeterminado"** (`UNDETERMINED`) |

Onde aparece, sempre só para achados **ACIMA** ou **DUVIDOSO**:

- **Tela do estudo**: coluna *Topografia*; "fora dos campos pulmonares" e
  "orientação incerta" quando for o caso.
- **Folha do laudo**: coluna TOPOGRAFIA, ex. `HTD inferior`, `Fora dos campos pulmonares`.
- **DICOM**: no bloco privado de cada item da sequência `(270F,xx03)`, o elemento
  `04` (LO), ex. `RIGHT/LOWER`, `BILATERAL/MIDDLE+LOWER`, `EXTRAPULMONARY`.
- **Banco**: `analysis_results.topography`, com `version: 2`, as frações e o
  status, para a regra ser auditável.

Sem o artefato de segmentação, ou sem dois pulmões encontrados (uma imagem que não
é tórax), não há topografia; os scores seguem iguais.

No exemplo com derrame franco (`examples/Pneumonia-X-rays-Pictures-7.jpg`):
Effusion *Bilateral inferior*, Consolidation *Bilateral inferior*, Infiltration
*Bilateral médio/inferior*; Mass e Lung Lesion, cuja evidência está fora, *Fora
dos campos pulmonares*.

### O que a topografia não é

- **Não é localização de lesão.** É onde está a evidência que *este modelo* usou,
  dentro dos pulmões como segmentados. Um modelo que se apoia em algo espúrio
  aponta para o espúrio — e agora isso aparece como "fora dos campos pulmonares"
  em vez de virar um terço.
- **O quadrado ainda pode cortar os ápices** num filme em retrato (seção 6): os
  terços são do pulmão visível no quadrado.
- **As margens (10 px, 25% abaixo) foram escolhidas nos 15 exemplos**, não
  calibradas em exames lidos. São constantes nomeadas em `topography.py`.

## 6. Um limite de sensibilidade que o artigo torna visível

`inference.preprocess` redimensiona o lado menor para 224 e **recorta o quadrado
central**. Num PA em retrato, isso corta parte do topo e da base do filme — os
ápices, onde o pneumotórax costuma estar, e os seios costofrênicos, onde o
derrame pequeno está. O detector do artigo vê o filme inteiro a 1025 × 1025.

Não foi alterado aqui: mudar o pré-processamento muda todos os scores gravados e
o `PREPROCESSING_VERSION`, e os pontos operacionais foram ajustados com este
recorte. Mas é a primeira hipótese a testar se a calibração local mostrar
sensibilidade baixa para pneumotórax ou derrame — comparando, no mesmo conjunto
lido, o recorte central contra a imagem inteira com *padding*.

## Recomendações

1. ~~Proposta de ponto por Youden e por sensibilidade-alvo, com RFS I–IV.~~ Feito.
2. ~~Topografia por hemitórax e terço na tela, na folha e no DICOM.~~ Feito.
3. **Montar um conjunto local graduado 0–4 por um radiologista**, com os quatro
   achados do artigo, e rodar a ferramenta em RFS IV. É o que decide se algum
   ponto padrão deve ficar mais sensível. Em aberto.
4. **Medir o efeito do recorte central** em pneumotórax e derrame (seção 6).
   Em aberto.
5. **Nódulo continua suprimido** até o item 3 dizer outra coisa. Em aberto.
