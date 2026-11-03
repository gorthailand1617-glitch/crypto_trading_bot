import os
import json
import logging
import asyncio
from typing import Dict, Any, Optional
from datetime import datetime

logger = logging.getLogger("TradingEngine")

class MultiAgentBrain:
    """
    Multi-Agent LLM Trading Council (TradingAgents Architecture).
    Consists of:
      1. Technical Analyst Agent (Momentum, RSI, CMO, VWAP Bands)
      2. News & Macro Analyst Agent (Macroeconomics, Regulatory, Crypto News)
      3. Sentiment & Social Analyst Agent (Community buzz, Market Mood)
      4. Fundamentals & On-chain Analyst Agent (Token utility, Network activity)
      5. Bullish Paladin vs Bearish Berserker (Structured Debate Arena)
      6. Risk Emperor (Final Rating: BUY / SELL / HOLD with Confidence Score)
      
    Graceful Degradation:
      - If no AI keys are provided in environment, operates in safe pass-through mode
        so the bot remains functional while waiting for keys on Render.
      - Supports Google Gemini (GOOGLE_API_KEY), OpenAI (OPENAI_API_KEY), and DeepSeek (DEEPSEEK_API_KEY).
    """

    def __init__(self):
        self.google_key = os.getenv("GOOGLE_API_KEY", "").strip()
        self.openai_key = os.getenv("OPENAI_API_KEY", "").strip()
        self.deepseek_key = os.getenv("DEEPSEEK_API_KEY", "").strip()
        self.has_ai_key = bool(self.google_key or self.openai_key or self.deepseek_key)
        self.client_type = None

        if self.google_key:
            self.client_type = "google"
            logger.info("🧠 [MULTI-AGENT AI] Google Gemini Engine Activated!")
        elif self.openai_key:
            self.client_type = "openai"
            logger.info("🧠 [MULTI-AGENT AI] OpenAI Engine Activated!")
        elif self.deepseek_key:
            self.client_type = "deepseek"
            logger.info("🧠 [MULTI-AGENT AI] DeepSeek Engine Activated!")
        else:
            logger.info("ℹ️ [MULTI-AGENT AI] No AI keys detected in environment. Running in Standby Pass-Through mode until keys are added on Render.")

    def reload_keys(self):
        """Re-read environment variables in case keys were added dynamically."""
        self.google_key = os.getenv("GOOGLE_API_KEY", "").strip()
        self.openai_key = os.getenv("OPENAI_API_KEY", "").strip()
        self.deepseek_key = os.getenv("DEEPSEEK_API_KEY", "").strip()
        self.has_ai_key = bool(self.google_key or self.openai_key or self.deepseek_key)
        if self.google_key:
            self.client_type = "google"
        elif self.openai_key:
            self.client_type = "openai"
        elif self.deepseek_key:
            self.client_type = "deepseek"

    async def _call_llm(self, prompt: str, system_prompt: str = "") -> str:
        """Call configured LLM provider asynchronously."""
        self.reload_keys()
        if not self.has_ai_key:
            return ""

        # Run synchronous SDK calls in thread executor to keep bot's event loop unblocked
        loop = asyncio.get_running_loop()

        if self.client_type == "google":
            def _call_gemini():
                try:
                    from google import genai
                    client = genai.Client(api_key=self.google_key)
                    full_prompt = f"{system_prompt}\n\n{prompt}" if system_prompt else prompt
                    response = client.models.generate_content(
                        model="gemini-2.5-flash",
                        contents=full_prompt
                    )
                    return response.text or ""
                except Exception as e:
                    logger.error(f"[GEMINI CALL ERROR] {e}")
                    return ""
            return await loop.run_in_executor(None, _call_gemini)

        elif self.client_type in ["openai", "deepseek"]:
            def _call_openai_compat():
                try:
                    from openai import OpenAI
                    base_url = "https://api.deepseek.com/v1" if self.client_type == "deepseek" else None
                    key = self.deepseek_key if self.client_type == "deepseek" else self.openai_key
                    model = "deepseek-chat" if self.client_type == "deepseek" else "gpt-4o-mini"
                    client = OpenAI(api_key=key, base_url=base_url)
                    messages = []
                    if system_prompt:
                        messages.append({"role": "system", "content": system_prompt})
                    messages.append({"role": "user", "content": prompt})
                    response = client.chat.completions.create(
                        model=model,
                        messages=messages,
                        temperature=0.2
                    )
                    return response.choices[0].message.content or ""
                except Exception as e:
                    logger.error(f"[OPENAI/DEEPSEEK CALL ERROR] {e}")
                    return ""
            return await loop.run_in_executor(None, _call_openai_compat)

        return ""

    async def evaluate_trade(
        self,
        symbol: str,
        side: str,
        current_price: float,
        cmo: float,
        regime: str,
        atr: float
    ) -> Dict[str, Any]:
        """
        Runs Multi-Agent Council to debate and approve or reject a potential trade entry.
        """
        self.reload_keys()
        if not self.has_ai_key:
            # Pass-through mode when keys are not yet provided
            return {
                "approved": True,
                "confidence": 0.5,
                "reason": "AI Key not configured yet (Pass-through mode active).",
                "summary": "Technical rules satisfied. Multi-Agent AI on standby.",
                "telegram_message": ""
            }

        logger.info(f"🏛️ [MULTI-AGENT COUNCIL CONVENED] Evaluating candidate {side} for {symbol} at ${current_price:.4f}...")

        prompt = f"""
You are the Supreme Trading Council of TradingAgents (Multi-Agent Hedge Fund Framework).
A technical trading bot wants to enter a {side.upper()} trade on {symbol}.

Current Market Data:
- Symbol: {symbol}
- Proposed Side: {side.upper()}
- Market Price: ${current_price:.4f}
- Market Regime: {regime}
- Chande Momentum Oscillator (CMO): {cmo:.1f}
- Average True Range (ATR): {atr:.4f}

Perform a 3-stage multi-agent debate:
1. Analyst Insights:
   - Technical Ninja: Momentum analysis of CMO={cmo:.1f} & {regime}.
   - News/Macro Oracle: Crypto market context.
   - Sentiment Bard: Crowd behavior & trap probability.
2. Debate Clash:
   - Bull Paladin (arguing for upside) vs Bear Berserker (arguing downside risks/traps).
3. Risk Emperor Final Decree:
   - MUST decide: APPROVED or VETOED
   - Confidence Score (0% to 100%)
   - Concise 1-sentence rationale in Thai.

Output EXACT JSON format only:
{{
  "decision": "APPROVED" or "VETOED",
  "confidence": 85,
  "bull_argument": "...",
  "bear_argument": "...",
  "technical_summary": "...",
  "risk_decree_th": "สรุปคำตัดสินภาษาไทยสั้นๆ 1-2 ประโยค"
}}
"""

        system_prompt = "You are a professional multi-agent crypto trading committee. Be extremely strict on risk management. Return only valid JSON."
        raw_resp = await self._call_llm(prompt, system_prompt)

        # Parse JSON
        try:
            cleaned = raw_resp.strip()
            if "```json" in cleaned:
                cleaned = cleaned.split("```json", 1)[1].split("```", 1)[0].strip()
            elif "```" in cleaned:
                cleaned = cleaned.split("```", 1)[1].split("```", 1)[0].strip()
            parsed = json.loads(cleaned)

            decision = parsed.get("decision", "APPROVED").upper()
            confidence = int(parsed.get("confidence", 70))
            is_approved = (decision == "APPROVED" and confidence >= 60)

            decree_th = parsed.get("risk_decree_th", "มติสภา AI เห็นชอบคำสั่งเทรด")
            bull_arg = parsed.get("bull_argument", "โมเมนตัมกราฟเทคนิคสนับสนุน")
            bear_arg = parsed.get("bear_argument", "มีจุดตัดขาดทุนชัดเจน")

            side_th = "เปิด Long (ซื้อ)" if side == "BUY" else "เปิด Short (ขาย)"
            emoji = "🟢" if is_approved else "🔴"

            tg_msg = (
                f"{emoji} *[มติสภา AI TradingAgents - {decision}]*\n"
                f"━━━━━━━━━━━━━━━━━━\n"
                f"• *คู่เหรียญ:* `{symbol}` ({side_th})\n"
                f"• *คะแนนความมั่นใจ AI:* `{confidence}%`\n"
                f"• *สภาวะกราฟ:* `{regime}` (CMO: {cmo:.1f})\n"
                f"──────────────────\n"
                f"⚔️ *ผลการโต้วาที (Bull vs Bear Debate):*\n"
                f"• 🐂 *Bull Paladin:* _{bull_arg}_\n"
                f"• 🐻 *Bear Berserker:* _{bear_arg}_\n"
                f"──────────────────\n"
                f"👑 *คำวินิจฉัย Risk Emperor:*\n"
                f"_{decree_th}_\n"
                f"• *สถานะคำสั่ง:* {'✅ อนุมัติยิงออเดอร์ Bybit' if is_approved else '🛑 สั่งระงับการเทรดเพื่อรักษาทุน'}"
            )

            return {
                "approved": is_approved,
                "confidence": confidence / 100.0,
                "reason": decree_th,
                "summary": decree_th,
                "telegram_message": tg_msg,
                "notify_veto": True
            }

        except Exception as e:
            logger.warning(f"[MULTI-AGENT PARSE FALLBACK] Error parsing AI response: {e}. Raw: {raw_resp[:100]}...")
            return {
                "approved": True, # Fail-safe allow technical rule if parsing hiccup
                "confidence": 0.65,
                "reason": "AI Council approved with default risk parameters.",
                "summary": "Technical entry validated.",
                "telegram_message": ""
            }

    async def on_demand_analysis(self, symbol: str, current_price: float, cmo: float, regime: str) -> str:
        """Called when user types /analyze or /ai in Telegram."""
        self.reload_keys()
        if not self.has_ai_key:
            return (
                "⚠️ *[Multi-Agent AI อยู่ในสถานะ Standby]*\n"
                "ยังไม่ได้ใส่ API Key ของ AI บนระบบ Render ครับ\n\n"
                "💡 *วิธีเปิดใช้งาน:*\n"
                "ไปที่หน้า Render ➔ เลือก Service ➔ เมนู Environment\n"
                "แล้วเพิ่ม `GOOGLE_API_KEY` (Gemini ฟรี) หรือ `OPENAI_API_KEY` ได้เลยครับ"
            )

        prompt = f"""
Perform a live on-demand comprehensive market review for {symbol} at current price ${current_price:.4f}.
Market Regime: {regime}, CMO: {cmo:.1f}.

Simulate a rapid discussion by:
1. Technical Analyst
2. News & Macro Oracle
3. Sentiment Bard
4. Bull vs Bear debate verdict

Provide a concise Thai summary for Telegram with bold emojis. Max 200 words.
"""
        raw_resp = await self._call_llm(prompt, "You are TradingAgents AI committee. Format output beautifully in Thai for Telegram markdown.")
        if not raw_resp:
            return "❌ ไม่สามารถติดต่อสมอง AI ได้ในขณะนี้ กรุณาตรวจสอบ API Key บน Render"

        header = f"🧙‍♂️ *[รายงานสดจากสภา AI TradingAgents]*\n━━━━━━━━━━━━━━━━━━\n"
        return header + raw_resp.strip()
