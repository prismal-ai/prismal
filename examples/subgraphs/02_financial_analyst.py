"""
Financial Analyst Subgraph — Análisis completo de acciones bursátiles
======================================================================
Subgraph: prismal.agents.subgraphs.financial

Dataset: Yahoo Finance / Alpha Vantage (datos bursátiles reales)
  • Ticker symbols: NVDA, MSFT, AAPL, GOOGL (2023-2024)
  • Referencia: yfinance API + Alpha Vantage gratuita
  • Por qué: El subgraph Financial tiene 5 agentes especializados y 3
    quality gates. Los datos de acciones tech de alta capitalización
    son ideales para demostrar análisis técnico, fundamental, y de riesgo.

Descripción del subgraph Financial Analyst:
  market_data_collector → technical_analyst → fundamental_analyst
  → risk_sentiment_analyst → [HITL gate opcional] → report_generator

  Quality Gates:
  1. Data Gate     : tras market_data_collector — omite si confidence < threshold
  2. Technical Gate: tras technical_analyst — omite fundamental si muy noisy
  3. HITL Gate     : tras report_generator — requiere aprobación humana (opcional)

Nodos:
  1. market_data_collector  — precios, volumen, datos históricos
  2. technical_analyst      — patrones chartistas, indicadores de momentum
  3. fundamental_analyst    — P/E, earnings growth, balance sheet
  4. risk_sentiment_analyst — VIX, beta, sentimiento de mercado
  5. report_generator       — síntesis con recomendación BUY/HOLD/SELL

Uso:
    uv run python examples/subgraphs/02_financial_analyst.py
"""

from __future__ import annotations

import asyncio

from langchain_core.messages import HumanMessage

from prismal.agents.state import create_initial_state

# Importar con manejo de error por si el subgraph no está registrado
try:
    from prismal.agents.subgraphs.financial.builder import (
        get_compiled_financial_analyst,
        register_financial_analyst,
    )

    FINANCIAL_SUBGRAPH_AVAILABLE = True
except ImportError:
    FINANCIAL_SUBGRAPH_AVAILABLE = False

# ── Dataset: tickers y metadatos ──────────────────────────────────────────────
ANALYSIS_REQUESTS = [
    {
        "ticker": "NVDA",
        "company": "NVIDIA Corporation",
        "sector": "Semiconductors",
        "analysis_date": "2024-01-15",
        "timeframe": "6M",
        "analysis_focus": "AI chip demand and data center growth",
        "context": (
            "NVIDIA ha experimentado un crecimiento masivo impulsado por la demanda "
            "de GPUs para entrenamiento de modelos de IA. Las acciones subieron >200% "
            "en 2023. Analiza si el momentum se mantendrá o hay riesgo de corrección."
        ),
    },
    {
        "ticker": "MSFT",
        "company": "Microsoft Corporation",
        "sector": "Software / Cloud",
        "analysis_date": "2024-01-15",
        "timeframe": "1Y",
        "analysis_focus": "Azure AI integration and Copilot monetization",
        "context": (
            "Microsoft ha integrado IA en todos sus productos con GitHub Copilot, "
            "Azure OpenAI Service y Microsoft 365 Copilot. Evalúa el impacto en "
            "la valoración y las perspectivas de crecimiento de ingresos."
        ),
    },
]

