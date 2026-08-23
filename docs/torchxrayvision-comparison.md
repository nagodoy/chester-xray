# Comparação com TorchXRayVision

**Data da consulta:** 22 de agosto de 2026  
**Objetivo:** registrar a comparação técnica e a decisão de manter o GraphModel
local do CHESTER como runtime ativo.

## Resumo executivo

TorchXRayVision não é um componente de navegador equivalente ao Chester. É
uma biblioteca Python/PyTorch para pesquisa, com modelos pré-treinados,
classes para datasets públicos e utilitários de pré-processamento.

Há, porém, uma relação técnica forte entre o modelo atual do Chester e o
classificador principal do TorchXRayVision. O Chester usa um GraphModel
TensorFlow.js convertido e local, enquanto o TorchXRayVision carrega pesos
PyTorch. Ainda assim, os dois expõem 18 saídas de patologias, usam entrada de
224 pixels, normalização aproximada para `[-1024, 1024]` e os mesmos 18 pontos
operacionais do modelo “all”. Isso indica provável mesma linhagem de
treinamento/conversão, mas não prova que os arquivos de pesos sejam
numericamente idênticos.

**Conclusão:** o app usa o GraphModel local do CHESTER no backend. Uma comparação
de pesquisa futura deve executar os dois modelos sobre exatamente as mesmas
imagens e registrar pré-processamento, scores sigmoid brutos e scores
normalizados; o TorchXRayVision não integra o runtime de produção.

## Nota sobre a URL fornecida

A URL fornecida, `https://github.com/nagodoy/torchxrayvision`, retornou 404 para
os arquivos públicos consultados. A análise foi feita usando o repositório
oficial disponível em `https://github.com/mlmed/torchxrayvision` e sua
documentação publicada em `https://mlmed.org/torchxrayvision/`.

## O que o TorchXRayVision oferece

### Modelos

O repositório consultado informa a versão `1.5.3` e disponibiliza estes pesos
principais:

| Família | Pesos documentados | Entrada | Uso |
| --- | --- | --- | --- |
| DenseNet-121 | `densenet121-res224-all` | 224 × 224 | Modelo treinado na combinação de NIH, PadChest, CheXpert, MIMIC-CXR, Google/NIH, OpenI e RSNA |
| DenseNet-121 | `densenet121-res224-nih` | 224 × 224 | NIH ChestX-ray14 |
| DenseNet-121 | `densenet121-res224-pc` | 224 × 224 | PadChest |
| DenseNet-121 | `densenet121-res224-chex` | 224 × 224 | CheXpert |
| DenseNet-121 | `densenet121-res224-rsna` | 224 × 224 | RSNA Pneumonia Challenge |
| DenseNet-121 | `densenet121-res224-mimic_nb` | 224 × 224 | MIMIC-CXR, variante NB |
| DenseNet-121 | `densenet121-res224-mimic_ch` | 224 × 224 | MIMIC-CXR, variante CH |
| ResNet | `resnet50-res512-all` | 512 × 512 | Classificador combinado em resolução maior |
| ResNetAE | `101-elastic` | definida pelo autoencoder | Representação e reconstrução |

O código também oferece modelos de referência adaptados à mesma interface,
incluindo ensemble CheXpert, JF Healthcare, segmentação ChestX-Det e
ChestXRayAnatomy, além de modelos para atributos como raça, idade, sexo e
projeção. Eles não são equivalentes diretos ao classificador de patologias do
Chester.

Os classificadores principais usam até 18 patologias:

```text
Atelectasis, Consolidation, Infiltration, Pneumothorax, Edema,
Emphysema, Fibrosis, Effusion, Pneumonia, Pleural_Thickening,
Cardiomegaly, Nodule, Mass, Hernia, Lung Lesion, Fracture,
Lung Opacity, Enlarged Cardiomediastinum
```

O modelo “all” traz pontos operacionais por patologia. Quando esses pontos são
usados, o fluxo aplica sigmoid e remapeia cada saída para que o ponto
operacional corresponda aproximadamente a `0.5`. Isso é diferente de dizer
que o valor remapeado é uma probabilidade clínica calibrada.

### Datasets e ferramentas de pesquisa

As classes documentadas incluem:

- NIH ChestX-ray14 e as relabelagens do Google;
- CheXpert;
- MIMIC-CXR;
- PadChest;
- RSNA Pneumonia Detection Challenge;
- OpenI/Indiana University;
- SIIM-ACR Pneumothorax;
- VinDr/VinBigData.

Cada dataset expõe uma interface comum com `pathologies`, `labels` e um
`DataFrame` de metadados. Os helpers permitem:

- alinhar rótulos entre datasets;
- combinar datasets;
- filtrar e criar subconjuntos;
- restringir projeções, como PA/AP;
- simular mudanças de covariáveis.

Esse é o principal diferencial de pesquisa da biblioteca. O Chester recebe um
arquivo individual para inferência e não implementa uma camada equivalente de
catalogação, alinhamento de rótulos ou avaliação entre coortes.

## Comparação com o Chester atual

| Aspecto | Chester neste projeto | TorchXRayVision |
| --- | --- | --- |
| Runtime | Página estática, inferência no navegador | Python e PyTorch |
| Modelo principal | GraphModel TensorFlow.js local em `models/xrv-all-45rot15trans15scale` | Pesos PyTorch `densenet121-res224-all` e variantes |
| Privacidade | Imagens e inferência permanecem no dispositivo | A biblioteca de pesquisa roda onde o script/serviço for executado |
| Entrada normal | PNG/JPEG | Arrays NumPy/PyTorch, imagens dos datasets e utilitário de arquivo |
| Entrada DICOM | Cornerstone no navegador, com codecs comprimidos, MONOCHROME1/2, rescale/window e seleção de frame | Helper Python com pydicom; documenta conversão para uma imagem 2D normalizada |
| Pré-processamento | Canal médio, crop quadrado central, resize para `224`, escala final aproximada `[-1024, 1024]` | `normalize(img, maxval)` para `[-1024, 1024]`, canal único, crop central e resize |
| Saídas | 18 posições; algumas entradas de rótulo no config estão vazias e a UI exibe apenas os nomes disponíveis | Lista completa de até 18 patologias por modelo |
| Thresholds | `OP_POINT` por saída e remapeamento visual no JavaScript | `op_threshs` por peso, com `op_norm` equivalente |
| Explicabilidade | Gradientes e reconstrução/indicador OOD no navegador | Features, classificadores e modelos de referência; não é a mesma visualização do Chester |
| Dados | Não baixa datasets; analisa um arquivo por vez | Classes para datasets, metadados e composição de coortes |
| Dependências | TensorFlow.js e loaders/decodificadores JavaScript já servidos localmente | PyTorch, torchvision, scikit-image, NumPy, pandas, requests, Pillow, imageio e utilitários de pesquisa |

### Evidência de linhagem compartilhada

O arquivo de configuração local do Chester contém:

- `IMAGE_SIZE: 224`;
- `IMAGE_SCALE: 1024`;
- 18 valores de `OP_POINT`;
- o mesmo vetor de pontos operacionais publicado para o peso
  `densenet121-res224-all` do TorchXRayVision.

O diretório do modelo também usa o nome `xrv-all-45rot15trans15scale`, coerente
com essa origem. A conversão para TensorFlow.js pode ter alterado o formato,
os nomes internos das operações e o empacotamento dos pesos; por isso a
identidade deve ser confirmada comparando saídas em uma imagem fixa, e não
apenas pelos nomes dos diretórios.

## Pré-processamento e DICOM

### TorchXRayVision

O caminho típico para uma imagem comum é:

1. ler a imagem;
2. normalizar pelo valor máximo esperado para aproximadamente `[-1024, 1024]`;
3. converter para um canal;
4. aplicar crop central e resize;
5. criar um tensor com dimensão de batch e canal.

O utilitário `read_xray_dcm` usa pydicom, aceita interpretações
`MONOCHROME1` e `MONOCHROME2`, pode aplicar VOI LUT e devolve uma imagem 2D
normalizada. O pacote principal não lista pydicom entre as dependências
obrigatórias do arquivo `requirements.txt`; o caminho DICOM depende dessa
instalação adicional.

### Chester

O Chester decodifica o DICOM no navegador com Cornerstone e só então converte
o frame para PNG em memória. A implementação atual cobre imagens monocromáticas
de 8 ou 16 bits, inclui os codecs RLE, JPEG, JPEG-LS e JPEG 2000, aplica slope,
intercept, window center/width e inversão MONOCHROME1, e pausa para o usuário
escolher um frame em estudos multi-frame.

