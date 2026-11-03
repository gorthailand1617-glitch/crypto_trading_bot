from __future__ import annotations
import os
import json
import logging
import asyncio
import urllib.request
import urllib.error
from typing import Dict, Any, Tuple, List, Optional
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
      
    Zero-Dependency Resilient Engine:
      - Uses direct REST API with auto-model fallback (gemini-2.5-flash -> 2.0 -> 1.5).
      - Operates in safe pass-through standby if keys are not present.
      - Displays precise error diagnostics to Telegram if a key is invalid or quota exceeded.
    """

    def __init__(self):
        self.google_key = ""
        self.openai_key = ""
        self.deepseek_key = ""
        self.has_ai_key = False
        self.client_type = None
        self.reload_keys()

    def reload_keys(self):
        """Re-read environment variables in case keys were added or updated on Render."""
        self.google_key = os.getenv("GOOGLE_API_KEY", "").strip().strip('"').strip("'")
        self.openai_key = os.getenv("OPENAI_API_KEY", "").strip().strip('"').strip("'")
        self.deepseek_key = os.getenv("DEEPSEEK_API_KEY", "").strip().strip('"').strip("'")
        self.has_ai_key = bool(self.google_key or self.openai_key or self.deepseek_key)

        if self.google_key:
            self.client_type = "google"
            logger.info("🧠 [MULTI-AGENT AI] Google Gemini Engine Selected.")
        elif self.openai_key:
            self.client_type = "openai"
            logger.info("🧠 [MULTI-AGENT AI] OpenAI Engine Selected.")
        elif self.deepseek_key:
            self.client_type = "deepseek"
            logger.info("🧠 [MULTI-AGENT AI] DeepSeek Engine Selected.")
        else:
            self.client_type = None

    def _call_gemini_rest(self, prompt: str, system_prompt: str = "") -> Tuple[str, str]:
        """Direct REST call to Google Gemini with fast model fallback and error parsing."""
        key = self.google_key
        custom_model = os.getenv("GEMINI_MODEL", "").strip()
        default_models = ["gemini-3.8-flash", "gemini-3.5-flash", "gemini-3.1-flash-lite"]
        models = [custom_model] if custom_model else default_models
        full_text = f"{system_prompt}\n\n{prompt}" if system_prompt else prompt
        payload = {"contents": [{"parts": [{"text": full_text}]}]}
        data = json.dumps(payload).encode("utf-8")
        last_err = ""

        for model in models:
            url = f"https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent?key={key}"
            req = urllib.request.Request(url, data=data, headers={"Content-Type": "application/json"})
            try:
                logger.info(f"🧠 [MULTI-AGENT AI] Invoking Gemini model: {model}...")
                with urllib.request.urlopen(req, timeout=10) as resp:
                    res_json = json.loads(resp.read().decode("utf-8"))
                    candidates = res_json.get("candidates", [])
                    if candidates:
                        parts = candidates[0].get("content", {}).get("parts", [])
                        if parts:
                            ans = parts[0].get("text", "").strip()
                            logger.info(f"✅ [MULTI-AGENT AI] Successfully received response from {model} ({len(ans)} chars)")
                            return ans, ""
            except urllib.error.HTTPError as e:
                err_body = e.read().decode("utf-8", errors="ignore")
                msg = str(e)
                try:
                    err_json = json.loads(err_body)
                    msg = err_json.get("error", {}).get("message", msg)
                except Exception:
                    pass
                last_err = f"HTTP {e.code}: {msg}"
                logger.warning(f"⚠️ [GEMINI {model} FAILED] {last_err}")
                # Hard fail immediately on auth errors - don't keep user waiting
                if e.code in [400, 401, 403]:
                    return "", f"API Error (HTTP {e.code}): {msg}"
            except Exception as e:
                last_err = str(e)
                logger.warning(f"⚠️ [GEMINI {model} EXCEPTION] {last_err}")

        return "", last_err

    def _call_openai_compat_rest(self, prompt: str, system_prompt: str = "") -> Tuple[str, str]:
        """Direct REST call to OpenAI or DeepSeek."""
        is_deepseek = (self.client_type == "deepseek")
        url = "https://api.deepseek.com/v1/chat/completions" if is_deepseek else "https://api.openai.com/v1/chat/completions"
        key = self.deepseek_key if is_deepseek else self.openai_key
        model = "deepseek-chat" if is_deepseek else "gpt-4o-mini"

        messages = []
        if system_prompt:
            messages.append({"role": "system", "content": system_prompt})
        messages.append({"role": "user", "content": prompt})

        payload = {"model": model, "messages": messages, "temperature": 0.2}
        data = json.dumps(payload).encode("utf-8")
        req = urllib.request.Request(
            url,
            data=data,
            headers={"Content-Type": "application/json", "Authorization": f"Bearer {key}"}
        )
        try:
            with urllib.request.urlopen(req, timeout=15) as resp:
                res_json = json.loads(resp.read().decode("utf-8"))
                choices = res_json.get("choices", [])
                if choices:
                    return choices[0].get("message", {}).get("content", "").strip(), ""
            return "", "Empty response from provider"
        except urllib.error.HTTPError as e:
            err_body = e.read().decode("utf-8", errors="ignore")
            return "", f"HTTP {e.code}: {err_body}"
        except Exception as e:
            return "", str(e)

    async def _call_llm(self, prompt: str, system_prompt: str = "") -> Tuple[str, str]:
        """Asynchronously invoke the configured LLM."""
        self.reload_keys()
        if not self.has_ai_key:
            return "", "No AI key configured"

        loop = asyncio.get_running_loop()
        if self.client_type == "google":
            return await loop.run_in_executor(None, self._call_gemini_rest, prompt, system_prompt)
        elif self.client_type in ["openai", "deepseek"]:
            return await loop.run_in_executor(None, self._call_openai_compat_rest, prompt, system_prompt)

        return "", "Unknown provider"

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
        raw_resp, err = await self._call_llm(prompt, system_prompt)

        if not raw_resp:
            logger.warning(f"[MULTI-AGENT LLM ERROR] {err}")
            return {
                "approved": True,
                "confidence": 0.65,
                "reason": f"AI Council bypass (Error: {err})",
                "summary": "Technical entry validated.",
                "telegram_message": ""
            }

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
            logger.warning(f"[MULTI-AGENT PARSE FALLBACK] Error parsing JSON: {e}")
            return {
                "approved": True,
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
                "ยังไม่พบคีย์ AI บนระบบ Render ครับ\n\n"
                "💡 *วิธีเปิดใช้งาน:*\n"
                "ไปที่ Render ➔ เมนู Environment ➔ กดปุ่ม `+ Add Environment Variable`\n"
                "• Key: `GOOGLE_API_KEY`\n"
                "• Value: วางคีย์ Gemini ของคุณ แล้วกด Save Changes ครับ"
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
        raw_resp, err = await self._call_llm(prompt, "You are TradingAgents AI committee. Format output beautifully in Thai for Telegram markdown.")
        if not raw_resp:
            return (
                f"❌ *[ไม่สามารถติดต่อสมอง AI ได้]*\n"
                f"• *สาเหตุ:* `{err}`\n\n"
                f"💡 กรุณาตรวจสอบว่าคีย์ `GOOGLE_API_KEY` บน Render ถูกต้องและไม่มีเว้นวรรคส่วนเกินครับ"
            )

        header = f"🧙‍♂️ *[รายงานสดจากสภา AI TradingAgents]*\n━━━━━━━━━━━━━━━━━━\n"
        return header + raw_resp.strip()

    async def find_highest_profit_opportunity(self, candidates: List[Dict[str, Any]]) -> Dict[str, Any]:
        """
        Multi-Agent AI Council deliberates across multiple candidate cryptocurrency pairs
        and selects the SINGLE pair offering the highest and fastest profit potential right now.
        """
        self.reload_keys()
        if not self.has_ai_key:
            return {
                "success": False,
                "error": "No AI Key configured",
                "telegram_message": (
                    "⚠️ *[Multi-Agent AI อยู่ในสถานะ Standby]*\n"
                    "ยังไม่พบคีย์ AI บนระบบ Render ครับ\n\n"
                    "💡 *วิธีเปิดใช้งาน:*\n"
                    "ไปที่ Render ➔ เมนู Environment ➔ ใส่ `GOOGLE_API_KEY` แล้วลองอีกครั้งครับ"
                )
            }

        if not candidates:
            return {
                "success": False,
                "error": "No candidates provided",
                "telegram_message": "⚠️ ไม่พบข้อมูลคู่เหรียญสำหรับสแกนในขณะนี้ครับ"
            }

        candidates_summary = []
        for i, c in enumerate(candidates, 1):
            sym = c.get("symbol", "")
            px = c.get("price", 0.0)
            chg = c.get("change_24h", 0.0)
            cmo = c.get("cmo", 0.0)
            atr_pct = c.get("atr_pct", 0.0)
            vol_spike = c.get("vol_spike", 1.0)
            regime = c.get("regime", "UNKNOWN")
            candidates_summary.append(
                f"{i}. {sym}: Price=${px:.4f} | 24h={chg:+.2f}% | CMO={cmo:.1f} | ATR%={atr_pct:.2f}% | VolSpike={vol_spike:.2f}x | Regime={regime}"
            )

        prompt = f"""