# ── Datos simulados de mercado (formato que usaría el agente) ─────────────────
MOCK_MARKET_DATA = {
    "NVDA": {
        "current_price": 495.22,
        "52w_high": 505.48,
        "52w_low": 108.13,
        "market_cap": "1.22T",
        "volume_avg": "48.2M",
        "pe_ratio": 65.3,
        "eps_ttm": 7.58,
        "revenue_growth_yoy": 122.4,
        "gross_margin": 73.8,
        "beta": 1.74,
        "rsi_14": 62.3,
        "macd_signal": "bullish_crossover",
        "support_levels": [450, 420, 390],
        "resistance_levels": [500, 520, 550],
        "analyst_consensus": "BUY",
        "price_targets": {"low": 400, "median": 550, "high": 700},
    },
    "MSFT": {
        "current_price": 374.51,
        "52w_high": 384.30,
        "52w_low": 219.35,
        "market_cap": "2.78T",
        "volume_avg": "22.1M",
        "pe_ratio": 36.2,
        "eps_ttm": 10.34,
        "revenue_growth_yoy": 16.0,
        "gross_margin": 69.8,
        "beta": 0.89,
        "rsi_14": 58.1,
        "macd_signal": "neutral",
        "support_levels": [360, 340, 315],
        "resistance_levels": [380, 395, 420],
        "analyst_consensus": "BUY",
        "price_targets": {"low": 325, "median": 410, "high": 480},
    },
}


def format_analysis_request(req: dict) -> str:
    """Formatea la solicitud de análisis como mensaje para el agente."""
    data = MOCK_MARKET_DATA.get(req["ticker"], {})

    return (
        f"Analiza la acción {req['ticker']} ({req['company']}) para la fecha "
        f"{req['analysis_date']}.\n\n"
        f"Sector: {req['sector']}\n"
        f"Periodo de análisis: {req['timeframe']}\n"
        f"Foco: {req['analysis_focus']}\n\n"
        f"Contexto:\n{req['context']}\n\n"
        f"Datos de mercado disponibles:\n"
        f"  Precio actual: ${data.get('current_price', 'N/A')}\n"
        f"  Cap. de mercado: {data.get('market_cap', 'N/A')}\n"
        f"  P/E Ratio: {data.get('pe_ratio', 'N/A')}\n"
        f"  Crecimiento ingresos YoY: {data.get('revenue_growth_yoy', 'N/A')}%\n"
        f"  Beta: {data.get('beta', 'N/A')}\n"
        f"  RSI(14): {data.get('rsi_14', 'N/A')}\n"
        f"  MACD: {data.get('macd_signal', 'N/A')}\n"
        f"  Consenso analistas: {data.get('analyst_consensus', 'N/A')}\n\n"
        f"Genera un análisis completo con recomendación BUY/HOLD/SELL y precio objetivo."
    )