Essa diferença é importante: dois fluxos podem receber o mesmo arquivo DICOM e
ainda produzir pixels diferentes se usarem VOI LUT, windowing, crop, inversão
ou escala de intensidade diferentes. Para uma comparação válida, o pixel
normalizado deve ser salvo ou inspecionado antes de cada modelo.

## Compatibilidade prática

### Comparação offline: viabilidade alta

É o caminho mais simples. Um script Python pode executar TorchXRayVision; o
Chester pode continuar funcionando sem mudanças. A comparação deve usar a
mesma coleção de imagens, a mesma seleção de vista e uma especificação comum
de normalização.

### Serviço backend: viabilidade alta, mas muda o produto

Um serviço Python poderia receber imagens e usar os pesos PyTorch diretamente.
Isso facilita a pesquisa e evita conversão de modelo, mas quebra o princípio
atual de inferência local, cria uma superfície de upload de dados médicos e
exige tratar autenticação, armazenamento temporário, limites, observabilidade e
política de retenção. Nada disso deve ser introduzido apenas para comparar
modelos.

### Conversão para o navegador: viabilidade incerta

Seria necessário converter o modelo PyTorch para uma representação compatível
com o TensorFlow.js ou executar um runtime PyTorch no navegador. Depois seria
necessário testar operações, precisão numérica, memória, tempo de carregamento,
gradientes e os pesos completos. Também seria necessário distribuir os pesos e
revisar licenças/termos. Não existe no TorchXRayVision uma chamada de
JavaScript que substitua diretamente o GraphModel atual.

## Estratégia experimental recomendada

1. **Fixar o conjunto de entrada.** Use imagens de-identificadas e registre
   dataset, paciente, vista e resolução. Evite misturar PA, AP e lateral sem
   estratificação.
2. **Fixar a transformação.** Documente grayscale, MONOCHROME, VOI/LUT,
   windowing, rescale, crop e resize. Gere um arquivo intermediário ou hash
   dos pixels normalizados para garantir que ambos os caminhos recebam a mesma
   entrada.
3. **Comparar o par correto.** Comece com o modelo local do Chester e
   `densenet121-res224-all`, por serem os candidatos com maior evidência de
   linhagem compartilhada. Inclua depois variantes por dataset para estudar
   mudança de domínio.
4. **Alinhar rótulos.** Compare as 18 patologias pelo nome canônico do
   TorchXRayVision. As posições vazias do config do Chester não devem ser
   tratadas como rótulos equivalentes sem verificar a origem do modelo.
5. **Separar saídas.** Guarde logits, probabilidades após sigmoid e scores
   remapeados por `OP_POINT` em colunas distintas. Nunca compare um score
   normalizado como se fosse uma probabilidade calibrada.
6. **Medir generalização.** Relate AUROC, AUPRC, sensibilidade, especificidade,
   calibração e intervalos de confiança por dataset e por vista. Thresholds de
   um dataset não devem ser reutilizados automaticamente em outro.
7. **Registrar limitações.** O resultado é uma comparação de software/modelos
   para pesquisa. Não demonstra equivalência clínica nem autoriza uso
   diagnóstico.

## Fontes consultadas

- [TorchXRayVision no GitHub](https://github.com/mlmed/torchxrayvision)
- [README e exemplo de inferência](https://raw.githubusercontent.com/mlmed/torchxrayvision/main/README.md)
- [Documentação de modelos](https://mlmed.org/torchxrayvision/models.html)
- [Documentação de datasets](https://mlmed.org/torchxrayvision/datasets.html)
- [Helpers de datasets](https://mlmed.org/torchxrayvision/dataset_helpers.html)
- [Implementação dos modelos](https://raw.githubusercontent.com/mlmed/torchxrayvision/main/torchxrayvision/models.py)
- [Implementação dos datasets](https://raw.githubusercontent.com/mlmed/torchxrayvision/main/torchxrayvision/datasets.py)
- [Utilitários de imagem e DICOM](https://raw.githubusercontent.com/mlmed/torchxrayvision/main/torchxrayvision/utils.py)