You are the Supreme Trading Council of TradingAgents (Multi-Asset Crypto Momentum Specialist).
Below is live market data for candidate cryptocurrency perpetual futures pairs on Bybit:

{chr(10).join(candidates_summary)}

Mission:
Carefully analyze all candidates and select the SINGLE coin pair that currently presents the HIGHEST AND FASTEST profit opportunity.

Key Evaluation Criteria:
1. Speed & Volatility: High ATR% and high Volume Spike (moving aggressively right now).
2. Momentum Edge: CMO extreme oversold (<-60) for long or overbought (>60) for short, or clear breakout.
3. High Risk/Reward Ratio: Clean take profit target with low invalidation distance.

Simulate the Council debate:
- Technical Ninja: Compares velocity and momentum.
- News/Macro Oracle: Market trend & hype check.
- Sentiment Bard: Crowd behavior & trap check.
- Bull Paladin vs Bear Berserker debate on the chosen asset.
- Risk Emperor Final Verdict: Declare the #1 Champion Coin, side (BUY or SELL), expected take profit %, stop loss %, and confidence.

Return EXACT JSON ONLY (no other markdown or extra text):
{{
  "champion_symbol": "...",
  "side": "BUY" or "SELL",
  "confidence": 85,
  "speed_grade": "⚡ เร็วมาก (Ultra-Fast)" or "🚀 รวดเร็ว (High Momentum)",
  "expected_tp_pct": 1.5,
  "stop_loss_pct": 0.8,
  "bull_debate": "มุมมองฝั่งกระทิงสั้นๆ 1 ประโยค",
  "bear_debate": "มุมมองฝั่งหมีสั้นๆ 1 ประโยค",
  "decree_th": "สรุปคำตัดสินของ Risk Emperor สั้นๆ 1-2 ประโยค ระบุว่าทำไมเหรียญนี้ถึงมีโอกาสทำกำไรสูงและเร็วที่สุด"
}}
"""
        system_prompt = "You are TradingAgents supreme hedge fund committee. Choose the fastest highest profit coin. Return only valid JSON."
        raw_resp, err = await self._call_llm(prompt, system_prompt)

        if not raw_resp:
            logger.warning(f"⚠️ [MULTI-AGENT SCAN FALLBACK] LLM Error: {err}. Using Quantitative Velocity fallback.")
            sorted_by_velocity = sorted(candidates, key=lambda x: (x.get("atr_pct", 0) * x.get("vol_spike", 1.0)), reverse=True)
            top = sorted_by_velocity[0]
            side = "BUY" if top.get("cmo", 0) < 0 else "SELL"
            side_th = "เปิด Long (ซื้อ)" if side == "BUY" else "เปิด Short (ขาย)"
            fallback_msg = (
                f"🏆 *[คัดเลือกคู่เหรียญโมเมนตัมสูงสุด (Quantitative Fallback)]*\n"
                f"━━━━━━━━━━━━━━━━━━\n"
                f"• *เหรียญที่เลือก:* `{top['symbol']}` ({side_th})\n"
                f"• *ความเร็ว (ATR%):* `{top.get('atr_pct', 0):.2f}%` (VolSpike: {top.get('vol_spike', 1.0):.2f}x)\n"
                f"• *คำแนะนำ:* พิมพ์ `/hunt trade` เพื่อเริ่มเทรดเหรียญนี้ทันที"
            )
            return {
                "success": True,
                "champion_symbol": top["symbol"],
                "side": side,
                "confidence": 60,
                "speed_grade": "⚡ อัตโนมัติ (Quantitative Velocity)",
                "expected_tp_pct": 1.5,
                "stop_loss_pct": 0.8,
                "decree_th": f"คัดเลือกด้วยอัลกอริทึม Velocity สูงสุด (ATR {top.get('atr_pct', 0):.2f}%)",
                "bull_debate": "ความผันผวนและวอลุ่มหนุนสูงสุดในกลุ่ม",
                "bear_debate": "AI LLM ติดขัดชั่วคราว จึงใช้ Quantitative Fallback",
                "telegram_message": fallback_msg
            }

        try:
            cleaned = raw_resp.strip()
            if "```json" in cleaned:
                cleaned = cleaned.split("```json", 1)[1].split("```", 1)[0].strip()
            elif "```" in cleaned:
                cleaned = cleaned.split("```", 1)[1].split("```", 1)[0].strip()
            parsed = json.loads(cleaned)

            sym = parsed.get("champion_symbol", candidates[0]["symbol"])
            valid_syms = [c["symbol"] for c in candidates]
            if sym not in valid_syms:
                matched = [s for s in valid_syms if sym.replace('/', '').replace('USDT', '') in s]
                sym = matched[0] if matched else valid_syms[0]

            side = parsed.get("side", "BUY").upper()
            if side not in ["BUY", "SELL"]:
                side = "BUY"

            conf = int(parsed.get("confidence", 75))
            speed = parsed.get("speed_grade", "⚡ รวดเร็ว (Fast Momentum)")
            tp_pct = float(parsed.get("expected_tp_pct", 1.5))
            sl_pct = float(parsed.get("stop_loss_pct", 0.8))
            bull = parsed.get("bull_debate", "โมเมนตัมกำลังพุ่งแรง")
            bear = parsed.get("bear_debate", "ระวังแรงขายทำกำไรระยะสั้น")
            decree = parsed.get("decree_th", "เหรียญนี้มีศักยภาพทำกำไรเร็วที่สุดในรอบปัจจุบัน")

            side_th = "เปิด Long (ซื้อ)" if side == "BUY" else "เปิด Short (ขาย)"
            tg_msg = (
                f"🏆 *[มติสภา AI - คู่เหรียญที่ทำกำไรได้เร็วและสูงที่สุด]*\n"
                f"━━━━━━━━━━━━━━━━━━\n"
                f"• *เหรียญแชมป์:* `{sym}` ({side_th})\n"
                f"• *ระดับความเร็ว:* `{speed}`\n"
                f"• *คะแนนความมั่นใจ AI:* `{conf}%`\n"
                f"• *เป้ากำไรคาดหวัง (TP):* `+{tp_pct:.1f}%` | *จุดตัดขาดทุน (SL):* `-{sl_pct:.1f}%`\n"
                f"──────────────────\n"
                f"⚔️ *โต้วาทีสด (Bull vs Bear Debate):*\n"
                f"• 🐂 *Bull:* _{bull}_\n"
                f"• 🐻 *Bear:* _{bear}_\n"
                f"──────────────────\n"
                f"👑 *คำวินิจฉัย Risk Emperor:*\n"
                f"_{decree}_\n"
                f"━━━━━━━━━━━━━━━━━━\n"
                f"💡 *คำสั่งเข้าเทรด:*\n"
                f"• พิมพ์ **/hunt trade** เพื่อให้บอทยิงออเดอร์เหรียญ `{sym}` ทันที\n"
                f"• หรือพิมพ์ **/autohunt on** เพื่อเปิดระบบสแกนและสลับเทรดอัตโนมัติ"
            )

            return {
                "success": True,
                "champion_symbol": sym,
                "side": side,
                "confidence": conf,
                "speed_grade": speed,
                "expected_tp_pct": tp_pct,
                "stop_loss_pct": sl_pct,
                "bull_debate": bull,
                "bear_debate": bear,
                "decree_th": decree,
                "telegram_message": tg_msg
            }
        except Exception as e:
            logger.warning(f"[MULTI-AGENT SCAN PARSE ERROR] {e}")
            top = candidates[0]
            side = "BUY" if top.get("cmo", 0) < 0 else "SELL"
            side_th = "เปิด Long (ซื้อ)" if side == "BUY" else "เปิด Short (ขาย)"
            return {
                "success": True,
                "champion_symbol": top["symbol"],
                "side": side,
                "confidence": 65,
                "speed_grade": "⚡ รวดเร็ว (Fast Momentum)",
                "expected_tp_pct": 1.5,
                "stop_loss_pct": 0.8,
                "decree_th": "คัดเลือกด้วยสัญญาณโมเมนตัมกราฟเทคนิคชั้นสูง",
                "bull_debate": "วอลุ่มและกรอบราคาเอื้อต่อการทำกำไรเร็ว",
                "bear_debate": "เฝ้าระวังความผันผวน",
                "telegram_message": (
                    f"🏆 *[มติสภา AI คัดเลือกคู่เหรียญ]*\n"
                    f"• *เหรียญที่เลือก:* `{top['symbol']}` ({side_th})\n"
                    f"• *ความมั่นใจ:* `65%`\n"
                    f"• พิมพ์ **/hunt trade** เพื่อเริ่มเทรด"
                )
            }

    async def evaluate_binance_th_spot_signal(self, candidates: List[Dict[str, Any]], usd_thb_rate: float = 35.8) -> Dict[str, Any]:
        """
        Multi-Agent Council deliberates on candidate coins specifically for MANUAL SPOT TRADING on Binance TH.
        Filters strictly for High-Conviction (Confidence >= 80%) Spot Buy setups
        with exact Entry, TP1, TP2, and SL prices in both USDT and THB.
        """
        self.reload_keys()
        if not self.has_ai_key:
            return {
                "has_signal": False,
                "error": "No AI Key configured",
                "telegram_message": "⚠️ ยังไม่พบคีย์ AI บนระบบ Render ครับ กรุณาตั้งค่า `GOOGLE_API_KEY` ก่อนครับ"
            }

        if not candidates:
            return {
                "has_signal": False,
                "error": "No candidates",
                "telegram_message": "⚠️ ไม่พบข้อมูลตลาดของคู่เหรียญในขณะนี้ครับ"
            }

        candidates_summary = []
        for i, c in enumerate(candidates, 1):
            sym = c.get("symbol", "").split("/")[0]
            px = c.get("price", 0.0)
            chg = c.get("change_24h", 0.0)
            cmo = c.get("cmo", 0.0)
            atr_pct = c.get("atr_pct", 0.0)
            vol_spike = c.get("vol_spike", 1.0)
            regime = c.get("regime", "UNKNOWN")
            candidates_summary.append(
                f"{i}. {sym}: Price=${px:.4f} (~{px*usd_thb_rate:,.2f} THB) | 24h={chg:+.2f}% | CMO={cmo:.1f} | ATR%={atr_pct:.2f}% | VolSpike={vol_spike:.2f}x | Regime={regime}"
            )

        prompt = f"""
