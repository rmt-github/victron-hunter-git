# Price Hunter v3 — Guia de Setup

Monitor automático OLX + Wallapop com alertas via **Ntfy** (push gratuito).

---

## Passo 1 — Instalar a app Ntfy (2 minutos)

1. Instale **ntfy** no telemóvel:
   - Android: [Play Store](https://play.google.com/store/apps/details?id=io.heckel.ntfy)
   - iOS: [App Store](https://apps.apple.com/app/ntfy/id1625396347)

2. Abra a app → toque em **"+"** → escreva o nome do seu canal

   O canal é apenas um nome único que só você conhece, ex:
   ```
   price-hunter-joao-2024
   ```
   > Use um nome difícil de adivinhar — qualquer pessoa com o nome pode subscrever.

3. Guarde esse nome — vai precisar dele na Railway.

---

## Passo 2 — Colocar na Railway

1. Crie conta em **railway.app** (login com GitHub)
2. **New Project → Deploy from GitHub repo** (faça upload desta pasta)
3. Em **Variables**, adicione:

| Variável | Exemplo | Descrição |
|---|---|---|
| `NTFY_CHANNEL` | `price-hunter-joao-2024` | Nome do canal Ntfy |
| `MIN_MARGIN` | `30` | Margem mínima para alertar (%) |
| `CHECK_INTERVAL` | `900` | Intervalo entre pesquisas (segundos) |
| `WALLAPOP_LAT` | `38.7169` | Latitude (padrão: centro de Portugal) |
| `WALLAPOP_LNG` | `-9.1395` | Longitude |
| `WALLAPOP_DIST_KM` | `400` | Raio de pesquisa em km |

4. O deploy arranca automaticamente. Veja os logs em tempo real.

---

## Como são as notificações

Cada alerta chega ao telemóvel com:
- **Título:** emoji de urgência + nome do produto
- **Corpo:** nicho, fonte (OLX/Wallapop), preço e margem estimada
- **Botão:** "Ver anúncio" — abre diretamente o link

A prioridade da notificação varia com a margem:
| Margem | Prioridade | Comportamento |
|---|---|---|
| ≥ 80% | 🔥 Urgente (5) | Vibra mesmo em modo silêncio |
| 50–80% | ✅ Alta (4) | Notificação destacada |
| 30–50% | 👀 Normal (3) | Notificação standard |

---

## Testar localmente

```bash
pip install -r requirements.txt

# Sem Ntfy (alertas na consola)
python src/hunter.py

# Com Ntfy
NTFY_CHANNEL=o-meu-canal python src/hunter.py
```

---

## Personalizar nichos e preços de mercado

Em `src/hunter.py`, secção `NICHOS`, ajuste o `preco_mercado` de cada termo
ao valor a que **consegue revender** — é a base do cálculo de margem.

```python
{"query": "victron multiplus", "preco_mercado": 350},
#                                               ^^^
#                  Afine com base nas suas vendas reais
```

---

## Estrutura do projeto

```
price-hunter/
├── src/
│   └── hunter.py        ← código principal
├── data/
│   └── seen_ads.json    ← anúncios já vistos (criado automaticamente)
├── requirements.txt
├── railway.toml
└── SETUP.md             ← este ficheiro
```