async def run_financial_analysis(request: dict) -> None:
    """Ejecuta el subgraph de análisis financiero.

    Args:
        request: Solicitud de análisis con ticker y metadatos.
    """
    print(f"\n[Análisis: {request['ticker']} — {request['company']}]")
    print(f"  Sector  : {request['sector']}")
    print(f"  Fecha   : {request['analysis_date']}")
    print(f"  Foco    : {request['analysis_focus']}")

    if not FINANCIAL_SUBGRAPH_AVAILABLE:
        # Modo demo: mostrar qué haría el subgraph
        print("\n  [Modo demo — subgraph simulado]")
        market_data = MOCK_MARKET_DATA.get(request["ticker"], {})

        print("\n  [Nodo 1: market_data_collector]")
        print(f"    Precio: ${market_data.get('current_price')}")
        print(f"    Cap: {market_data.get('market_cap')}")
        print("    ✓ Data Gate: confidence alta → procede")

        print("\n  [Nodo 2: technical_analyst]")
        print(
            f"    RSI(14): {market_data.get('rsi_14')} ({'sobrecomprado' if market_data.get('rsi_14', 0) > 70 else 'neutral'})"
        )
        print(f"    MACD: {market_data.get('macd_signal')}")
        sups = market_data.get("support_levels", [])
        resis = market_data.get("resistance_levels", [])
        print(f"    Soportes: {sups[:2]}")
        print(f"    Resistencias: {resis[:2]}")
        print("    ✓ Technical Gate: señales claras → procede a fundamental")

        print("\n  [Nodo 3: fundamental_analyst]")
        print(f"    P/E Ratio: {market_data.get('pe_ratio')} (sector avg: ~30)")
        print(f"    EPS TTM: ${market_data.get('eps_ttm')}")
        print(f"    Revenue growth: {market_data.get('revenue_growth_yoy')}% YoY")
        print(f"    Gross Margin: {market_data.get('gross_margin')}%")

        print("\n  [Nodo 4: risk_sentiment_analyst]")
        beta = market_data.get("beta", 1.0)
        risk_level = "ALTO" if beta > 1.5 else "MEDIO" if beta > 1.0 else "BAJO"
        print(f"    Beta: {beta} → Riesgo relativo al mercado: {risk_level}")
        print(
            f"    Precio/52w Max: {(market_data.get('current_price', 0) / market_data.get('52w_high', 1) * 100):.1f}%"
        )

        print("\n  [Nodo 5: report_generator]")
        consensus = market_data.get("analyst_consensus", "HOLD")
        target_med = market_data.get("price_targets", {}).get("median", 0)
        current = market_data.get("current_price", 0)
        upside = ((target_med - current) / current * 100) if current > 0 else 0
        print(f"    Recomendación: {consensus}")
        print(f"    Precio objetivo (mediana): ${target_med}")
        print(f"    Potencial de subida: {upside:+.1f}%")
        print(f"    Targets analistas: {market_data.get('price_targets')}")
        return

    # Modo real con subgraph
    await register_financial_analyst()
    subgraph = await get_compiled_financial_analyst()

    state = create_initial_state(session_id=f"example-financial-{request['ticker']}")
    state["messages"] = [HumanMessage(content=format_analysis_request(request))]
    state["metadata"] = {
        "ticker": request["ticker"],
        "market_data": MOCK_MARKET_DATA.get(request["ticker"], {}),
        "hitl_enabled": False,  # desactivar aprobación humana para el ejemplo
    }

    config = {"configurable": {"thread_id": f"fin_{request['ticker']}_001"}}
    final_state = await subgraph.ainvoke(state, config=config)

    messages = final_state.get("messages", [])
    if messages:
        print("\n  Informe generado:")
        print(f"  {str(messages[-1].content)[:500]}")


async def main() -> None:
    print("=" * 70)
    print("  Financial Analyst Subgraph — Dataset: Yahoo Finance (NVDA, MSFT)")
    print("=" * 70)

    # Arquitectura del subgraph
    print("\n[Arquitectura del subgraph Financial]")
    nodes_and_gates = [
        ("market_data_collector", "Recopila precios, volumen, histórico"),
        ("[Data Gate]           ", "confidence >= threshold?"),
        ("technical_analyst     ", "RSI, MACD, Bollinger, patrones"),
        ("[Technical Gate]      ", "señal técnica suficientemente clara?"),
        ("fundamental_analyst   ", "P/E, EPS, revenue growth, balances"),
        ("risk_sentiment_analyst", "beta, VIX, sentimiento"),
        ("[HITL Gate opcional]  ", "¿requiere aprobación humana?"),
        ("report_generator      ", "síntesis + recomendación BUY/HOLD/SELL"),
    ]
    for node, desc in nodes_and_gates:
        print(f"  {node}: {desc}")

    # Ejecutar análisis para cada ticker
    for request in ANALYSIS_REQUESTS:
        await run_financial_analysis(request)
        print("─" * 70)

    # Información adicional sobre los gates
    print("\n[Quality Gates del subgraph]")
    print("  Gate 1 (Data Availability):")
    print("    Si missing_fields > 0 AND confidence < min_threshold")
    print("    → Salta directamente al report (datos insuficientes)")
    print()
    print("  Gate 2 (Technical Confidence):")
    print("    Si señal técnica demasiado ruidosa")
    print("    → Salta análisis fundamental (evita análisis sin base)")
    print()
    print("  Gate 3 (HITL):")
    print("    Si hitl_enabled=True → pausa y espera aprobación humana")
    print("    Configurable: settings.hitl_enabled = False para omitir")


if __name__ == "__main__":
    asyncio.run(main())