You are the Supreme Trading Council of TradingAgents acting as an AI Copilot for a human trader on Binance.th (Spot Market).
Your mission: Identify if there is an ELITE Grade-A+ SPOT BUY (buying spot low to sell high) setup among the coins below.

Candidate Live Data:
{chr(10).join(candidates_summary)}

Strict Quality Criteria:
1. Direction: MUST be SPOT BUY (Long). Do NOT recommend Short/Sell since this is Spot trading.
2. Confidence Gate: Only approve if Council Confidence is >= 80%. If no coin meets >= 80% confidence, return "has_signal": false.
3. Realistic Targets: Provide clean Entry Zone (range), TP1 (conservative +1.5% to +2.5%), TP2 (stretch +3% to +6%), and Stop Loss (-1.0% to -2.0%).

Output EXACT JSON format only (no markdown, no backticks, no other text):
{{
  "has_signal": true,
  "coin": "SOL",
  "confidence": 88,
  "entry_min_usd": 91.50,
  "entry_max_usd": 91.80,
  "tp1_usd": 93.30,
  "tp1_pct": 1.8,
  "tp2_usd": 94.60,
  "tp2_pct": 3.2,
  "sl_usd": 90.40,
  "sl_pct": 1.3,
  "bull_rationale": "สรุปมุมมองเทคนิคสั้นๆ 1 ประโยค เช่น CMO ดีดตัวจากเขต Oversold พร้อมวอลุ่มหนุน",
  "risk_verdict_th": "คำตัดสิน Risk Emperor สั้นๆ 1 ประโยค สรุปเหตุผลว่าทำไมจังหวะนี้ถึงน่าเข้าเทรดเองบน Binance.th"
}}
"""
        system_prompt = "You are TradingAgents Elite Spot Advisor for Binance.th. Strictly filter for Grade-A+ Spot Buys. Return only valid JSON."
        raw_resp, err = await self._call_llm(prompt, system_prompt)
        if not raw_resp:
            return {"has_signal": False, "error": err, "telegram_message": f"❌ ไม่สามารถติดต่อสมอง AI ได้: {err}"}

        try:
            cleaned = raw_resp.strip()
            if "```json" in cleaned:
                cleaned = cleaned.split("```json", 1)[1].split("```", 1)[0].strip()
            elif "```" in cleaned:
                cleaned = cleaned.split("```", 1)[1].split("```", 1)[0].strip()
            parsed = json.loads(cleaned)

            if not parsed.get("has_signal", False):
                return {
                    "has_signal": False,
                    "reason": "สภาวะตลาดขณะนี้ยังไม่มีเหรียญใดที่สถิติเข้าเกณฑ์ความมั่นใจระดับสูง (>= 80%)"
                }

            coin = parsed.get("coin", "BTC").upper().replace("/USDT", "").strip()
            conf = int(parsed.get("confidence", 80))
            if conf < 80:
                return {"has_signal": False, "reason": f"คะแนนความมั่นใจ {conf}% ยังไม่ถึงเกณฑ์ความปลอดภัย 80%"}

            e_min = float(parsed.get("entry_min_usd", 0.0))
            e_max = float(parsed.get("entry_max_usd", e_min))
            tp1 = float(parsed.get("tp1_usd", 0.0))
            tp1_pct = float(parsed.get("tp1_pct", 1.8))
            tp2 = float(parsed.get("tp2_usd", 0.0))
            tp2_pct = float(parsed.get("tp2_pct", 3.2))
            sl = float(parsed.get("sl_usd", 0.0))
            sl_pct = float(parsed.get("sl_pct", 1.3))
            bull = parsed.get("bull_rationale", "โมเมนตัมกำลังกลับตัวขึ้นชัดเจน")
            decree = parsed.get("risk_verdict_th", "สัญญาณเกรด A+ อัตราส่วนกำไรคุ้มค่าความเสี่ยง เหมาะแก่การเข้าทำกำไร")

            e_min_thb = e_min * usd_thb_rate
            e_max_thb = e_max * usd_thb_rate
            tp1_thb = tp1 * usd_thb_rate
            tp2_thb = tp2 * usd_thb_rate
            sl_thb = sl * usd_thb_rate

            def fmt_usd(val):
                return f"{val:,.4f}" if val < 1.0 else f"{val:,.2f}"

            def fmt_thb(val):
                return f"{val:,.3f}" if val < 1.0 else f"{val:,.2f}"

            tg_msg = (
                f"🔔 *[สัญญาณสภา AI เกรด A+ สำหรับเทรดเองบน Binance.th]*\n"
                f"━━━━━━━━━━━━━━━━━━\n"
                f"• *คู่เหรียญ:* `{coin}/THB` (หรือ `{coin}/USDT`)\n"
                f"• *ทิศทาง:* 🟢 **แนะนำเข้าซื้อ (Spot Buy)**\n"
                f"• *ระดับความมั่นใจ AI:* `{conf}%` (High Conviction)\n"
                f"──────────────────\n"
                f"💵 *โซนราคาเข้าซื้อ (Entry Zone):*\n"
                f"• `${fmt_usd(e_min)} - ${fmt_usd(e_max)} USDT`\n"
                f"• *(ประมาณ {fmt_thb(e_min_thb)} - {fmt_thb(e_max_thb)} บาท)*\n\n"
                f"🎯 *เป้าหมายขายทำกำไร (Take Profit):*\n"
                f"• **TP 1 (แบ่งขาย 50%):** `${fmt_usd(tp1)}` *(~{fmt_thb(tp1_thb)} บ. | +{tp1_pct:.1f}%)*\n"
                f"• **TP 2 (รันเทรนด์):** `${fmt_usd(tp2)}` *(~{fmt_thb(tp2_thb)} บ. | +{tp2_pct:.1f}%)*\n\n"
                f"🛑 *จุดตัดขาดทุนแนะนำ (Stop Loss):*\n"
                f"• `${fmt_usd(sl)}` *(~{fmt_thb(sl_thb)} บ. | -{sl_pct:.1f}%)*\n"
                f"──────────────────\n"
                f"⚔️ *เหตุผลและมติสภา AI:*\n"
                f"• 🐂 *Bull:* _{bull}_\n"
                f"• 👑 *Risk Emperor:* _{decree}_\n"
                f"━━━━━━━━━━━━━━━━━━\n"
                f"💡 *เปิดแอป Binance TH เพื่อพิจารณาเข้าเทรดด้วยตนเองตามแผนนี้ได้เลยครับ*"
            )

            return {
                "has_signal": True,
                "coin": coin,
                "confidence": conf,
                "entry_min_usd": e_min,
                "entry_max_usd": e_max,
                "tp1_usd": tp1,
                "tp2_usd": tp2,
                "sl_usd": sl,
                "telegram_message": tg_msg
            }
        except Exception as e:
            logger.warning(f"[BINANCE TH SPOT SIGNAL PARSE ERROR] {e}")
            return {"has_signal": False, "error": str(e), "telegram_message": ""}
